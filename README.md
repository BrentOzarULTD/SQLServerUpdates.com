# SQLServerUpdates.com

The source for [SQLServerUpdates.com](https://sqlserverupdates.com) — a
community-maintained list of SQL Server build numbers, cumulative updates, and
download links.

The site is **static HTML generated from small data files** and hosted on
GitHub Pages. Contributors keep it current by editing data and opening pull
requests — no HTML required.

## Quick start

```bash
python3 validate.py       # validate the data files
python3 build.py          # generate the site into _site/
python3 test_snapshot.py  # verify output matches the reference snapshots
```

No dependencies — just Python 3 (standard library only).

## Repository layout

```
data/
  versions.json              site metadata, nav, and the home-page summary table
  updates/<slug>.csv         one CSV per SQL Server version (the update tables)
  fragments/<slug>.top.html  intro text above each version's table
  fragments/<slug>.bottom.html  footnotes below each table
  fragments/home.top.html    home page intro / outro
  pages/<slug>.html          static pages (download, FAQ, contact, privacy)
assets/style.css             site styling
reference/                   frozen original HTML, used by the fidelity test
build.py                     the generator (stdlib only)
validate.py                  data validation (runs in CI)
test_snapshot.py             verifies generated tables == reference
.github/workflows/           CI (on PRs) and deploy (to GitHub Pages)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: add a line to the
right CSV in `data/updates/`, open a pull request, and the checks + deploy run
automatically.

## How hosting works

- Pushes to `main` trigger `.github/workflows/deploy.yml`, which builds the site
  and publishes it to GitHub Pages.
- The custom domain is set via the `CNAME` file emitted into `_site/`.
- Existing URLs are preserved exactly (e.g. `/sql-server-2022-updates/`).
