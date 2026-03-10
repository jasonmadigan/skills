---
name: external-contribs
description: Find external contributions (issues, PRs, comments) in a GitHub org that may need attention. Use when checking for community engagement or triaging outside contributions.
allowed-tools: Bash
---

# External Contributions Finder

Find issues, PRs, and optionally comments from non-members in a GitHub org.

Arguments: $ARGUMENTS
- First argument must be the org name (e.g. `Kuadrant`).
- A number (e.g. `30`): use that as the lookback window in days. Default 90.
- `comments`: also scan for recent comments from non-members (slower, hits every repo).

Examples:
- `/external-contribs Kuadrant` — Kuadrant org, last 90 days
- `/external-contribs Kuadrant 30` — Kuadrant org, last 30 days
- `/external-contribs some-org comments` — some-org, last 90 days, include comments

## Steps

### 1. Parse arguments

Extract the org name (first non-numeric, non-keyword argument), lookback days (numeric argument, default 90), and whether `comments` flag is present.

### 2. Fetch org members

```bash
MEMBERS=$(gh api --paginate orgs/{ORG}/members --jq '.[].login' | tr '[:upper:]' '[:lower:]' | sort -u)
```

### 3. Search and filter

Search open issues and PRs, then use jq to filter out members and bots in one pass. Build the members list as a JSON array and pass it to jq with `--argjson`:

```bash
MEMBERS_JSON=$(echo "$MEMBERS" | jq -R . | jq -s .)

gh search issues --owner {ORG} --state open --created ">=YYYY-MM-DD" --limit 500 \
  --json repository,number,title,author,createdAt,url | \
  jq --argjson members "$MEMBERS_JSON" \
  '[.[] | select(.author.is_bot == false)
    | select(.author.login | ascii_downcase as $a | ($a | test("\\[bot\\]|dependabot|renovate|github-actions")) | not)
    | select(.author.login | ascii_downcase as $a | $members | index($a) | not)]'
```

Same for PRs with `gh search prs`.

Important: do all jq filtering in jq itself, not in shell loops with `!` (zsh interprets `!` as history expansion and breaks).

### 4. Check for org member responses

For each external item, check comments and (for PRs) reviews:

```bash
gh api --paginate "repos/{ORG}/{repo}/issues/{number}/comments" --jq '.[].user.login'
gh api --paginate "repos/{ORG}/{repo}/pulls/{number}/reviews" --jq '.[].user.login'
```

Compare commenters/reviewers against the members list (case-insensitive). Mark as responded if any match.

### 5. Optional: scan for external comments

If `comments` argument is passed, loop through org repos and check issue comments:

```bash
gh api --paginate "repos/{ORG}/{repo}/issues/comments?since={since}T00:00:00Z&per_page=100" --jq '.[].user.login'
```

## Output format

Present results grouped with "Needs attention" first (no org member response AND older than 7 days).

Use tables with columns: Repo, #, Title, Author, Created, and either Age (for needs-attention) or Last response (for responded items).
