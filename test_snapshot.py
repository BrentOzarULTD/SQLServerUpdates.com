#!/usr/bin/env python3
"""
Fidelity test: the tables rendered into _site/ must preserve the structure and
historical content captured in reference/. Version pages may prepend new data
rows. The home page may advance only its latest-update links and build numbers.
This protects people who parse the site programmatically without preventing
legitimate updates.

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

# Version-page CSVs are newest-first, so legitimate additions appear directly
# after the header. The home page may update only its Latest Update and Build
# Number cells; its shape and other cells remain frozen.
ALLOW_PREPENDED_ROWS = set(PAGES) - {"index.html"}

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

def row_differences(generated, reference, allow_prepend=False):
    """Return row-level fidelity failures.

    With allow_prepend, generated may contain extra rows immediately after the
    header. The reference header and historical data rows must still match at
    their shifted positions, which rejects deletions, edits, and rows appended
    at the bottom.
    """
    if not allow_prepend:
        if len(generated) != len(reference):
            return [f"row count {len(generated)} != reference {len(reference)}"]
        pairs = [(i, i) for i in range(len(reference))]
    else:
        if len(generated) < len(reference):
            return [f"row count {len(generated)} < reference {len(reference)}"]
        if not reference:
            return [] if not generated else ["reference has no rows but generated table does"]
        added = len(generated) - len(reference)
        pairs = [(0, 0)] + [(i + added, i) for i in range(1, len(reference))]

    failures = []
    for gen_i, ref_i in pairs:
        if generated[gen_i] != reference[ref_i]:
            failures.append(
                f"row {gen_i} differs from reference row {ref_i}\n"
                f"   generated : {generated[gen_i]}\n"
                f"   reference : {reference[ref_i]}"
            )
    return failures

def home_row_differences(generated, reference):
    """Allow only Latest Update and Build Number cells to change on home."""
    if len(generated) != len(reference):
        return [f"row count {len(generated)} != reference {len(reference)}"]

    failures = []
    for row_i, (gen_row, ref_row) in enumerate(zip(generated, reference)):
        if len(gen_row) != len(ref_row):
            failures.append(
                f"row {row_i} cell count {len(gen_row)} != reference {len(ref_row)}"
            )
            continue
        protected_columns = range(len(ref_row)) if row_i == 0 else (0, 3, 4)
        for col_i in protected_columns:
            if gen_row[col_i] != ref_row[col_i]:
                failures.append(
                    f"row {row_i} column {col_i} differs\n"
                    f"   generated : {gen_row[col_i]}\n"
                    f"   reference : {ref_row[col_i]}"
                )
    return failures

def selftest():
    header = [("Update", []), ("Build", [])]
    old1 = [("CU2", ["https://example.com/2"]), ("2.0", [])]
    old2 = [("CU1", ["https://example.com/1"]), ("1.0", [])]
    new = [("CU3", ["https://example.com/3"]), ("3.0", [])]
    changed = [("CU2 changed", ["https://example.com/2"]), ("2.0", [])]
    reference = [header, old1, old2]
    home_header = [("Version", []), ("Latest Update", []), ("Build Number", []),
                   ("Support Ends", []), ("Other Updates", [])]
    home_old = [("SQL Server 2025", []), ("CU6", ["https://example.com/6"]),
                ("17.0.4055.5", []), ("2036/01/06", []), ("Other", ["/2025/"])]
    home_new = [("SQL Server 2025", []), ("CU7", ["https://example.com/7"]),
                ("17.0.4065.4", []), ("2036/01/06", []), ("Other", ["/2025/"])]
    home_bad = list(home_new)
    home_bad[3] = ("changed", [])

    checks = [
        ("exact match", not row_differences(reference, reference)),
        ("prepended row allowed", not row_differences([header, new, old1, old2], reference, True)),
        ("prepended row rejected in strict mode", bool(row_differences([header, new, old1, old2], reference))),
        ("historical edit rejected", bool(row_differences([header, new, changed, old2], reference, True))),
        ("historical deletion rejected", bool(row_differences([header, old2], reference, True))),
        ("appended row rejected", bool(row_differences([header, old1, old2, new], reference, True))),
        ("header edit rejected", bool(row_differences([[('Patch', []), ('Build', [])], old1, old2], reference, True))),
        ("home latest cells allowed",
         not home_row_differences([home_header, home_new], [home_header, home_old])),
        ("home protected cell rejected",
         bool(home_row_differences([home_header, home_bad], [home_header, home_old]))),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS" if ok else "FAIL"), name)
    if failed:
        sys.exit(1)
    print("\nSELFTEST OK")

def main():
    failures = []
    added_rows = 0
    for gen, ref in PAGES.items():
        gen_html = (SITE / gen).read_text(encoding="utf-8")
        ref_html = (REF / ref).read_text(encoding="utf-8")
        g = table_rows(gen_html)
        r = table_rows(ref_html)
        allow_prepend = gen in ALLOW_PREPENDED_ROWS
        messages = (row_differences(g, r, allow_prepend) if allow_prepend
                    else home_row_differences(g, r))
        failures.extend(f"{gen}: {msg}" for msg in messages)
        if allow_prepend and len(g) >= len(r):
            added_rows += len(g) - len(r)
    if failures:
        print("FAIL — %d table difference(s):\n" % len(failures))
        print("\n".join(failures[:40]))
        sys.exit(1)
    historical = sum(len(table_rows((REF / r).read_text(encoding="utf-8"))) for r in PAGES.values())
    print(f"PASS — all {len(PAGES)} pages preserve the reference; {historical} historical "
          f"table rows verified (cell text + links identical), {added_rows} new row(s) allowed.")

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
