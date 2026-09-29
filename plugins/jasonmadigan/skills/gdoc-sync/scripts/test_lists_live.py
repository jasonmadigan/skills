# /// script
# requires-python = ">=3.11"
# dependencies = ["google-api-python-client>=2.100", "google-auth>=2.20", "google-auth-httplib2>=0.2"]
# ///
"""live list check for gdoc_sync: pushes list edits of every shape to a scratch doc, checks each item's level,
kind, indent and list membership against the Docs API, then deletes the doc.

usage: uv run test_lists_live.py [--email E] [--helper PATH]
"""
import argparse
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

BASE = """## UL

- one
- two
- three

## OL

1. a
2. b
3. c

## Nested

- parent
  - child one
  - child two
  - child four
- other

## Tail

- top
  - nested

## OLNested

1. first
   1. sub a
   2. sub b
2. second

## Plain

- x
- y

## Mixed

- a1

| h |
|-|
| c |

## Last

- e1
"""

NEXT = """## UL

- zero
- one
- one and a half
- two changed
- three
- four

## OL

1. a0
2. a
3. a2
4. b
5. c

## Nested

- parent
  - child zero
  - child one
  - child two
  - child three
- middle
  - child four
- other

## Tail

- top
  - nested
- appended

## OLNested

1. first
   1. sub a
2. new second
   1. sub b
3. second

## Plain

intro

- x

between

- y

## Mixed

- a1
  - a2

| h |
|-|
| c |

- b1
  - b2

## Last

- e1
- e2
  - e3
"""

# (section, text) -> nesting level, or None for a plain paragraph
EXPECT = {
    ("UL", "zero"): 0, ("UL", "one"): 0, ("UL", "one and a half"): 0, ("UL", "two changed"): 0,
    ("UL", "three"): 0, ("UL", "four"): 0,
    ("OL", "a0"): 0, ("OL", "a"): 0, ("OL", "a2"): 0, ("OL", "b"): 0, ("OL", "c"): 0,
    ("Nested", "parent"): 0, ("Nested", "child zero"): 1, ("Nested", "child one"): 1, ("Nested", "child two"): 1,
    ("Nested", "child three"): 1, ("Nested", "middle"): 0, ("Nested", "child four"): 1, ("Nested", "other"): 0,
    ("Tail", "top"): 0, ("Tail", "nested"): 1, ("Tail", "appended"): 0,
    ("OLNested", "first"): 0, ("OLNested", "sub a"): 1, ("OLNested", "new second"): 0, ("OLNested", "sub b"): 1,
    ("OLNested", "second"): 0,
    ("Plain", "intro"): None, ("Plain", "x"): 0, ("Plain", "between"): None, ("Plain", "y"): 0,
    ("Mixed", "a1"): 0, ("Mixed", "a2"): 1, ("Mixed", "b1"): 0, ("Mixed", "b2"): 1,
    ("Last", "e1"): 0, ("Last", "e2"): 0, ("Last", "e3"): 1,
}
# lists per section: the table in Mixed splits it in two
LISTS = {"Mixed": 2}


def push(a, doc_id, md):
    out = subprocess.run(["uv", "run", "--quiet", a.helper, "--email", a.email, "push", doc_id, "--tab", "t.0", "--file", str(md)],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"push failed:\n{out.stdout}\n{out.stderr}")
    return [l for l in out.stdout.splitlines() if l.startswith(("VERIFY", "    ", "rebuilt"))]


def check(g, svc, doc_id):
    dt = g.find_tab(g.get_doc(svc, doc_id), "t.0")["documentTab"]
    lists = dt.get("lists", {})
    section, failures, ids = None, [], {}
    dim = lambda d, k: d.get(k, {}).get("magnitude", 0)
    for el in dt["body"]["content"]:
        p = el.get("paragraph")
        if not p:
            continue
        raw = "".join(r.get("textRun", {}).get("content", "") for r in p["elements"]).rstrip("\n")
        ps = p.get("paragraphStyle", {})
        if ps.get("namedStyleType", "").startswith("HEADING"):
            section = raw.strip()
            continue
        if not raw.strip():
            continue
        key = (section, raw.strip())
        if key not in EXPECT:
            failures.append(f"{key}: unexpected paragraph")
            continue
        want, b = EXPECT[key], p.get("bullet")
        indent = (dim(ps, "indentStart"), dim(ps, "indentFirstLine"))
        if raw.startswith("\t"):
            failures.append(f"{key}: literal tab left in the text")
        if want is None:
            if b or indent != (0, 0):
                failures.append(f"{key}: should be a plain paragraph, has bullet {bool(b)} indent {indent}")
            continue
        if not b:
            failures.append(f"{key}: no bullet")
            continue
        level = b.get("nestingLevel", 0)
        if level != want:
            failures.append(f"{key}: level {level}, expected {want}")
        spec = lists[b["listId"]]["listProperties"]["nestingLevels"][level]
        if indent != (dim(spec, "indentStart"), dim(spec, "indentFirstLine")):
            failures.append(f"{key}: indent {indent}, its level has {(dim(spec, 'indentStart'), dim(spec, 'indentFirstLine'))}")
        ids.setdefault(section, set()).add(b["listId"])
    for sec, found in ids.items():
        if len(found) != LISTS.get(sec, 1):
            failures.append(f"{sec}: items spread over {len(found)} lists, ordered numbering restarts at each")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=os.environ.get("GDOC_SYNC_EMAIL"))
    ap.add_argument("--helper", default=str(pathlib.Path(__file__).with_name("gdoc_sync.py")))
    a = ap.parse_args()
    if not a.email:
        sys.exit("pass --email or set GDOC_SYNC_EMAIL")
    spec = importlib.util.spec_from_file_location("gdoc_sync", a.helper)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    svc = g.service(a.email)
    doc_id = svc.documents().create(body={"title": "gdoc-sync list test (scratch)"}).execute()["documentId"]
    try:
        with tempfile.TemporaryDirectory() as d:
            md = pathlib.Path(d) / "test.md"
            # a new table needs an anchor already in the doc, so the base goes in without its tables first
            md.write_text("\n".join(l for l in BASE.split("\n") if not l.startswith("|")))
            push(a, doc_id, md)
            md.write_text(BASE)
            push(a, doc_id, md)
            md.write_text(NEXT)
            print("\n".join(push(a, doc_id, md)))
        failures = check(g, svc, doc_id)
    finally:
        g.drive_service(a.email).files().delete(fileId=doc_id).execute()
    for f in failures:
        print("FAIL", f)
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
