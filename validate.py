#!/usr/bin/env python3
"""
Validate the data files before building. Runs in CI on every pull request so
contributors get fast, clear feedback. Standard library only.

Run:  python3 validate.py
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

BUILD_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}")  # allows trailing notes after the date
HREF_RE = re.compile(r'href="([^"]*)"')

errors = []
warnings = []

def err(msg): errors.append(msg)
def warn(msg): warnings.append(msg)

def check_links(where, cell):
    for href in HREF_RE.findall(cell):
        if not re.match(r"^(https?:)?//|^https?://", href) and not href.startswith("#"):
            err(f"{where}: link is not an absolute URL: {href!r}")

def main():
    data = json.loads((DATA / "versions.json").read_text(encoding="utf-8"))

    # --- versions.json sanity ---
    for key in ("site", "nav", "home_table", "versions", "static_pages"):
        if key not in data:
            err(f"versions.json missing top-level key: {key}")

    # --- home table ---
    ht = data["home_table"]
    ncol = len(ht["header"])
    for i, row in enumerate(ht["rows"]):
        for f in ("version", "latest_update", "build", "support_ends", "other_updates"):
            if f not in row:
                err(f"home_table row {i} missing field {f}")
        check_links(f"home_table row {i}", row.get("latest_update", ""))
        check_links(f"home_table row {i}", row.get("other_updates", ""))
        b = row.get("build", "").strip()
        if b and not BUILD_RE.match(b):
            warn(f"home_table row {i}: unusual build number {b!r}")

    # --- version pages ---
    for v in data["versions"]:
        slug = v["slug"]
        csv_path = DATA / "updates" / f"{slug}.csv"
        top = DATA / "fragments" / f"{slug}.top.html"
        if not csv_path.exists():
            err(f"{slug}: missing {csv_path.name}")
            continue
        if not top.exists():
            err(f"{slug}: missing fragment {top.name}")
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            err(f"{slug}: CSV is empty")
            continue
        header = rows[0]
        width = len(header)
        # locate columns by name
        idx = {name.strip().lower(): i for i, name in enumerate(header)}
        build_i = idx.get("build")
        date_i = idx.get("release date")
        seen_builds = {}
        for r, row in enumerate(rows[1:], start=2):
            if len(row) != width:
                err(f"{slug} line {r}: has {len(row)} columns, expected {width} ({header})")
                continue
            for cell in row:
                check_links(f"{slug} line {r}", cell)
            if build_i is not None:
                b = re.sub("<[^>]+>", "", row[build_i]).strip()
                if b:
                    if not BUILD_RE.match(b):
                        warn(f"{slug} line {r}: unusual build number {b!r}")
                    elif b in seen_builds:
                        err(f"{slug} line {r}: duplicate build {b} (also line {seen_builds[b]})")
                    else:
                        seen_builds[b] = r
            if date_i is not None:
                d = re.sub("<[^>]+>", "", row[date_i]).strip()
                if d and not DATE_RE.match(d):
                    warn(f"{slug} line {r}: date {d!r} not in YYYY/MM/DD form")

    # --- static pages exist ---
    for p in data["static_pages"]:
        pg = DATA / "pages" / f"{p['slug']}.html"
        if not pg.exists():
            err(f"static page missing: {pg.name}")

    if warnings:
        print("Warnings:")
        for w in warnings:
            print("  ⚠ ", w)
    if errors:
        print("\nFAIL — %d error(s):" % len(errors))
        for e in errors:
            print("  ✗ ", e)
        sys.exit(1)
    print("PASS — data files are valid.")

if __name__ == "__main__":
    main()
