# skills

Claude Code plugin by Jason Madigan.

## Skills

| Skill | Description |
|-|-|
| `external-contribs` | Find external issues/PRs in a GitHub org that need attention |
| `gdoc-sync` | Sync a markdown file with one tab of a Google Doc, editing in place so comments on untouched text survive |
| `irish-british-humour` | Write casual or technical prose in a dry Irish or British comedic voice |

## Install

```
/plugin marketplace add jasonmadigan/skills
/plugin install jasonmadigan
```

Then:

```
/jasonmadigan:external-contribs Kuadrant
/jasonmadigan:gdoc-sync push notes.md to https://docs.google.com/document/d/<id>/edit as you@example.com
/jasonmadigan:irish-british-humour write a leaving card for a colleague
```

## Prerequisites

- `external-contribs`: `gh` (GitHub CLI), authenticated, and `jq`
- `gdoc-sync`: `uv`, and a [workspace-mcp](https://workspacemcp.com/) OAuth token for the Google account in `~/.google_workspace_mcp/credentials/<email>.json` (or `WORKSPACE_MCP_CREDENTIALS_DIR`). Without the Drive scope it cannot warn about comments a push would orphan
