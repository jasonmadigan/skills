#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-api-python-client>=2.100",
#   "google-auth>=2.20",
#   "google-auth-httplib2>=0.2",
# ]
# ///
"""markdown <-> google docs tab sync with exact indices.

reads with documents.get (tab content included), writes with documents.batchUpdate.
credentials come from the workspace-mcp token store, so no new consent is needed.

commands:
  tabs    DOC                      list tabs
  dump    DOC --tab T [--json F]   block model with indices
  render  DOC --tab T [--out F]    tab as markdown (pull side)
  push    DOC --tab T --file MD [--dry-run] [--force] [--allow-move] [--email E]
  pull    DOC --tab T --file MD [--dry-run]   rewrite the local file from the tab
  base    DOC --tab T --file MD    write the sync snapshot without pushing
  lists   DOC --tab T [--fix]      list items indented off their level or starting with a tab
"""
import argparse
import difflib
import json
import os
import pathlib
import re
import sys

CRED_DIR = pathlib.Path(os.environ.get("WORKSPACE_MCP_CREDENTIALS_DIR", pathlib.Path.home() / ".google_workspace_mcp" / "credentials"))
DEFAULT_EMAIL = os.environ.get("GDOC_SYNC_EMAIL")
CODE_FONTS = {"Courier New", "Consolas", "Roboto Mono", "Source Code Pro", "DejaVu Sans Mono", "IBM Plex Mono"}
CODE_STYLE = {"family": "Courier New", "fg": None}  # replaced per tab by pick_code_style()
CODE_BLOCK_SHADING = {"backgroundColor": {"color": {"rgbColor": {"red": 0.945, "green": 0.945, "blue": 0.945}}}}
ORDERED_GLYPHS = {"DECIMAL", "ZERO_DECIMAL", "UPPER_ALPHA", "ALPHA", "UPPER_ROMAN", "ROMAN"}
UL_PRESET = "BULLET_DISC_CIRCLE_SQUARE"
OL_PRESET = "NUMBERED_DECIMAL_ALPHA_ROMAN"
PLACEHOLDER = "x"  # stand-in list item, inserted and deleted in the same batch
STYLE_FIELDS = "bold,italic,strikethrough,underline,link,weightedFontFamily,fontSize,foregroundColor,backgroundColor"


# ---------------------------------------------------------------- google api

def service(email):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    path = CRED_DIR / f"{email}.json"
    if not path.exists():
        sys.exit(f"no workspace-mcp credentials for {email} at {path}")
    info = json.loads(path.read_text())
    info.pop("expiry", None)  # force a refresh check below instead of parsing formats
    creds = Credentials.from_authorized_user_info(info)
    creds.refresh(Request())
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def with_retry(call, write):
    """the plan is recomputed from the document on every run, so aborting is always safe.
    reads retry on any transient error; writes retry only on 429, where the request was rejected
    outright. a 5xx on a write may have applied, so it aborts and asks for a re-run."""
    import random
    import time
    from googleapiclient.errors import HttpError
    delay = 1.0
    for attempt in range(5):
        try:
            return call.execute()
        except HttpError as e:
            code = getattr(e.resp, "status", 0)
            retryable = code == 429 or (not write and 500 <= code < 600)
            if not retryable or attempt == 4:
                if write and 500 <= code < 600:
                    sys.exit(f"write failed with HTTP {code}; it may or may not have applied. "
                             f"re-run the dry run: the plan is rebuilt from the document's current state.")
                raise
        except (ConnectionError, TimeoutError, OSError) as e:
            if write:
                sys.exit(f"write failed in transit ({e}); it may or may not have applied. "
                         f"re-run the dry run: the plan is rebuilt from the document's current state.")
            if attempt == 4:
                raise
        time.sleep(delay + random.random() * 0.3)
        delay *= 2


def get_doc(svc, doc_id):
    return with_retry(svc.documents().get(documentId=doc_id, includeTabsContent=True), write=False)


def iter_tabs(doc):
    def walk(tabs):
        for t in tabs:
            yield t
            yield from walk(t.get("childTabs", []))
    return list(walk(doc.get("tabs", [])))


def find_tab(doc, tab_id):
    for t in iter_tabs(doc):
        if t["tabProperties"]["tabId"] == tab_id:
            return t
    sys.exit(f"tab {tab_id} not found; run `tabs`")


def batch(svc, doc_id, requests, dry):
    if not requests:
        return
    if dry:
        return
    with_retry(svc.documents().batchUpdate(documentId=doc_id, body={"requests": requests}), write=True)


# ---------------------------------------------------------------- text helpers

def ulen(s):
    return len(s.encode("utf-16-le")) // 2


def norm(s):
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


ESC = {"|": "\ue001", "[": "\ue002", "]": "\ue003", "*": "\ue004", "_": "\ue005", "+": "\ue006",
       "-": "\ue007", "#": "\ue008", "`": "\ue009", ">": "\ue00a", "(": "\ue00b", ")": "\ue00c",
       ".": "\ue00d", "!": "\ue00e", "\\": "\ue00f"}
UNESC = {v: k for k, v in ESC.items()}


def protect(s):
    return re.sub(r"\\([|\[\]*_+\-#`>().!\\])", lambda m: ESC[m.group(1)], s)


def unprotect(s):
    for k, v in UNESC.items():
        s = s.replace(k, v)
    return s


# ---------------------------------------------------------------- block model
# paragraph block: {"kind":"p","level":0-6,"list":None|("ul"|"ol",nesting),"code":bool,"runs":[(text,style)],"text":str,"start","end"}
# table block:     {"kind":"table","rows":[[cell]],"start","end"}  cell = {"runs","text","start","end"}
# style: {"bold","italic","strike","code","link"}

def is_code_shade(bg):
    """a uniform light grey marks a code block; Docs rounds the components we send, so compare loosely"""
    rgb = ((bg or {}).get("color") or {}).get("rgbColor")
    if not rgb:
        return False
    vals = [rgb.get(k, 0.0) for k in ("red", "green", "blue")]
    return max(vals) - min(vals) < 0.02 and 0.85 < sum(vals) / 3 < 0.99


def run_style(ts):
    fam = (ts.get("weightedFontFamily") or {}).get("fontFamily")
    st = {"bold": bool(ts.get("bold")), "italic": bool(ts.get("italic")), "strike": bool(ts.get("strikethrough")),
          "code": fam in CODE_FONTS, "link": ((ts.get("link") or {}).get("url"))}
    if st["code"]:
        st["_family"] = fam
        st["_fg"] = json.dumps(ts.get("foregroundColor"), sort_keys=True) if ts.get("foregroundColor") else None
    return st


def sig(st):
    return tuple(sorted((k, v) for k, v in st.items() if not k.startswith("_")))


def merge_runs(runs):
    out = []
    for text, st in runs:
        if not text:
            continue
        if out and sig(out[-1][1]) == sig(st):
            out[-1] = (out[-1][0] + text, st)
        else:
            out.append((text, st))
    return out


