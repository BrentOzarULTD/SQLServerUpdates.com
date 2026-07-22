#!/usr/bin/env python3
"""
Static site generator for SQLServerUpdates.com.

Reads structured data from data/ and renders static HTML into _site/.
Standard library only -- no pip install, no npm. Run with:  python3 build.py
"""
import csv
import html
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "_site"
ASSETS = ROOT / "assets"

def read(p):
    return Path(p).read_text(encoding="utf-8")

def load_data():
    return json.loads(read(DATA / "versions.json"))

# ---------------------------------------------------------------- rendering ---

def render_table(header, rows, header_tag="th"):
    """Render a wp-block-table-compatible table. Cells contain trusted inner HTML
    taken verbatim from the data files."""
    out = ['<figure class="wp-block-table"><table><tbody>']
    if header:
        cells = "".join(f"<{header_tag}>{c}</{header_tag}>" for c in header)
        out.append(f"<tr>{cells}</tr>")
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        out.append(f"<tr>{cells}</tr>")
    out.append("</tbody></table></figure>")
    return "\n".join(out)

def load_csv(slug):
    with open(DATA / "updates" / f"{slug}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])

def layout(data, title, body, description=None, canonical=None):
    site = data["site"]
    desc = description or site["description"]
    nav = "\n".join(
        f'          <li><a href="{html.escape(n["url"])}">{html.escape(n["label"])}</a></li>'
        for n in data["nav"]
    )
    canonical_tag = f'\n  <link rel="canonical" href="{html.escape(canonical)}">' if canonical else ""
    year = "2026"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(desc)}">{canonical_tag}
  <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
  <header class="site-header">
    <div class="wrap">
      <a class="brand" href="/">{html.escape(site["title"])}</a>
      <button class="nav-toggle" aria-label="Toggle navigation" onclick="document.body.classList.toggle('nav-open')">&#9776;</button>
      <nav class="site-nav">
        <ul>
{nav}
        </ul>
      </nav>
    </div>
  </header>
  <main class="wrap content">
{body}
  </main>
  <footer class="site-footer">
    <div class="wrap">
      <p>{html.escape(site["title"])} is an independently maintained community resource.
         Found an out-of-date build?
         <a href="https://github.com/BrentOzarULTD/SQLServerUpdates.com">Contribute on GitHub</a>.</p>
      <p><a href="/frequently-asked-questions/">FAQ</a> &middot;
         <a href="/contact-us/">Contact</a> &middot;
         <a href="/privacy-policy/">Privacy Policy</a> &middot;
         &copy; {year} SQLServerUpdates.com</p>
    </div>
  </footer>
</body>
</html>
"""

def write_page(rel_url, contents):
    # rel_url "/" -> index.html ; "/foo/" -> foo/index.html
    if rel_url == "/":
        target = OUT / "index.html"
    else:
        target = OUT / rel_url.strip("/") / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return target

# --------------------------------------------------------------------- build ---

def build():
    data = load_data()
    site = data["site"]
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # assets
    if ASSETS.exists():
        shutil.copytree(ASSETS, OUT / "assets")

    pages_built = []

    # ---- home ----
    home_top = read(DATA / "fragments" / "home.top.html").strip()
    home_bottom = read(DATA / "fragments" / "home.bottom.html").strip()
    ht = data["home_table"]
    home_rows = [[r["version"], r["latest_update"], r["build"], r["support_ends"], r["other_updates"]]
                 for r in ht["rows"]]
    body = "\n".join([home_top, render_table(ht["header"], home_rows, "td"), home_bottom])
    html_doc = layout(data, f'{site["title"]} - {site["description"]}', body,
                      canonical=site["url"] + "/")
    write_page("/", html_doc)
    pages_built.append("/")

    # ---- version pages ----
    for v in data["versions"]:
        slug = v["slug"]
        top = read(DATA / "fragments" / f"{slug}.top.html").strip()
        bottom = read(DATA / "fragments" / f"{slug}.bottom.html").strip()
        header, rows = load_csv(slug)
        table = render_table(header, rows, v.get("header_tag", "th"))
        parts = [f'<h1>{html.escape(v["menu"])} Updates</h1>', top, table]
        if bottom:
            parts.append(bottom)
        body = "\n".join(parts)
        title = f'{v["menu"]} Updates - Build Numbers & Downloads'
        html_doc = layout(data, title, body, canonical=f'{site["url"]}/{slug}/')
        write_page(f"/{slug}/", html_doc)
        pages_built.append(f"/{slug}/")

    # ---- static pages ----
    for p in data["static_pages"]:
        slug = p["slug"]
        content = read(DATA / "pages" / f"{slug}.html").strip()
        body = f'<h1>{html.escape(p["title"])}</h1>\n{content}'
        html_doc = layout(data, p["title"], body, canonical=f'{site["url"]}/{slug}/')
        write_page(f"/{slug}/", html_doc)
        pages_built.append(f"/{slug}/")

    # ---- domain / hosting files ----
    (OUT / "CNAME").write_text("sqlserverupdates.com\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {site['url']}/sitemap.xml\n", encoding="utf-8")

    # ---- sitemap ----
    urls = "".join(
        f"  <url><loc>{site['url']}{u if u!='/' else '/'}</loc></url>\n" for u in pages_built)
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n", encoding="utf-8")

    print(f"Built {len(pages_built)} pages into {OUT}/")
    for u in pages_built:
        print("  ", u)

if __name__ == "__main__":
    build()
