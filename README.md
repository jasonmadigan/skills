# skills

Claude Code plugin by Jason Madigan.

## Skills

| Skill | Description |
|-|-|
| `external-contribs` | Find external issues/PRs in a GitHub org that need attention |
| `gdoc-sync` | Sync a markdown file with one tab of a Google Doc, editing in place so comments on untouched text survive |

## Install

```
/plugin marketplace add jasonmadigan/skills
/plugin install jasonmadigan
```

Then:

```
/jasonmadigan:external-contribs Kuadrant
/jasonmadigan:gdoc-sync push notes.md to https://docs.google.com/document/d/<id>/edit as you@example.com
```

## Prerequisites

- `external-contribs`: `gh` (GitHub CLI), authenticated, and `jq`
- `gdoc-sync`: `uv`, and a [workspace-mcp](https://workspacemcp.com/) OAuth token for the Google account in `~/.google_workspace_mcp/credentials/<email>.json` (or `WORKSPACE_MCP_CREDENTIALS_DIR`). Without the Drive scope it cannot warn about comments a push would orphan
