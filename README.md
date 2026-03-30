# GitHub Skills MCP

This project provides an MCP server for working with skill libraries stored in one or more GitHub repositories.

It is designed for the workflow you described:

- search across a growing collection of skill repos
- inspect a matching skill directly from GitHub
- download a skill locally when the agent should install it
- return a grounded starter scaffold when a close match exists but a direct download is not the right move

## What the server exposes

- `github_skills_list_repositories`
- `github_skills_search_skills`
- `github_skills_get_skill`
- `github_skills_install_skill`
- `github_skills_suggest_skill_scaffold`

## Configuration

The server ships with these built-in default sources:

- `pinkpixel-dev/skills-collection-1`
- `pinkpixel-dev/skills-collection-2`

That means the server works out of the box with no `repos.json` at all.

Users can add more repositories in either of these ways:

1. Create `repos.json` in the project root by copying `repos.example.json`
2. Or set `GITHUB_SKILLS_REPOS` to a JSON array with the same schema

Each repo entry supports:

- `name`: short alias used in skill slugs
- `owner`: GitHub owner or org
- `repo`: GitHub repo name
- `ref`: branch or tag to read from
- `root`: optional subdirectory that contains skills
- `github_token_env`: optional environment variable holding a GitHub token

For public repositories, a GitHub token is optional. Users can run anonymously, or provide their own `GITHUB_TOKEN` for higher rate limits.

For private repositories, each user should provide their own token with the access they need. Do not ship your personal token with the server.

### Default and custom source behavior

- By default, custom repos are added on top of the built-in two repos.
- If a custom repo uses the same `name` as a built-in repo, the custom one wins.
- To disable the built-in repos entirely, set `GITHUB_SKILLS_REPLACE_DEFAULTS=true`.
- To disable built-in repos without replacement, set `GITHUB_SKILLS_INCLUDE_DEFAULTS=false`.

## Install

```bash
uv sync
```

If a user wants to add more sources, they can create `repos.json` from the example:

```bash
cp repos.example.json repos.json
```

## Run

For stdio transport:

```bash
uv run agentskills-mcp
```

For a quick smoke test without leaving a hanging process:

```bash
timeout 5s uv run agentskills-mcp
```

## Example Claude Code registration

```bash
claude mcp add github-skills --env GITHUB_TOKEN=$GITHUB_TOKEN -- uv run agentskills-mcp
```

For public repos, users can also add the server without any token:

```bash
claude mcp add github-skills -- uv run agentskills-mcp
```

If you also want a default install target for downloaded skills:

```bash
claude mcp add github-skills \
  --env GITHUB_TOKEN=$GITHUB_TOKEN \
  --env GITHUB_SKILLS_INSTALL_ROOT=/absolute/path/to/skills \
  -- uv run agentskills-mcp
```

## PyPI and uvx

Once published, users can run the server without cloning the repo:

```bash
uvx pinkpixel-agentskills-mcp
```

For release steps, see [PUBLISHING.md](/home/sizzlebop/PINKPIXEL/PROJECTS/CURRENT/skills-mcp/PUBLISHING.md).

## Notes

- Skill discovery is currently based on finding `SKILL.md` files in configured repos.
- Built-in defaults make the server usable immediately, while optional config lets users extend the source list.
- Search ranking is intentionally simple for the first version and can be upgraded later with repo-specific metadata or embeddings.
- The scaffold tool is meant to help another agent create a new skill grounded in existing examples; it does not replace a full generation pipeline by itself.
- Public-repo access works without credentials; tokens are an optional per-user enhancement, not a baked-in server secret.
