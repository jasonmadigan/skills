---
name: gdoc-sync
description: Sync a local markdown file to/from one tab of a Google Doc using exact-index edits that preserve comments and suggestions on untouched text. Use when collaborating on a doc where others leave comments, when the user says "sync to doc", "update the doc", "push changes to Google Docs", "pull from doc", or "sync down" and comment preservation matters.
---

# Google Doc Sync (comment-preserving)

One tab of a Google Doc mirrors one local markdown file. Edits are computed block by block (paragraph, list item, table) with exact indices read from the Docs API, so untouched text, and the comments anchored to it, are never rewritten.

The work is done by the bundled helper, not by hand-built `find_and_replace` calls:

```
uv run ~/.claude/skills/gdoc-sync/scripts/gdoc_sync.py --email <google email> <command> ...
```

Commands: `tabs`, `dump`, `render`, `push`, `pull`, `base`, `lists`.

Set `GDOC_SYNC_EMAIL` to skip `--email`. The helper reuses the workspace-mcp OAuth token store (`~/.google_workspace_mcp/credentials/<email>.json`), so no new consent is needed. It never sends anything else anywhere.

## Setup

Collect once per session:

1. **Local file path**: the `.md` file. It is the source of truth for a push.
2. **Doc ID**: from `docs.google.com/document/d/{ID}/edit`.
3. **Tab ID**: `gdoc_sync.py tabs {ID}` lists `tab_id  title`. A doc always has at least `t.0`. Never sync without a tab: the same heading can exist in several tabs and untargeted `find_and_replace` hits all of them.
4. **Google email** for the credentials.

No doc yet? Create one with `mcp__workspacemcp__create_doc(user_google_email, title)` and use `t.0`.

## Direction

**Push (local to doc)**: "sync up", "push to doc", "update the doc", "sync to doc".
**Pull (doc to local)**: "sync down", "pull from doc", "update the local file", "pull changes".
**Ambiguous**: ask exactly "Which direction? Push (local to doc) or pull (doc to local)?"

## Push

```
gdoc_sync.py push DOC --tab TAB --file local.md --dry-run     # plan only
gdoc_sync.py push DOC --tab TAB --file local.md               # apply
```

Always run the dry run first and show the user the report: sections added, deleted, modified, moved, tables to be created, request count. Push only after they have seen it.

What the helper does:

1. Reads the tab with `documents.get` (tab content included) and parses the local markdown into the same block model.
2. Splits both into sections by heading (heading text, with `#2`, `#3` suffixes for duplicates).
3. **Snapshot check.** `local.md.gdocsync.<tab>.json` holds the tab as it was after the last sync. Sections changed in the doc since then are listed; sections changed on both sides abort the push unless `--force`. No snapshot means edits made in the doc by others cannot be detected: say so.
4. **Block diff inside each modified section.** Equal blocks are left alone. Inserted blocks are inserted after their preceding block; deleted blocks are deleted; changed blocks are deleted and re-inserted. Identity is plain text plus absolute link targets; bold and italic differences alone do not count, so the doc's own emphasis survives.
5. **Tables.** A table that is still a table with the same column count is edited in place, never recreated: changed cells are replaced, and added or removed rows become `insertTableRow` / `deleteTableRow` with the new rows populated from a fresh index read. Only a genuinely new table, or one whose column count changed, is created from scratch after its anchor paragraph. Recreating a table would delete every comment anchored in it, so the row path matters.
6. **Headings.** A doc paragraph whose text equals a local heading that has no heading counterpart is promoted in place with one style request, not deleted.
7. **Moved sections** are reported and left in place. `--allow-move` deletes and re-inserts them, which loses comment anchors on those sections. Same constraint as cut and paste in the Docs UI. Ask before using it.
8. All edits run highest index first, so earlier indices stay valid. Where a delete and an insert share an index the delete goes first, otherwise it removes the text just inserted. Text styles are reset on every inserted range before markers are applied, because inserted text inherits the style of its neighbour.
9. **Lists.** Inserted text takes on the bullet of the paragraph it lands in, and `createParagraphBullets` leaves an already bulleted paragraph alone. A new item that should look exactly like that paragraph (same kind and level) keeps its bullet, and stays in its list, so numbering carries on. Any other item has the bullet dropped and rebuilt. Rebuilt bullets that join the list above put their first item at the level of the item above, whatever its tabs, and nest the rest by tabs relative to it. A first item that has to go deeper gets a placeholder at the level above, deleted in the same batch. One that has to sit shallower cannot join at all: once the tables are in, its whole run of list items is rebuilt from the local file (bullets off, one tab per level, bullets on). That run becomes a new list with the preset's glyphs.
10. Every deferred table anchor is resolved against the document **before** the first write, so a push cannot stop half way and leave a table deleted.
11. Verifies section by section after the push and rewrites the snapshot. Block identity ignores list kind, level and indent, so every list item the push inserted is checked for those and for stray leading tabs as well. A residual difference in the report means something did not apply: read it, do not re-run blindly.

Formatting mapped on insert: `#`..`######` headings, `- ` and `1. ` lists with 2-space nesting, `**bold**`, `*italic*`, `***both***`, `~~strike~~`, `` `code` ``, fenced code blocks, `[text](url)`, tables with `|-|` separators, backslash escapes from Docs exports (`\[`, `\|`, `\+`, ...). Heading anchors like `{#todos}` are stripped.

