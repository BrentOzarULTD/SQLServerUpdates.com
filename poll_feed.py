#!/usr/bin/env python3
"""
Poll Microsoft's SQL Server blog RSS feed for new build releases (cumulative
updates, GDR/security updates, service packs), use an LLM to extract the
structured details from each post, and add them to the data files.

Designed to run in GitHub Actions on a schedule. When it changes any data file,
the workflow opens a pull request for a human to review and merge -- the LLM is
never trusted to publish build numbers unreviewed.

Dependency-free: standard library only. The OpenAI API is called over HTTPS with
urllib. Set OPENAI_API_KEY in the environment.

Usage:
    python3 poll_feed.py            # real run (needs network + OPENAI_API_KEY)
    python3 poll_feed.py --selftest # offline unit checks of the pure logic
"""
import csv
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FEED_URL = "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=SQLServer"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Versions we track -> CSV slug. Order matters for "2008 R2" before "2008".
VERSION_TO_SLUG = {
    "2025": "sql-server-2025-updates",
    "2022": "sql-server-2022-updates",
    "2019": "sql-server-2019-updates",
    "2017": "sql-server-2017-updates",
    "2016": "sql-server-2016-updates",
    "2014": "sql-server-2014-updates",
    "2012": "sql-server-2012-updates",
    "2008 R2": "sql-server-2008-r2-updates",
    "2008": "sql-server-2008-updates",
}
TRACKED_TOKENS = list(VERSION_TO_SLUG.keys())

# Only titles that look like an engine build release for a tracked version.
RELEASE_KEYWORDS = re.compile(r"cumulative update|security update|service pack|\bGDR\b|\bCU\d", re.I)
# Obvious non-engine posts to drop before spending an LLM call.
EXCLUDE = re.compile(r"management studio|\bSSMS\b|ODBC|JDBC|OLE DB|python|driver|feature pack", re.I)


# ----------------------------------------------------------------- pure logic ---

def parse_feed(xml_text):
    """Return list of dicts: {title, link, guid, date, description}."""
    items = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        def g(tag):
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        items.append({
            "title": g("title"),
            "link": g("link"),
            "guid": g("guid") or g("link"),
            "date": g("pubDate"),
            "description": g("description"),
        })
    return items

def title_version(title):
    """Best-guess tracked version token from a title, or None."""
    if "2008 r2" in title.lower():
        return "2008 R2"
    for tok in TRACKED_TOKENS:
        if tok == "2008 R2":
            continue
        if re.search(r"\b%s\b" % re.escape(tok), title):
            return tok
    return None

def is_candidate(title):
    """Cheap pre-filter: a build release for a tracked version, not a driver/SSMS."""
    if EXCLUDE.search(title):
        return False
    if not RELEASE_KEYWORDS.search(title):
        return False
    return title_version(title) is not None

def existing_builds(slug):
    path = DATA / "updates" / f"{slug}.csv"
    builds = set()
    if not path.exists():
        return builds
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx = {n.strip().lower(): i for i, n in enumerate(header)}
    bi = idx.get("build")
    for row in rows[1:]:
        if bi is not None and bi < len(row):
            b = re.sub("<[^>]+>", "", row[bi]).strip()
            if b:
                builds.add(b)
    return builds

def build_row(slug, label, kb_url, date, build):
    """Construct a new CSV row matching this version's column schema, carrying
    the Support Ends value forward from the current newest row. Returns
    (header, new_row, all_rows_after_insert)."""
    path = DATA / "updates" / f"{slug}.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx = {n.strip().lower(): i for i, n in enumerate(header)}
    row = [""] * len(header)
    link = f'<a href="{kb_url}">{label}</a>'
    if "cumulative update" in idx:
        row[idx["cumulative update"]] = link
    elif "service pack" in idx:
        row[idx["service pack"]] = link
    if "release date" in idx:
        row[idx["release date"]] = date
    if "build" in idx:
        row[idx["build"]] = build
    if "support ends" in idx and len(rows) > 1:
        top = rows[1]
        si = idx["support ends"]
        if si < len(top):
            row[si] = top[si]
    new_rows = [header, row] + rows[1:]
    return header, row, new_rows

def write_rows(slug, all_rows):
    path = DATA / "updates" / f"{slug}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in all_rows:
            w.writerow(r)


# ------------------------------------------------------------------- network ---

def http_get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": "sqlserverupdates-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")

