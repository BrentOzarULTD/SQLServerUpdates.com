#!/usr/bin/env python3
"""
Poll Microsoft's SQL Server blog RSS feed for new build releases (cumulative
updates, GDR/security updates, service packs), use an LLM to extract the
structured details, and add them to the data files.

Designed to run in GitHub Actions on a schedule. When it changes any data file,
the workflow opens a pull request for a human to review and merge -- the LLM is
never trusted to publish build numbers unreviewed.

The techcommunity.microsoft.com ARTICLE pages block scripted requests (HTTP 403),
so we never fetch them. Instead we take the KB / release-notes link out of the
RSS <description> and read the build number from the KB page on
learn.microsoft.com / support.microsoft.com, which are not blocked.

Dependency-free: standard library only. The OpenAI API is called over HTTPS with
urllib. Set OPENAI_API_KEY in the environment.

Usage:
    python3 poll_feed.py            # real run (needs network + OPENAI_API_KEY)
    python3 poll_feed.py --selftest # offline unit checks of the pure logic
"""
import csv
import gzip
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DATA = ROOT / "data"
FEED_URL = "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=SQLServer"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
SITE_TIMEZONE = ZoneInfo("America/Los_Angeles")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

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

# Product-version major prefix per version. Used to reject file versions
# (e.g. "2025.170.4045.5") and cross-version hallucinations. Only enforced for
# the modern versions that actually still receive updates.
EXPECTED_MAJOR = {
    "2025": "17.0", "2022": "16.0", "2019": "15.0", "2017": "14.0",
    "2016": "13.0", "2014": "12.0", "2012": "11.0",
}

RELEASE_KEYWORDS = re.compile(r"cumulative update|security update|service pack|\bGDR\b|\bCU\d", re.I)
EXCLUDE = re.compile(r"management studio|\bSSMS\b|ODBC|JDBC|OLE DB|python|driver|feature pack", re.I)
BUILD_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")


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

def normalize_feed_date(value):
    """Convert an RSS timestamp to the site's Pacific calendar date."""
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(SITE_TIMEZONE).date().strftime("%Y/%m/%d")
    except (TypeError, ValueError, OverflowError):
        return ""

def choose_release_date(rss_date, model_date):
    """Prefer the normalized RSS date; fall back only when the feed lacks one."""
    return (rss_date or (model_date or "")).strip()

def oldest_first(items):
    """Process oldest releases first because each write prepends its row."""
    return sorted(items, key=lambda item: normalize_feed_date(item.get("date", "")))

def build_key(build):
    """Comparable numeric key for a validated SQL Server build string."""
    try:
        return tuple(int(part) for part in build.split("."))
    except (AttributeError, ValueError):
        return ()

def normalize_update_label(title, label):
    """Security updates on a CU branch must be labeled as GDR updates."""
    label = (label or "Update").strip()
    if "security update" in title.lower() and "GDR" not in label.upper():
        return f"{label} + GDR"
    return label

def table_release_metadata(page_html, build):
    """Find a build's specific KB URL and date in Microsoft's update table."""
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_html or "", re.S | re.I):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S | re.I)
        if not cells:
            continue
        first = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
        if first != build:
            continue
        kb = ""
        for url in re.findall(r'href="([^"]+)"', tr, re.I):
            if re.match(r"https://support\.microsoft\.com/(?:help|kb)/\d+$", url, re.I):
                kb = html.unescape(url)
                break
        date = ""
        for cell in reversed(cells):
            value = html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            try:
                date = datetime.strptime(value, "%B %d, %Y").strftime("%Y/%m/%d")
                break
            except ValueError:
                continue
        return kb, date
    return "", ""

def is_generic_kb_url(url):
    return "download-and-install-latest-updates" in (url or "")

def replace_last_link(value, url, label):
    """Preserve the install path on the home page and replace its last update."""
    link = f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
    matches = list(re.finditer(r"<a\b[^>]*>.*?</a>", value or "", re.S | re.I))
    if not matches:
        return link
    last = matches[-1]
    return value[:last.start()] + link + value[last.end():]

def apply_home_update(data, version, label, build, kb_url):
    """Advance a home-table row only when the candidate has a higher build."""
    wanted = f"SQL Server {version}"
    for row in data.get("home_table", {}).get("rows", []):
        if row.get("version") != wanted:
            continue
        if build_key(build) <= build_key(row.get("build", "")):
            return False
        row["latest_update"] = replace_last_link(row.get("latest_update", ""), kb_url, label)
        row["build"] = build
        return True
    return False