def para_from_api(el, lists):
    p = el["paragraph"]
    runs = []
    for e in p.get("elements", []):
        tr = e.get("textRun")
        if tr:
            runs.append((tr.get("content", ""), run_style(tr.get("textStyle", {}))))
        elif "person" in e:
            pp = e["person"].get("personProperties", {})
            st = run_style(e["person"].get("textStyle", {}))
            st["link"] = ("mailto:" + pp["email"]) if pp.get("email") else None
            runs.append((pp.get("name") or pp.get("email", ""), st))
        elif "richLink" in e:
            rp = e["richLink"].get("richLinkProperties", {})
            st = run_style(e["richLink"].get("textStyle", {}))
            st["link"] = rp.get("uri")
            runs.append((rp.get("title") or rp.get("uri", ""), st))
    text = "".join(t for t, _ in runs)
    if text.endswith("\n"):
        text = text[:-1]
        if runs:
            last, st = runs[-1]
            runs[-1] = (last[:-1], st)
    named = p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
    level = 0
    if named.startswith("HEADING_"):
        level = int(named[-1])
    elif named == "TITLE":
        level = 1
    lst = None
    indent_ok = True
    b = p.get("bullet")
    if b:
        nesting = b.get("nestingLevel", 0)
        glyph = ""
        lp = lists.get(b.get("listId"), {}).get("listProperties", {}).get("nestingLevels", [])
        if nesting < len(lp):
            glyph = lp[nesting].get("glyphType", "")
            # a bullet with an explicit indent other than its list level's sits at the wrong offset
            ps = p.get("paragraphStyle", {})
            for k in ("indentStart", "indentFirstLine"):
                if k in ps and ps[k].get("magnitude", 0) != lp[nesting].get(k, {}).get("magnitude", 0):
                    indent_ok = False
        lst = ("ol" if glyph in ORDERED_GLYPHS else "ul", nesting)
    runs = merge_runs(runs)
    code = bool(runs) and all(st["code"] for _, st in runs) and level == 0
    fenced = code and is_code_shade((p.get("paragraphStyle", {}).get("shading") or {}).get("backgroundColor"))
    return {"kind": "p", "level": level, "list": lst, "code": code, "fenced": fenced, "runs": runs, "text": text,
            "indent_ok": indent_ok, "start": el["startIndex"], "end": el["endIndex"]}


def table_from_api(el, lists):
    rows = []
    for r in el["table"]["tableRows"]:
        cells = []
        for c in r["tableCells"]:
            paras = [para_from_api(e, lists) for e in c["content"] if "paragraph" in e]
            runs = []
            for i, p in enumerate(paras):
                if i:
                    runs.append((" ", {"bold": False, "italic": False, "strike": False, "code": False, "link": None}))
                runs.extend(p["runs"])
            runs = merge_runs(runs)
            cells.append({"runs": runs, "text": "".join(t for t, _ in runs),
                          "start": paras[0]["start"] if paras else c["startIndex"] + 1,
                          "end": (paras[-1]["end"] - 1) if paras else c["startIndex"] + 1})
        rows.append(cells)
    return {"kind": "table", "rows": rows, "col_props": el["table"].get("tableStyle", {}).get("tableColumnProperties"),
            "start": el["startIndex"], "end": el["endIndex"]}


def doc_blocks(tab):
    dt = tab["documentTab"]
    lists = dt.get("lists", {})
    blocks = []
    for el in dt["body"]["content"]:
        if "paragraph" in el:
            blocks.append(para_from_api(el, lists))
        elif "table" in el:
            blocks.append(table_from_api(el, lists))
    return blocks


def pick_code_style(blocks):
    """match the inline-code convention the tab already uses (face and colour), so inserted code fits in"""
    global CODE_STYLE
    seen = {}
    def count(runs):
        for text, st in runs:
            if st["code"] and st.get("_family"):
                key = (st["_family"], st.get("_fg"))
                seen[key] = seen.get(key, 0) + 1
    for b in blocks:
        if b["kind"] == "table":
            for r in b["rows"]:
                for c in r:
                    count(c["runs"])
        else:
            count(b["runs"])
    if seen:
        family, fg = max(seen.items(), key=lambda kv: kv[1])[0]
        CODE_STYLE = {"family": family, "fg": json.loads(fg) if fg else None}
    return CODE_STYLE


def segment_end(tab):
    return tab["documentTab"]["body"]["content"][-1]["endIndex"]


# ---------------------------------------------------------------- markdown parsing

INLINE = re.compile(r"(`[^`]+`)|(\[[^\]]+\]\([^)\s]+\))|(\*\*\*.+?\*\*\*)|(\*\*.+?\*\*)|(~~.+?~~)|(?<![\w*])(\*(?!\s)[^*]+?(?<!\s)\*)(?![\w*])")


def parse_inline(text, base=None):
    base = base or {"bold": False, "italic": False, "strike": False, "code": False, "link": None}
    runs = []
    pos = 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], dict(base)))
        tok = m.group(0)
        st = dict(base)
        if m.group(1):
            st["code"] = True
            runs.append((tok[1:-1], st))
        elif m.group(2):
            lm = re.match(r"\[(.+)\]\((.+)\)", tok)
            st["link"] = unprotect(lm.group(2))
            runs.extend(parse_inline(lm.group(1), st))
        elif m.group(3):
            st["bold"] = True
            st["italic"] = True
            runs.extend(parse_inline(tok[3:-3], st))
        elif m.group(4):
            st["bold"] = True
            runs.extend(parse_inline(tok[2:-2], st))
        elif m.group(5):
            st["strike"] = True
            runs.extend(parse_inline(tok[2:-2], st))
        else:
            st["italic"] = True
            runs.extend(parse_inline(tok[1:-1], st))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], dict(base)))
    return runs


def finish_runs(runs):
    runs = [(unprotect(t), st) for t, st in runs]
    return merge_runs(runs)


def split_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def local_blocks(md):
    lines = protect(md.replace("\r\n", "\n")).split("\n")
    blocks = []
    i = 0
    n = len(lines)

    def para(text, level=0, lst=None, code=False, fenced=False):
        runs = finish_runs(parse_inline(text)) if not code else [(unprotect(text), {"bold": False, "italic": False, "strike": False, "code": True, "link": None})]
        return {"kind": "p", "level": level, "list": lst, "code": code, "fenced": fenced, "runs": runs,
                "text": "".join(t for t, _ in runs), "start": None, "end": None}

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            i += 1
            while i < n and not lines[i].startswith("```"):
                blocks.append(para(lines[i], code=True, fenced=True))
                i += 1
            i += 1
            continue
        hm = re.match(r"^(#{1,6}) (.*)$", line)
        if hm:
            text = re.sub(r"\s*\{#[^}]*\}\s*$", "", hm.group(2)).strip()
            blocks.append(para(text, level=len(hm.group(1))))
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                if not re.match(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$", lines[i]):
                    cells = []
                    for c in split_cells(lines[i]):
                        runs = finish_runs(parse_inline(c))
                        cells.append({"runs": runs, "text": "".join(t for t, _ in runs), "start": None, "end": None})
                    rows.append(cells)
                i += 1
            width = max(len(r) for r in rows)
            for r in rows:
                while len(r) < width:
                    r.append({"runs": [], "text": "", "start": None, "end": None})
            blocks.append({"kind": "table", "rows": rows, "start": None, "end": None})
            continue
        lm = re.match(r"^(\s*)([-*]|\d+[.)]) (.*)$", line)
        if lm:
            nesting = len(lm.group(1).replace("\t", "  ")) // 2
            kind = "ol" if lm.group(2)[0].isdigit() else "ul"
            text = lm.group(3)
            i += 1
            while i < n and lines[i].strip() and not re.match(r"^(\s*)([-*]|\d+[.)]) ", lines[i]) and not lines[i].startswith(("#", "|", "```")):
                text += " " + lines[i].strip()
                i += 1
            blocks.append(para(text, lst=(kind, nesting)))
            continue
        text = line.strip()
        quote = text.startswith(">")
        if quote:
            text = text.lstrip("> ").strip()
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(\s*)([-*]|\d+[.)]) ", lines[i]) and not lines[i].startswith(("#", "|", "```")):
            text += " " + lines[i].strip().lstrip("> ")
            i += 1
        blocks.append(para(text))
    return blocks


# ---------------------------------------------------------------- rendering (doc -> markdown)

