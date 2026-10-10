# Equibase Race Chart Scraper

Conservative collection of authorized official Equibase chart PDFs, layout-aware
race extraction, and private Supabase persistence. Python 3.12+.

**Version 0.1.0: Version 1 implementation with limited live validation.** An official
Churchill Downs chart and discovery dry run were verified locally. Live Supabase
persistence and broader chart formats remain unverified, so **1.0.0 is not released**.
Unattended production scraping remains disabled by default.

## Features and architecture

- Discover track codes, names, countries, dates, and race links from actual HTML navigation.
- Single-card, recent-track, and all-current-index-track modes; no historical enumeration.
- Sequential paced HTTP, shared request budget, bounded retries, and access-block stopping.
- Verify PDF signatures/structure; hash bytes, conditionally revalidate, and write atomically.
- Extract supported race conditions and primary horse results by PDF columns and baselines.
  Preserve raw text, warnings, null values, timing, and footnotes.
- Archive originals privately using content-hashed paths. Save each race and its related
  entries/fractions in one PostgreSQL transaction; preserve valid snapshots after failures.
- JSON logs, truthful summaries, discovery-only dry runs, and offline parsing.
- GitHub Actions for testing and opt-in twice-daily collection.

```text
.github/workflows/          scrape.yml and tests.yml
database/migrations/        versioned transactional SQL
src/equibase_scraper/
  config.py                 validated environment settings
  models.py                 Pydantic boundary objects
  http.py                   pacing, retries, size and request limits
  discovery.py              HTML navigation and listing metadata
  downloader.py             verified PDFs, hashes, conditional requests, local files
  parser.py                 headers, primary results, trainers, timing, footnotes
  database.py               Supabase lookups and transactional RPCs
  storage.py                private immutable original PDFs
  pipeline.py               sequential orchestration and recovery
  main.py                   CLI and execution summaries
  logging_config.py         JSON logs without SDK credential output
tests/                      synthetic PDFs/HTML and mocked integrations
.env.example                configuration template
AGENTS.md                   future development instructions
CHANGELOG.md                verified development history
pyproject.toml              packaging, dependencies, pytest and Ruff settings
```

Network operations are separate from parsing. Browser automation, proxies, AI parsing,
dashboards, betting analysis, and bulk backfilling are outside this release.

## Installation (Windows 11 / PowerShell)

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

If activation is restricted, invoke `.\.venv\Scripts\python.exe` directly instead.
Development dependencies include pytest, Ruff, YAML/SQL syntax validation, and actionlint.

## Environment configuration

Edit `.env` locally. Existing environment variables take precedence. GitHub Actions
uses repository variables/secrets, not a committed environment file.

| Variable | Default / purpose |
| --- | --- |
| `SUPABASE_URL` | HTTPS project URL; required for persistence |
| `SUPABASE_SECRET_KEY` | Server-side secret/service-role key; required for persistence |
| `STORAGE_BUCKET` | `race-charts`, must already exist and be private |
| `SCRAPER_ENABLED` | `false`; prevents remote scraping, including dry runs |
| `LOOKBACK_DAYS` | `7`, allowed 1–31; filters published index dates |
| `LOG_LEVEL` | `INFO` |
| `REQUEST_DELAY_SECONDS` | `2`, minimum 1 second between request starts |
| `REQUEST_TIMEOUT_SECONDS` | `30` per HTTP operation |
| `MAX_RETRIES` | `3` additional attempts; configurable maximum 5 |
| `MAX_CHARTS_PER_RUN` | `25` matched races attempted, including skips/dry-run actions |
| `MAX_REQUESTS_PER_RUN` | `150`, includes discovery, redirects, and retries |
| `MAX_RETRY_WAIT_SECONDS` | `300`; stop if the server requires a longer wait |
| `MAX_PDF_BYTES` | `20000000`, bounds decoded responses and offline PDFs |
| `USER_AGENT` | Identifiable `equibase-scraper/0.1.0` with repository contact URL |
| `CHART_INDEX_URL` | `https://tvg.equibase.com/static/chart/pdf/` |
| `DATA_DIR` | `data/charts`, ignored by Git |

Invalid settings fail at startup without printing input values. Dry runs need no Supabase
credentials. `SCRAPER_ENABLED=false` is a successful, logged no-op. Enable network access
only for an intentional authorized test or approved collection.