def title_version(title):
    if "2008 r2" in title.lower():
        return "2008 R2"
    for tok in TRACKED_TOKENS:
        if tok == "2008 R2":
            continue
        if re.search(r"\b%s\b" % re.escape(tok), title):
            return tok
    return None

def is_candidate(title):
    if EXCLUDE.search(title):
        return False
    if not RELEASE_KEYWORDS.search(title):
        return False
    return title_version(title) is not None

def extract_kb_links(description):
    """All learn/support.microsoft.com URLs in the RSS description (href or bare)."""
    urls = re.findall(r'https?://(?:learn|support)\.microsoft\.com/[^\s"\'<>)]+', description or "")
    out = []
    for u in urls:
        u = u.rstrip(".,);")
        if u not in out:
            out.append(u)
    return out

def pick_kb(urls):
    """Prefer a release-specific KB/release-notes page over generic landing pages."""
    for u in urls:
        if re.search(r"/kb/\d+|kb\d{6,}|cumulativeupdate\d+|securityupdate|/sqlserver-20\d\d/", u, re.I):
            return u
    return urls[0] if urls else None

def strip_html(html_text, limit=8000):
    text = re.sub(r"<script.*?</script>", " ", html_text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]

def existing_builds(slug):
    path = DATA / "updates" / f"{slug}.csv"
    builds = set()
    if not path.exists():
        return builds
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    idx = {n.strip().lower(): i for i, n in enumerate(rows[0])}
    bi = idx.get("build")
    for row in rows[1:]:
        if bi is not None and bi < len(row):
            b = re.sub("<[^>]+>", "", row[bi]).strip()
            if b:
                builds.add(b)
    return builds

