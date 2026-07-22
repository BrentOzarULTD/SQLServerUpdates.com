# SQLServerUpdates.com — Migration Plan (WordPress → static site on GitHub)

Goal: replace the WordPress site with a set of **static HTML pages built from
structured data**, hosted on **GitHub Pages** at the existing domain
**SQLServerUpdates.com**, so that followers can contribute new SQL Server builds
by opening **pull requests**.

## Guiding decisions (agreed)

- **Authoring model:** structured data + a generator. Contributors edit small,
  human-friendly data files (not raw HTML). A dependency-free build script
  renders the static pages. The home-page roll-up table is generated from the
  same per-version data, so it can never drift from the version pages.
- **"Identical content":** same *structure & data* — same tables, columns, build
  numbers, KB links, and element nesting that existing scrapers rely on. Trivial
  whitespace/wrapper differences are acceptable; the parseable structure is
  preserved and guarded by a snapshot test.
- **URLs & hosting:** exact same URLs (e.g. `/sql-server-2022-updates/`),
  served from **GitHub Pages** on the apex domain `SQLServerUpdates.com` over
  HTTPS (via a `CNAME` file + DNS records).
- **Dropped:** everything under `/news`, all blog posts, the WordPress blog
  scaffolding pages, the "Recent Updates" sidebar, and the "Subscribe" form.
  (The last two were theme chrome, not page content.)
- **Safety:** the `old-wordpress/` folder — including the database backup — is
  gitignored and must **never** be committed or pushed.

## Content inventory (pages kept)

Source of truth is the database backup. Front page is WordPress `page_on_front=5`.

| Live URL | Source page | Notes |
|---|---|---|
| `/` | The Most Recent Updates for Microsoft SQL Server (ID 5) | Home; roll-up comparison table |
| `/sql-server-2025-updates/` | ID 512 | Version page |
| `/sql-server-2022-updates/` | ID 365 | Version page |
| `/sql-server-2019-updates/` | ID 304 | Version page |
| `/sql-server-2017-updates/` | ID 154 | Version page |
| `/sql-server-2016-updates/` | ID 100 | Version page |
| `/sql-server-2014-updates/` | ID 10 | Version page |
| `/sql-server-2012-updates/` | ID 26 | Version page |
| `/sql-server-2008-r2-updates/` | ID 12 | Version page |
| `/sql-server-2008-updates/` | ID 7 | Version page |
| `/download-sql-server/` | ID 32 (Get SQL Server and Management Studio) | Static content |
| `/frequently-asked-questions/` | ID 14 | Static content |
| `/contact-us/` | ID 28 | Static content |
| `/privacy-policy/` | ID 223 | Footer link |

Dropped pages: `search-results-page` (268), `blog-home-page` (269), and all
`post`-type entries (the `/news` blog).

## Data model

Per-version data is split into two pieces, both editable without any tooling:

- `data/updates/<version>.csv` — one row per update, columns
  `name,date,build,url` (mirrors the version-page table exactly). **This is the
  file contributors touch to add a new build — one new line.**
- `data/versions.json` — per-version metadata: display name, menu order,
  support/mainstream end dates, and the home-page "Latest Update" / "Other
  Updates" links.

Static pages (download, FAQ, contact, privacy) are stored as content fragments
and wrapped by the same layout.

## Build pipeline

- `build.py` — a single **Python standard-library-only** generator (no
  `npm install`, no `pip install`). Reads the data files, applies HTML
  templates that reproduce the current markup, and writes the site into
  `_site/` with folder-per-URL (`/sql-server-2022-updates/index.html`, etc.).
- Runs identically in local dev, in CI, and on the maintainer's machine.

## Verification

- Reference snapshots of every kept page's current HTML are captured from the
  backup and stored under `reference/`.
- A snapshot test asserts the generated pages match the reference in structure
  and data (tables, rows, build numbers, links), so accidental structural
  regressions fail CI.

## Phases

1. **Extract & freeze current content** — pull exact HTML per kept page, save
   reference snapshots, inventory URLs. *(complete)*
2. **Design data model** — convert frozen tables into `data/` files; verify by
   regenerating and diffing against snapshots.
3. **Build generator & templates** — reproduce markup, wire home-page roll-up,
   pass the snapshot test.
4. **Repo for contributions** — `CONTRIBUTING.md`, PR template, data
   validation, and CI checks on every pull request.
5. **Deploy on GitHub Pages** — build-and-publish Action, `CNAME`, HTTPS;
   staged first on the `*.github.io` URL.
6. **DNS cutover & verification** — point DNS at GitHub Pages, confirm HTTPS,
   verify every old URL resolves to matching content and scrapers still parse.
   Keep the WordPress backup as rollback.

## Contributor workflow (end state)

1. Fork the repo, add one line to `data/updates/<version>.csv` (or edit
   `versions.json`).
2. Open a pull request. CI validates the data (format, no duplicate builds,
   links well-formed) and builds the site.
3. Maintainer reviews a small data diff and merges. A deploy Action rebuilds and
   publishes; the version page **and** the home-page row update together.
