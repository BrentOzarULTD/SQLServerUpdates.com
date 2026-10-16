# Contributing to SQLServerUpdates.com

Thanks for helping keep SQL Server build numbers current! This site is a set of
static pages generated from small data files. **You never have to write HTML** —
you edit a data file, open a pull request, and automation does the rest.

## How the site is built

- Data lives in [`data/`](data/).
- [`build.py`](build.py) turns that data into the static pages in `_site/`.
- Everything is **Python standard library** — no `pip install`, no `npm`.

## Adding a new build (the common case)

Each SQL Server version has a CSV file of its updates at
`data/updates/sql-server-<year>-updates.csv`. The first line is the column
header; every line after that is one update.

1. Open the CSV for the version you're updating, e.g.
   `data/updates/sql-server-2022-updates.csv`.
2. Add a new line at the **top** of the data, following the same
   columns as the row above it. For SQL Server 2022 the columns are
   `Cumulative Update,Release Date,Build`:

   ```csv
   "<a href=""https://learn.microsoft.com/troubleshoot/sql/releases/sqlserver-2022/cumulativeupdate26"">CU26</a>",2026/08/12,16.0.4260.1
   ```

   - Dates use `YYYY/MM/DD`.
   - Published tables sort by numeric build number, highest first, so RTM GDRs
     appear below the higher CU builds regardless of publication date.
   - The update-name cell contains a normal link to the Microsoft KB / release
     notes page. In CSV, double quotes inside a field are written as `""`.
3. If this update is now the **latest** for that version, also update that
   version's row in `data/versions.json` (the `home_table` section) so the home
   page's summary matches — change the `build` and the `latest_update` link.
   A CU plus GDR needs two separate links: `CU9` then `GDR`, each pointing to
   its own KB/download page. Keep any RTM or service-pack installation steps.
4. Commit and open a pull request.

## What happens when you open a PR

Automated checks run on your pull request:

- **Validate** — data files parse, columns line up, build numbers look right,
  no duplicate builds, links are absolute URLs.
- **Build** — the site generates cleanly.
- **Verify** — version tables sort by numeric build and preserve all historical
  rows and table headers from `reference/` (including cell text and links).

A maintainer reviews the small data diff and merges. On merge, the site is
rebuilt and deployed automatically.

## Running it locally (optional)

```bash
python3 validate.py       # check the data
python3 build.py          # generate _site/
python3 test_snapshot.py  # confirm historical rows still match reference
# then open _site/index.html in a browser
```

## Editing other pages

- Version-page intro text and footnotes: `data/fragments/<slug>.top.html` and
  `<slug>.bottom.html`.
- Home page intro/outro: `data/fragments/home.top.html`, `home.bottom.html`.
- Download / FAQ / Contact / Privacy pages: `data/pages/<slug>.html`.

## A note on the reference snapshots

`reference/` holds a frozen copy of the original page content. On version pages,
the verify step allows additions and build sorting while requiring the header
and every historical row to remain intact. On home, only Latest Update and
Build Number cells may change. If you intentionally change a table's structure,
update the reference in the same PR and explain why.
