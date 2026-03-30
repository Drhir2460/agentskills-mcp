# Agent Skills MCP

`pinkpixel-agentskills-mcp` is a `stdio` MCP server for discovering, reading, and downloading agent skills from curated GitHub repositories.

GitHub: https://github.com/pinkpixel-dev/agentskills-mcp

It is built for a practical workflow:

- search a large curated skill collection instead of searching all of GitHub
- inspect a matching skill directly from GitHub
- install a skill locally when the agent should actually use it
- suggest a grounded starter scaffold when there is not an exact match

## Why This Exists

This server exists because a large skill library is only useful if an agent can actually find the right skill quickly.

With more than 1,600 collected skills spread across curated repositories, manual browsing becomes slow and noisy. This MCP server gives agents a direct way to search those collections, inspect likely matches, and install the right skill when it is needed.

Skills are genuinely useful when they are easy to discover and apply in context. The goal here is to make a large curated skill archive feel usable instead of overwhelming.

## What the server exposes

- `github_skills_list_repositories`
- `github_skills_search_skills`
- `github_skills_get_skill`
- `github_skills_install_skill`
- `github_skills_suggest_skill_scaffold`

## Example Use

Example user request:

```text
Can you use the pinkpixel-agentskills-mcp tools and find skills for Rust development?
```

Example result:

- The server searches the built-in skill indexes.
- It can identify strong matches like `skills-collection-2:rust-pro` and `skills-collection-2:rust-async-patterns`.
- It can inspect those skill folders directly from GitHub before recommending them.
- It can then install the selected skill locally with the MCP install tool.

This is especially helpful when a broad keyword search would otherwise return noisy matches, such as `rust` appearing inside `trust`.

## Quickstart

Run from PyPI with `uvx`:

```bash
uvx pinkpixel-agentskills-mcp
```

If your environment still prefers the explicit package-to-command form, this works too:

```bash
uvx --from pinkpixel-agentskills-mcp agentskills-mcp
```

Register it in Claude:

```bash
claude mcp add github-skills -- uvx pinkpixel-agentskills-mcp
```

With a GitHub token for better rate limits:

```bash
claude mcp add github-skills --env GITHUB_TOKEN=$GITHUB_TOKEN -- uvx pinkpixel-agentskills-mcp
```

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

For local development:

```bash
uv sync
```

If a user wants to add more sources, they can create `repos.json` from the example:

```bash
cp repos.example.json repos.json
```

## Local Run

This is a `stdio` server. To run it locally from the repo:

```bash
uv run agentskills-mcp
```

For a quick smoke test without leaving a hanging process:

```bash
timeout 5s uv run agentskills-mcp
```

## Claude Registration

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

The published package name is `pinkpixel-agentskills-mcp`.

The server command is available as both:

- `pinkpixel-agentskills-mcp`
- `agentskills-mcp`

That means the most convenient public install path is:

```bash
uvx pinkpixel-agentskills-mcp
```

If you ever hit an environment that does not pick the matching executable automatically, use:

```bash
uvx --from pinkpixel-agentskills-mcp agentskills-mcp
```

For release steps, see [PUBLISHING.md](/home/sizzlebop/PINKPIXEL/PROJECTS/CURRENT/skills-mcp/PUBLISHING.md).

## Notes

- This server uses `stdio`, not HTTP/SSE transport.
- Skill discovery is currently based on finding `SKILL.md` files in configured repos.
- Built-in defaults make the server usable immediately, while optional config lets users extend the source list.
- Search ranking is intentionally simple for the first version and can be upgraded later with repo-specific metadata or embeddings.
- The scaffold tool is meant to help another agent create a new skill grounded in existing examples; it does not replace a full generation pipeline by itself.
- Public-repo access works without credentials; tokens are an optional per-user enhancement, not a baked-in server secret.
