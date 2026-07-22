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
python3 test_snapshot.py  # verify output preserves the reference snapshots
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
test_snapshot.py             verifies historical rows and table structure
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

## Automated build detection

`.github/workflows/poll-feed.yml` runs every 6 hours and checks Microsoft's
[SQL Server blog RSS feed](https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=SQLServer)
for new cumulative updates, GDR/security updates, and service packs. For each
new release it uses an LLM (`poll_feed.py`, OpenAI, called over plain `urllib` —
no dependencies) to extract the version, build number, and KB link. The release
date comes from the RSS publication timestamp. It adds a row to the right
`data/updates/*.csv`, advances the home-page pointer when the build is higher,
and opens a **pull request** labeled `needs-review`. A human always reviews
before it goes live; the same CI checks run on the change. Requires an
`OPENAI_API_KEY` repository secret.