HTTP 401/403, visible access challenges, and 429 without `Retry-After` stop use of that host
for the run. Eligible 429/5xx/timeouts use bounded exponential backoff. Server waits are
never shortened. Only approved HTTPS Equibase hosts are followed.

## Supabase setup and migrations

1. Inspect the separately prepared database before applying
   `database/migrations/001_initial_schema.sql`. It intentionally fails if conflicting
   tables exist; it does not delete or adopt an unknown schema.
2. Apply the migration **once** in Supabase SQL Editor to the intended project. It is
   transactional. Resolve prepared-schema differences manually with a new numbered
   migration; never edit an already deployed migration.
3. Confirm `race-charts` exists and is **private**. Optional MIME restrictions should allow
   `application/pdf`; the bucket size limit must accommodate your configured PDF limit.
   The application neither creates buckets nor makes them public.
4. Set `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and `STORAGE_BUCKET`. Use a backend
   secret/service-role key, never a public/publishable or frontend key.

The tables are `tracks`, `races`, `race_entries`, `charts`, and `race_fractions`, with UUID
keys, foreign keys, date indexes, and unique race/program/fraction constraints. Horse names
are not globally unique; no reliable stable horse identifier appears in the tested PDF.

RLS is enabled. Anonymous/authenticated roles receive no table/RPC privileges and no access
policies. `service_role` needs table read/write, execution of `save_chart(jsonb)` and
`record_chart_attempt(jsonb)`, and private bucket inspection/upload/download/object checks.
This backend credential has broad project privileges; keep it confined to local backend
configuration and GitHub Actions secrets.

### Idempotency and recovery

- Race identity is track code, date, and race number. Originals use paths such as
  `2026/09/27/CD/race-1-<full-sha256>.pdf`. Changed content creates a new immutable version;
  old versions are retained. Unchanged content reuses the existing object.
- ETag/Last-Modified revalidation avoids duplicate PDF bodies where supported. Without
  validators the source must be received again to detect changes; a matching known valid
  hash still skips parsing, upload, and structured replacement.
- Skip checks confirm the stored original exists; missing objects trigger repair.
- Storage succeeds before the save RPC. An interruption can leave an unreferenced object;
  the next run verifies/reuses it instead of overwriting it.
- The save RPC locks the race and replaces related results atomically. Failed transactions
  preserve the snapshot. Incomplete results and unknown finishes cannot replace it.
- Shorter primary results, lost fractions, or lost populated fields on matching finished
  entries require manual review. Missing optional race fields retain existing values.
- Active chart hashes, paths, and successful timestamps describe the last valid snapshot.
  `attempted_*`, status, warnings, and errors describe later attempts. Unsupported PDFs
  may be archived but are not reported as successfully parsed/saved.
- Individual download/parse failures can continue to the next race. Access restrictions,
  request-budget exhaustion, and persistence failures stop collection. Every recorded
  failure produces a nonzero CLI exit. If failure recording also fails during an outage,
  logs retain the original issue and the next run retries.

These paths are mocked in tests. Actual PostgreSQL transactions and private Storage
operations remain subject to the manual integration validation below.

## Local execution

Check help and disabled behavior:

```powershell
python -m equibase_scraper.main --help
python -m equibase_scraper.main --dry-run
```

### One-race discovery test: no persistence

The sample listing was verified on October 10, 2026. It is absent from the current recent
index, so supply its published listing URL:

```powershell
$env:SCRAPER_ENABLED = "true"
$env:MAX_CHARTS_PER_RUN = "1"
python -m equibase_scraper.main --track CD --date 2026-09-27 --race 1 `
  --listing-url "https://tvg.equibase.com/static/chart/pdf/CD092726USA.html" --dry-run
$env:SCRAPER_ENABLED = "false"
```

Only the listing is fetched: no PDF, local file, upload, database lookup, or write.
Explicit remote cards must be within the last 31 days, including today. Once this sample
ages out, use a currently published card/URL; offline parsing has no age restriction.

### Single-card persistent test

After Supabase setup and approval for the limited test, enable scraping **locally** and
run the same command **without `--dry-run`**. Keep `--race 1` and a one-chart cap for the
initial persistence check. Remove `--race` to process the specified card subject to caps.
Cards linked in the current index need no `--listing-url`.

### Recent and all-track discovery

