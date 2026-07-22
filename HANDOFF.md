# HANDOFF — SQLServerUpdates.com

Status as of 2026-07-22. Written for whoever continues this work.

## TL;DR

- **Migration WordPress → static site on GitHub Pages: DONE and LIVE** at
  https://sqlserverupdates.com (identical URLs and content, HTTPS).
- **Contribution workflow (data + generator + CI): DONE.**
- **RSS auto-poller for new builds: BUILT, currently FAILING at the OpenAI step.**
  This is the single open item. It is almost certainly an `OPENAI_API_KEY`
  secret/config problem, not a code bug. Details below.

## What is live and working

The site is generated from data files by `build.py` (Python stdlib only, no
installs) and deployed to GitHub Pages by `.github/workflows/deploy.yml` on push
to `main`. Custom domain is set via the `CNAME` emitted into `_site/`.

- `data/updates/<slug>.csv` — one CSV per SQL Server version (the update tables).
- `data/versions.json` — site metadata, nav, and the home-page roll-up table.
- `data/fragments/*.html` — intro/footnote prose per page.
- `data/pages/*.html` — static pages (download, FAQ, contact, privacy).
- `reference/*.html` — frozen original HTML; `test_snapshot.py` diffs generated
  output against it (371 table rows verified identical). Runs in CI.
- Contribution flow: edit a CSV → open PR → CI (`validate.py` → `build.py` →
  `test_snapshot.py`) → merge → auto-deploy.

Local dev: `python3 validate.py && python3 build.py && python3 test_snapshot.py`.

## THE OPEN PROBLEM — RSS poller

Files: `poll_feed.py` and `.github/workflows/poll-feed.yml` (runs every 6h +
`workflow_dispatch`). Goal: read Microsoft's SQL Server blog RSS, extract new
builds with an LLM (OpenAI), and open a `needs-review` PR.

### Current symptom
Last manual run (id `29933353845`) **failed with exit code 1 after 43s**.
That exit code is deliberate — see "Diagnosis" step 3.

### Genuinely-missing builds (confirmed)
The site is behind the feed. As of now the feed has builds the site lacks:
- SQL Server 2025 **CU7** and **CU6** (site CSV top row is CU6 already? verify —
  `data/updates/sql-server-2025-updates.csv` top was CU6 `17.0.4055.5`; CU7 is new)
- SQL Server 2022 **CU26** (site top is CU25 `16.0.4255.1`)
- Plus recent GDR/security updates.

### Diagnosis (what has been PROVEN)
1. **Feed fetch works.** 20 items, 13 candidates on the last run.
2. **The original 403 problem is SOLVED.** Microsoft's techcommunity *article*
   pages block scripted requests (bot User-Agent → HTTP 403). The fix is already
   in `poll_feed.py`: it (a) pulls the KB link out of the RSS `<description>`,
   (b) reads the build number from the `learn.microsoft.com` /
   `support.microsoft.com` KB page (not blocked), and (c) sends a browser
   User-Agent. A probe run *on the actual GitHub runner* confirmed it now fetches
   the feed, the KB pages, AND the article pages successfully.
3. **The remaining failure is the OpenAI call.** Every candidate reaches the
   `llm_extract()` call and every call fails. `poll_feed.py` now surfaces this
   instead of silently going green: if it fetched the KB pages but every LLM call
   failed, it prints an error and `sys.exit(1)`. That is the exit-1 above.

### Most likely root cause
The `OPENAI_API_KEY` repository secret. One of:
- invalid / mistyped key,
- the key's project has **no access to model `gpt-4o-mini`** (very common with a
  fresh *restricted* key),
- `insufficient_quota` — the project has no credit or a $0 budget.

### How to confirm the EXACT cause (trivial with normal `gh`/API access)
Read the failing run's log:
```
gh run view 29933353845 --log | grep -iE "llm failed|Summary|candidates"
```
The `skip (llm failed): <title>: <error>` lines show the exact HTTP status
(401 invalid key / 404 model_not_found / 429 insufficient_quota).