def render_runs(runs):
    out = []
    for text, st in runs:
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not core:
            out.append(text)
            continue
        if st["code"]:
            core = f"`{core}`"
        else:
            if st["bold"] and st["italic"]:
                core = f"***{core}***"
            elif st["bold"]:
                core = f"**{core}**"
            elif st["italic"]:
                core = f"*{core}*"
            if st["strike"]:
                core = f"~~{core}~~"
        if st["link"]:
            core = f"[{core}]({st['link']})"
        out.append(lead + core + trail)
    return "".join(out)


def render_blocks(blocks):
    lines = []
    prev_list = False
    prev_fence = False
    for b in blocks:
        if prev_fence and (b["kind"] == "table" or not b.get("fenced")):
            lines.append("```")
            lines.append("")
            prev_fence = False
        if b["kind"] == "table":
            if lines and lines[-1] != "":
                lines.append("")
            width = max(len(r) for r in b["rows"])
            for ri, r in enumerate(b["rows"]):
                lines.append("| " + " | ".join(render_runs(c["runs"]).replace("|", "\\|") for c in r) + " |")
                if ri == 0:
                    lines.append("|" + "-|" * width)
            lines.append("")
            prev_list = False
            continue
        if b.get("fenced"):
            if not prev_fence:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append("```")
            lines.append("".join(t for t, _ in b["runs"]))
            prev_fence = True
            prev_list = False
            continue
        if prev_fence:
            lines.append("```")
            lines.append("")
            prev_fence = False
        text = render_runs(b["runs"])
        if b["level"]:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("#" * b["level"] + " " + text)
            lines.append("")
            prev_list = False
        elif b["list"]:
            if not prev_list and lines and lines[-1] != "":
                lines.append("")
            kind, nesting = b["list"]
            marker = "1." if kind == "ol" else "-"
            lines.append("  " * nesting + marker + " " + text)
            prev_list = True
        else:
            if lines and lines[-1] != "":
                lines.append("")
            if not text.strip():
                continue
            lines.append(text)
            lines.append("")
            prev_list = False
    if prev_fence:
        lines.append("```")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- sections and diff

def block_key(b):
    """identity of a block for diffing: text plus absolute link targets; bold/italic are not part of it"""
    if b["kind"] == "table":
        return "T:" + " | ".join(norm(c["text"]) for r in b["rows"] for c in r) + link_sig(c["runs"] for r in b["rows"] for c in r)
    return ("H%d:" % b["level"] if b["level"] else ("F:" if b.get("fenced") else "P:")) + norm(b["text"]) + link_sig([b["runs"]])


def link_sig(run_lists):
    urls = [st["link"] for runs in run_lists for _, st in runs if absolute(st["link"])]
    return (" {" + ",".join(urls) + "}") if urls else ""


def sections(blocks):
    """list of dicts: heading block (or None), body blocks, key"""
    out = []
    cur = {"heading": None, "blocks": []}
    for b in blocks:
        if b["kind"] == "p" and b["level"] and b["text"].strip():
            out.append(cur)
            cur = {"heading": b, "blocks": []}
        else:
            if b["kind"] == "p" and not b["text"].strip() and b["start"] is not None:
                continue  # empty doc paragraphs carry no content
            cur["blocks"].append(b)
    out.append(cur)
    seen = {}
    for s in out:
        base = norm(s["heading"]["text"]) if s["heading"] else "(preamble)"
        seen[base] = seen.get(base, 0) + 1
        s["key"] = base if seen[base] == 1 else f"{base} #{seen[base]}"
        s["plain"] = "\n".join(block_key(b) for b in s["blocks"])
    return out


def side_by_side(local_secs, doc_secs):
    dkeys = {s["key"]: s for s in doc_secs}
    lkeys = {s["key"]: s for s in local_secs}
    added = [s for s in local_secs if s["key"] not in dkeys]
    deleted = [s for s in doc_secs if s["key"] not in lkeys]
    modified = [s for s in local_secs if s["key"] in dkeys and dkeys[s["key"]]["plain"] != s["plain"]]
    order_local = [s["key"] for s in local_secs if s["key"] in dkeys]
    order_doc = [s["key"] for s in doc_secs if s["key"] in lkeys]
    sm = difflib.SequenceMatcher(None, order_doc, order_local, autojunk=False)
    stable = {k for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag == "equal" for k in order_doc[i1:i2]}
    moved = [k for k in order_local if k not in stable]
    return added, deleted, modified, moved, dkeys, lkeys


# ---------------------------------------------------------------- request builders

def absolute(url):
    return bool(url) and re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url) is not None


def links(runs):
    return [(norm(t), st["link"]) for t, st in runs if absolute(st["link"])]


def rng(tab, a, b):
    return {"startIndex": a, "endIndex": b, "tabId": tab}


def loc(tab, i):
    return {"index": i, "tabId": tab}


def style_requests(tab, start, runs, reset=True):
    """text style requests for runs laid out from index start"""
    reqs = []
    total = sum(ulen(t) for t, _ in runs)
    if total == 0:
        return reqs
    if reset:
        reqs.append({"updateTextStyle": {"range": rng(tab, start, start + total), "textStyle": {}, "fields": STYLE_FIELDS}})
    pos = start
    for text, st in runs:
        ln = ulen(text)
        if ln:
            ts = {}
            if st["bold"]:
                ts["bold"] = True
            if st["italic"]:
                ts["italic"] = True
            if st["strike"]:
                ts["strikethrough"] = True
            if st["code"]:
                ts["weightedFontFamily"] = {"fontFamily": CODE_STYLE["family"]}
                if CODE_STYLE["fg"]:
                    ts["foregroundColor"] = CODE_STYLE["fg"]
            if absolute(st["link"]):
                ts["link"] = {"url": st["link"]}
            if ts:
                reqs.append({"updateTextStyle": {"range": rng(tab, pos, pos + ln), "textStyle": ts, "fields": ",".join(ts.keys())}})
        pos += ln
    return reqs


def list_items(paras, pred, succ):
    """decide how each inserted paragraph gets its bullet.

    inserted text takes on the bullet of the paragraph it lands in (succ). createParagraphBullets leaves an
    already bulleted paragraph alone, so a list item that should look exactly like succ keeps that bullet, and with
    it succ's list, level and indent. every other item is rebuilt. a rebuilt run that joins the list item above it
    (pred, or the item before it in this group) takes that item's level for its first paragraph whatever its tabs,
    and nests the rest by their tabs relative to the first; when the first needs another level, a placeholder at
    the level above goes first and is deleted once the bullets exist."""
    items = []
    above = pred
    for p in paras:
        lst = p["list"]
        if not lst:
            items.append({"p": p, "mode": "plain", "tabs": 0})
            above = None
        elif lst == succ:
            items.append({"p": p, "mode": "inherit", "tabs": 0})
            above = lst
        else:
            continues = items and items[-1]["mode"] == "fresh" and items[-1]["p"]["list"][0] == lst[0]
            if not continues and above and above[0] == lst[0] and above[1] != lst[1]:
                items.append({"p": None, "mode": "placeholder", "tabs": above[1], "kind": lst[0]})
            items.append({"p": p, "mode": "fresh", "tabs": lst[1]})
            above = lst
    return items