```powershell
# Requires intentional SCRAPER_ENABLED=true; discovers only.
python -m equibase_scraper.main --track KEE --dry-run
python -m equibase_scraper.main --all-tracks --dry-run
```

`LOOKBACK_DAYS` filters dates actually present in the Full Charts index, using today's
America/New_York date. It does not generate URLs or open historical calendars. The default
without `--track` is all currently listed tracks. Removing `--dry-run` requires Supabase
and approval for collection beyond the initial test. `--date` requires `--track`, `--race`
requires a date, and `--listing-url` requires track/date.

### Offline PDF parsing

No source or Supabase requests occur:

```powershell
python -m equibase_scraper.main --parse-file "data\charts\your-authorized-race.pdf" `
  --track CD --date 2026-09-27 --race 1
```

Use an actual local file and matching source identity. Output is validated JSON containing
raw text, warnings, and `complete`. Incomplete/unreadable parsing exits nonzero. This mode
performs no upload or database persistence.

### Summaries

Start/finish logs and summaries report mode, tracks, distinct track/date combinations,
matched charts discovered, PDF bodies downloaded, complete parses, confirmed saves,
unchanged skips, failures, source requests, runtime, and caps. A listing may expose more
races than the remaining processing allowance. An intentional chart cap is reported;
request-budget exhaustion is a failure. Failed uploads or transactions never count as saves.

## GitHub Actions configuration

Add repository **secrets** under Settings → Secrets and variables → Actions:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

Add ordinary settings as repository **variables** using `.env.example` names.
Keep **`SCRAPER_ENABLED=false`** until unattended collection is explicitly approved.
Absent variables retain safe defaults. Customize `USER_AGENT` with an appropriate contact.

`scrape.yml` uses Ubuntu/Python 3.12, read-only token permissions, a 30-minute timeout,
and shared concurrency without cancelling an in-flight run. Its schedule is `17 6,18 * * *`
with timezone `America/New_York` (approximately 6:17 AM/PM Eastern, including DST). GitHub
can delay scheduled runs. They execute on the repository's **default branch**; confirm
the intended stable `main` branch in repository settings.

Manual runs require **track, date, and race**, and **dry-run defaults to true**. The workflow
validates inputs before launching the CLI: a 1–6-character alphanumeric track code, a real
calendar date in `YYYY-MM-DD` format, race 1–99, and a true/false dry-run value. Track codes
are uppercased and surrounding whitespace is trimmed. Empty or invalid targets fail the
job without invoking the scraper; manual runs never fall back to all-tracks discovery.
The existing optional listing URL must be an approved HTTPS Equibase card listing matching
the requested track/date. Scheduled runs ignore manual inputs and invoke the CLI without
targeting arguments, retaining normal recent-date discovery and configured limits.

For a controlled manual test in the GitHub Actions interface:

1. Select **Collect race charts → Run workflow** and the reviewed branch containing this workflow.
2. Enter a currently available track code, date, and race number; keep **dry_run checked**.
3. If that card is absent from the recent index, supply its published listing URL. The CLI's
   existing last-31-days restriction for explicit remote cards still applies.
4. Run the workflow and inspect the summary: `dry-run:single-card`, only the selected race
   reported as an intended action, and zero downloads/uploads/database writes.

`SCRAPER_ENABLED` is never automatically enabled, even when dry-run is unchecked. With
`SCRAPER_ENABLED=false`, a valid manual test is a logged no-op. Network discovery requires
that setting to be explicitly enabled under the project's approval rules; enabling the
repository variable also permits scheduled collection, so coordinate that change with
approval for unattended runs. Unchecking dry-run additionally requires Supabase setup and
approval for persistent collection. All existing pacing, request/chart caps, secrets,
logging, timeout, and concurrency settings apply to both execution modes.

Disabled scheduled runs produce no-op summaries. PDFs are neither committed nor uploaded
as Actions artifacts. Runner files are ephemeral; persistent originals belong in private
Supabase Storage.

`tests.yml` runs on push/PR/manual invocation for Python 3.12/3.13, without secrets and with
scraping disabled. It runs pytest, Ruff, and actionlint. YAML and actionlint validation
passed locally; hosted workflows have not been triggered.

## Testing and verified scope

```powershell
python -m pytest
ruff check .
actionlint -shellcheck="" -pyflakes=""
python -m pip check
```

Tests generate original synthetic PDFs in memory and mock HTTP/Supabase. An autouse guard
rejects real httpx transports. No official PDFs, credentials, or collected-data dumps are
committed. PostgreSQL's parser checks SQL and PL/pgSQL syntax, not live transactional behavior.

Limited live validation on **2026-10-10**:

- Inspected the Full Charts index and Churchill Downs 2026-09-27 listing with ten races.
- Both tested individual/full-card viewer links returned 404. Literal mobile PDF navigation
  targets were used without generating URLs.
- Downloaded/structurally verified **only race 1**, a one-page official PDF. Matched all nine
  primary rows against text/layout: finishes vs. raised margins, names, jockeys, program-keyed
  trainers, posts, odds, and weights. Race metadata, three ordered fractions, final time,
  and footnotes were checked.
- The CLI single-race live dry run used one listing request and zero persistence operations.
  A compressed-response double-decoding bug found during validation was fixed and tested.
- The sample PDF remains only under ignored `data/charts/`. No Supabase connection or
  production collection was attempted.

### Version 1 acceptance status

| Criterion | Evidence / status |
| --- | --- |
| Specified card discovery and official PDF retrieval | Verified for the sample |
| Required race/horse fields for supported format | Verified sample and synthetic edge tests |
| Private Supabase uploads | Implemented/mocked; **live pending** |
| PostgreSQL structured persistence | SQL/PLpgSQL syntax and SDK mocks pass; **live pending** |
| Duplicate/atomic failure protection | Implemented/mocked; **real DB rerun/rollback pending** |
| Failed downloads/parses and conservative limits | Offline automated tests |
| Recent/all-track discovery | Implemented/index fixtures; **broader live coverage pending** |
| Local dry run | Disabled and enabled limited-live behavior verified |
| Actions and disabled production defaults | YAML/actionlint pass; **hosted execution pending** |
| Tests/docs/no tracked secrets or PDFs | Locally checked; no commit or push performed |

### Remaining manual integration validation

1. Review/apply the migration to your prepared schema and confirm private bucket access.
2. Configure local backend credentials; approve/run the one-race persistent command.
3. Inspect its PDF object, nine primary entries, fractions, race fields, parsed status,
   active hash, and successful timestamp.
4. Rerun exactly that race. Confirm one race/chart, unchanged entry/fraction counts, one
   object for its hash, and a skipped outcome. Servers without validators may send the PDF again.
5. Validate rollback/interruption recovery in your development Supabase project, followed
   by representative charts from additional tracks/formats.
6. After review/approval, commit/push intended changes and run hosted tests/manual dry run.
   Enable unattended collection only after explicit approval. Release 1.0.0 after remaining
   acceptance items pass.

## Current limitations and future development

- Only the observed text-based format has live PDF validation. Image-only, rotated,
  ambiguous, and boundary-less results are rejected or incomplete. Unverified page-break
  continuations cannot be persisted.
- Multi-race complete-card PDFs are **rejected by distinct race-header detection**;
  they are never labeled as a single race. Individual published PDFs are the supported path.
- Missing optional values remain null with warnings. Trainers come only from program-keyed
  mappings; no winner-trainer fallback is used.
- Explicit Fin-column DH/SCR/DQ/DNF markers have synthetic coverage. Disqualification
  narratives require manual official-order review rather than an assigned order.
- Fraction distance labels, full points of call, payouts, and stable horse IDs are not
  extracted. Fraction labels are ordinal (`fraction-1`, etc.).
- The index exposes available recent tracks/dates, not every historical/inactive track.
  Explicit listing URLs cover a recent card absent from it. Only the sample listing format
  has live validation; unsupported variants fail clearly.
- Small caps can leave cards unprocessed. Repeated capped all-track runs do not promise
  fair coverage of every available card. Use approved track-specific runs and review summaries.
- Immutable old versions and unreferenced interruption objects are retained. Automatic
  cleanup, historical backfill, and distributed scheduling are outside Version 1.

Next work is live Supabase/hosted verification and tested additional chart formats. Later
historical discovery can reuse card/race interfaces after separate approval. Increment
`PARSER_VERSION` when changing deployed extraction rules to reprocess unchanged source bytes.
Develop on local `dev`; `main` is stable/default. See `AGENTS.md` for development expectations.