Or test the key directly (no secret in the output):
```
curl -s https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_KEY" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'
```

### Fix by cause
- **invalid key** → `gh secret set OPENAI_API_KEY`
- **no model access** → grant the OpenAI project access to `gpt-4o-mini`, OR set
  the model to one the project can use. The workflow/script reads env
  `OPENAI_MODEL` (default `gpt-4o-mini`) — add it to the poll step, or change the
  default in `poll_feed.py`.
- **quota** → add credit / raise the project budget.

Then trigger and verify:
```
gh workflow run poll-feed.yml
```
Expect a PR titled "New SQL Server build(s) from Microsoft RSS".

### IMPORTANT untested prerequisite
For the bot to open PRs, Actions must be allowed to create them:
```
gh api -X PUT repos/BrentOzarULTD/SQLServerUpdates.com/actions/permissions/workflow \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true
```
This has **never been exercised** because the run never got past the OpenAI step.
After fixing OpenAI, if the "Open pull request" step fails with a permissions
error, run the command above (needs repo admin) and re-run.

## poll_feed.py internals (for whoever continues)

Stdlib only; OpenAI called over `urllib`. Key pieces:
- `VERSION_TO_SLUG` — versions tracked (2008–2025) → CSV slug.
- `is_candidate(title)` — cheap title filter (CU/GDR/SP for a tracked version,
  excludes SSMS/drivers/feature packs).
- `extract_kb_links()` / `pick_kb()` — get the KB/release-notes URL from the RSS
  `<description>` (the build number is NOT in the description — it's on the KB
  page, which is why we fetch it).
- `http_get()` — browser UA, `Accept-Encoding: identity`, gzip fallback.
- `llm_extract()` — OpenAI chat completions, JSON mode; returns
  `{is_release, version, update_label, build, kb_url, release_date}`.
- `valid_build()` — enforces the product-version major per version (rejects file
  versions like `2025.170.x` and cross-version mistakes).
- `build_row()` — inserts a schema-correct top row per version, carrying the
  "Support Ends" column forward on the multi-column tables.
- Dedup is by existing build numbers in the CSV.
- `python3 poll_feed.py --selftest` runs 18 offline unit checks (all passing).

## Latent notes
- The home-page "Latest Update" pointer in `data/versions.json` is intentionally
  NOT auto-updated by the bot; each PR says to bump it manually if wanted.
- FAQ/Contact pages had broken WordPress `wp-content/uploads` image refs; removed.

## Why this session could not finish it (access notes)

This Cowork session was launched against the **local folder** via the device
bridge, NOT connected to the GitHub repo. Consequences observed:
- `git` clone/push over HTTPS with a token **works** from the cloud sandbox.
- The GitHub **REST API is gated** by an Anthropic proxy:
  `"GitHub access to this repository is not enabled for this session. Use
  add_repo to request access."` So reading Actions logs and managing PRs via the
  API is blocked from here. Direct browser/curl to github.com are gated too.
- No GitHub connector is installed in the org (so no MCP GitHub tools either).
- A normal local `gh` (Codex on your machine) has full access and can just run
  the `gh run view ... --log` command above to see the exact OpenAI error in
  seconds. Alternatively, connect a GitHub connector in claude.ai settings, or
  start the Cowork session connected to the repo rather than the folder.

## Commits made on `main` by this session
- Convert site to static site generator (data-driven, GitHub Pages)
- Remove broken image references from FAQ and Contact pages
- Add RSS-polling bot for new builds
- Fix poller: read builds from KB pages, not blocked article pages
- Harden poller: gzip handling + surface LLM/API failures instead of silent skip

The immediate next action: read the log of run `29933353845` (one `gh` command
above), see the exact OpenAI error, fix the key/model/quota, re-run. That's the
whole remaining task.