def post_excerpt(html_text, limit=6000):
    """Strip a post's HTML to text plus the KB/release-notes links it contains."""
    links = re.findall(r'href="(https?://(?:support|learn|www)\.microsoft\.com[^"]+)"', html_text)
    text = re.sub(r"<script.*?</script>", " ", html_text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    seen = []
    for l in links:
        if l not in seen:
            seen.append(l)
    return text[:limit], seen[:15]

def llm_extract(api_key, title, text, links):
    system = (
        "You extract SQL Server release details from a Microsoft blog post. "
        "Respond ONLY with a JSON object with keys: is_release (boolean), "
        "version (one of " + json.dumps(TRACKED_TOKENS) + " or null), "
        "update_label (short, e.g. 'CU25', 'GDR', 'CU24 GDR', 'SP3'), "
        "build (e.g. '16.0.4255.1'), kb_url (the KB or release-notes URL for this "
        "update), release_date (YYYY/MM/DD), reason (one sentence). "
        "is_release is true ONLY if the post announces a specific build number for "
        "one of the listed SQL Server engine versions. It is false for SSMS, "
        "drivers, feature packs, or general blog posts."
    )
    user = (
        f"TITLE: {title}\n\n"
        f"CANDIDATE LINKS (pick the KB/download link for THIS update):\n"
        + "\n".join(links) + "\n\n"
        f"POST TEXT:\n{text}"
    )
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        OPENAI_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return json.loads(data["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------- main ---

BUILD_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")

def run():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    feed = parse_feed(http_get(FEED_URL))
    candidates = [it for it in feed if is_candidate(it["title"])]
    print(f"Feed items: {len(feed)}; candidates: {len(candidates)}")

    added = []  # (version, slug, label, build, date, kb, title, link)
    for it in candidates:
        try:
            body = http_get(it["link"])
        except Exception as e:
            print(f"  skip (fetch failed): {it['title']}: {e}")
            continue
        text, links = post_excerpt(body)
        try:
            r = llm_extract(api_key, it["title"], text, links)
        except Exception as e:
            print(f"  skip (llm failed): {it['title']}: {e}")
            continue
        if not r.get("is_release"):
            continue
        version = r.get("version")
        build = (r.get("build") or "").strip()
        slug = VERSION_TO_SLUG.get(version)
        if not slug or not BUILD_RE.match(build):
            print(f"  skip (unclear): {it['title']} -> {r}")
            continue
        if build in existing_builds(slug):
            continue  # already on the site
        label = (r.get("update_label") or "Update").strip()
        date = (r.get("release_date") or "").strip()
        kb = (r.get("kb_url") or it["link"]).strip()
        _, _, all_rows = build_row(slug, label, kb, date, build)
        write_rows(slug, all_rows)
        added.append((version, slug, label, build, date, kb, it["title"], it["link"]))
        print(f"  ADDED {version}: {label} {build} ({date})")

    # PR body for the workflow to consume.
    if added:
        lines = ["Automated update from the Microsoft SQL Server blog RSS feed.\n",
                 "The following build(s) were detected and added. **Please verify each "
                 "against its source post before merging.**\n",
                 "| Version | Update | Build | Date | Source |",
                 "|---|---|---|---|---|"]
        for v, slug, label, build, date, kb, title, link in added:
            lines.append(f"| SQL Server {v} | {label} | `{build}` | {date} | [post]({link}) |")
        lines.append("\n> Note: the home-page \"Latest Update\" pointer in "
                     "`data/versions.json` is intentionally left unchanged; update it "
                     "here if this is now the newest release for a version.")
        Path(ROOT / "pr_body.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"\n{len(added)} build(s) added.")
    else:
        print("\nNo new builds found.")

# ------------------------------------------------------------------ selftest ---

def selftest():
    ok = True
    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name)
        ok = ok and cond

    # candidate filtering
    check("CU is candidate", is_candidate("Cumulative Update #25 for SQL Server 2022 RTM"))
    check("GDR is candidate", is_candidate("Security Update for SQL Server 2019 RTM CU32"))
    check("SSMS excluded", not is_candidate("Announcing SQL Server Management Studio 22.6.0"))
    check("driver excluded", not is_candidate("Microsoft ODBC Driver 17.11.1 for SQL Server Released"))
    check("untracked year excluded", not is_candidate("Cumulative Update for SQL Server 2005"))
    check("2008 R2 detected", title_version("Security Update for SQL Server 2008 R2 SP3") == "2008 R2")
    check("2008 not R2", title_version("Security Update for SQL Server 2008 SP4") == "2008")
    check("2022 detected", title_version("Cumulative Update #25 for SQL Server 2022 RTM") == "2022")

    # feed parsing
    sample = """<?xml version="1.0"?><rss><channel>
      <item><title>Cumulative Update #99 for SQL Server 2022 RTM</title>
        <link>https://example.com/cu99</link><guid>g1</guid>
        <pubDate>Wed, 01 Jul 2026 00:00:00 GMT</pubDate>
        <description>build 16.0.9999.1</description></item>
    </channel></rss>"""
    items = parse_feed(sample)
    check("feed parsed one item", len(items) == 1 and items[0]["title"].startswith("Cumulative"))

    # row building against the real 3-col (2022) and 5-col (2016) schemas
    if (DATA / "updates" / "sql-server-2022-updates.csv").exists():
        h, row, allr = build_row("sql-server-2022-updates", "CU99",
                                 "https://support.microsoft.com/kb/9999999", "2026/07/01", "16.0.9999.1")
        bi = [c.lower() for c in h].index("build")
        check("2022 row build set", row[bi] == "16.0.9999.1")
        check("2022 row has link", 'href="https://support.microsoft.com/kb/9999999"' in row[0])
        check("2022 row inserted at top", allr[1] == row)
        h2, row2, _ = build_row("sql-server-2016-updates", "GDR",
                                "https://support.microsoft.com/kb/1", "2026/07/01", "13.0.9999.1")
        idx2 = {c.lower(): i for i, c in enumerate(h2)}
        check("2016 label in Cumulative Update col", 'GDR' in row2[idx2["cumulative update"]])
        check("2016 service pack col blank", row2[idx2["service pack"]] == "")
        check("2016 support ends carried", row2[idx2["support ends"]] != "")
        # important: selftest must not leave modified CSVs on disk
        check("build_row did not write to disk",
              "16.0.9999.1" not in existing_builds("sql-server-2022-updates"))

    print("\nSELFTEST", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run()
