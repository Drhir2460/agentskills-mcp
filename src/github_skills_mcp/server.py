from __future__ import annotations

import asyncio
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

SERVER_NAME = "github_skills_mcp"
CHARACTER_LIMIT = 25000
DEFAULT_CONFIG_PATH = "repos.json"
DEFAULT_INSTALL_DIR = "downloaded-skills"
RAW_BASE_URL = "https://raw.githubusercontent.com"
API_BASE_URL = "https://api.github.com"
SKILL_FILE_NAME = "SKILL.md"
DEFAULT_REPOS_DATA = [
    {
        "name": "skills-collection-1",
        "owner": "pinkpixel-dev",
        "repo": "skills-collection-1",
        "ref": "main",
        "root": "SKILLS",
    },
    {
        "name": "skills-collection-2",
        "owner": "pinkpixel-dev",
        "repo": "skills-collection-2",
        "ref": "main",
        "root": "SKILLS",
    },
]

mcp = FastMCP(SERVER_NAME)


def _truncate(text: str, limit: int = CHARACTER_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 100] + "\n\n[truncated]"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "skill"


@dataclass(frozen=True)
class RepoConfig:
    name: str
    owner: str
    repo: str
    ref: str = "main"
    root: str = ""
    github_token_env: str = "GITHUB_TOKEN"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def normalized_root(self) -> str:
        return self.root.strip("/").strip()

    def matches_alias(self, value: str) -> bool:
        return value in {self.name, self.full_name}


@dataclass(frozen=True)
class SkillRecord:
    repo_name: str
    repo_full_name: str
    ref: str
    skill_name: str
    skill_slug: str
    directory: str
    skill_file_path: str
    score: int
    matched_terms: tuple[str, ...]


class RepoConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=120)
    owner: str = Field(..., min_length=1, max_length=120)
    repo: str = Field(..., min_length=1, max_length=120)
    ref: str = Field(default="main", min_length=1, max_length=120)
    root: str = Field(default="", max_length=300)
    github_token_env: str = Field(default="GITHUB_TOKEN", min_length=1, max_length=120)

    def to_repo_config(self) -> RepoConfig:
        return RepoConfig(**self.model_dump())


class ListReposInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_format: Literal["markdown", "json"] = Field(
        default="markdown",
        description="Output format."
    )


class SearchSkillsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Free-text query such as 'figma design skill' or 'security review'.",
    )
    repo: str | None = Field(
        default=None,
        description="Optional configured repo name or owner/repo to narrow the search."
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum matching skills to return.")
    response_format: Literal["markdown", "json"] = Field(default="markdown")


class GetSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    skill_slug: str = Field(
        ...,
        min_length=1,
        max_length=240,
        description="Skill identifier from search results, for example 'public-skills:figma-implement-design'.",
    )
    max_files: int = Field(default=12, ge=1, le=50, description="Maximum files to include from the skill directory.")
    max_chars_per_file: int = Field(default=5000, ge=200, le=20000, description="Maximum characters per file body.")
    response_format: Literal["markdown", "json"] = Field(default="markdown")


class InstallSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    skill_slug: str = Field(..., min_length=1, max_length=240)
    destination_dir: str | None = Field(
        default=None,
        description="Optional output directory. Defaults to GITHUB_SKILLS_INSTALL_ROOT or ./downloaded-skills."
    )
    overwrite: bool = Field(default=False, description="Whether to overwrite an existing destination.")
    preserve_repo_prefix: bool = Field(
        default=True,
        description="Whether to nest the skill under a repo-specific directory to avoid collisions.",
    )


class SuggestSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request: str = Field(
        ...,
        min_length=5,
        max_length=400,
        description="The capability the user wants, for example 'Create a skill for scraping docs sites and packaging results'.",
    )
    limit: int = Field(default=3, ge=1, le=8, description="How many similar skills to use as grounding.")
    response_format: Literal["markdown", "json"] = Field(default="markdown")

    @field_validator("request")
    @classmethod
    def validate_request(cls, value: str) -> str:
        if len(value.split()) < 2:
            raise ValueError("Provide a slightly more descriptive request so the server can ground the scaffold.")
        return value


_tree_cache: dict[tuple[str, str, str], list[str]] = {}


def _parse_repo_data(raw: Any) -> list[RepoConfig]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Repo configuration must be a non-empty JSON array.")
    return [RepoConfigModel.model_validate(item).to_repo_config() for item in raw]


def _merge_repos(base: list[RepoConfig], extra: list[RepoConfig]) -> list[RepoConfig]:
    merged: dict[str, RepoConfig] = {}
    for repo in base + extra:
        merged[repo.name] = repo
    return list(merged.values())


def _load_repos() -> list[RepoConfig]:
    include_defaults = os.environ.get("GITHUB_SKILLS_INCLUDE_DEFAULTS", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    default_repos = _parse_repo_data(DEFAULT_REPOS_DATA) if include_defaults else []
    config_path = Path(os.environ.get("GITHUB_SKILLS_CONFIG", DEFAULT_CONFIG_PATH))
    raw_config = os.environ.get("GITHUB_SKILLS_REPOS")
    replace_defaults = os.environ.get("GITHUB_SKILLS_REPLACE_DEFAULTS", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    if raw_config:
        configured_repos = _parse_repo_data(json.loads(raw_config))
    elif config_path.exists():
        configured_repos = _parse_repo_data(json.loads(config_path.read_text()))
    else:
        configured_repos = []

    repos = configured_repos if replace_defaults else _merge_repos(default_repos, configured_repos)

    if not repos:
        raise ValueError(
            "No repo configuration found. Enable built-in defaults or set GITHUB_SKILLS_REPOS / repos.json."
        )

    return repos


def _find_repo(repo_alias: str | None) -> RepoConfig | None:
    if repo_alias is None:
        return None

    for repo in _load_repos():
        if repo.matches_alias(repo_alias):
            return repo

    raise ValueError(f"Unknown repo '{repo_alias}'. Use github_skills_list_repositories first.")


def _github_request(url: str, token_env: str) -> Any:
    token = os.environ.get(token_env) or os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": SERVER_NAME,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        message = body or exc.reason
        if exc.code == 403:
            raise ValueError(
                "GitHub API denied the request. If these repos are private or rate-limited, set GITHUB_TOKEN."
            ) from exc
        if exc.code == 404:
            raise ValueError(f"GitHub resource not found for URL: {url}") from exc
        raise ValueError(f"GitHub API error {exc.code}: {message}") from exc


def _raw_request(url: str, token_env: str) -> str:
    token = os.environ.get(token_env) or os.environ.get("GITHUB_TOKEN")
    headers = {"User-Agent": SERVER_NAME}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _contents_request(url: str, token_env: str) -> bytes:
    token = os.environ.get(token_env) or os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": SERVER_NAME,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _get_repo_tree(repo: RepoConfig) -> list[str]:
    cache_key = (repo.owner, repo.repo, repo.ref)
    if cache_key in _tree_cache:
        return _tree_cache[cache_key]

    branch_url = f"{API_BASE_URL}/repos/{repo.owner}/{repo.repo}/branches/{urllib.parse.quote(repo.ref)}"
    branch_data = _github_request(branch_url, repo.github_token_env)
    tree_sha = branch_data["commit"]["commit"]["tree"]["sha"]

    tree_url = f"{API_BASE_URL}/repos/{repo.owner}/{repo.repo}/git/trees/{tree_sha}?recursive=1"
    tree_data = _github_request(tree_url, repo.github_token_env)
    paths = [item["path"] for item in tree_data.get("tree", []) if item.get("type") == "blob"]
    _tree_cache[cache_key] = paths
    return paths


def _iter_skill_records(repo: RepoConfig) -> list[SkillRecord]:
    root_prefix = repo.normalized_root
    records: list[SkillRecord] = []

    for path in _get_repo_tree(repo):
        if not path.endswith(f"/{SKILL_FILE_NAME}") and path != SKILL_FILE_NAME:
            continue
        if root_prefix and not (path == root_prefix + f"/{SKILL_FILE_NAME}" or path.startswith(root_prefix + "/")):
            continue

        directory = path[: -len(SKILL_FILE_NAME)].rstrip("/")
        relative_directory = directory
        if root_prefix and directory.startswith(root_prefix):
            relative_directory = directory[len(root_prefix):].strip("/")

        skill_name = relative_directory.split("/")[-1] if relative_directory else repo.repo
        records.append(
            SkillRecord(
                repo_name=repo.name,
                repo_full_name=repo.full_name,
                ref=repo.ref,
                skill_name=skill_name,
                skill_slug=f"{repo.name}:{skill_name}",
                directory=directory,
                skill_file_path=path,
                score=0,
                matched_terms=tuple(),
            )
        )

    return records


def _score_skill(query: str, record: SkillRecord) -> SkillRecord:
    tokens = [token for token in re.split(r"[^a-z0-9]+", query.lower()) if len(token) > 1]
    haystack = f"{record.skill_name} {record.directory}".lower()
    matched = [token for token in tokens if token in haystack]
    score = len(matched) * 10

    if query.lower() in record.skill_name.lower():
        score += 25
    if record.skill_name.lower().startswith(query.lower()):
        score += 15

    return SkillRecord(
        repo_name=record.repo_name,
        repo_full_name=record.repo_full_name,
        ref=record.ref,
        skill_name=record.skill_name,
        skill_slug=record.skill_slug,
        directory=record.directory,
        skill_file_path=record.skill_file_path,
        score=score,
        matched_terms=tuple(sorted(set(matched))),
    )


def _all_skill_records(repo_alias: str | None = None) -> list[SkillRecord]:
    repos = [_find_repo(repo_alias)] if repo_alias else _load_repos()
    return [record for repo in repos if repo for record in _iter_skill_records(repo)]


def _lookup_skill(skill_slug: str) -> tuple[RepoConfig, SkillRecord]:
    if ":" not in skill_slug:
        raise ValueError("Skill slug must look like 'repo-name:skill-name'.")

    repo_alias, _skill_name = skill_slug.split(":", 1)
    repo = _find_repo(repo_alias)
    if repo is None:
        raise ValueError(f"Repo '{repo_alias}' is not configured.")

    for record in _iter_skill_records(repo):
        if record.skill_slug == skill_slug:
            return repo, record

    raise ValueError(f"Skill '{skill_slug}' was not found. Re-run github_skills_search_skills first.")


def _github_contents_url(repo: RepoConfig, path: str) -> str:
    encoded_path = urllib.parse.quote(path)
    encoded_ref = urllib.parse.quote(repo.ref)
    return f"{API_BASE_URL}/repos/{repo.owner}/{repo.repo}/contents/{encoded_path}?ref={encoded_ref}"


def _raw_file_url(repo: RepoConfig, path: str) -> str:
    encoded_parts = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return f"{RAW_BASE_URL}/{repo.owner}/{repo.repo}/{urllib.parse.quote(repo.ref)}/{encoded_parts}"


def _list_skill_files(repo: RepoConfig, record: SkillRecord) -> list[str]:
    prefix = f"{record.directory}/" if record.directory else ""
    files = []
    for path in _get_repo_tree(repo):
        if prefix:
            if path.startswith(prefix):
                files.append(path)
        elif "/" not in path:
            files.append(path)
        else:
            files.append(path)
    return sorted(set(files))


def _fetch_text_file(repo: RepoConfig, path: str) -> str:
    return _raw_request(_raw_file_url(repo, path), repo.github_token_env)


def _download_file(repo: RepoConfig, path: str) -> bytes:
    return _contents_request(_github_contents_url(repo, path), repo.github_token_env)


def _render_markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No results."
    header = "| " + " | ".join(rows[0].keys()) + " |"
    divider = "| " + " | ".join(["---"] * len(rows[0])) + " |"
    lines = [header, divider]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row.values()) + " |")
    return "\n".join(lines)


@mcp.tool(
    name="github_skills_list_repositories",
    annotations={
        "title": "List configured skill repositories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def github_skills_list_repositories(params: ListReposInput) -> str:
    """List the GitHub skill repositories configured for this MCP server."""
    repos = await asyncio.to_thread(_load_repos)
    payload = [
        {
            "name": repo.name,
            "repo": repo.full_name,
            "ref": repo.ref,
            "root": repo.normalized_root or ".",
        }
        for repo in repos
    ]
    if params.response_format == "json":
        return json.dumps(payload, indent=2)

    return _render_markdown_table(payload)


@mcp.tool(
    name="github_skills_search_skills",
    annotations={
        "title": "Search for skills across configured GitHub repositories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def github_skills_search_skills(params: SearchSkillsInput) -> str:
    """Search skill directories by matching the query against skill names and paths."""
    records = await asyncio.to_thread(_all_skill_records, params.repo)
    scored = [_score_skill(params.query, record) for record in records]
    matches = [record for record in scored if record.score > 0]
    matches.sort(key=lambda item: (-item.score, item.skill_slug))
    selected = matches[: params.limit]

    payload = [
        {
            "skill_slug": record.skill_slug,
            "repo": record.repo_full_name,
            "path": record.directory or ".",
            "score": record.score,
            "matched_terms": list(record.matched_terms),
        }
        for record in selected
    ]

    if params.response_format == "json":
        return json.dumps(
            {
                "query": params.query,
                "count": len(payload),
                "results": payload,
            },
            indent=2,
        )

    if not payload:
        return (
            f"No skills matched '{params.query}'. Try broader terms or use "
            "`github_skills_list_repositories` to confirm the configured repos."
        )

    intro = f"Found {len(payload)} matching skill(s) for `{params.query}`.\n\n"
    return intro + _render_markdown_table(payload)


@mcp.tool(
    name="github_skills_get_skill",
    annotations={
        "title": "Read a skill from GitHub",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def github_skills_get_skill(params: GetSkillInput) -> str:
    """Fetch the key files for a specific skill directory from GitHub."""
    repo, record = await asyncio.to_thread(_lookup_skill, params.skill_slug)
    files = await asyncio.to_thread(_list_skill_files, repo, record)
    selected_files = files[: params.max_files]

    file_payload = []
    for path in selected_files:
        text = await asyncio.to_thread(_fetch_text_file, repo, path)
        file_payload.append(
            {
                "path": path,
                "content": _truncate(text, params.max_chars_per_file),
            }
        )

    payload = {
        "skill_slug": record.skill_slug,
        "repo": repo.full_name,
        "ref": repo.ref,
        "directory": record.directory or ".",
        "files": file_payload,
        "total_files_in_skill": len(files),
        "truncated_file_count": max(0, len(files) - len(selected_files)),
    }

    if params.response_format == "json":
        return json.dumps(payload, indent=2)

    sections = [
        f"# {record.skill_slug}",
        f"- Repo: `{repo.full_name}`",
        f"- Ref: `{repo.ref}`",
        f"- Directory: `{record.directory or '.'}`",
        f"- Files shown: {len(selected_files)} of {len(files)}",
    ]
    for item in file_payload:
        sections.append(f"\n## {item['path']}\n```md\n{item['content']}\n```")
    return _truncate("\n".join(sections))


@mcp.tool(
    name="github_skills_install_skill",
    annotations={
        "title": "Install a skill from GitHub to local disk",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def github_skills_install_skill(params: InstallSkillInput) -> str:
    """Download a skill directory from GitHub into a local folder."""
    repo, record = await asyncio.to_thread(_lookup_skill, params.skill_slug)
    files = await asyncio.to_thread(_list_skill_files, repo, record)

    base_dir = Path(
        params.destination_dir
        or os.environ.get("GITHUB_SKILLS_INSTALL_ROOT")
        or DEFAULT_INSTALL_DIR
    )
    target_dir = base_dir
    if params.preserve_repo_prefix:
        target_dir = target_dir / _safe_slug(repo.name)
    target_dir = target_dir / _safe_slug(record.skill_name)

    if target_dir.exists() and any(target_dir.iterdir()) and not params.overwrite:
        raise ValueError(
            f"Destination '{target_dir}' already exists. Re-run with overwrite=true or choose another destination_dir."
        )

    target_dir.mkdir(parents=True, exist_ok=True)

    written_files: list[str] = []
    for path in files:
        relative_path = Path(path).relative_to(record.directory) if record.directory else Path(path)
        content = await asyncio.to_thread(_download_file, repo, path)
        output_path = target_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        written_files.append(str(output_path))

    return _truncate(
        json.dumps(
            {
                "installed_skill": record.skill_slug,
                "repo": repo.full_name,
                "target_dir": str(target_dir.resolve()),
                "file_count": len(written_files),
                "files": written_files,
            },
            indent=2,
        )
    )


@mcp.tool(
    name="github_skills_suggest_skill_scaffold",
    annotations={
        "title": "Suggest a new skill scaffold grounded in similar GitHub skills",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def github_skills_suggest_skill_scaffold(params: SuggestSkillInput) -> str:
    """Create a grounded starter scaffold for a new skill using the closest existing skills as examples."""
    records = await asyncio.to_thread(_all_skill_records, None)
    scored = [_score_skill(params.request, record) for record in records]
    matches = [record for record in scored if record.score > 0]
    matches.sort(key=lambda item: (-item.score, item.skill_slug))
    selected = matches[: params.limit]

    examples = []
    for record in selected:
        repo = _find_repo(record.repo_name)
        if repo is None:
            continue
        try:
            body = await asyncio.to_thread(_fetch_text_file, repo, record.skill_file_path)
        except Exception:
            body = ""
        examples.append(
            {
                "skill_slug": record.skill_slug,
                "path": record.directory or ".",
                "excerpt": _truncate(body, 1200),
            }
        )

    suggested_name = _safe_slug(params.request.lower())[:60]
    scaffold = textwrap.dedent(
        f"""\
        ---
        name: {suggested_name}
        description: This skill should be used when the user asks for {params.request.strip()}.
        ---

        # Purpose

        Explain what the skill helps another agent accomplish and the boundaries of the workflow.

        # When to Use

        Trigger this skill when the request clearly aligns with {params.request.strip()}.

        # Workflow

        1. Inspect the local project context before making assumptions.
        2. Load only the minimum references needed to answer or implement the request.
        3. Prefer bundled scripts for repetitive or deterministic tasks.
        4. Return concise results and note any missing inputs or follow-up steps.

        # Bundled Resources

        - Add scripts in `scripts/` for repeatable logic.
        - Add references in `references/` for domain knowledge or API docs.
        - Add assets in `assets/` only when they are reused in final outputs.
        """
    ).strip()

    payload = {
        "request": params.request,
        "suggested_skill_name": suggested_name,
        "scaffold": scaffold,
        "grounding_examples": examples,
    }

    if params.response_format == "json":
        return json.dumps(payload, indent=2)

    parts = [
        f"# Suggested scaffold for `{suggested_name}`",
        "## Starter SKILL.md",
        f"```md\n{scaffold}\n```",
    ]
    if examples:
        parts.append("## Grounding examples")
        for example in examples:
            parts.append(
                f"### {example['skill_slug']} ({example['path']})\n```md\n{example['excerpt']}\n```"
            )
    return _truncate("\n\n".join(parts))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