### Matching the doc's own styling

New content must look like the content already there. Traps, all of them caught the hard way:

- **Inserted text inherits the character style of its neighbour**, not just bold and italic: font size and colour come with it. Text inserted next to a heading arrives at the heading's point size and stays there. Every inserted range is therefore reset over `bold,italic,strikethrough,underline,link,weightedFontFamily,fontSize,foregroundColor,backgroundColor` before its own markers are applied. Link colour and underline come back automatically when the link is set.
- **Applying a named paragraph style clears direct character formatting** in that paragraph. Bold, italic and monospace vanish if `updateParagraphStyle` runs after `updateTextStyle`. Paragraph styles go first, character styles second, always. In table cells this means the cell's paragraph is normalised in the same request batch, before its text is styled.
- **Inline code follows the tab, not a default.** `pick_code_style` tallies the monospace runs already in the tab and reuses the dominant face and colour (this doc: Roboto Mono in dark green). Hardcoding Courier New makes every inserted snippet stand out.
- **Fenced code blocks are marked with a light grey paragraph shading** so a later read can tell them from a paragraph that merely contains inline code. Detection tolerates the rounding Docs applies to colour components, and accepts any uniform light grey.
- **New tables copy the column widths** of an existing table with the same column count when that table uses fixed widths.
- **A list item's indent belongs to its list level.** Zeroing the indent of a paragraph that arrived with a bullet and then calling `createParagraphBullets` leaves the item at the margin with its wrapped lines under the glyph, and any leading tabs as literal text. Earlier versions did exactly that to every item inserted in front of another; `lists --fix` resets the indent on items left like that.

New tables also pin their header row (`pinTableHeaderRows`), bold it, and inherit the doc's cell padding and alignment, matching the tables already in the doc.

After a push that created a table, compare it against an existing table of similar shape: pinned header, padding, content alignment, header bold, explicit font sizes (there should be none) and column width type. `dump` plus a short API read is enough.

Not mapped: images, footnotes, blockquotes (rendered as plain paragraphs), links that are not absolute URLs (relative paths and `#anchors` keep their text but get no link, because Docs turns them into broken `http://` links), person and file smart chips (read as their display text and left alone when unchanged).

Never bypass the helper with `find_and_replace_doc` on this kind of doc. It matches across all tabs, never inside table cells, and the uniqueness check it needs is meaningless when a heading exists in more than one tab.

### Comments at risk

Before writing, a push lists every comment whose quoted text sits inside text it is about to rewrite, with the author, the comment and the anchor text. If any of those are **unresolved**, the push aborts; `--force` overrides. Resolved comments are reported but do not block. A missing Drive scope degrades to a printed warning rather than a failure.

### Failures

Reads retry with backoff. Writes retry only on HTTP 429, where the request was rejected outright. On a 5xx or a dropped connection mid-write the push stops and tells you to re-run, because the plan is rebuilt from the document's current state on every run: re-running converges, blind retrying could double-apply.

## Pull

```
gdoc_sync.py pull DOC --tab TAB --file local.md --dry-run   # writes local.md.pulled
gdoc_sync.py pull DOC --tab TAB --file local.md             # rewrites local.md
```

The local file is rebuilt section by section in the doc's order. A section whose content matches the doc is copied from the local file **byte for byte**, so untouched markdown keeps its own wording, spacing and fence languages and `git diff` shows only what actually changed in the doc. Changed and new sections are rendered from the doc; a rendered fence gets its language back when the block body is unchanged. Sections the doc no longer has are dropped, and the report names them. Always dry-run first and read the diff.

`render DOC --tab TAB --out f.md` still exists for inspecting a tab without touching anything.

## Known gaps

- Images, footnotes and blockquotes are not mapped; blockquotes arrive as plain paragraphs.
- A fence's language is lost if someone edits that code block in the doc, since a Doc does not record it.
- Two consecutive blank lines in the local file collapse to one on a pull; a Doc cannot represent the difference.
- One local file maps to one tab, by design.
- A change of list level or kind on its own is neither pushed nor pulled: block identity is text plus links.
- A new table needs its anchor paragraph in the doc before the push starts. A table that follows paragraphs added by the same push fails the anchor check: push the text, then the table.
- Sections are keyed by heading text, with `#2`, `#3` for repeats. Adding a section above another of the same name (a new week's `Shipped` above last week's) shifts every later key, so the plan pairs the wrong sections.

## Inspecting

```
gdoc_sync.py dump DOC --tab TAB            # one line per block with indices
gdoc_sync.py dump DOC --tab TAB --json f   # full block model
gdoc_sync.py lists DOC --tab TAB [--fix]   # list items off their level's indent, or starting with a tab
```

`scripts/test_lists_live.py` pushes list edits of every shape to a scratch doc, checks level, indent and list membership of each item, and deletes the doc. Run it after touching the list code.

Use `mcp__workspacemcp__list_document_comments` before a push that deletes or moves anything, and tell the user which comments sit on text that will be rewritten.

## Report

After a push, give the user: sections added, deleted, modified, moved (or left in place with the reason), tables created or cells changed, request count, whether the verification passed, and the snapshot path. After a pull, the sections applied locally.

## When to sync

- After completing a batch of work
- When switching context
- When the user asks
- Before ending a session
