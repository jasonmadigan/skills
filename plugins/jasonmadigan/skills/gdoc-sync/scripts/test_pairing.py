# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""offline check for gdoc_sync: section pairing (repeated, renamed and moved headings) and the requests that
rewrite a renamed heading in place. no google access needed.

usage: uv run test_pairing.py [--helper PATH]
"""
import argparse
import importlib.util
import pathlib
import sys


def load(path):
    spec = importlib.util.spec_from_file_location("gdoc_sync", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", default=str(pathlib.Path(__file__).with_name("gdoc_sync.py")))
    g = load(ap.parse_args().helper)
    fails = []

    def plan(local_md, doc_md):
        secs = [g.sections(g.local_blocks(md)) for md in (local_md, doc_md)]
        a, d, m, mv, twin = g.side_by_side(*secs)
        ren = sorted(f"{twin[s['key']]['key']} -> {s['key']}" for s in m if g.heading_key(s) != g.heading_key(twin[s["key"]]))
        return {"added": [s["key"] for s in a], "deleted": [s["key"] for s in d], "modified": [s["key"] for s in m],
                "renamed": ren, "moved": mv}

    def check(name, got, **want):
        bad = {k: (got[k], v) for k, v in want.items() if got[k] != v}
        print(("ok  " if not bad else "FAIL"), name, bad or "")
        if bad:
            fails.append(name)

    doc = "## W8\n- a\n\n## W25 May\n- cyber\n- psap\n\n## W25 May\n- lfx\n\n## W18\n- s\n"
    loc = "## W8\n- a\n\n## W1 June\n- cyber\n- psap\n\n## W25 May\n- lfx\n\n## W18\n- s\n"
    check("rename first of two duplicates", plan(loc, doc), added=[], deleted=[], modified=["W1 June"], renamed=["W25 May -> W1 June"], moved=[])
    check("unchanged duplicates", plan(doc, doc), added=[], deleted=[], modified=[], renamed=[])

    doc = "## P\n### Notes\n- a\n### Notes\n- b\n## Q\n- q\n"
    loc = "## P\n### Notes\n- new\n### Notes\n- a\n### Notes\n- b\n## Q\n- q\n"
    check("repeated heading added above its twins", plan(loc, doc), added=["P / Notes"], deleted=[], modified=[], renamed=[])
    loc = "## P\n### Notes\n- a\n### Notes\n- b2\n## Q\n- q\n"
    check("second of two repeated headings edited", plan(loc, doc), added=[], deleted=[], modified=["P / Notes #2"], renamed=[])
    loc = "## P\n### Notes\n- b\n## Q\n- q\n"
    check("first of two repeated headings deleted", plan(loc, doc), added=[], deleted=["P / Notes"], modified=[], renamed=[])

    doc = "## Old\n### Shipped\n- s\n### Notes\n- n\n## Next\n- x\n"
    loc = "## New\n### Shipped\n- s\n### Notes\n- n2\n## Next\n- x\n"
    check("parent renamed, children follow", plan(loc, doc), added=[], deleted=[], modified=["New", "New / Notes"], renamed=["Old -> New"])

    doc = "## A\n- a\n## X\n- x\n## B\n- b\n"
    loc = "## A\n- a\n## Y\n- y\n## B\n- b\n"
    check("one section swapped for another in the same place", plan(loc, doc), added=[], deleted=[], modified=["Y"], renamed=["X -> Y"])
    loc = "## A\n- a\n## Y\n- y\n## Z\n- z\n## B\n- b\n"
    check("one replaced by two stays add and delete", plan(loc, doc), added=["Y", "Z"], deleted=["X"], modified=[], renamed=[])
    loc = "## A\n- a\n### X\n- x\n## B\n- b\n"
    check("level change pairs as rename", plan(loc, doc), added=[], deleted=[], modified=["A / X"], renamed=["X -> A / X"])

    doc = "## A\n- a\n## B\n- b\n## C\n- c\n"
    loc = "## B\n- b\n## A\n- a\n## C\n- c\n"
    check("moved unique section still reported as moved", plan(loc, doc), added=[], deleted=[], renamed=[], moved=["B"])

    plain = {"bold": False, "italic": False, "strike": False, "code": False, "link": None}

    def head(text, level=2, start=100, runs=None, end=None):
        return {"kind": "p", "level": level, "list": None, "text": text, "runs": runs or [(text, plain)],
                "start": start, "end": end if end is not None else start + g.ulen(text) + 1}

    def edit(dh, lh):
        doomed = []
        return {"r": g.heading_edit("t.0", dh, lh, doomed)[2], "d": doomed}

    def rng(a, b):
        return {"startIndex": a, "endIndex": b, "tabId": "t.0"}

    def at(i):
        return {"index": i, "tabId": "t.0"}

    def kinds(got):
        return {"r": [next(iter(r)) for r in got["r"]], "d": got["d"]}

    check("splice replaces only the date", edit(head("Week of 25th May, 2026"), head("Week of 1st June, 2026")),
          r=[{"deleteContentRange": {"range": rng(108, 116)}}, {"insertText": {"location": at(108), "text": "1st June"}}], d=["25th May"])
    check("level only", kinds(edit(head("Notes", level=3), head("Notes", level=2))), r=["updateParagraphStyle"], d=[])
    check("growth at the end", edit(head("aa"), head("aaa")), r=[{"insertText": {"location": at(102), "text": "a"}}])
    check("shrink at the end", edit(head("abcabc"), head("abc")), r=[{"deleteContentRange": {"range": rng(103, 106)}}])
    check("utf-16 offsets", edit(head("Wk \U0001F600 1"), head("Wk \U0001F600 2")),
          r=[{"deleteContentRange": {"range": rng(106, 107)}}, {"insertText": {"location": at(106), "text": "2"}}])
    # a person chip is one index wide, so its text does not map onto indices
    check("chip forces whole replace", kinds(edit(head("Ann notes", end=107), head("Bob notes"))),
          r=["deleteContentRange", "insertText", "updateTextStyle"], d=["Ann notes"])
    linked = [("New", {**plain, "link": "https://example.com/"})]
    check("link forces whole replace with link style", kinds(edit(head("Old"), head("New", runs=linked))),
          r=["deleteContentRange", "insertText", "updateTextStyle", "updateTextStyle"], d=["Old"])
    check("normalised equal text is left alone", edit(head("Same text"), head("Same text")), r=[], d=[])

    print("PASS" if not fails else f"{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
