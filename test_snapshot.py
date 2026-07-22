#!/usr/bin/env python3
"""
Fidelity test: the tables rendered into _site/ must contain exactly the same
rows, cells, and links as the original WordPress content captured in reference/.
This is what protects the people who parse this site programmatically.

Run:  python3 build.py && python3 test_snapshot.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "_site"
REF = ROOT / "reference"

# map: generated page  ->  reference snapshot file
PAGES = {
    "index.html": "the-most-recent-updates-for-microsoft-sql-server.html",
    "sql-server-2025-updates/index.html": "sql-server-2025-updates.html",
    "sql-server-2022-updates/index.html": "sql-server-2022-updates.html",
    "sql-server-2019-updates/index.html": "sql-server-2019-updates.html",
    "sql-server-2017-updates/index.html": "sql-server-2017-updates.html",
    "sql-server-2016-updates/index.html": "sql-server-2016-updates.html",
    "sql-server-2014-updates/index.html": "sql-server-2014-updates.html",
    "sql-server-2012-updates/index.html": "sql-server-2012-updates.html",
    "sql-server-2008-r2-updates/index.html": "sql-server-2008-r2-updates.html",
    "sql-server-2008-updates/index.html": "sql-server-2008-updates.html",
}

def norm(s):
    return re.sub(r"\s+", " ", s).strip()

def cell_text(cell):
    """Visible text of a cell, whitespace-normalized (entities preserved)."""
    return norm(re.sub(r"<[^>]+>", "", cell))

def cell_links(cell):
    return [norm(h) for h in re.findall(r'href="([^"]+)"', cell)]

def table_rows(htmlstr):
    """All tables -> list of rows; each row is a list of cells; each cell is
    (text, [links])."""
    rows = []
    for table in re.findall(r"<table.*?</table>", htmlstr, re.S):
        for tr in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
            rows.append([(cell_text(c), cell_links(c)) for c in cells])
    return rows

def main():
    failures = []
    for gen, ref in PAGES.items():
        gen_html = (SITE / gen).read_text(encoding="utf-8")
        ref_html = (REF / ref).read_text(encoding="utf-8")
        g = table_rows(gen_html)
        r = table_rows(ref_html)
        if len(g) != len(r):
            failures.append(f"{gen}: row count {len(g)} != reference {len(r)}")
            continue
        for i, (gr, rr) in enumerate(zip(g, r)):
            if gr != rr:
                failures.append(f"{gen}: row {i} differs\n   generated : {gr}\n   reference : {rr}")
    if failures:
        print("FAIL — %d table difference(s):\n" % len(failures))
        print("\n".join(failures[:40]))
        sys.exit(1)
    total = sum(len(table_rows((SITE / g).read_text(encoding="utf-8"))) for g in PAGES)
    print(f"PASS — all {len(PAGES)} pages match reference; {total} table rows verified "
          f"(cell text + links identical).")

if __name__ == "__main__":
    main()