def paragraph_group(tab, index, paras, at_end, pred=None, succ=None):
    """requests inserting consecutive paragraph blocks at index.
    at_end: index is the segment's final newline, text gets a leading newline instead of a trailing one.
    pred, succ: list state, (kind, level) or None, of the paragraph before the insertion point and of the one the
    text lands in"""
    items = list_items(paras, pred, succ)
    texts = ["\t" * it["tabs"] + (PLACEHOLDER if it["p"] is None else "".join(t for t, _ in it["p"]["runs"])) for it in items]
    body = "\n".join(texts)
    text = ("\n" + body) if at_end else (body + "\n")
    reqs = [{"insertText": {"location": loc(tab, index), "text": text}}]
    pos = index + (1 if at_end else 0)
    spans = []
    for it, t in zip(items, texts):
        spans.append((it, pos, pos + ulen(t)))
        pos += ulen(t) + 1
    rebuilt = [(it, a, b) for it, a, b in spans if it["mode"] != "inherit"]
    # drop the bullet the text landed with before the style reset: deleteParagraphBullets holds the paragraph in
    # place by adding indent, which the reset then clears. a kept bullet is left alone, a reset would zero its indent
    for it, a, b in rebuilt:
        reqs.append({"deleteParagraphBullets": {"range": rng(tab, a, b + 1)}})
    # paragraph styles before character styles (no length change)
    for it, a, b in rebuilt:
        p = it["p"] or {"level": 0}
        named = ("HEADING_%d" % p["level"]) if p["level"] else "NORMAL_TEXT"
        style = {"namedStyleType": named, "indentStart": {"magnitude": 0, "unit": "PT"}, "indentFirstLine": {"magnitude": 0, "unit": "PT"}}
        fields = "namedStyleType,indentStart,indentFirstLine"
        if p.get("fenced"):
            style["shading"] = CODE_BLOCK_SHADING
            style["spaceAbove"] = {"magnitude": 0, "unit": "PT"}
            style["spaceBelow"] = {"magnitude": 0, "unit": "PT"}
            fields += ",shading,spaceAbove,spaceBelow"
        reqs.append({"updateParagraphStyle": {"range": rng(tab, a, b + 1), "paragraphStyle": style, "fields": fields}})
    for it, a, b in spans:
        if it["p"] is not None:
            reqs.extend(style_requests(tab, a + it["tabs"], it["p"]["runs"]))
    # bullets last, bottom-up: createParagraphBullets strips the tabs, which moves everything after them
    runs_of_lists = []
    for it, a, b in rebuilt:
        if it["mode"] == "plain":
            continue
        kind = it["kind"] if it["p"] is None else it["p"]["list"][0]
        last = runs_of_lists[-1] if runs_of_lists else None
        if it["mode"] == "fresh" and last and last["kind"] == kind and last["end"] == a:
            last["end"] = b + 1
        else:
            runs_of_lists.append({"kind": kind, "start": a, "end": b + 1, "placeholder": it["p"] is None})
    for r in reversed(runs_of_lists):
        reqs.append({"createParagraphBullets": {"range": rng(tab, r["start"], r["end"]), "bulletPreset": OL_PRESET if r["kind"] == "ol" else UL_PRESET}})
        if r["placeholder"]:
            # its tabs are gone by now, so it is the placeholder and its newline
            reqs.append({"deleteContentRange": {"range": rng(tab, r["start"], r["start"] + ulen(PLACEHOLDER) + 1)}})
    return reqs