def build_row(slug, label, kb_url, date, build):
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
    with open(DATA / "updates" / f"{slug}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in all_rows:
            w.writerow(r)

def valid_build(version, build):
    if not BUILD_RE.match(build):
        return False
    major = EXPECTED_MAJOR.get(version)
    return True if major is None else build.startswith(major + ".")


# ------------------------------------------------------------------- network ---

def http_get(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if data[:2] == b"\x1f\x8b":            # gzip, even though we asked for identity
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")

def fetch_first_ok(urls):
    """Fetch the first URL that responds; return (url, text) or (None, None)."""
    for u in urls:
        try:
            return u, http_get(u)
        except Exception as e:
            print(f"    kb fetch failed ({u}): {e}")
    return None, None

def llm_extract(api_key, title, rss_date, desc_text, kb_url, kb_text, candidate_links):
    system = (
        "You extract SQL Server release details. Respond ONLY with a JSON object: "
        "is_release (boolean), version (one of " + json.dumps(TRACKED_TOKENS) + " or null), "
        "update_label (short, e.g. 'CU25','GDR','CU24 GDR','SP3'), "
        "build (the SQL Server DATABASE ENGINE product version, formatted like "
        "17.0.4045.5 -- NOT the file version like 2025.170.4045.5, and NOT the "
        "Analysis Services version), kb_url (best KB/download link for THIS update), "
        "release_date (YYYY/MM/DD; use the supplied RSS publication date when present), "
        "reason. is_release is true ONLY if this announces "
        "a specific engine build number for one of the listed versions; false for "
        "SSMS, drivers, feature packs, roundups, or general posts."
    )
    user = (
        f"TITLE: {title}\n\n"
        f"RSS PUBLICATION DATE (authoritative): {rss_date or 'unknown'}\n\n"
        f"RSS SUMMARY:\n{desc_text}\n\n"
        f"CANDIDATE KB LINKS: {candidate_links}\n\n"
        f"KB PAGE ({kb_url}):\n{kb_text}"
    )
    payload = {
        "model": OPENAI_MODEL, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        OPENAI_URL, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read())
    return json.loads(data["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------- main ---

def run():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    feed = parse_feed(http_get(FEED_URL))
    candidates = oldest_first([it for it in feed if is_candidate(it["title"])])
    print(f"Feed items: {len(feed)}; candidates: {len(candidates)}")

    added = []
    kb_ok = 0
    llm_err = 0
    for it in candidates:
        rss_date = normalize_feed_date(it["date"])
        kb_links = extract_kb_links(it["description"])
        if not kb_links:
            print(f"  skip (no KB link in feed): {it['title']}")
            continue
        preferred = pick_kb(kb_links)
        order = [preferred] + [u for u in kb_links if u != preferred]
        kb_url, kb_text = fetch_first_ok(order)
        if not kb_text:
            print(f"  skip (KB fetch failed): {it['title']}")
            continue
        kb_ok += 1
        try:
            r = llm_extract(api_key, it["title"], rss_date,
                            strip_html(it["description"], 2000), kb_url,
                            strip_html(kb_text), kb_links)
        except Exception as e:
            llm_err += 1
            print(f"  skip (llm failed): {it['title']}: {e}")
            continue
        if not r.get("is_release"):
            continue
        version = r.get("version")
        build = (r.get("build") or "").strip()
        slug = VERSION_TO_SLUG.get(version)
        if not slug or not valid_build(version, build):
            print(f"  skip (unclear/bad build): {it['title']} -> version={version} build={build!r}")
            continue
        if build in existing_builds(slug):
            continue
        label = normalize_update_label(it["title"], r.get("update_label"))
        llm_date = (r.get("release_date") or "").strip()
        table_kb, table_date = table_release_metadata(kb_text, build)
        date = table_date or choose_release_date(rss_date, llm_date)
        if rss_date and llm_date and rss_date != llm_date:
            source = "Microsoft table" if table_date else "RSS"
            print(f"    date mismatch: model={llm_date}, using {source}={date}")
        # Prefer the build's exact KB from Microsoft's update-index table. A
        # generic index is useful for extraction but not as a reader-facing row
        # link, so fall back to the specific source post if no KB can be found.
        kb = table_kb or (it["link"] if is_generic_kb_url(kb_url) else kb_url.strip())
        _, _, all_rows = build_row(slug, label, kb, date, build)
        write_rows(slug, all_rows)
        added.append((version, slug, label, build, date, kb, it["title"], it["link"]))
        print(f"  ADDED {version}: {label} {build} ({date})")

    if added:
        versions_path = DATA / "versions.json"
        versions_data = json.loads(versions_path.read_text(encoding="utf-8"))
        home_updates = {}
        for version, slug, label, build, date, kb, title, link in added:
            if apply_home_update(versions_data, version, label, build, kb):
                home_updates[version] = (label, build)
        if home_updates:
            versions_path.write_text(json.dumps(versions_data, indent=2) + "\n", encoding="utf-8")

        lines = ["Automated update from the Microsoft SQL Server blog RSS feed.\n",
                 "The following build(s) were detected and added. **Please verify each "
                 "against its source post before merging.**\n",
                 "| Version | Update | Build | Date | KB | Source |",
                 "|---|---|---|---|---|---|"]
        for v, slug, label, build, date, kb, title, link in added:
            lines.append(f"| SQL Server {v} | {label} | `{build}` | {date} | [KB]({kb}) | [post]({link}) |")
        if home_updates:
            summary = ", ".join(f"SQL Server {v} to {label} (`{build}`)"
                                for v, (label, build) in home_updates.items())
            lines.append(f"\nThe home-page latest-build pointer was advanced for: {summary}.")
        else:
            lines.append("\nThe home-page latest-build pointers were already on higher builds.")
        Path(ROOT / "pr_body.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nSummary: candidates={len(candidates)} kb_fetched={kb_ok} "
          f"llm_errors={llm_err} added={len(added)}")
    if added:
        print(f"{len(added)} build(s) added.")
    elif kb_ok and llm_err == kb_ok:
        print("ERROR: fetched the KB pages but EVERY LLM extraction failed. This is "
              "almost always the OPENAI_API_KEY secret: invalid key, or the key's "
              "project has no access to the model, or it is over its budget/rate limit.")
        sys.exit(1)
    else:
        print("No new builds found.")


# ------------------------------------------------------------------ selftest ---

def selftest():
    ok = True
    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name)
        ok = ok and cond

    check("CU is candidate", is_candidate("Cumulative Update #25 for SQL Server 2022 RTM"))
    check("GDR is candidate", is_candidate("Security Update for SQL Server 2019 RTM CU32"))
    check("SSMS excluded", not is_candidate("Announcing SQL Server Management Studio 22.6.0"))
    check("driver excluded", not is_candidate("Microsoft ODBC Driver 17.11.1 for SQL Server Released"))
    check("2008 R2 detected", title_version("Security Update for SQL Server 2008 R2 SP3") == "2008 R2")
    check("2008 not R2", title_version("Security Update for SQL Server 2008 SP4") == "2008")

    desc = ('<P>The 5th cumulative update...<BR/></P><UL>'
            '<LI>CU5 KB Article: https://learn.microsoft.com/troubleshoot/sql/releases/sqlserver-2025/cumulativeupdate5</LI>'
            '<LI>Update Center: https://learn.microsoft.com/en-us/troubleshoot/sql/releases/download-and-install-latest-updates</LI></UL>')
    links = extract_kb_links(desc)
    check("kb links extracted", any("cumulativeupdate5" in u for u in links))
    check("pick_kb prefers specific", "cumulativeupdate5" in (pick_kb(links) or ""))
    gdr = 'See https://support.microsoft.com/kb/5090407 for details.'
    check("support kb extracted", pick_kb(extract_kb_links(gdr)) == "https://support.microsoft.com/kb/5090407")

    check("product version valid", valid_build("2025", "17.0.4045.5"))
    check("file version rejected", not valid_build("2025", "2025.170.4045.5"))
    check("cross-version rejected", not valid_build("2022", "17.0.4045.5"))
    check("2022 build valid", valid_build("2022", "16.0.4300.1"))
    check("RSS date normalized",
          normalize_feed_date("Thu, 16 Jul 2026 17:30:00 +0000") == "2026/07/16")
    check("RSS midnight converted to Pacific date",
          normalize_feed_date("Fri, 17 Jul 2026 00:30:00 +0000") == "2026/07/16")
    check("invalid RSS date rejected", normalize_feed_date("not a date") == "")
    check("RSS date overrides model date",
          choose_release_date("2026/07/16", "2023/07/14") == "2026/07/16")
    check("model date is fallback", choose_release_date("", "2026/07/14") == "2026/07/14")
    ordered = oldest_first([{"date": "Thu, 16 Jul 2026 00:00:00 +0000", "id": "new"},
                            {"date": "Tue, 14 Jul 2026 00:00:00 +0000", "id": "old"}])
    check("feed processed oldest first", [item["id"] for item in ordered] == ["old", "new"])
    check("security CU label includes GDR",
          normalize_update_label("Security Update for SQL Server 2017 RTM CU31", "CU31") == "CU31 + GDR")
    check("plain CU label unchanged",
          normalize_update_label("Cumulative Update 7 for SQL Server 2025", "CU7") == "CU7")
    table_html = ('<table><tr><td>17.0.4060.2</td><td>None</td><td>CU6 + GDR</td>'
                  '<td><a href="https://support.microsoft.com/help/5101346">KB5101346</a></td>'
                  '<td>July 14, 2026</td></tr></table>')
    table_kb, table_date = table_release_metadata(table_html, "17.0.4060.2")
    check("specific KB extracted from update table",
          table_kb == "https://support.microsoft.com/help/5101346")
    check("release date extracted from update table", table_date == "2026/07/14")
    check("generic update index detected",
          is_generic_kb_url("https://learn.microsoft.com/x/download-and-install-latest-updates"))
    home = {"home_table": {"rows": [{
        "version": "SQL Server 2025",
        "latest_update": '<a href="download">Download RTM</a> then <a href="cu6">CU6</a>',
        "build": "17.0.4055.5",
    }]}}
    check("higher build advances home",
          apply_home_update(home, "2025", "CU7", "17.0.4065.4", "https://example.com/cu7"))
    check("home install path preserved",
          home["home_table"]["rows"][0]["latest_update"].startswith('<a href="download">'))
    check("lower build does not regress home",
          not apply_home_update(home, "2025", "GDR", "17.0.1125.2", "https://example.com/gdr"))

    if (DATA / "updates" / "sql-server-2022-updates.csv").exists():
        h, row, allr = build_row("sql-server-2022-updates", "CU99",
                                 "https://support.microsoft.com/kb/9999999", "2026/07/01", "16.0.9999.1")
        bi = [c.lower() for c in h].index("build")
        check("2022 row build set", row[bi] == "16.0.9999.1")
        check("2022 row inserted at top", allr[1] == row)
        h2, row2, _ = build_row("sql-server-2016-updates", "GDR",
                                "https://support.microsoft.com/kb/1", "2026/07/01", "13.0.9999.1")
        idx2 = {c.lower(): i for i, c in enumerate(h2)}
        check("2016 label in CU col", "GDR" in row2[idx2["cumulative update"]])
        check("2016 support ends carried", row2[idx2["support ends"]] != "")
        check("build_row did not write", "16.0.9999.1" not in existing_builds("sql-server-2022-updates"))

    print("\nSELFTEST", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        run()
