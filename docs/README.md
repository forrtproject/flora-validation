# FLoRA Validation

FLoRA Validation is the human-review and publication layer for the
[FORRT FLoRA database](https://forrt.org/replication-hub/flora/). It receives
replication/reproduction pairs from the separate `flora-extractor` project, asks
two validators to review each extraction, uses Gemini as a sanity check or
tiebreaker, gives administrators the final publication decision, and exports the
approved records as CSV.

This repository also contains a second, independent pipeline for importing and
reviewing FLoRA's published Google entry sheets. Those rows live in
`source_records`, are edited through a separate admin screen, and are transformed
into `output/flora_entry_sheets.csv`.

This document describes the implementation in the checked-out `main` revision,
not the intended design in old planning documents. Where the current code and an
older document disagree, the Python, JavaScript, and `db_schema.sql` behavior
described here is the source of truth.

> [!CAUTION]
> The current authentication model is suitable only for a controlled deployment.
> Validator endpoints trust a client-supplied `coder_id`. Admin passwords are
> stored as plaintext, admin tokens are deterministic password hashes, and the
> fallback password is `flora-admin-2025` when `ADMIN_PASSWORD` is absent. Set a
> unique production password before first startup and read
> [Security and current limitations](#security-and-current-limitations) before
> exposing the service publicly.

## Contents

- [What belongs in this repository](#what-belongs-in-this-repository)
- [System architecture](#system-architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Application startup](#application-startup)
- [Extractor CSV import pipeline](#extractor-csv-import-pipeline)
- [Human validation workflow](#human-validation-workflow)
- [Consensus and Gemini validation](#consensus-and-gemini-validation)
- [Administrator workflow](#administrator-workflow)
- [Source Records pipeline](#source-records-pipeline)
- [Database reference](#database-reference)
- [API reference](#api-reference)
- [Scheduled work and exports](#scheduled-work-and-exports)
- [Maintenance commands](#maintenance-commands)
- [Repository file map](#repository-file-map)
- [Testing and verification](#testing-and-verification)
- [Deployment notes](#deployment-notes)
- [Security and current limitations](#security-and-current-limitations)
- [Troubleshooting](#troubleshooting)

## What belongs in this repository

This is the validation application, not the academic-paper discovery/extraction
engine.

The upstream `forrtproject/flora-extractor` repository is responsible for:

1. discovering candidate papers;
2. classifying papers as replications, reproductions, or false positives;
3. linking each replication/reproduction to an original study;
4. extracting outcome labels, evidence, identifiers, and provenance; and
5. writing `data/extracted.csv`.

This repository is responsible for:

1. downloading or accepting that CSV;
2. importing eligible rows into PostgreSQL;
3. serving the validator and administrator interfaces;
4. collecting two human judgements per record;
5. running Gemini sanity checks and tiebreaks;
6. storing approved rows in `validated`;
7. exporting the validated data;
8. synchronizing the separate FLoRA entry sheets into `source_records`; and
9. reviewing, deduplicating, and transforming those entry-sheet rows.

The two input pipelines share PostgreSQL and the admin frontend, but they do not
merge their records in the application:

```text
flora-extractor/data/extracted.csv
        |
        v
sync_csv.py -> csv_to_db.py
        |
        v
unvalidated + record_metadata + validation_queue
        |
        v
two humans -> consensus_engine.py -> Gemini/admin
        |
        v
validated -> export_validated.py -> data/validated_export.csv


published Google entry sheets
        |
        v
sources.yml -> sync_sources.py -> source_records
        |
        v
admin Source Records review + duplicate decisions
        |
        v
transform_sources.py -> output/flora_entry_sheets.csv
```

## System architecture

| Layer | Implementation |
| --- | --- |
| HTTP server | FastAPI in `app.py` |
| Production process | Uvicorn, configured by `Procfile` |
| Database | PostgreSQL via synchronous `psycopg2` connections |
| Schema/migrations | Idempotent SQL in `db_schema.sql`; legacy copier in `db_migrate.py` |
| Frontend | One static HTML page, plain JavaScript, and CSS in `docs/` |
| LLM | Google Gemini through `google-genai` |
| In-process schedules | APScheduler |
| Repository schedules | GitHub Actions |
| Data processing | pandas, PyYAML, Python standard library |
| Email | Resend, used only for validator handle reminders |
| Tests | pytest with mocked cursors, HTTP, and Gemini calls |

There is no frontend build step. `docs/index.html` loads `docs/style.css` and
`docs/app.js` directly. FastAPI mounts `docs/` at `/` after registering all API
routes. The same frontend can fall back to a static/localStorage demo when its
probe to `./api/leaderboard` fails.

The app uses one short-lived PostgreSQL connection per `db()` context. Each
context commits on success, rolls back on any exception, and closes the connection.
There is no ORM and no persistent connection pool in this repository.

## Quick start

### Prerequisites

- Python 3.12 is the declared runtime (`runtime.txt`).
- A PostgreSQL database. Supabase is the documented hosted option, but ordinary
  PostgreSQL-compatible providers work because the app uses `psycopg2` directly.
- A Gemini API key for consensus calls.
- Optional Resend credentials for handle-reminder email.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`, at minimum setting `DATABASE_URL`, `GEMINI_API_KEY`, and a strong
`ADMIN_PASSWORD`, then start the app:

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`.

### macOS or Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

### Important first-start behavior

Importing `app.py` is not read-only. Before Uvicorn begins serving requests, it:

1. requires `DATABASE_URL`;
2. executes all of `db_schema.sql`;
3. checks whether `unvalidated` is empty;
4. invokes `csv_to_db.py` on `data/extracted_latest.csv` if it is empty;
5. creates the initial `admin` account if `admins` is empty; and
6. starts four APScheduler jobs.

Use a disposable database for development unless you intend those actions to run.

## Configuration

`python-dotenv` loads `.env` from the repository root. `.env` is ignored by Git.

| Variable | Required | Default in code | Used by |
| --- | --- | --- | --- |
| `DATABASE_URL` | Yes | none | App, schema initialization, import, migration, sync, transform, export, and maintenance scripts |
| `GEMINI_API_KEY` | Required when consensus invokes Gemini | none | `llm_validator.py` |
| `ADMIN_PASSWORD` | Strongly required in production | `flora-admin-2025` | Seeds the first trusted `admin` account |
| `RESEND_API_KEY` | No | empty | Enables `/api/forgot-handle`; without it the endpoint returns 503 |
| `EMAIL_FROM` | No | `Flora Validator <noreply@forrt.org>` | Sender for handle-reminder email |
| `GITHUB_TOKEN` | No for a public source repository | empty | Authorization header for nightly extractor CSV download |
| `GITHUB_REPO` | No | `forrtproject/flora-extractor` | Extractor source repository |
| `GITHUB_BRANCH` | No | `main` | Extractor source branch |
| `OPENALEX_MAILTO` | No | maintainer email embedded in code | OpenAlex work-ID backfill polite-pool contact |
| `PORT` | Provided by many hosts | none | Expanded by the `Procfile`, not read in Python |

The checked-in `.env.example` explicitly sets `GITHUB_BRANCH=feature/extract`,
while `sync_csv.py` defaults to `main` when the variable is absent. Choose the
branch intentionally; deleting the setting changes the downloaded contract.

`export_validated.py` and `fetch_oa.py` contain a maintainer contact directly in
the source for OpenAlex/Unpaywall requests. Only `backfill_oa_work_ids.py` exposes
that contact through `OPENALEX_MAILTO`.

Do not commit `.env`, database passwords, Gemini keys, GitHub tokens, or Resend
keys. The repository does not provide a secret manager.

## Application startup

`app.py` performs startup work at module scope rather than through a FastAPI
lifespan hook:

```text
load .env
  -> read DATABASE_URL and optional settings
  -> construct FastAPI app
  -> execute db_schema.sql
  -> bootstrap extractor CSV if unvalidated is empty
  -> seed first admin if admins is empty
  -> start APScheduler
  -> mount docs/ at /
```

The schema is designed to be re-executable: it uses `IF NOT EXISTS`, guarded
`DO` blocks, upserts, and repeatable data updates. It is still real migration
work and can lock or rewrite rows during startup.

The CSV bootstrap uses `subprocess.run(..., check=False)`. A failed import does
not stop startup, so an empty deployment can come online with zero records. Check
the process logs and query `unvalidated` after a fresh deployment.

The background scheduler is created in every process that imports `app.py`.
Running `--reload` can restart it; running multiple Uvicorn workers creates one
scheduler per worker. This matters for network calls and recurring database work.
Use one app worker unless schedules are moved into a dedicated worker or protected
with distributed locks.

The public health endpoint is `GET /api/health`. It deliberately performs no
database query, so it proves only that the Python process can answer HTTP.

## Extractor CSV import pipeline

### Download

`sync_csv.py` builds this URL:

```text
https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/data/extracted.csv
```

It sends `Authorization: token ...` only when `GITHUB_TOKEN` is set and uses a
60-second request timeout. A successful download is written to both:

- `data/extracted_DD.MM.YYYY.csv`; and
- `data/extracted_latest.csv`.

It then calls `csv_to_db.run_import()` with `extracted_latest.csv`.

The current implementation writes both files before validating/importing the
payload. If import fails, the bad payload remains `extracted_latest.csv`. The
function catches the exception, prints a traceback, and does not re-raise it.
Operators must inspect logs; a normal process exit does not prove a successful
nightly import.

### Eligibility rules

`csv_to_db.py` loads the entire CSV with `dtype=str`, UTF-8 BOM handling, and
blank-cell replacement. A row is eligible only when both conditions hold:

- `paper_type` (or legacy `filter_status`) is `replication` or `reproduction`;
- `link_method` is one of:

  - `author_year_match`
  - `llm_abstract`
  - `llm_fulltext`
  - `single_candidate_after_requery`
  - `title_pattern_match`
  - `citation_context_match`
  - `same_author_year_title_overlap`

False positives, `no_original_found`, unresolved, pending, and API-error rows are
reported in summary counts but not imported.

The importer has no complete required-header validation. It explicitly requires a
paper-type column and directly indexes `link_method`; other missing columns become
empty because most values are accessed with `row.get(...)`. Use `--dry-run` on a
new extractor contract before writing to production.

### Imported records

Each newly accepted row creates:

- one `unvalidated` row with a new UUID `record_id`;
- one `record_metadata` row linked by that UUID; and
- three `validation_queue` rows: `human_1`, `human_2`, and `llm`.

The `llm` queue slot is created for schema symmetry but the current consensus
code stores Gemini's result in `unvalidated.llm_validator`; it does not complete
the `llm` queue row.

The main field mapping is:

| CSV | Database destination |
| --- | --- |
| `pair_id` | `unvalidated.pair_id`, `record_metadata.pair_id` |
| `doi_r` | `unvalidated.doi_r` |
| `title_r` | `unvalidated.study_r` |
| `year_r`, `url_r`, `ref_r`, `abstract_r` | corresponding `unvalidated` columns |
| `doi_o` | `unvalidated.doi_o` |
| `title_o` | `unvalidated.study_o` |
| `year_o`, `ref_o` | corresponding `unvalidated` columns |
| `url_o` | preferred for `unvalidated.url_o`; otherwise derived as `https://doi.org/{doi_o}` |
| `oa_work_id_o` or `openalex_id_o` | bare `W...` in `unvalidated.oa_work_id_o` |
| `oa_work_id_r` or `openalex_id_r` | bare `W...` in `unvalidated.oa_work_id_r` |
| `type` | `unvalidated.type` |
| `outcome` | `unvalidated.outcome`; exact `success`/`failure` become `successful`/`failed` |
| `outcome_phrase` | `unvalidated.outcome_quote` |
| `out_quote_source` | `unvalidated.out_quote_source` |
| filter/link/provenance/bibliography fields | `record_metadata` |

Metadata currently stores filter status/method/evidence/confidence, original-match
type/confidence, DOI verification, link method/evidence/confidence/model, outcome
confidence, both author strings, replication journal/OpenAlex/source, and
`original_rank`/`n_originals`.

### DOI-less originals

An original may legitimately have no DOI. In that case the importer keeps
`doi_o` as an empty string, preserves an extractor-provided `url_o` (usually an
OpenAlex URL), and stores a bare OpenAlex ID when available.

Before import, `_flag_ambiguous_doi_o_titles()` groups DOI-less originals by
replication DOI/title. A blank original title, or the same normalized original
title appearing more than once for the same replication, is appended to
`unvalidated.admin_notes` for manual review. The row is still imported.

### Idempotency and updates

`pair_id` is the import identity. Existing `pair_id` values are loaded before the
loop and skipped. `INSERT ... ON CONFLICT (pair_id) DO NOTHING` provides a second
guard. The whole non-dry import runs in one transaction: an uncaught error rolls
back all rows from that run.

Re-running the importer does not refresh an existing pair. It does not update
titles, study numbers, outcomes, OpenAlex IDs, reproduction axes, or metadata for
previously imported rows. Use the narrow maintenance scripts where appropriate,
or implement a reviewed upsert policy before treating recurring imports as a
full synchronization.

### Current extractor-contract mismatch

The checked-in `data/extracted_latest.csv` contains newer fields that this `main`
importer does not carry end to end:

| Newer extractor information | Current `main` behavior |
| --- | --- |
| `study_r`, `study_o` study numbers | Ignored; `title_r` and `title_o` are written into database `study_r`/`study_o` |
| Independent reproduction outcome axes and their quotes/sources | Ignored by the importer; the validation schema stores one joined `outcome` and one quote/source |
| `pdf_source`, `parse_method` | Ignored; no matching `record_metadata` columns |
| Extractor lineage such as `work_id`/`release_id` if supplied | No destination columns exist in `record_metadata` |
| Corrected fields for an existing `pair_id` | Skipped rather than backfilled |

This table documents the code currently on `main`; it is not a claim that the
newer extractor fields are unnecessary.

### Outcome vocabulary enforced by the validation schema

Replication outcomes accepted by `unvalidated.outcome` are:

- `successful`
- `failed`
- `mixed`
- `uninformative`
- `descriptive`
- `cannot_be_determined`

The five accepted joined reproduction labels are:

- `computationally successful, robust`
- `computationally successful, robustness challenges`
- `computation not checked, robust`
- `computation not checked, robustness challenges`
- `computational issues, robustness challenges`

Blank `outcome` values are allowed as `NULL`, but the current importer turns blank
CSV cells into an empty string. PostgreSQL rejects an empty string against this
check constraint. A single rejected insert rolls back that import transaction.

## Human validation workflow

### Validator identity and onboarding

Validators log in with a handle plus either an email address or personal code.
The first login inserts a `validators` row; later logins require the same handle
for that email/code. Email ownership and personal-code ownership are not verified.
The API returns the numeric `coder_id`, which the browser stores and sends on
later requests.

New validators complete the curated examples in root `onboarding.json` (served by
`GET /api/onboarding`). Returning validators can be shown release notes from
`docs/updates.json` when `last_seen_update` is behind
`CURRENT_UPDATE_VERSION` in `app.py`.

Validator tiers are:

| Tier | Meaning in current code |
| --- | --- |
| 0 | Regular validator |
| 1 | Trusted label; included in admin quality counts |
| 2 | Senior; may fast-reject and can auto-validate an agreed record |

`vote_score` defaults to 10 and is the base for points. Tier changes do not
automatically change `vote_score`.

### Record serving

The browser prefetches up to three pairs through `GET /api/next-pairs`:

- one started pair, with a five-day lock;
- remaining pairs as buffered claims, with a 45-minute lock;
- at most five pairs can be requested in one call.

The claim update uses `FOR UPDATE SKIP LOCKED` on the queue slot. A validator
cannot receive a record already associated with their `validator_id`, and only
records with a free human slot are candidates.

Normal mode excludes records with no abstract or outcome
`cannot_be_determined`. Hard mode contains those records and awards double
backend points. Restricted-access records are removed from both pools until an
administrator assigns them.

Admins can enable priority serving for `failed`, `successful`, or `mixed`
outcomes and optionally a four-digit year range. `priority_share` controls the
probability of selecting the priority subset; the selector falls back to the
other subset and then the whole pool so the rule cannot empty the queue.

### Three review gates

The live UI asks:

1. Is the extracted type correct (`replication`, `reproduction`, or neither)?
2. Is the linked original paper correct (`correct`, wrong paper, or can't tell)?
3. Is the outcome correct (`looks right`, mischaracterised, or can't tell)?

Validators can also suggest an original DOI/title, edit the replication title,
suggest a replication URL or published DOI, edit the abstract, edit/extend the
outcome quote, choose a corrected outcome, and add notes.

“Can't tell” is stored as `incorrect` in the three check columns and preserved as
`was_unsure_original` or `was_unsure_outcome` in `additional_checks`. The consensus
engine reads those flags and sends the record to `need_review`.

For normal-mode abstract quotes, the frontend uses a fuzzy quote-in-abstract gate.
If the effective quote is not found, it adds `quote_not_in_abstract`; consensus
then requires admin review. The check is skipped for declared full-text quotes,
hard mode, assignments, not-a-validation decisions, and missing quote/abstract.

### Reproduction outcome UI

When the effective type is reproduction, the frontend replaces the replication
outcome choices with two three-option axes:

- computation: successful, issues, not checked;
- robustness: robust, challenges, not checked.

JavaScript immediately joins those selections into one `corrected_outcome` string.
There are no independent axis columns in `JudgeRequest`, `validation_queue`, or
`validated` on current `main`. The UI can generate nine combinations, while the
validation table's check constraint accepts only five joined combinations listed
above. This is a current schema/UI mismatch, not an alternate supported contract.

### Points

The live backend calculates a normal submission as:

```text
validator.vote_score
+ 2 when original_check == "correct"
+ 2 when outcome_check == "correct"
+ 1 when nonblank validator_notes are supplied
```

Hard-pool submissions multiply that total by two. Assigned restricted records
also multiply the normal total by two. Senior fast-reject awards only the base
`vote_score`. Skipping and reporting inaccessible content award no points.

Some frontend labels and static-demo scoring constants do not match the live
backend. In particular, the note UI says `+3 pts`, but `_points_for()` adds one.
The backend response and database totals are authoritative in online mode.

### Restricted access and assignments

In hard mode a validator can report that the article is inaccessible. The backend:

1. sets `unvalidated.restricted_access` and reporter metadata;
2. releases that validator's unfinished human slot; and
3. removes the record from ordinary serving.

An admin can assign or reassign the record through `assignments`. The assignee
submits one judgement through `/api/assignment-judge`; that judgement directly
sets `consensus_reached` (or `rejected` for `not_validation`), closes the
assignment, clears restricted access, and awards double points. It still awaits
admin approval when accepted.

### History, messages, and static mode

“My Judgements” returns at most the latest 100 completed queue rows and can open
a detail view with raw extraction, final validated values, flags, and a linked
message thread. The current response does not include independent reproduction
axes because those fields do not exist in the validation schema.

Admins can send individual or broadcast messages. Flagging a judgement with a
reason creates a linked outbound message. Validators can reply once to an
outbound/root message; administrators can continue the thread.

If the initial leaderboard probe fails, the browser switches to static mode. It
loads `docs/pairs.json`, `docs/hard_pairs.json`, and `docs/onboarding.json`, then
stores users/judgements in localStorage. Static mode is a demonstration, not an
offline replica of PostgreSQL behavior: its routes, points, identity, and workflow
are simpler, and it has no real admin/consensus/export path.

## Consensus and Gemini validation

`evaluate_consensus(cur, record_id)` runs after every ordinary human submission.
It returns without action until both human slots are complete.

Before ordinary agreement logic, it checks three hard stops:

1. a `senior_reject` marker makes rejection authoritative;
2. either validator's “can't tell” flag sends the record to `need_review`;
3. either validator's quote gate flag sends the record to `need_review`.

Corrections are compared exactly for DOI/title/outcome/type/replication title/URL,
except:

- published replication DOIs are normalized before comparison; and
- corrected abstracts are compared after lowercasing and removing non-alphanumerics.

The implemented decision tree is:

| Human result | Gemini | Stored result |
| --- | --- | --- |
| Checks and corrections agree; both say `not_validation` | Sanity check confirms the same human checks | `rejected` |
| Same rejection, but Gemini errors/disagrees/is uncertain | Sanity check | `need_review` |
| Checks and corrections agree on a valid record; at least one submitted validator is senior | Sanity check is recorded but does not overrule humans | `validated` and inserted into `validated` |
| Same agreement without a senior | Sanity check is recorded but does not overrule humans | `consensus_reached`, awaiting admin approval |
| Checks agree but corrections differ | Not called | `need_review` |
| Checks differ; Gemini uniquely matches one non-rejecting human | Tiebreak | `consensus_reached` with that human as winner |
| Checks differ; Gemini supports a `not_validation` human | Tiebreak | `need_review` for an admin decision |
| Gemini errors, is uncertain on a disputed field, matches both/neither, or produces a three-way split | Tiebreak | `need_review`, with `is_tiebreaker` when applicable |

Final values use the winning human's corrections and raw extraction as fallback.
When either human edited an abstract or quote, the longest submitted text is
selected. The quote source is `abstract` when normalized quote text occurs inside
the final abstract, otherwise `full_text`; an existing source is kept when no new
quote was suggested.

### Gemini implementation

`llm_validator.py` currently uses `gemini-3.1-flash-lite` and structured JSON
output. Every check can be `correct`, `incorrect`, or `uncertain`. Unknown/missing
check strings become `uncertain`, never `correct`.

The model receives only the abstract and extracted metadata; the prompt tells it
not to use external knowledge. Replication and reproduction outcome vocabularies
are selected separately. Corrected outcomes are constrained by the prompt,
Gemini response schema, and server-side coercion. A small synonym map handles
common replication-label near misses; an unknown suggestion is dropped and the
outcome check becomes uncertain.

Gemini calls retry once. Persistent failures return an error object instead of
raising through the submission transaction. The nightly retry job revisits only
`need_review` tiebreakers whose stored Gemini object contains `error`; genuine
three-way disagreements are not repeatedly called.

## Administrator workflow

The admin interface is one screen with tabs for validation entries, Source
Records, validator statistics, dashboard metrics, priority serving,
restricted-access assignments, messaging, and admin accounts/site banner.

### Login and admin accounts

On an empty `admins` table, startup creates:

- handle: `admin`
- password: `ADMIN_PASSWORD`, or the unsafe fallback
- trusted: true

`POST /api/admin/login` compares plaintext passwords and returns
`sha256(password + ":flora-admin-v1")`. Every protected route expects that value
in `X-Admin-Token`. Trusted admins can create/delete admins and toggle trust.
An admin cannot delete their own account, change their own trust, or delete the
last admin.

### Validation entry states

`unvalidated.validation_status` can be:

| Status | Meaning |
| --- | --- |
| `unvalidated` | No active/completed human work |
| `validation_inprogress` | At least one queue slot is claimed or complete |
| `consensus_reached` | Automated consensus succeeded; ordinary records await admin approval |
| `need_review` | Uncertainty, conflicting corrections, quote flag, LLM ambiguity/error, or manual flag |
| `validated` | Accepted into the authoritative `validated` table |
| `rejected` | Confirmed not to belong in FLoRA |

The entries table supports filters, DOI/title search, safe whitelisted sorting,
agreement percentages, LLM-dissent markers, validator/tier counts, and pagination.
The detail view includes raw/final values, human/LLM summaries, queue rows,
validator history counts, flags, notes, quote-source controls, and correction
fields.

### Approve, review, reject, and resolve

- **Approve** accepts only `consensus_reached`, marks the row `validated`, and
  inserts/upserts the effective final values into `validated`.
- **Flag for review** moves a pending approval back to `need_review` and can save
  an admin note.
- **Resolve** can correct type, both paper identities/titles/links, abstract,
  outcome/quote/source, published DOI, and alternative identifiers. A final type
  of `not_validation` rejects and deletes any `validated` row for that record.
- **Senior fast-reject** is a validator action, but the admin view exposes the
  resulting rejection and allows an override through resolve.

Before inserting an approved/resolved row, code deletes the existing validated
row with the same `record_id`. The insert then uses the natural unique key
`(doi_r, study_r, doi_o, study_o)` with `ON CONFLICT DO UPDATE`.

That conflict handler can update an already-existing row belonging to a different
record when an admin resolves two records onto the same natural key. Current
`main` has no explicit validation-record merge table or duplicate-resolution
workflow for this path. Resolve duplicates deliberately and verify both
`record_id` values before using a colliding natural key.

### Admin metrics and communication

The dashboard reports pipeline statuses, validated outcomes, correction counts,
human agreement, extractor-to-final changes, validator activity, tiebreakers, and
admin overrides. Validator statistics include timing only for submissions between
10 seconds and 90 minutes after display.

Admins can flag/unflag an individual queue judgement. Supplying a flag reason
creates a linked validator message. Messaging supports per-validator messages,
broadcasts, threads, read state, and inbox badges. A public site banner is stored
as a single row and returned without admin authentication at `/api/banner`.

## Source Records pipeline

The Source Records subsystem is separate from human validation. Its detailed
design is also documented in [SOURCE_RECORDS.md](SOURCE_RECORDS.md), but the
runtime behavior is summarized here.

### Registry

`sources.yml` defines one published Google document with two tabs:

| Source key | Type | Display prefix | Validation column |
| --- | --- | --- | --- |
| `replications` | replication | `REPL` | `validation_status` |
| `reproductions` | reproduction | `REPRO` | `validation` (mapped to `validation_status`) |

Only these sheet values are accepted:

- `validated - chosen`
- `validated - changed`
- `validated - unchanged`

The registry lists expected headers, promoted columns, renames, UUID column,
Google `gid`, and source label. Unpromoted sheet columns remain recoverable in
the `raw` JSONB object.

### Sync integrity gates

`sync_sources.py` applies the following before inserts:

0. every configured promoted column must exist in the fixed database mapping;
1. download retries up to three times with a 30-second timeout;
2. payload must be nonempty CSV rather than an HTML sign-in page;
3. pandas must parse it;
4. all expected source headers must exist;
5. row count cannot fall below 50% of the last successful/unchanged run by default;
6. an unchanged SHA-256 payload is recorded and skipped.

Each source commits independently. Gate failure records a failed
`source_sync_runs` row and leaves existing source records untouched. A non-dry run
writes the source payload into `snapshots/{source}.csv`; GitHub Actions uploads
those snapshots as artifacts rather than committing them.

### Insert-only identity

Source sync is insert-only. Identity is `(source, sheet_row_id)`, where
`sheet_row_id` must be the static UUID in the sheet. A blank or malformed UUID is
skipped. If an accepted sheet contains the same UUID more than once, every row
with that duplicated UUID is skipped to avoid choosing one silently.

New rows receive sequential human-readable IDs such as `REPL-000001`. The
database trigger calculates a duplicate fingerprint from normalized original DOI
plus replication DOI (or replication URL). The fingerprint is a review signal,
not identity, and moves automatically when an admin edits an identifier.

Once a source row exists, later sheet changes do not update it. The database and
admin edit history become authoritative.

### Review service

`source_records_service.py` contains transport-independent SQL used by FastAPI.
The Source Records UI supports:

- pagination up to 200 rows per page;
- type, status, outcome-axis, reviewed/unreviewed, duplicate, and text filters;
- whitelisted sorting;
- CSV export of the full filtered set;
- dynamic vocabularies from stored values;
- full raw JSON and edit history;
- previous/next navigation inside the active filter;
- editable promoted fields;
- review stamps even when no value changed; and
- optimistic concurrency through an integer `version` plus `SELECT ... FOR UPDATE`.

A stale save receives HTTP 409 with current version/reviewer headers. Changed
fields are appended to `source_record_edits`; an unchanged save still increments
the version and stamps the reviewer.

### Source duplicate decisions

Duplicate groups are computed across both sheets. An admin can mark a row:

- `distinct`: keep it in transform output; or
- `duplicate`: exclude it and point `duplicate_of` at the surviving row.

The service rejects self-duplicates and refuses to point at a survivor already
marked as a duplicate. Marking A as a duplicate of B automatically marks an
unreviewed B as distinct. Database constraints require coherent status/pointer
combinations and prevent self-reference.

### Transform

`transform_sources.py` is read-only with respect to PostgreSQL. It:

1. derives one outcome (`outcome_alias` for replications and the two-dimensional
   `reproduction_outcome_map` for reproductions);
2. cleans DOI prefixes, case, whitespace, and known scraped suffixes;
3. applies `transform_exclusions` by replication DOI or URL;
4. removes `url_r` when it merely repeats the DOI resolver;
5. removes admin-confirmed duplicates and collapses remaining identifier
   duplicates, except rows marked distinct; and
6. writes the 14-column FLoRA projection.

Reproduction quote text and quote sources are joined with ` || ` so neither axis
is silently discarded. Unknown axis combinations produce a visible warning and
a blank derived outcome. `DUMMY_...` identifiers are stripped before output.

The output columns are:

```text
doi_o, ref_o, url_o,
doi_r, ref_r, url_r,
abstract_r,
outcome, outcome_quote, outcome_quote_source,
type, source,
alt_identifier_o, alt_identifier_r
```

## Database reference

`db_schema.sql` currently creates or evolves 17 application tables. It also
contains data migrations, indexes, helper functions, and triggers, so it should
be reviewed as executable migration history, not only fresh-install DDL.

### Validation tables

| Table | Purpose and important keys |
| --- | --- |
| `validators` | One row per validator; unique handle/email/code, tier, vote score, totals, onboarding/login/update/reminder state |
| `unvalidated` | One extractor pair per UUID; unique `pair_id`, raw paper fields, workflow flags, three JSONB summaries, `final_*`, admin/restriction/OpenAlex/published-ID fields |
| `validation_queue` | Unique `(record_id, validator_slot)` rows for `human_1`, `human_2`, `llm`; claims, checks, corrections, flags, notes, points, timestamps |
| `validated` | Authoritative accepted output; UUID PK, source `record_id`, effective paper/outcome fields, unique natural key `(doi_r, study_r, doi_o, study_o)` |
| `record_metadata` | One-to-one extractor provenance and bibliography linked to `unvalidated.record_id` |
| `assignments` | One restricted record assignment; unique `record_id`, assignee, assigner, open/done timestamps |

### Accounts, communication, and serving

| Table | Purpose |
| --- | --- |
| `admins` | Plaintext handle/password and trusted flag |
| `site_banner` | Singleton public banner row (`id = 1`) |
| `validator_messages` | Bidirectional messages, parent threads, queue link, validator/admin read state |
| `serving_config` | Singleton priority-serving rule (`id = 1`) |

### Source Records tables

| Table | Purpose |
| --- | --- |
| `source_records` | Insert-only sheet identity, promoted/raw fields, review/version state, duplicate decision |
| `source_record_edits` | Append-oriented changed-field and duplicate-decision audit rows |
| `source_sync_runs` | Per-source freshness, gate result, counts, and payload hash |
| `source_display_counters` | Last sequential number handed out per source |
| `transform_exclusions` | DOI/URL decisions omitted from transformed output |
| `outcome_alias` | Replication raw-to-canonical outcome lookup |
| `reproduction_outcome_map` | Computational × robustness to canonical outcome lookup |

### Database functions and triggers

| Object | Behavior |
| --- | --- |
| `clear_stale_oa_work_id()` / `trg_clear_stale_oa_work_id` | Clears validation-pipeline OpenAlex IDs when an effective DOI changes |
| `clear_stale_source_oa_work_id()` / `trg_clear_stale_source_oa_work_id` | Clears Source Records OpenAlex IDs after DOI edits |
| `source_norm_doi()` | Normalizes DOI text for a Source Records fingerprint |
| `source_norm_url()` | Normalizes URL text for a Source Records fingerprint |
| `source_content_fingerprint()` | Calculates duplicate-review fingerprint |
| `set_source_content_fingerprint()` / `trg_set_source_content_fingerprint` | Recalculates the fingerprint on insert or identifier edit |

`db_schema.sql` also normalizes historical `success`/`failure` values, migrates
old tier columns, seeds singleton/rule rows, backfills available OpenAlex IDs into
`validated`, renames an old display-counter column, and removes the edit-history
foreign key that formerly cascaded deletions.

## API reference

There are 59 FastAPI operations. All `/api/admin/...` routes except admin login
require `X-Admin-Token`. Normal validator routes rely on `coder_id` in a query or
request body rather than an authenticated session.

### Public and validator routes

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/login` | Register/login using handle plus email or code |
| GET | `/api/onboarding` | Return curated onboarding pairs |
| POST | `/api/onboarding/complete` | Stamp onboarding completion |
| POST | `/api/update-seen` | Record current update version |
| GET | `/api/my-judgements` | Latest 100 completed judgements for `coder_id` |
| GET | `/api/my-judgements/{queue_id}` | One judgement, final record, and message thread |
| GET | `/api/next-pairs` | Resume/claim active and buffered pairs; normal/hard mode |
| POST | `/api/pairs/{queue_id}/start` | Promote a buffered claim to started |
| GET | `/api/health` | Process-only liveness check |
| POST | `/api/restricted` | Report inaccessible hard-mode article and release slot |
| GET | `/api/my-assignments` | Open restricted assignments for a validator |
| GET | `/api/assignment/{record_id}` | Load one assigned record |
| POST | `/api/assignment-judge` | Resolve assigned record and award double points |
| POST | `/api/judge` | Submit ordinary judgement and run consensus |
| POST | `/api/skip` | Release a claimed slot and increment skips |
| POST | `/api/senior-reject` | Tier-2 immediate rejection |
| GET | `/api/stats` | Validator totals, queue total, and rank |
| GET | `/api/leaderboard` | Validators sorted by points/judgements/handle |
| POST | `/api/forgot-handle` | Rate-limited, non-enumerating Resend reminder |
| GET | `/api/banner` | Public active site banner |
| GET | `/api/messages` | Validator message list |
| POST | `/api/messages/{msg_id}/read` | Mark owned message read |
| POST | `/api/messages/{parent_id}/reply` | Reply to an owned outbound root message |

### Admin validation and account routes

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/admin/login` | Plaintext credential check; return deterministic token |
| GET | `/api/admin/stats` | Per-validator timing, flags, approved count, summary |
| GET | `/api/admin/dashboard` | Pipeline/outcome/correction/agreement matrices |
| GET | `/api/admin/serving-config` | Read priority-serving singleton |
| PUT | `/api/admin/serving-config` | Save priority-serving rule |
| GET | `/api/admin/serving-config/preview` | Count proposed priority/rest pools |
| POST | `/api/admin/banner` | Set or disable site banner |
| GET | `/api/admin/validators` | Minimal validator picker list |
| GET | `/api/admin/restricted` | Restricted-access queue and assignment state |
| POST | `/api/admin/assign` | Assign/reassign restricted record |
| GET | `/api/admin/validators/{validator_id}/flagged` | Flagged judgements for one validator |
| POST | `/api/admin/validators/{validator_id}/set-tier` | Set tier 0, 1, or 2 |
| GET | `/api/admin/admins` | List admin accounts |
| POST | `/api/admin/admins` | Trusted-admin account creation |
| DELETE | `/api/admin/admins/{admin_id}` | Trusted-admin account deletion |
| POST | `/api/admin/admins/{admin_id}/toggle-trusted` | Toggle another admin's trust |
| GET | `/api/admin/entries` | Filtered/paginated validation entry grid |
| GET | `/api/admin/entries/{record_id}` | Full review detail and queue rows |
| POST | `/api/admin/entries/{record_id}/approve` | Approve pending consensus |
| POST | `/api/admin/entries/{record_id}/flag-review` | Move pending record to review |
| POST | `/api/admin/entries/{record_id}/note` | Save persistent admin note |
| POST | `/api/admin/entries/{record_id}/resolve` | Correct and accept/reject record |
| POST | `/api/admin/queue/{queue_id}/flag` | Toggle judgement flag and optionally message validator |

### Admin messaging routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/messages` | Thread-level conversation list |
| GET | `/api/admin/thread/{thread_id}` | Load/optionally mark one thread read |
| POST | `/api/admin/thread/{thread_id}/reply` | Admin thread reply |
| GET | `/api/admin/messages/{validator_id}` | Full validator conversation (legacy view) |
| POST | `/api/admin/message` | Individual or broadcast message |

### Source Records admin routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/source-records` | Filtered, sorted, paginated grid |
| GET | `/api/admin/source-records/export.csv` | Export every filtered row |
| GET | `/api/admin/source-records/sync-status` | Latest run per source |
| GET | `/api/admin/source-records/duplicates` | Cross-table fingerprint groups |
| POST | `/api/admin/source-records/{record_id}/duplicate` | Mark distinct/duplicate and survivor |
| GET | `/api/admin/source-records/vocabularies` | Distinct dropdown values |
| PATCH | `/api/admin/source-records/{record_id}` | Version-checked review/edit/save |
| GET | `/api/admin/source-records/{record_id}` | Detail, history, duplicates, neighbors |

Literal Source Records routes are declared before the `{record_id}` detail route
so values such as `export.csv` and `duplicates` are not parsed as UUIDs.

## Scheduled work and exports

### APScheduler inside the web process

| UTC schedule | Function | Effect |
| --- | --- | --- |
| 00:22 daily | `_retry_tiebreakers()` | Retries only failed Gemini tiebreakers |
| 02:00 daily | `sync_csv.sync_once()` | Downloads and imports extractor CSV |
| 02:30 daily | `_backfill_oa_work_ids()` | Looks up missing/corrected DOI work IDs |
| Every 2 minutes | `_reap_stale_slots()` | Releases 45-minute buffered and five-day started claims |

The OpenAlex backfill reads missing IDs from `unvalidated`, fetches DOI batches
of up to 50 without holding a DB connection, then bulk-updates `unvalidated`.
`db_schema.sql` copies known IDs into `validated` on a later schema execution;
the backfill itself does not update `validated`.

### GitHub Actions

`.github/workflows/sync-sources.yml` runs at 03:00 UTC and manually. It:

1. installs Python 3.12 dependencies;
2. runs `sync_sources.py`;
3. runs `transform_sources.py` even when source sync failed; and
4. uploads transformed output and sheet snapshots as 90-day artifacts.

`.github/workflows/daily-export.yml` runs at 04:00 UTC and manually. It:

1. runs `export_validated.py` with repository secret `DATABASE_URL`;
2. stages `data/validated_export.csv`, `data/needs_manual_refs.csv`, and
   `oa_ref_cache.json`; and
3. commits/pushes only when those files changed.

No workflow currently runs pytest or JavaScript syntax checks.

### Validated export

`export_validated.py` reads `validated`, joins the extractor `source` from
`record_metadata`, and writes:

- `data/validated_export.csv`; and
- `data/needs_manual_refs.csv`.

The validated export currently contains:

```text
doi_r, doi_o, oa_work_id_r, oa_work_id_o,
url_r, url_o, ref_r, ref_o,
abstract_r, year_r, year_o,
type, outcome, outcome_quote, outcome_quote_source, source,
doi_r_published, alt_identifier_r
```

The last two columns may be absent from an older committed snapshot but are
selected by current code. OpenAlex citation strings replace stored references
when a lookup succeeds; stored references remain as fallback. Responses are
cached in committed `oa_ref_cache.json`.

`needs_manual_refs.csv` flags replication/original sides without a real DOI and
usable URL (including non-DOI URLs placed in DOI fields) and provides blank
reference-completion columns.

## Maintenance commands

Run dry modes before write modes and back up the database before destructive
operations.

### Extractor data

```bash
# Preview/import newly eligible pair_ids
python csv_to_db.py --input data/extracted_latest.csv --dry-run
python csv_to_db.py --input data/extracted_latest.csv

# Download from configured extractor branch and import
python sync_csv.py

# Find append-only rows missing from current eligible CSV
python find_orphans.py --input data/extracted_latest.csv

# Preview/delete only untouched unvalidated orphans
python cleanup_orphans.py --input data/extracted_latest.csv
python cleanup_orphans.py --input data/extracted_latest.csv --apply

# Refresh raw original-study fields for existing pair_ids
python update_originals.py data/extracted_latest.csv
python update_originals.py data/extracted_latest.csv --apply

# Refresh outcome/type/quote only for untouched unvalidated rows
python update_outcomes.py --input data/extracted_latest.csv --dry-run
python update_outcomes.py --input data/extracted_latest.csv
```

`update_originals.py` never changes `final_*` or human decisions. It normally
refuses blank replacements, except a blank DOI explicitly verified as `no_doi`.
It also applies the DOI-less ambiguity flag.

`update_outcomes.py` touches only `outcome`, `type`, `outcome_quote`, and
`out_quote_source` when the record is still `unvalidated` and no human slot was
shown or completed. Like the importer, it converts blanks to empty strings, which
can violate the outcome check constraint.

### Backfills and caches

```bash
python backfill_oa_work_ids.py --dry-run
python backfill_oa_work_ids.py

python backfill_quote_source.py
python backfill_quote_source.py --apply
python backfill_quote_source.py --recompute-all
python backfill_quote_source.py --recompute-all --apply

python fetch_oa.py
python build_static.py
```

Quote-source backfill updates `validated` only. It never overwrites a source
locked by a non-null `out_quote_source_by`. `fetch_oa.py` refreshes the Unpaywall
cache used for UI links; `build_static.py` regenerates all three docs JSON files
from root demo inputs.

### Source Records

```bash
python sync_sources.py --dry-run
python sync_sources.py
python sync_sources.py --source replications

python transform_sources.py --stats-only
python transform_sources.py
python transform_sources.py --output output/flora_entry_sheets.csv
```

Dry Source Records sync still opens a read-only database connection because row
count and payload-hash gates depend on previous runs.

### Schema and migration

```bash
# Copy old pairs/coders/judgements schema into the current tables
python db_migrate.py

# Interactive destructive reset
python db_reset.py
```

`db_migrate.py` is intended for a legacy database and should run before the app.
It executes the current schema, copies coders by handle, copies JSON pairs, and
maps old judgements into free human slots. Current code silently skips malformed
pair JSON and assumes old coder IDs exist among newly created validator IDs; it
does not run consensus afterward. Audit migrated counts and statuses manually.

`db_reset.py` requires typing `YES`, but its docstring is wrong: it does not keep
validators. Its clear list includes `validated`, `validation_queue`,
`record_metadata`, `unvalidated`, and `validators`, each truncated with `CASCADE`.
Because of cascades, dependent application data can also be removed. Do not run it
against a database you have not backed up.

## Repository file map

### Runtime backend

| File | Role |
| --- | --- |
| `app.py` | FastAPI app, database context, startup, all 59 routes, scheduler, static mount |
| `db_schema.sql` | Current PostgreSQL schema, repeatable migrations, constraints, seed rows, triggers |
| `csv_to_db.py` | Append-only extractor CSV importer and DOI-less ambiguity flag |
| `consensus_engine.py` | Two-human/LLM decision tree and final-row insertion |
| `llm_validator.py` | Gemini prompt, response schema, coercion, retry, error object |
| `source_records_service.py` | Source Records queries, review edits, versions, duplicate decisions |
| `email_templates.py` | Inline HTML/plaintext handle-reminder email |

### Synchronization, export, and maintenance

| File | Role |
| --- | --- |
| `sync_csv.py` | Nightly extractor download, dated/latest writes, importer invocation |
| `sync_sources.py` | Gated insert-only Google entry-sheet synchronization |
| `transform_sources.py` | Source Records cleaning/dedup/projection CSV transform |
| `export_validated.py` | Validated CSV and manual-reference report, OpenAlex citation cache |
| `backfill_oa_work_ids.py` | Batched OpenAlex work-ID lookup for `unvalidated` |
| `backfill_quote_source.py` | Dry-by-default quote-source classification on `validated` |
| `update_originals.py` | Dry-by-default raw original-reference refresh by `pair_id` |
| `update_outcomes.py` | Outcome/type/quote refresh for untouched rows |
| `find_orphans.py` | Read-only append-only drift report |
| `cleanup_orphans.py` | Dry-by-default deletion of untouched stale rows |
| `db_migrate.py` | Legacy `pairs/coders/judgements` copier |
| `db_reset.py` | Interactive destructive truncate utility |
| `fetch_oa.py` | Unpaywall cache refresh for static/open-access links |
| `build_static.py` | Static demo JSON generator |

### Frontend

| File | Role |
| --- | --- |
| `docs/index.html` | All login, update, onboarding, game, admin, modal, history, assignment, and inbox markup |
| `docs/app.js` | Online/static API adapter, state, rendering, validation gates, buffering, admin and Source Records UI |
| `docs/style.css` | Entire responsive visual system |
| `docs/favicon.svg` | Application icon, also referenced by email HTML |
| `docs/updates.json` | Version-1 returning-user update cards |
| `docs/pairs.json` | Generated static normal-mode dataset |
| `docs/hard_pairs.json` | Generated static hard-mode dataset |
| `docs/onboarding.json` | Generated/decorated onboarding dataset |
| `docs/_config.yml` | GitHub Pages/Jekyll exclusion for `superpowers/` |

The HTML loads Google Fonts plus CDN copies of canvas-confetti, marked, and
Chart.js. Online deployments therefore depend on those public CDNs for the
associated presentation features.

### Input, output, and caches

| File/path | Meaning |
| --- | --- |
| `extracted.csv` | Small root dataset used by static generation, not the nightly database source |
| `onboarding.json` | Curated root onboarding source |
| `oa_cache.json` | Checked-in Unpaywall UI-link cache |
| `oa_ref_cache.json` | Checked-in OpenAlex citation cache for export |
| `data/extracted_latest.csv` | Latest local extractor snapshot; may be replaced before a failed import |
| `data/extracted_DD.MM.YYYY.csv` | Dated extractor snapshots |
| `data/validated_export.csv` | Committed generated validated export |
| `data/needs_manual_refs.csv` | Committed generated manual-reference queue |
| `output/flora_entry_sheets.csv` | Generated Source Records transform |
| `snapshots/` | Ignored local Source Records payload snapshots; workflow artifacts in CI |
| `data.db` | Ignored local legacy SQLite artifact; current app does not read it |

The repository currently tracks multiple dated CSV snapshots and the generated
exports. Large data diffs are therefore possible after branch changes or scheduled
work; inspect them separately from source changes.

### Configuration and deployment

| File | Role |
| --- | --- |
| `.env.example` | Environment template; includes the explicit extractor branch |
| `requirements.txt` | Unpinned minimum Python package versions |
| `runtime.txt` | Python 3.12 runtime declaration |
| `Procfile` | Single Uvicorn web process using host `0.0.0.0` and `$PORT` |
| `sources.yml` | Authoritative entry-sheet registry and promoted-field mapping |
| `.github/workflows/daily-export.yml` | 04:00 UTC export-and-commit job |
| `.github/workflows/sync-sources.yml` | 03:00 UTC source sync/transform/artifact job |
| `.gitignore` | Ignores secrets, virtualenv, caches, local SQLite, snapshots, and `.claude` |

### Tests

| File | Current coverage focus |
| --- | --- |
| `tests/test_consensus_engine.py` | 25 consensus, uncertainty, quote-source, URL/published-DOI, senior, and LLM branches |
| `tests/test_csv_to_db.py` | 11 URL fallback and DOI-less duplicate-title cases |
| `tests/test_llm_validator.py` | 13 structured response, uncertainty, vocabulary, synonym, malformed/error, and retry cases |
| `tests/test_sync_csv.py` | 6 fetch, file-write, import-call, and error-log cases |
| `tests/__init__.py` | Empty package marker |

### Documentation and historical material

| File | Status |
| --- | --- |
| `docs/README.md` | This implementation-based project guide |
| `docs/PROJECT.md` | Detailed validator/admin narrative; useful but must be checked against code |
| `docs/SOURCE_RECORDS.md` | Detailed Source Records design and operating notes |
| `docs/SETUP.md` | Older setup guide; contains stale API/worker assumptions |
| `docs/ARCHITECTURE.md` | Older high-level architecture; names old route/model behavior |
| `docs/CSV_SCHEMA.md` | Extractor-oriented historical schema; Stage 4 describes an obsolete Flask/SQLite design |
| `docs/VALIDATION_DB_SCHEMA.md` | Historical five-table snapshot; current SQL has 17 tables |
| `docs/STAGE4_VALIDATE.md` | Historical Stage 4 integration material |
| `docs/FLoRA_Preparation_Pipeline.r` | Downstream/historical R preparation pipeline |
| `docs/superpowers/specs/...` | Dated design specification, not runtime code |
| `docs/superpowers/plans/...` | Dated implementation plan, not runtime code |

Other tracked tooling includes a local `frontend-design` agent skill and
`skills-lock.json`. `node_modules/playwright*` is vendored, but this repository
has no root `package.json`, Playwright config, or first-party Playwright test suite.
Treat vendored package code as third-party. `.DS_Store` is a tracked operating
system artifact and has no runtime role.

There is no root `README.md` and no root license file in the current tree. This
file is the existing project README under `docs/`.

## Testing and verification

Install development dependencies through the same requirements file, then run:

```bash
python -m pytest -q
```

Current `main` collects 55 tests across four test modules. They run without a live
PostgreSQL or Gemini service by mocking cursors and external calls.

Useful focused commands:

```bash
python -m pytest -q tests/test_consensus_engine.py
python -m pytest -q tests/test_csv_to_db.py
python -m pytest -q tests/test_llm_validator.py
python -m pytest -q tests/test_sync_csv.py
node --check docs/app.js
```

The current tests do not exercise FastAPI routes end to end, real PostgreSQL
constraints/transactions, Source Records sync/service/transform, migrations,
exports, scheduler duplication, admin conflict behavior, or browser interaction.
A passing 55-test run is useful regression evidence, not full system validation.

For database changes, also apply `db_schema.sql` to a temporary PostgreSQL
database and test representative inserts/updates. SQLite cannot validate the
PostgreSQL JSONB, partial indexes, triggers, `FILTER`, `LATERAL`, UUID, or locking
behavior used here.

## Deployment notes

The declared process is:

```text
web: uvicorn app:app --host 0.0.0.0 --port $PORT
```

Before deployment:

1. create a database backup;
2. set all production secrets, especially `ADMIN_PASSWORD`;
3. confirm `GITHUB_BRANCH` points at the intended extractor contract;
4. run `csv_to_db.py --dry-run` against the exact CSV;
5. inspect schema/import logs from a staging database;
6. run pytest and `node --check`;
7. use one app worker unless the scheduler is externalized;
8. verify `unvalidated`, queue-slot counts, and admin login after startup; and
9. verify an actual Gemini consensus call before accepting validator traffic.

Use HTTPS at the reverse proxy. The application sets no secure session cookie
because it has no session system. Database connections are opened per operation;
set provider connection limits and timeouts accordingly.

The in-process CSV sync writes into the checked-out filesystem. On an ephemeral
host those archives disappear on restart and are not committed by the app. The
database remains the durable imported state, while the latest local file may not.

The GitHub export workflow needs `contents: write` and a `DATABASE_URL` repository
secret. The Source Records workflow needs `DATABASE_URL` but does not request
write permission because it uploads artifacts rather than committing output.

## Security and current limitations

This section records important properties of the current implementation so they
are not mistaken for guarantees.

### Authentication and secrets

- Normal endpoints accept `coder_id` from the client and do not authenticate it.
  Anyone who learns an ID can act as or read messages/history for that validator.
- Login verifies only string ownership already stored in the database; it does
  not verify email delivery or use a secret code hash.
- Admin passwords are plaintext in PostgreSQL.
- Admin tokens are deterministic SHA-256 hashes of the password plus a fixed
  suffix; two admins with the same password have the same token.
- There is no token expiry, revocation list, rate limit, CSRF protection, or
  server-side session.
- The first admin gets a known fallback password when `ADMIN_PASSWORD` is absent.

Do not consider a public deployment secure until validator sessions and modern
password/token handling replace this model.

### Data integrity and concurrency

- Queue claims use row locks, but `/api/judge` and `/api/assignment-judge` do not
  atomically change only an open row while awarding points. Concurrent duplicate
  submissions can overwrite judgement fields and increment totals twice.
- Request models permit contradictory combinations such as `type_check="correct"`
  with `corrected_type="not_validation"`; raw corrections can later influence
  consensus.
- Admin natural-key conflicts can overwrite a different validated record instead
  of running an explicit merge workflow.
- The consensus quote policy can select the longest quote from one human and an
  associated source/evidence decision derived independently; the schema does not
  model a quote/source pair as one atomic object.
- Validation reproduction axes are joined in the browser and are not independently
  constrained or retained.
- Source Records reproduction axis strings have no database check constraints;
  unknown combinations become blank outcomes during transform.

### Import, migration, and synchronization

- Nightly extractor sync replaces `extracted_latest.csv` before import succeeds.
- Bootstrap ignores importer exit failure and can leave a fresh app empty.
- Existing `pair_id` rows never refresh through normal import.
- Study-number, lineage, full-text provenance, and independent reproduction-axis
  fields from newer extractor contracts do not have complete destinations.
- `update_outcomes.py` and the importer can submit empty strings to constrained
  outcome columns.
- Legacy migration skips malformed pair JSON without failing the run, assumes old
  coder IDs match new validator IDs, and does not recompute workflow status or
  consensus after migrated judgements.
- OpenAlex work-ID backfill updates `unvalidated`; propagation to an existing
  `validated` row relies on later schema execution.
- In-process schedules repeat in every web worker and have no distributed lock.

### Coverage and operations

- There are no API integration, database integration, Source Records, migration,
  workflow, or browser tests in current `main`.
- Requirements use lower bounds rather than a lock file, so future installs can
  resolve materially different dependency versions.
- `GET /api/health` does not check the database, scheduler, Gemini, Resend, GitHub,
  Google Sheets, or successful import freshness.
- Several historical documents describe obsolete routes, models, tables, and
  SQLite/Flask behavior. Read this README and code before copying commands.

## Troubleshooting

### The app fails immediately with `DATABASE_URL`

`app.py` reads `os.environ["DATABASE_URL"]` during import. Ensure `.env` is in the
repository root and the URL is a direct PostgreSQL URI. Test it independently
before starting Uvicorn.

### Startup is slow or changes the database unexpectedly

Every import of `app.py` executes the complete schema and starts the scheduler.
`--reload` and multiworker operation can repeat startup. Check process count and
use one worker for diagnosis.

### Fresh deployment shows no validation records

Check startup output from the `csv_to_db.py` subprocess. Bootstrap ignores a
nonzero exit code. Run the importer manually with `--dry-run`, inspect required
headers/outcome values, then run the real import and query:

```sql
SELECT COUNT(*) FROM unvalidated;
SELECT validator_slot, COUNT(*) FROM validation_queue GROUP BY validator_slot;
```

For each clean import, the three slot counts should match imported record count.

### CSV import rolls back

The whole run is transactional. Look for the first PostgreSQL constraint error.
Common current causes are an empty-string outcome, a joined reproduction outcome
outside the five-value validation constraint, schema drift, and duplicate/natural
key assumptions. Dry-run checks row eligibility but does not execute database
constraints.

### A recurring import does not update a record

That is current append-only-by-`pair_id` behavior. Use a reviewed maintenance
script for its narrow field set or implement/test an explicit backfill. Do not
delete validated work merely to force a reimport.

### “My Judgements” shows no reproduction axes

Current validation tables/API store only joined `corrected_outcome`. Independent
axis values and their individual quotes/sources cannot be returned because the
fields do not exist in this `main` schema.

### Static mode appears on the live site

The browser switches to static mode when `./api/leaderboard` fails. Open that URL
directly and inspect HTTP/proxy routing. Static mode can appear even while the
HTML itself is served correctly.

### Gemini records stay in `need_review`

Inspect `unvalidated.llm_validator`. An `error` key qualifies the row for the
00:22 UTC retry. A successful but uncertain/disagreeing result intentionally
stays for human/admin review and is not retried nightly.

### Source Records freshness is red or stale

Inspect the latest `source_sync_runs` rows and workflow logs. Typical gate errors
are an unpublished sheet returning HTML, renamed/missing headers, row count below
the configured floor, malformed UUIDs, duplicated sheet UUIDs, or a display
counter collision. Existing rows are left intact after gate failure.

### Source transform has blank reproduction outcomes

Run `python transform_sources.py --stats-only`. It prints unmapped
computational/robustness pairs. Add a deliberate canonical row to
`reproduction_outcome_map`, review the resulting vocabulary, and rerun.

### OpenAlex IDs or references are missing

Use `backfill_oa_work_ids.py --dry-run` for work IDs and inspect
`oa_ref_cache.json` for export-reference errors. DOI corrections deliberately
clear stale work IDs. Remember that work-ID backfill does not immediately copy
new IDs into already validated rows.

### Destructive cleanup is being considered

Run `find_orphans.py` first, then `cleanup_orphans.py` without `--apply`. The
cleanup utility preserves any row with submitted validator work. Avoid
`db_reset.py` unless the entire validation dataset and validator accounts are
intended to be truncated and a verified backup exists.