def cell_replace(tab, cell, runs, normalise=False):
    """normalise=True also drops inherited paragraph spacing so the cell matches the doc's other tables.
    the paragraph style goes first: applying a named style clears direct character formatting."""
    reqs = []
    if cell["end"] > cell["start"]:
        reqs.append({"deleteContentRange": {"range": rng(tab, cell["start"], cell["end"])}})
    text = "".join(t for t, _ in runs)
    if normalise:
        reqs.append({"updateParagraphStyle": {"range": rng(tab, cell["start"], cell["start"] + 1),
                                              "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                              "fields": "namedStyleType,lineSpacing,spaceAbove,spaceBelow,indentStart,indentFirstLine"}})
    if text:
        reqs.append({"insertText": {"location": loc(tab, cell["start"]), "text": text}})
        reqs.extend(style_requests(tab, cell["start"], runs))
    return reqs


def delete_range(tab, blocks, seg_end):
    a, b = blocks[0]["start"], blocks[-1]["end"]
    if b >= seg_end:  # cannot delete the segment's final newline
        return {"deleteContentRange": {"range": rng(tab, a - 1, b - 1)}}
    return {"deleteContentRange": {"range": rng(tab, a, b)}}


# ---------------------------------------------------------------- planning

def row_key(row):
    return " | ".join(norm(c["text"]) for c in row) + link_sig(c["runs"] for c in row)


def plan_table(tab, dtable, ltable, section_key, ordinal, pending_rows, doomed):
    """row-level diff of one table: cell edits in place, rows added and removed as rows.
    returns char-indexed cell ops; structural changes are deferred to pending_rows"""
    ops = []
    ok, nk = [row_key(r) for r in dtable["rows"]], [row_key(r) for r in ltable["rows"]]
    sm = difflib.SequenceMatcher(None, ok, nk, autojunk=False)
    deletes, inserts = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for oi, nj in zip(range(i1, i2), range(j1, j2)):
                for oc, nc in zip(dtable["rows"][oi], ltable["rows"][nj]):
                    if norm(oc["text"]) != norm(nc["text"]) or links(oc["runs"]) != links(nc["runs"]):
                        ops.append((oc["start"], "cell", cell_replace(tab, oc, nc["runs"], normalise=True)))
                        doomed.append(oc["text"])
            # uneven replace: the leftovers are row additions or removals
            if i2 - i1 > j2 - j1:
                deletes.extend(range(i1 + (j2 - j1), i2))
            elif j2 - j1 > i2 - i1:
                inserts.append((i2, ltable["rows"][j1 + (i2 - i1):j2]))
        elif tag == "delete":
            deletes.extend(range(i1, i2))
        elif tag == "insert":
            inserts.append((i1, ltable["rows"][j1:j2]))
    for ri in deletes:
        doomed.append(" ".join(c["text"] for c in dtable["rows"][ri]))
    if deletes or inserts:
        pending_rows.append({"section": section_key, "ordinal": ordinal, "cols": len(dtable["rows"][0]),
                             "deletes": sorted(set(deletes)), "inserts": inserts,
                             "expect_rows": len(dtable["rows"])})
    return ops


def block_text(b):
    if b["kind"] == "table":
        return " ".join(c["text"] for r in b["rows"] for c in r)
    return b["text"]


def list_context(dblocks, seg_end):
    """list state around an insertion point: the paragraph ending there, and the one the inserted text lands in,
    whose bullet it takes on. that is the paragraph after any blocks deleted at the same index, and at the end of
    the segment the last paragraph, whose final newline the text is inserted in front of"""
    starts = {b["start"]: b for b in dblocks if b["kind"] == "p"}
    ends = {b["end"]: b for b in dblocks if b["kind"] == "p"}

    def lst(b):
        return b["list"] if b else None

    def context(index, at_end, lands_at=None):
        if at_end:
            return {"pred": lst(ends.get(seg_end)), "succ": lst(ends.get(seg_end))}
        return {"pred": lst(ends.get(index)), "succ": lst(starts.get(index if lands_at is None else lands_at))}
    return context


def plan_section(tab, lsec, dsec, seg_end, pending_tables, pending_rows, doomed, context):
    """block-level diff inside a section; returns requests (unsorted, each tagged with its index)"""
    ops = []
    lb, db = lsec["blocks"], dsec["blocks"]
    sm = difflib.SequenceMatcher(None, [block_key(b) for b in db], [block_key(b) for b in lb], autojunk=False)
    doc_tables = [b for b in db if b["kind"] == "table"]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old, new = db[i1:i2], lb[j1:j2]
        # a table that stayed a table with the same column count is edited in place, never recreated
        if tag == "replace" and len(old) == 1 and len(new) == 1 and old[0]["kind"] == new[0]["kind"] == "table" \
                and len(old[0]["rows"][0]) == len(new[0]["rows"][0]):
            ops.extend(plan_table(tab, old[0], new[0], lsec["key"], doc_tables.index(old[0]), pending_rows, doomed))
            continue
        if old:
            ops.append((old[0]["start"], "delete", [delete_range(tab, old, seg_end)]))
            doomed.extend(block_text(b) for b in old)
        if new:
            # insertion point: end of the doc block preceding the change (or the heading)
            anchor_block = db[i1 - 1] if i1 > 0 else dsec["heading"]
            if anchor_block is None:  # preamble with nothing before it
                index, at_end = 1, False
            else:
                index, at_end = anchor_block["end"], False
            if index >= seg_end:
                index, at_end = seg_end - 1, True
            ops.append((index, "insert", new, at_end, lsec["key"], block_key(anchor_block) if anchor_block is not None and anchor_block["kind"] == "p" else None,
                        context(index, at_end, old[-1]["end"] if old else None)))
    return ops


def expand_insert(tab, index, blocks, at_end, pending_tables, section_key, anchor_key=None, ctx=None):
    """turn a run of local blocks into requests; tables are deferred with a text anchor"""
    groups = []
    i = 0
    prev_text_anchor = anchor_key
    while i < len(blocks):
        if blocks[i]["kind"] == "table":
            pending_tables.append({"section": section_key, "anchor": prev_text_anchor, "table": blocks[i], "after_index": index})
            i += 1
            continue
        j = i
        while j < len(blocks) and blocks[j]["kind"] == "p":
            j += 1
        groups.append(blocks[i:j])
        prev_text_anchor = block_key(blocks[j - 1])
        i = j
    ctx = ctx or {"pred": None, "succ": None}
    reqs = []
    # same index: later groups first, so the first group ends up on top. until phase B puts the table between them,
    # each group sits right on top of the next one and, away from the segment end, lands in its first paragraph
    for k in range(len(groups) - 1, -1, -1):
        pred = ctx["pred"] if k == 0 else groups[k - 1][-1]["list"]
        succ = ctx["succ"] if at_end or k == len(groups) - 1 else groups[k + 1][0]["list"]
        reqs.extend(paragraph_group(tab, index, groups[k], at_end, pred, succ))
    return reqs


def build_plan(tab, local_secs, doc_secs, seg_end, allow_move, dblocks):
    added, deleted, modified, moved, dkeys, lkeys = side_by_side(local_secs, doc_secs)
    report = {"added": [s["key"] for s in added], "deleted": [s["key"] for s in deleted],
              "modified": [s["key"] for s in modified], "moved": moved, "tables": []}
    if moved and not allow_move:
        report["warning"] = "section order differs; order is left as-is (pass --allow-move to delete and re-insert, which loses comment anchors on those sections)"
    if moved and allow_move:
        # a moved section is deleted where it is and re-inserted where the local file has it
        deleted = deleted + [dkeys[k] for k in moved]
        added = added + [lkeys[k] for k in moved]
        modified = [s for s in modified if s["key"] not in moved]
        added.sort(key=lambda s: local_secs.index(s))
    anchors = {s["key"] for s in local_secs if s["key"] in dkeys and s["key"] not in moved}
    ops = []
    pending_tables = []
    pending_rows = []
    doomed = []
    context = list_context(dblocks, seg_end)
    for s in modified:
        ops.extend(plan_section(tab, s, dkeys[s["key"]], seg_end, pending_tables, pending_rows, doomed, context))
    for s in deleted:
        blocks = [s["heading"]] + s["blocks"] if s["heading"] else s["blocks"]
        if blocks:
            ops.append((blocks[0]["start"], "delete", [delete_range(tab, blocks, seg_end)]))
            doomed.extend(block_text(b) for b in blocks)
    # added sections go before the next local section that stays in place in the doc, else at the end
    for s in added:
        pos = local_secs.index(s)
        nxt = next((x for x in local_secs[pos + 1:] if x["key"] in anchors), None)
        blocks = ([s["heading"]] if s["heading"] else []) + s["blocks"]
        if nxt is not None and dkeys[nxt["key"]]["heading"] is not None:
            at = dkeys[nxt["key"]]["heading"]["start"]
            ops.append((at, "insert", blocks, False, s["key"], None, context(at, False)))
        else:
            ops.append((seg_end - 1, "insert", blocks, True, s["key"], None, context(seg_end - 1, True)))
    # materialise, highest index first so earlier indices stay valid.
    # at a shared index a delete must run before an insert, or it eats the text just inserted;
    # inserts sharing an index go last-first, so the first one ends up on top.
    rank = {"delete": 0, "cell": 1, "insert": 2}
    ops = [(o, n) for n, o in enumerate(ops)]
    ops.sort(key=lambda t: (t[0][0], -rank[t[0][1]], t[1]), reverse=True)
    ops = [o for o, _ in ops]
    requests = []
    inserted_items = []
    for op in ops:
        if op[1] == "insert":
            index, blocks, at_end = op[0], op[2], op[3]
            key = op[4] if len(op) > 4 else "(modified)"
            anchor_key = op[5] if len(op) > 5 else None
            ctx = op[6] if len(op) > 6 else None
            requests.extend(expand_insert(tab, index, blocks, at_end, pending_tables, key, anchor_key, ctx))
            inserted_items.extend((key, b) for b in blocks if b["kind"] == "p" and b["list"])
        else:
            requests.extend(op[2])
    report["requests_phase_a"] = len(requests)
    report["tables"] = [{"section": t["section"], "anchor": (t["anchor"] or "")[:60], "rows": len(t["table"]["rows"]), "cols": len(t["table"]["rows"][0])} for t in pending_tables]
    report["table_rows"] = [{"section": r["section"], "delete": len(r["deletes"]), "insert": sum(len(rows) for _, rows in r["inserts"])} for r in pending_rows]
    return requests, pending_tables, pending_rows, doomed, report, inserted_items


def wrong_list_items(inserted_items, doc_secs):
    """list items this push inserted, checked against the doc; returns (doc block, problem) pairs.
    block keys ignore list kind, level, indent and stray tabs, so a push can verify clean with all of them wrong"""
    found = {}
    for s in doc_secs:
        for b in s["blocks"]:
            if b["kind"] == "p":
                found.setdefault((s["key"], block_key(b)), []).append(b)
    wrong = []
    for key, want in inserted_items:
        got = found.get((key, block_key(want)))
        if not got:
            continue  # missing blocks are already reported as residual differences
        b = got.pop(0)
        what = f"{key}: {b['text'].strip()[:50]!r}"
        if b["list"] != want["list"]:
            wrong.append((b, f"{what} is {b['list']}, expected {want['list']}"))
        elif not b["indent_ok"]:
            wrong.append((b, f"{what} is indented off its list level"))
        elif b["text"].startswith("\t"):
            wrong.append((b, f"{what} starts with a literal tab"))
    return wrong


def list_runs(blocks):
    """maximal runs of adjacent list paragraphs of one kind"""
    runs = []
    for b in blocks:
        if b["kind"] == "p" and b["list"]:
            if runs and runs[-1][-1]["end"] == b["start"] and runs[-1][-1]["list"][0] == b["list"][0]:
                runs[-1].append(b)
            else:
                runs.append([b])
    return runs


def wanted_lists(local_secs, doc_secs):
    """doc paragraph start -> the list state the local file gives it, for sections that match block for block"""
    want = {}
    dsecs = {s["key"]: s for s in doc_secs}
    for ls in local_secs:
        ds = dsecs.get(ls["key"])
        if ds is not None and ds["plain"] == ls["plain"]:
            for lb, db in zip(ls["blocks"], ds["blocks"]):
                if db["kind"] == "p":
                    want[db["start"]] = lb["list"]
    return want


def repair_lists(svc, a, local_secs, inserted_items):
    """rebuild, from the local file, each list run holding an inserted item that came out wrong.
    new bullets that join a list can only sit at or below the item above them, so an item that has to be shallower
    than that is only reachable by recreating its whole run: bullets off, one tab per level, bullets back on. the
    run starts after a non-list paragraph, so it becomes a new list with the preset's glyphs and tab-set levels.
    returns the number of runs rebuilt"""
    rebuilt, tried = 0, set()
    while True:
        tab = find_tab(get_doc(svc, a.doc), a.tab)
        dblocks = doc_blocks(tab)
        doc_secs = sections(dblocks)
        wrong = {b["start"] for b, _ in wrong_list_items(inserted_items, doc_secs)}
        run = next((r for r in list_runs(dblocks) if any(b["start"] in wrong for b in r)), None)
        if run is None:
            return rebuilt
        want = wanted_lists(local_secs, doc_secs)
        targets = [want.get(b["start"]) for b in run]
        sig = tuple(b["text"] for b in run)
        if sig in tried or any(t is None or t[0] != targets[0][0] for t in targets):
            return rebuilt  # already rebuilt once, or the local file does not see it as one list: verify reports it
        tried.add(sig)
        start, end = run[0]["start"], run[-1]["end"]
        reqs = [{"deleteParagraphBullets": {"range": rng(a.tab, start, end)}},
                {"updateParagraphStyle": {"range": rng(a.tab, start, end),
                                          "paragraphStyle": {"indentStart": {"magnitude": 0, "unit": "PT"}, "indentFirstLine": {"magnitude": 0, "unit": "PT"}},
                                          "fields": "indentStart,indentFirstLine"}}]
        shift = 0
        for b, t in reversed(list(zip(run, targets))):
            stray = len(b["text"]) - len(b["text"].lstrip("\t"))
            if stray:
                reqs.append({"deleteContentRange": {"range": rng(a.tab, b["start"], b["start"] + stray)}})
            if t[1]:
                reqs.append({"insertText": {"location": loc(a.tab, b["start"]), "text": "\t" * t[1]}})
            shift += t[1] - stray
        reqs.append({"createParagraphBullets": {"range": rng(a.tab, start, end + shift),
                                                "bulletPreset": OL_PRESET if targets[0][0] == "ol" else UL_PRESET}})
        batch(svc, a.doc, reqs, False)
        rebuilt += 1


def locate_anchor(tab, blocks, section_key, anchor_key):
    """find the block after which a deferred table goes, in a fresh dump"""
    secs = sections(blocks)
    sec = next((s for s in secs if s["key"] == section_key), None)
    if sec is None:
        sys.exit(f"cannot find section {section_key!r} for table insertion")
    if anchor_key is None:
        return sec["heading"]
    for b in sec["blocks"]:
        if block_key(b) == anchor_key:
            return b
    sys.exit(f"cannot find anchor {anchor_key[:60]!r} in section {section_key!r}")


def column_widths(blocks, cols):
    """reuse the width pattern of an existing table with the same column count"""
    for b in blocks:
        if b["kind"] == "table" and b.get("col_props") and len(b["col_props"]) == cols:
            if any(p.get("widthType") == "FIXED_WIDTH" for p in b["col_props"]):
                return b["col_props"]
    return None


def find_table_after(blocks, index):
    return next((b for b in blocks if b["kind"] == "table" and b["start"] >= index), None)


def find_section_table(blocks, section_key, ordinal):
    secs = sections(blocks)
    sec = next((s for s in secs if s["key"] == section_key), None)
    if sec is None:
        sys.exit(f"cannot find section {section_key!r} for a table edit")
    tables = [b for b in sec["blocks"] if b["kind"] == "table"]
    if ordinal >= len(tables):
        sys.exit(f"table {ordinal} not found in section {section_key!r}")
    return tables[ordinal]


def drive_service(email):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    info = json.loads((CRED_DIR / f"{email}.json").read_text())
    info.pop("expiry", None)
    creds = Credentials.from_authorized_user_info(info)
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def doc_comments(email, doc_id):
    try:
        drive = drive_service(email)
        out, token = [], None
        while True:
            r = with_retry(drive.comments().list(fileId=doc_id, fields="nextPageToken,comments(id,author/displayName,content,resolved,quotedFileContent/value)", pageSize=100, pageToken=token), write=False)
            out.extend(r.get("comments", []))
            token = r.get("nextPageToken")
            if not token:
                return out
    except Exception as e:  # a missing drive scope must not block a push
        print(f"could not read comments ({e}); anchor risk not checked")
        return None


def comments_at_risk(comments, doomed_text):
    """a comment whose quoted text sits inside text this push will delete loses its anchor"""
    if not comments:
        return []
    hay = " \u0000 ".join(norm(t) for t in doomed_text)
    hits = []
    for c in comments:
        q = norm((c.get("quotedFileContent") or {}).get("value", ""))
        if len(q) >= 12 and q in hay:
            hits.append(c)
    return hits


def check_anchors(blocks, pending_tables):
    """resolve every deferred anchor before anything is written, so a push cannot stop half way"""
    for t in pending_tables:
        locate_anchor(None, blocks, t["section"], t["anchor"])


# ---------------------------------------------------------------- snapshot

def sidecar(path, tab):
    return pathlib.Path(str(path) + f".gdocsync.{tab}.json")


def load_base(path, tab):
    p = sidecar(path, tab)
    return json.loads(p.read_text()) if p.exists() else None


def save_base(path, tab, doc_id, doc_secs):
    sidecar(path, tab).write_text(json.dumps({"doc": doc_id, "tab": tab, "sections": {s["key"]: s["plain"] for s in doc_secs}}, indent=1))


# ---------------------------------------------------------------- commands

def cmd_tabs(a):
    doc = get_doc(service(a.email), a.doc)
    for t in iter_tabs(doc):
        print(t["tabProperties"]["tabId"], "\t", t["tabProperties"].get("title"))


def cmd_dump(a):
    doc = get_doc(service(a.email), a.doc)
    blocks = doc_blocks(find_tab(doc, a.tab))
    pick_code_style(blocks)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(blocks, indent=1, ensure_ascii=False))
        print(f"wrote {len(blocks)} blocks to {a.json}")
    else:
        for b in blocks:
            if b["kind"] == "table":
                print(f"{b['start']:6d}-{b['end']:6d} table {len(b['rows'])}x{len(b['rows'][0])}")
            else:
                tag = f"H{b['level']}" if b["level"] else ("L" if b["list"] else "P")
                print(f"{b['start']:6d}-{b['end']:6d} {tag:2s} {b['text'][:90]}")


def cmd_render(a):
    doc = get_doc(service(a.email), a.doc)
    blocks = doc_blocks(find_tab(doc, a.tab))
    pick_code_style(blocks)
    md = render_blocks(blocks)
    if a.out:
        pathlib.Path(a.out).write_text(md)
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(md)


def raw_sections(md):
    """split the source into (key, raw chunk) so that concatenating the chunks reproduces it exactly.
    headings inside fenced blocks are not headings."""
    lines = md.split("\n")
    starts, fence = [], False
    for i, line in enumerate(lines):
        if line.startswith("```"):
            fence = not fence
        elif not fence and re.match(r"^#{1,6} ", line):
            starts.append(i)
    bounds = [0] + starts + [len(lines)]
    # sections() always yields a leading preamble, even an empty one; mirror that so the keys line up
    out, seen = ([] if (not starts or starts[0] != 0) else [("(preamble)", "")]), {}
    for a, b in zip(bounds, bounds[1:]):
        if a == b:  # a file that opens with a heading has no preamble
            continue
        if a == 0 and (not starts or starts[0] != 0):
            key = "(preamble)"
        else:
            hb = local_blocks(lines[a])
            key = norm(hb[0]["text"]) if hb else norm(lines[a])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key} #{seen[key]}"
        chunk = "\n".join(lines[a:b])
        if b < len(lines):
            chunk += "\n"
        out.append((key, chunk))
    return out


def restore_fence_info(rendered, original):
    """a render cannot know a fence's language; copy it back when the block body is unchanged"""
    info = {}
    body, tag, buf = None, None, []
    for line in original.split("\n"):
        if line.startswith("```"):
            if body is None:
                body, tag, buf = True, line[3:].strip(), []
            else:
                if tag:
                    info["\n".join(buf)] = tag
                body = None
        elif body:
            buf.append(line)
    if not info:
        return rendered
    out, inside, buf, at = [], False, [], None
    for line in rendered.split("\n"):
        if line.startswith("```"):
            if not inside:
                inside, buf, at = True, [], len(out)
                out.append(line)
            else:
                out.append(line)
                tag = info.get("\n".join(buf))
                if tag:
                    out[at] = "```" + tag
                inside = False
        else:
            (buf if inside else out).append(line) if inside else out.append(line)
            if inside:
                out.append(line)
    return "\n".join(out)


def cmd_pull(a):
    svc = service(a.email)
    path = pathlib.Path(a.file)
    original = path.read_text()
    doc = get_doc(svc, a.doc)
    tab = find_tab(doc, a.tab)
    dblocks = doc_blocks(tab)
    pick_code_style(dblocks)
    doc_secs = sections(dblocks)
    local_secs = sections(local_blocks(original))
    lraw = dict(raw_sections(original))
    lplain = {s["key"]: s["plain"] for s in local_secs}

    pieces, changed, added = [], [], []
    for s in doc_secs:
        key = s["key"]
        if key in lplain and lplain[key] == s["plain"]:
            pieces.append(lraw[key])          # unchanged: keep the local text byte for byte
            continue
        blocks = ([s["heading"]] if s["heading"] else []) + s["blocks"]
        rendered = restore_fence_info(render_blocks(blocks), original).rstrip("\n") + "\n\n"
        pieces.append(rendered)
        (changed if key in lplain else added).append(key)
    removed = [s["key"] for s in local_secs if s["key"] not in {d["key"] for d in doc_secs}]
    result = "".join(pieces).rstrip("\n") + "\n"

    print(json.dumps({"modified": changed, "added": added, "removed_from_doc": removed}, indent=1, ensure_ascii=False))
    if not (changed or added or removed):
        print("local file already matches the doc tab")
        save_base(path, a.tab, a.doc, doc_secs)
        return
    if a.dry_run:
        out = pathlib.Path(a.out) if a.out else path.with_suffix(path.suffix + ".pulled")
        out.write_text(result)
        print(f"dry run: result written to {out}, local file untouched")
        return
    path.write_text(result)
    save_base(path, a.tab, a.doc, doc_secs)
    print(f"{path} updated; snapshot written: {sidecar(path, a.tab)}")
    print("sections the doc did not have were dropped; check `git diff` before committing" if removed else "check `git diff` before committing")


def cmd_base(a):
    doc = get_doc(service(a.email), a.doc)
    secs = sections(doc_blocks(find_tab(doc, a.tab)))
    save_base(pathlib.Path(a.file), a.tab, a.doc, secs)
    print(f"snapshot written: {sidecar(a.file, a.tab)}")


def promote_headings(tab_id, local, dblocks):
    """a doc paragraph with the exact text of a local heading that has no doc heading counterpart
    becomes that heading in place (one style request) instead of a delete plus insert"""
    doc_heads = {norm(b["text"]) for b in dblocks if b["kind"] == "p" and b["level"]}
    wanted = {norm(b["text"]): b["level"] for b in local if b["kind"] == "p" and b["level"] and norm(b["text"]) not in doc_heads}
    reqs = []
    for b in dblocks:
        if b["kind"] == "p" and not b["level"] and not b["list"] and norm(b["text"]) in wanted:
            b["level"] = wanted[norm(b["text"])]
            reqs.append({"updateParagraphStyle": {"range": rng(tab_id, b["start"], b["end"]), "paragraphStyle": {"namedStyleType": "HEADING_%d" % b["level"]}, "fields": "namedStyleType"}})
    return reqs


def cmd_lists(a):
    """list items indented off their list level, and list items that start with a literal tab. --fix resets the
    indent to the level's. a stray tab usually means a wrong level too, which only the local file knows"""
    svc = service(a.email)
    tab = find_tab(get_doc(svc, a.doc), a.tab)
    dt = tab["documentTab"]
    lists = dt.get("lists", {})
    reqs, found = [], 0
    for el in dt["body"]["content"]:
        if "paragraph" not in el or "bullet" not in el["paragraph"]:
            continue
        b = para_from_api(el, lists)
        bullet = el["paragraph"]["bullet"]
        level = lists[bullet["listId"]]["listProperties"]["nestingLevels"][bullet.get("nestingLevel", 0)]
        if not b["indent_ok"]:
            found += 1
            print(f"{b['start']:>7}  indented off level {b['list'][1]}: {b['text'][:60]!r}")
            style = {k: {"magnitude": level.get(k, {}).get("magnitude", 0), "unit": "PT"} for k in ("indentStart", "indentFirstLine")}
            reqs.append({"updateParagraphStyle": {"range": rng(a.tab, b["start"], b["end"]), "paragraphStyle": style,
                                                  "fields": "indentStart,indentFirstLine"}})
        if b["text"].startswith("\t"):
            found += 1
            print(f"{b['start']:>7}  literal tab, level {b['list'][1]} may be wrong: {b['text'][:60]!r}")
    if not found:
        print("lists: nothing to report")
    elif a.fix and reqs:
        for chunk in chunks(reqs, 400):
            batch(svc, a.doc, chunk, False)
        print(f"lists: indent reset on {len(reqs)} item(s)")


def cmd_push(a):
    svc = service(a.email)
    path = pathlib.Path(a.file)
    local = local_blocks(path.read_text())
    local_secs = sections(local)
    doc = get_doc(svc, a.doc)
    tab = find_tab(doc, a.tab)
    dblocks = doc_blocks(tab)
    pick_code_style(dblocks)
    promote = promote_headings(a.tab, local, dblocks)
    doc_secs = sections(dblocks)
    seg_end = segment_end(tab)

    base = load_base(path, a.tab)
    if base and base.get("doc") != a.doc:
        print(f"snapshot belongs to document {base.get('doc')}, not {a.doc}; ignoring it")
        base = None
    if base:
        changed_in_doc = {k for k, v in base["sections"].items() if k in {s["key"]: 1 for s in doc_secs} and next(s for s in doc_secs if s["key"] == k)["plain"] != v}
        changed_locally = {s["key"] for s in local_secs if s["key"] in base["sections"] and base["sections"][s["key"]] != s["plain"]}
        conflicts = sorted(changed_in_doc & changed_locally)
        doc_only = sorted(changed_in_doc - changed_locally)
        if doc_only:
            print("doc changed since last sync in sections you did not touch (will be overwritten by a push):")
            for k in doc_only:
                print("   ", k)
        if conflicts:
            print("CONFLICT: changed on both sides since last sync:")
            for k in conflicts:
                print("   ", k)
            if not a.force:
                sys.exit("aborting; pull first or pass --force")
    else:
        print("no snapshot yet; cannot detect edits made in the doc since the last sync")

    requests, pending, pending_rows, doomed, report, inserted_items = build_plan(a.tab, local_secs, doc_secs, seg_end, a.allow_move, dblocks)
    report["promoted_headings"] = len(promote)
    check_anchors(dblocks, pending)  # fail before writing, never half way through
    risky = comments_at_risk(doc_comments(a.email, a.doc), doomed)
    if risky:
        print(f"{len(risky)} comment(s) are anchored to text this push rewrites:")
        for c in risky:
            state = "resolved" if c.get("resolved") else "OPEN"
            print(f"   [{state}] {c['author']['displayName']}: {norm(c.get('content',''))[:70]}")
            print(f"      on: {norm((c.get('quotedFileContent') or {}).get('value',''))[:70]}")
        report["comments_at_risk"] = len(risky)
        if any(not c.get("resolved") for c in risky) and not (a.force or a.dry_run):
            sys.exit("aborting: open comments would lose their anchor; pass --force to proceed anyway")
    requests = promote + requests  # style changes first: they do not move indices
    print(json.dumps(report, indent=1, ensure_ascii=False))
    if a.dry_run:
        if a.plan:
            pathlib.Path(a.plan).write_text(json.dumps({"phase_a": requests, "tables": [t["anchor"] for t in pending],
                                                        "table_rows": report["table_rows"]}, indent=1, ensure_ascii=False))
            print(f"phase A requests written to {a.plan}")
        print("dry run, nothing written")
        return

    # phase A: text and cell edits, deletions
    for chunk in chunks(requests, 400):
        batch(svc, a.doc, chunk, False)
    # phase A2: row additions and removals inside tables that already exist, one table at a time
    for pr in pending_rows:
        doc = get_doc(svc, a.doc)
        tab = find_tab(doc, a.tab)
        table = find_section_table(doc_blocks(tab), pr["section"], pr["ordinal"])
        if len(table["rows"]) != pr["expect_rows"]:
            sys.exit(f"table in {pr['section']!r} has {len(table['rows'])} rows, expected {pr['expect_rows']}; re-run the dry run")
        for ri in sorted(pr["deletes"], reverse=True):
            batch(svc, a.doc, [{"deleteTableRow": {"tableCellLocation": {"tableStartLocation": loc(a.tab, table["start"]), "rowIndex": ri, "columnIndex": 0}}}], False)
            doc = get_doc(svc, a.doc)
            tab = find_tab(doc, a.tab)
            table = find_section_table(doc_blocks(tab), pr["section"], pr["ordinal"])
        for at_row, rows in sorted(pr["inserts"], key=lambda x: x[0], reverse=True):
            shift = sum(1 for d in pr["deletes"] if d < at_row)
            at = at_row - shift
            for offset, row in enumerate(rows):
                below = at + offset - 1 >= 0
                batch(svc, a.doc, [{"insertTableRow": {"tableCellLocation": {"tableStartLocation": loc(a.tab, table["start"]),
                                                                            "rowIndex": max(at + offset - 1, 0), "columnIndex": 0},
                                                       "insertBelow": below}}], False)
                doc = get_doc(svc, a.doc)
                tab = find_tab(doc, a.tab)
                table = find_section_table(doc_blocks(tab), pr["section"], pr["ordinal"])
                cells = table["rows"][at + offset]
                reqs = []
                for c in range(len(row) - 1, -1, -1):
                    reqs.extend(cell_replace(a.tab, cells[c], row[c]["runs"], normalise=True))
                for chunk in chunks(reqs, 400):
                    batch(svc, a.doc, chunk, False)
                doc = get_doc(svc, a.doc)
                tab = find_tab(doc, a.tab)
                table = find_section_table(doc_blocks(tab), pr["section"], pr["ordinal"])
    # phase B: table shells, bottom-up, each located against a fresh dump
    pending.sort(key=lambda t: t["after_index"], reverse=True)
    for t in pending:
        doc = get_doc(svc, a.doc)
        tab = find_tab(doc, a.tab)
        anchor = locate_anchor(a.tab, doc_blocks(tab), t["section"], t["anchor"])
        rows, cols = len(t["table"]["rows"]), len(t["table"]["rows"][0])
        # insertTable adds a newline before the table; that new empty paragraph inherits the style of the
        # paragraph it split (a heading when the table goes right before one), so reset it in the same batch
        batch(svc, a.doc, [{"insertTable": {"location": loc(a.tab, anchor["end"]), "rows": rows, "columns": cols}},
                           {"updateParagraphStyle": {"range": rng(a.tab, anchor["end"], anchor["end"] + 1), "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "namedStyleType"}},
                           {"deleteParagraphBullets": {"range": rng(a.tab, anchor["end"], anchor["end"] + 1)}}], False)
        # phase C: populate from a fresh dump
        doc = get_doc(svc, a.doc)
        tab = find_tab(doc, a.tab)
        blocks = doc_blocks(tab)
        anchor = locate_anchor(a.tab, blocks, t["section"], t["anchor"])
        table = find_table_after(blocks, anchor["end"])
        if table is None or len(table["rows"]) != rows:
            sys.exit("table shell not found after insertion; stopping before populating")
        reqs = []
        cells = [(r, c) for r in range(rows) for c in range(cols)]
        for r, c in reversed(cells):
            runs = t["table"]["rows"][r][c]["runs"]
            if r == 0:
                runs = [(txt, {**st, "bold": True}) for txt, st in runs]
            reqs.extend(cell_replace(a.tab, table["rows"][r][c], runs, normalise=True))
        reqs.append({"pinTableHeaderRows": {"tableStartLocation": loc(a.tab, table["start"]), "pinnedHeaderRowsCount": 1}})
        widths = column_widths(blocks, cols)
        if widths:
            reqs.append({"updateTableColumnProperties": {"tableStartLocation": loc(a.tab, table["start"]),
                                                        "columnIndices": list(range(cols)),
                                                        "tableColumnProperties": widths[0],
                                                        "fields": "widthType,width"}})
        for chunk in chunks(reqs, 400):
            batch(svc, a.doc, chunk, False)
    # phase D: list runs that cannot be nested in place
    rebuilt = repair_lists(svc, a, local_secs, inserted_items) if inserted_items else 0
    if rebuilt:
        print(f"rebuilt {rebuilt} list(s) from the local file: an inserted item had to sit shallower than the item above it")
    # verify
    doc = get_doc(svc, a.doc)
    tab = find_tab(doc, a.tab)
    doc_secs = sections(doc_blocks(tab))
    added, deleted, modified, moved, _, _ = side_by_side(local_secs, doc_secs)
    if added or deleted or modified:
        print("VERIFY: residual differences after push:", json.dumps({"added": [s["key"] for s in added], "deleted": [s["key"] for s in deleted], "modified": [s["key"] for s in modified]}, ensure_ascii=False))
    else:
        print("VERIFY: doc tab matches local file section by section")
    problems = wrong_list_items(inserted_items, doc_secs)
    if problems:
        print(f"VERIFY: {len(problems)} inserted list item(s) came out wrong:")
        for _, p in problems:
            print("   ", p)
    save_base(path, a.tab, a.doc, doc_secs)
    print(f"snapshot written: {sidecar(path, a.tab)}")


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", default=DEFAULT_EMAIL, help="google account whose workspace-mcp credentials to use")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tabs"); p.add_argument("doc"); p.set_defaults(fn=cmd_tabs)
    p = sub.add_parser("dump"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--json"); p.set_defaults(fn=cmd_dump)
    p = sub.add_parser("render"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--out"); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("base"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--file", required=True); p.set_defaults(fn=cmd_base)
    p = sub.add_parser("lists"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--fix", action="store_true"); p.set_defaults(fn=cmd_lists)
    p = sub.add_parser("pull"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--file", required=True)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--out"); p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("push"); p.add_argument("doc"); p.add_argument("--tab", required=True); p.add_argument("--file", required=True)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true"); p.add_argument("--allow-move", action="store_true"); p.add_argument("--plan")
    p.set_defaults(fn=cmd_push)
    a = ap.parse_args()
    if not a.email:
        sys.exit("pass --email or set GDOC_SYNC_EMAIL")
    a.fn(a)


if __name__ == "__main__":
    main()
