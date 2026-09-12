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
> Identity is now server-side: no endpoint accepts a client-supplied `coder_id`,
> sessions are opaque `HttpOnly` cookies that expire and can be revoked, and
> there is no fallback admin password.
>
> **Validator sign-in is deliberately weak.** A handle plus the account's email
> address signs you in; the email that follows is only a notice. Handles are
> public, so anyone who guesses a validator's address can obtain their session.
> This is a chosen trade-off, not an oversight — see
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
extractor_maintenance.py
        |
        +-> sync_csv.py -> csv_to_db.py
        +-> find_orphans.py                 (nightly/read-only)
        +-> cleanup_orphans.py --apply      (separate manual action)
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
| `ADMIN_HANDLE` | No | `flora_muenster` | Handle for the bootstrap administrator. Not a secret |
| `APP_BASE_URL` | No | `https://validation.forrt.org` | Origin used to build invitation, recovery and sign-in links, and allowed to make state-changing requests |
| `ALLOWED_ORIGINS` | No | empty | Additional comma-separated origins allowed to make state-changing requests |
| `SESSION_COOKIE_INSECURE` | No | unset | Development only: drop `Secure` from the session cookie for plain-HTTP localhost |
| `ADMIN_PASSWORD` | Required when `admins` is empty | none — startup fails without it | Seeds the first trusted administrator. Stored as an Argon2id hash; there is no fallback password |
| `RESEND_API_KEY` | No | empty | Enables `/api/forgot-handle`; without it the endpoint returns 503 |
| `EMAIL_FROM` | No | `Flora Validator <noreply@forrt.org>` | Sender for handle-reminder email |
| `GITHUB_TOKEN` | No for a public source repository | empty | Authorization header for nightly extractor CSV download |
| `GITHUB_REPO` | No | `forrtproject/flora-extractor` | Extractor source repository |
| `GITHUB_BRANCH` | No | `main` | Extractor source branch |
| `EXTRACTOR_DATA_DIR` | No, but shared durable storage is required in Kubernetes | `data/` | Directory holding `extracted_latest.csv` and the immutable per-run snapshot archives that Parts 2 and 3 verify by sha256 |
| `EXTRACTOR_MAINTENANCE_LOG` | No | `logs/extractor_maintenance.log` | Combined audit log for nightly sync/report and manually requested cleanup |
| `EXTRACTOR_MAX_REMOVAL_PERCENT` | No | `10` | Finite threshold from `0` through `100`; invalid, `NaN`, infinite, or out-of-range values block sync before download/import |
| `EXTRACTOR_STAGE_TIMEOUT_SECONDS` | No | `7200` | Maximum runtime for each sync/report/cleanup subprocess |
| `EXTRACTOR_LOCK_WAIT_SECONDS` | No | `15` | Brief advisory-lock retry for an already-reserved run |
| `SUBMISSION_FAILURE_STAMP_TTL_MINUTES` | No | `30` | Lifetime of a one-time automatic-release capability issued after a server-observed judgement failure |
| `OPENALEX_MAILTO` | No | maintainer email embedded in code | OpenAlex work-ID backfill polite-pool contact |
| `PORT` | Provided by many hosts | none | Expanded by the `Procfile`, not read in Python |

The checked-in `.env.example` and `sync_csv.py` both use the extractor's `main`
branch. Override `GITHUB_BRANCH` only when intentionally testing another contract.

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

When `unvalidated` is empty and `data/extracted_latest.csv` exists, startup runs
the importer with `check=True` and the explicit `--allow-legacy-schema` flag used
for the bundled archived seed. An importer error therefore aborts startup instead
of presenting an empty application as healthy. If the file itself is absent,
startup has nothing to bootstrap and can still come online with zero records;
verify both the file and `unvalidated` on a fresh deployment.

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
60-second request timeout. A successful download is first written to:

- `data/extracted_YYYYMMDDTHHMMSSZ_<run-id>.csv` (immutable archive); and
- a temporary `data/.extracted_candidate_*.csv` staging file.

The archive is read back off disk and its sha256 recorded in the run history as
`archive_file`/`archive_sha256`. That digest — not `extracted_latest.csv` — is
what Parts 2 and 3 are bound to, so a truncated or silently failed write fails
Part 1 instead of authorising deletion.

It calls `csv_to_db.run_import()` with the staged candidate. Only after import
succeeds does `os.replace()` atomically promote that candidate to
`data/extracted_latest.csv`; the promoted bytes are then compared directly with
the downloaded candidate before Part 1 is marked complete.

`sync_once()` returns a boolean to the orchestrated child process. A standalone
`sync_csv.py` command routes through `extractor_maintenance.py`, so it receives the
same PostgreSQL lock, durable run history, completion gate, and non-zero failure
exit as scheduled/admin synchronization. Therefore a committed import followed by
a failed promotion is retained as an incomplete newest Part 1 and blocks orphan
analysis/deletion.

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

Strict mode validates the complete validation-side subset of the Stage-3 header
before considering rows. It also requires either `paper_type` or its historical name
`filter_status`. Missing identity, evidence, lineage, or provenance columns raise
`InputSchemaError` rather than being converted to empty strings. The
September 2026 extractor-only additions (`pdf_url`, `pdf_name`, `study_status`,
`study_status_reasoning`, `study_status_model`, and `osf_type`) are accepted and
ignored, as are unknown future extra columns. They do not become database fields
or make older valid 52-column exports fail. The
`--allow-legacy-schema` switch deliberately bypasses this header gate for an
intentional archived-snapshot replay; it should not be used for routine syncs.
Use `--dry-run` on a new extractor contract before writing to production.

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
| `study_r` | `unvalidated.study_r` as the within-paper study number(s) |
| `title_r` | `unvalidated.title_r` as the replication/reproduction paper title |
| `year_r`, `url_r`, `ref_r`, `abstract_r` | Corresponding `unvalidated` columns |
| `doi_o` | `unvalidated.doi_o` |
| `study_o` | `unvalidated.study_o` as the original paper's within-paper study number(s) |
| `title_o` | `unvalidated.title_o` as the original paper title |
| `year_o`, `ref_o` | Corresponding `unvalidated` columns |
| `url_o` | preferred for `unvalidated.url_o`; otherwise derived as `https://doi.org/{doi_o}` |
| `oa_work_id_o` or `openalex_id_o` | bare `W...` in `unvalidated.oa_work_id_o` |
| `oa_work_id_r` or `openalex_id_r` | bare `W...` in `unvalidated.oa_work_id_r` |
| `type` | `unvalidated.type` |
| `outcome` | `unvalidated.outcome`; exact `success`/`failure` become `successful`/`failed` |
| `outcome_phrase` | `unvalidated.outcome_quote` |
| `out_quote_source` | `unvalidated.out_quote_source` |
| Reproduction axes and their quote/source fields | Independent `unvalidated` axis/evidence columns |
| Replication OpenAlex work ID | Numeric `record_metadata.work_id` lineage key |
| Import/row `release_id`, when supplied | `record_metadata.release_id` |
| Filter/link/full-text provenance and bibliography fields | `record_metadata` |

Metadata stores filter status/method/evidence/confidence, original-match
type/confidence, DOI verification, link method/evidence/confidence/model,
`screen_categories`, `pdf_source`, `parse_method`, outcome
confidence/reasoning/model, both author strings, replication
journal/OpenAlex/source, BibTeX references, `work_id`, optional `release_id`, and
`original_rank`/`n_originals`.

### DOI-less originals

An original may legitimately have no DOI. In that case the importer keeps
`doi_o` as an empty string, preserves an extractor-provided `url_o` (usually an
OpenAlex URL), and stores a bare OpenAlex ID when available.

Before import, `_flag_ambiguous_doi_o_titles()` groups DOI-less originals by the
replication DOI, study number, and normalized paper title. A blank original
title, or the same normalized `(study_o, title_o)` identity appearing more than
once in one replication group, is appended to `unvalidated.admin_notes` for
manual review. The row is still imported.

### Idempotency and updates

`pair_id` is the normal import identity. `INSERT ... ON CONFLICT (pair_id) DO
NOTHING` remains the final duplicate-insert guard, but an existing row is no
longer ignored: the importer refreshes extractor-owned raw fields and upserts
`record_metadata`. Human summaries, `final_*` decisions, points, and workflow
decisions are not overwritten. For an already validated record, only safe
identity backfills—study numbers and matching-paper OpenAlex IDs—propagate into
`validated`.

If an upstream correction changes `pair_id`, the importer uses the stable
`(work_id, original_rank)` source slot to re-key the existing raw record. It
refuses an ambiguous source-slot match instead of merging records arbitrarily.
Blank or duplicate resolved `pair_id` values, missing required OpenAlex lineage,
and DOI-less originals without a stable OpenAlex identity fail validation before
database writes.

The whole non-dry import runs in one transaction: any uncaught error rolls back
inserts, refreshes, re-keys, metadata changes, and queue creation from that run.

### Current extractor contract

Strict imports require the current Stage-3 column set. Missing identity,
provenance, or evidence columns raise `InputSchemaError`; the importer does not
silently substitute blanks. `--allow-legacy-schema` is an explicit exception for
intentional archived-snapshot replay and is used only for the bundled seed during
fresh startup.

| Extractor information | Database destination and update behavior |
| --- | --- |
| `study_r`, `study_o` | Stored as within-paper study identifiers in `unvalidated` and propagated to the matching `validated` row; titles remain in `title_r`/`title_o`. |
| Reproduction axes and axis quote/source evidence | Stored independently in `unvalidated`, queue corrections, final fields, and `validated`; flat outcome is derived from the axes. |
| `pdf_source`, `parse_method` | Stored in `record_metadata`; `llm_fulltext` with blank `pdf_source` produces an import warning. |
| `work_id`, `release_id` lineage | Numeric replication work identity and optional routing release are stored in `record_metadata`; missing resolved work identity is rejected. |
| OpenAlex IDs | Stored on both papers; existing raw rows refresh, while validated IDs backfill only when the DOI identity still matches. |
| `pdf_url`, `pdf_name`, `study_status`, `study_status_reasoning`, `study_status_model`, `osf_type` | Accepted as extractor-only diagnostics and intentionally ignored; unrelated extra columns never break ingestion. |
| Corrected extractor fields for an existing `pair_id` | Raw extraction and metadata refresh in place without replacing validator/admin decisions. |

### Outcome vocabulary enforced by the validation schema

Replication outcomes accepted by `unvalidated.outcome` are:

- `successful`
- `failed`
- `mixed`
- `uninformative`
- `descriptive only`
- `statistically successful but flawed`
- `cannot_be_determined`
- `not_a_replication` at normalization boundaries (not published as a validated
  replication outcome)

Reproductions use the independent axis vocabularies documented below. The four
settled computation values and three settled robustness values produce twelve
valid flat compatibility labels. Historical spellings such as
`computationally successful` and `computation not checked` are normalized during
import/migration. Blank axis cells are stored as SQL `NULL`, never `''`, so
replication rows with no reproduction axes satisfy the database constraints.

## Human validation workflow

### Validator identity and onboarding

Validators sign in with a handle plus the email address on that account. Both
values must match the same row; a first-time pairing inserts a `validators` row.
`POST /api/login` opens a server-side session and sets an `HttpOnly` cookie, and
every later request is identified by that cookie. The browser never sends an
identity of its own.

**Mailbox ownership is not verified.** The email that follows a sign-in is a
notice sent afterwards, so anyone who knows a validator's public handle and
guesses their address can obtain a real session. This is a deliberate trade-off
in favour of frictionless sign-in — see
[Security and current limitations](#security-and-current-limitations).

Accounts created before email sign-in hold a personal code instead. Their owner
exchanges it once through `POST /api/login/claim-code`, which attaches an email,
clears the code, and signs them in.

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

The browser prefetches up to three pairs through `POST /api/next-pairs`
(a POST because it claims queue rows):

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

When the effective type is `reproduction`, the frontend replaces the single
replication outcome gate with two independently reviewed axes:

| Axis | Canonical choices | Uncertainty choice |
| --- | --- | --- |
| Computation | `computationally reproducible`, `computational issues`, `technical failure`, `not checked` | `cannot_be_determined` (displayed as **Can't tell**) |
| Robustness | `robust`, `robustness challenges`, `not checked` | `cannot_be_determined` (displayed as **Can't tell**) |

Each axis has its own **Looks right / Mischaracterised / Can't tell** decision and
its own quote and quote-source fields. The browser sends
`corrected_outcome_computation`, `corrected_computational_quote`,
`corrected_computational_source`, `corrected_outcome_robustness`,
`corrected_robustness_quote`, and `corrected_robustness_source`.

`JudgeRequest`, `validation_queue`, `unvalidated`, and `validated` all carry those
independent fields. `extractor_vocab.py` validates the vocabularies and derives the
flat compatibility outcome only after both axes are resolved. The twelve settled
4x3 combinations remain valid; an incomplete or uncertain pair derives
`cannot_be_determined`. Legacy joined outcomes are translated at import/migration
boundaries rather than treated as the authoritative judgement. Changing a record
between replication and reproduction clears fields that do not belong to the new
type, preventing a joined reproduction result or stale axes from leaking across.

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
`vote_score`. Skipping and reporting inaccessible content award no points. A normal
skip records a controlled reason and optional comment in `validation_skips`; comments
are required for eligibility, data-quality, and "other" reasons. The queue release,
history insert, and `skipped_count` increment commit atomically.
The endpoint locks the `unvalidated` row before clearing a queue slot, so two
simultaneous skips cannot leave `validation_status` stuck in progress.

Some frontend labels and static-demo scoring constants do not match the live
backend. In particular, the note UI says `+3 pts`, but `_points_for()` adds one.
The backend response and database totals are authoritative in online mode.

### Durable background submission recovery

Normal-mode buffered judgements are saved optimistically in the background. The
complete payload remains in `localStorage` until the server confirms either a
successful judgement or an authorised recovery outcome. Every queued payload has
a browser-generated UUID `submission_id`; persisted payloads from an older
frontend receive one before their next retry.

```text
queued judgement + submission_id
        |
        v
POST /api/judge
        |
        +-- commit succeeds ----------------------> remove local pending copy
        |
        +-- server observes pre-commit failure
                |
                v
        verify unfinished slot ownership
                |
                v
        store SHA-256(stamp) + scoped audit row
                |
                v
        browser retries, then consumes raw stamp at
        POST /api/submission-failures/release
```

The server creates or rotates a stamp only when all of these conditions hold:

1. `/api/judge` actually reached the application and failed before commit;
2. that failure was a **server-side** one — an unhandled exception, or a
   deliberate 5xx. A 4xx is a decision about the request, not a failed save, so
   it is returned unchanged with no stamp;
3. the request contains a valid `submission_id`; and
4. the requested validator still owns an unfinished human queue slot for that
   record.

Condition 2 matters because the browser treats every 4xx as terminal and spends
whatever stamp it is handed. Without it, an ordinary correctable error such as
`type_check must be 'correct' or 'incorrect'` would authorise the browser to
discard the validator's completed judgement and reassign the record. Slot-gone
answers (409 "already submitted", 400 "Already judged this record") need no
stamp either: the server has already released the slot, and the browser closes
the pending item from the response itself.

The random raw stamp is returned only in structured error detail with code
`judgement_save_failed`. PostgreSQL stores only its SHA-256 digest. The default
lifetime is 30 minutes and can be changed with
`SUBMISSION_FAILURE_STAMP_TTL_MINUTES`.

The release endpoint accepts only the stamp—never a client-supplied `coder_id`,
`record_id`, or `queue_id`. It locks in the same order used by judgement and Skip
transactions (`unvalidated` record, queue slot, failure audit row), re-checks the
stamp and exact binding, and conditionally clears only that unfinished slot.

| Recovery state | Meaning |
| --- | --- |
| `save_failed` | The server rolled back the judgement and the current stamp may still be consumed before expiry. |
| `saved_after_retry` | The browser resent the queued judgement with the same `submission_id` and it committed, so the capability was never needed. Closed by `/api/judge` itself, in the same transaction as the judgement. |
| `released` | The one-time stamp released its exact unfinished slot. |
| `slot_closed` | The slot had already been submitted, released, or changed; the stamp was consumed without clearing anything. |
| `expired` | The stamp lifetime elapsed. A later server-confirmed failure must issue a fresh stamp before release. |

The endpoint is idempotent after consumption: replaying the same stamp reports its
final state but cannot act again. The two-minute stale-slot reaper materialises
elapsed `save_failed` rows as `expired` for accurate admin history.

A retry that succeeds closes its own row. Nothing else can know it did: the
browser simply drops the queued item and never tells the server. Without that
step the row stayed `save_failed` until the reaper marked it `expired` — an
audit trail claiming the validator lost work they had in fact saved, and
indistinguishable from the case where they really did. Historical rows that
already aged into `expired` cannot be reclassified, because the distinction was
never recorded; the fix applies from here on.

Network failures, gateway failures that do not return a stamp, client errors
raised inside the handler, and FastAPI request validation failures outside the
`/api/judge` handler cannot authorise a release. The browser keeps the entire
pending judgement in those cases. It also retains the payload if stamp
consumption fails, expires, or returns an unknown state.

A submission the server rejected is shown in the Background saves list as
"rejected by server; kept" rather than "save failed; release pending" — no
release is coming, and the work is still there.

A parked first item does not block later submissions: the processor skips
blocked entries and continues through the remaining queue. The header's
pending-save button opens a recovery dialog at any time. Each blocked judgement
offers **Retry** (which clears an expired failure capability), **Export JSON**
(a local backup that does not submit or remove it), and explicitly confirmed
**Discard**. Until a server-confirmed outcome or deliberate discard occurs, the
complete payload remains in `localStorage` across reloads.

A queued judgement belongs to the validator who wrote it, not to the browser it
was written in. Each one records its author, and it is only ever sent, listed or
acted on while that person is signed in. Signing out deliberately leaves the
queue intact — the work is theirs and it resumes when they return — but the next
person to use that machine can neither see it nor flush it. This matters because
the server now takes identity from the session cookie rather than from a
`coder_id` in the payload: without the ownership check, a judgement left behind
by one sign-in would be submitted as whoever signed in next.

Automatic recovery is deliberately separate from voluntary Skip behavior:

- it writes `submission_failure_releases`, not `validation_skips`;
- it does not increment `validators.skipped_count` or affect Skipped-panel
  escalation thresholds;
- admin record detail shows it in a separate collapsed **Automatic save recovery**
  ledger; and
- `/api/skip` rejects `submission_failed`. That value remains in the SQL constraint
  only so historical audit rows continue to load.

This capability closes the misleading internal-reason path. Validator routes no
longer trust a client-supplied `coder_id` — identity comes from the session
cookie — but sign-in itself still does not prove mailbox ownership, so read
[Security and current limitations](#security-and-current-limitations) before
deploying publicly.

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
message thread. List rows include extracted and corrected reproduction axes. The
detail response also includes each axis's extracted/corrected/final quote and
source evidence. DOI-less originals render their stored/OpenAlex URL instead of a
dash when no registered DOI exists.

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
Records, validator statistics, admin accounts/site banner, dashboard metrics,
priority serving, the extractor pipeline, restricted-access assignments, and
messaging.

### Login and admin accounts

On an empty `admins` table, startup creates:

- handle: `admin`
- password: `ADMIN_PASSWORD`, or the unsafe fallback
- trusted: true

### Sessions

A successful sign-in mints a random token, stores only its SHA-256 digest in
`sessions`, and returns it in an `HttpOnly; Secure; SameSite=Lax` cookie. Page
scripts cannot read it, and it never appears in a URL or a request body.

Every private endpoint takes a `current_validator` or `current_admin`
dependency, so identity comes from that cookie. `coder_id` is no longer accepted
anywhere — not in a body, not in a query string, and not as a field on any
request model. A caller asking for another validator's id simply gets their own
data, or a 401 if they have no session.

Expiry and revocation are columns evaluated by the database at the moment of
use, which is what makes logout, password changes, and admin deletion take
effect immediately.

### Coming back without signing in again

Both sign-in forms offer **"Stay signed in on this device"**. It lengthens the
session; it does not store a credential:

| | Session lasts |
| --- | --- |
| Ticked | 30 days |
| Unticked | 12 hours |

**No password is ever written to the browser, for either role.** That is not an
omission to be fixed later — it cannot be done safely. `localStorage` is
readable by any script on the page, and encrypting it needs a key that same
script can read, so the encryption protects nothing. The session cookie is the
correct mechanism and already does the job better: it is `HttpOnly`, so page
scripts cannot read it at all, and the server can revoke it at any moment.

What *is* remembered is the identifier, so returning users find the form filled:
the handle for administrators, the handle and email for validators.

That validator prefill deserves care. Because sign-in needs only those two
values, a filled form on a shared machine is a working credential. Two things
follow:

- The login screen shows **"Not you? Clear this device"** whenever it has
  prefilled anything.
- Signing out deliberately clears the remembered email, keeping only the handle.
  A session that merely expired keeps both, because nobody chose to leave.

Leaving the box unticked is the right choice on a shared or public machine: the
session then dies in 12 hours whatever else happens. **The choice is remembered
per device**, so unticking it is not silently undone on the next visit, and
"Not you? Clear this device" resets it to the safe option rather than leaving a
30-day session armed for whoever sits down next.

### Inactivity auto-logout

After 30 minutes without input (warned at 25, counted in wall-clock time so a
sleeping laptop or a throttled background tab cannot extend it) the browser
signs the session out and returns to the login screen. All open tabs follow.

This **revokes the session on the server**, exactly as pressing Log out does; it
is not a local screen change. The distinction matters because the session is an
`HttpOnly` cookie rather than a value in `localStorage`: clearing local state
alone would leave the cookie live, and the reload would resolve it through
`GET /api/me` and sign the same person straight back in. Only the tab's own
session ends — the same account stays signed in on other devices.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/login` | Handle + account email; opens a validator session immediately |
| `POST /api/admin/login` | Password sign-in, opens an administrator session |
| `GET /api/me` | Who the cookie belongs to |
| `POST /api/logout` | Revoke this session |

Accounts created before email sign-in have a personal code and no address.
`POST /api/login/claim-code` lets that owner present the code once, choose an
email, and be signed in; the code is cleared in the same statement so the weaker
credential does not survive as a second way in. Only an account with no email
can be claimed, so a guessed code cannot repoint somebody else's account at a new
mailbox. Every failure returns one generic message, and attempts are throttled.

That last guard is a check-then-act on one row, so the row is locked with
`SELECT ... FOR UPDATE` and the write is conditional on `email IS NULL`. Without
both, two people presenting the same code concurrently each received a session
and whichever transaction committed last decided which mailbox owned the
account.

`POST /api/login` is subject to the same class of race when two first-time
sign-ins collide on a handle or an email. The insert catches the unique
violation and answers `409 — please try again` rather than a 500, and the login
button is disabled while a request is open so a double-click cannot cause it.

Validator sign-in does **not** require reading the mailbox: handle plus the
address on the account is enough, and `_notify_sign_in()` then emails the owner
that a sign-in happened. Because the session already exists by then, that email
reports rather than prevents. Both values must match the same account, and
attempts are throttled, but neither stops someone who knows both.

Cross-site protection is middleware, not a per-route decision: any
state-changing request whose `Sec-Fetch-Site` is `cross-site`, or whose `Origin`
is not `APP_BASE_URL` or one of `ALLOWED_ORIGINS`, is refused with 403.
`/api/next-pairs` became a POST because it claims queue rows. API responses
carry `Cache-Control: no-store`.

Sign-in attempts are throttled per identifier and per client address: 8 failures
in 15 minutes and further attempts get 429 until the window passes.

Every branch that tells a caller something about an account they do not own
counts against that throttle — including "that handle is already taken" and
"this account was created with a personal code". The second matters most: it
names exactly the accounts that still hold a personal code, which is the list
worth guessing against `/api/login/claim-code`. Unrecorded, either could be
probed without limit.

#### Which address the server believes

Both halves of that throttle have to be real, and the address half is the one an
attacker gets a say in. A caller trying one password against many different
handles never accumulates against the identifier clause, so the address clause
is the only thing counting — and an address the caller chooses counts against
nothing.

`X-Forwarded-For` grows left to right: every proxy **appends** the address it
actually saw. With one proxy in front of the app an honest request arrives as
`<client>`, while a caller who sets the header themselves arrives as
`<anything they like>, <client>`. The leftmost entry is therefore theirs to
invent, and a fresh one per request defeats any per-address limit.

So the server reads the entry `TRUSTED_PROXY_HOPS` places from the **right** —
the one its own proxy wrote — and ignores everything to the left of it. Honest
callers see no difference: behind a single proxy the two readings are the same
address. Set `TRUSTED_PROXY_HOPS=0` when nothing proxies the app and the header
is ignored outright; a chain shorter than the configured hop count is not
trusted either, and falls back to the peer address. Misconfiguring it too high
resolves addresses to a proxy, which over-throttles rather than under-throttles.

The same address is what `security_events` records, so an audit trail cannot be
signed with an address of the caller's choosing. Entries that are not valid IP
addresses are discarded rather than stored.

This bounds abuse per address; it does not make an address an identity. Several
validators behind one institutional NAT share a count, which is why the
identifier clause exists alongside it.

### Administrator sign-in

Administrators sign in through their own form, reached by triple-clicking the
panel on the left of the login screen or by opening `/?admin=1`. It prefills the
remembered handle and focuses the password field; the password itself is never
stored. It is out of the
way for tidiness, **not** as a security measure: `/api/admin/login` is a public
endpoint, and the password, throttling and session are what protect it.

That form replaced a heuristic which treated any value in the login screen's
Email field with no `@` in it as an admin password. A password that did contain
one — as strong passwords often do — was posted to the *validator* endpoint
instead, where it was stored in `validators.email` in plaintext and opened a
validator session under the admin's handle. The Email field is now only ever an
email address.

### Security event audit trail

Privileged actions are appended to `security_events`: admin sign-in, failed
sign-in, sign-out, invitations, password changes, admin deletion, trust changes,
validator tier changes, code claims, assignments, and maintenance runs. Each row
records the action, the actor **as the server resolved them**, the target, the
client address, and a JSON detail blob.

Events are written on the same cursor as the action they describe, so an action
that rolls back takes its event with it. A failed audit write is logged loudly
but never fails the action itself. Every field is length-bounded inside
`security_events.record()` rather than at each call site: some of what lands
here is attacker-supplied — the handle on a failed sign-in, for one — and an
audit table nobody reads until it matters is a tempting place to dump data.
Credential fields are separately bounded on the request models, so an
unauthenticated caller cannot make the server hash a megabyte with Argon2. Rows are kept for 365 days — far longer than
`login_attempts`, which exists to throttle rather than to explain — and pruned by
the hourly housekeeping job.

`GET /api/admin/security-events` reads the trail (filters: `action`, `days`,
`limit`). It is read-only: nothing in the application updates or deletes an
event except the retention prune, so an admin cannot tidy away their own trail.

### Administrator invitations

A trusted administrator creates an account with a handle and an email address —
never a password. The row is written with `password_hash` NULL, which cannot be
authenticated against, and a single-use link is emailed. The recipient opens it,
chooses their own password, and the link is spent. Nobody else ever sees that
password, including the person who sent the invitation.

Only the SHA-256 digest of the link token is stored in `auth_links`, the same
discipline used for submission-failure stamps: a database dump yields no working
link. Issuing a new link revokes any outstanding one for that account and
purpose, so a superseded email cannot still be redeemed. Invitations last 48
hours; recovery links last 2.

If `RESEND_API_KEY` is unset or delivery fails, the link is returned **once** to
the trusted administrator who triggered it, so an invitee is never stranded by a
mail outage. `python admin_password.py --handle NAME --invite` prints one from
the command line for the same reason.

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /api/admin/admins` | trusted admin | Create an account and email its invitation |
| `POST /api/admin/invite/resend` | trusted admin | Replace an outstanding link |
| `GET /api/admin/auth-link/{token}` | public | Name the account a live link belongs to |
| `POST /api/admin/auth-link/redeem` | public | Spend a link to set the password |

`POST /api/admin/login` verifies the submitted password against an Argon2id hash
and opens an administrator session, returned as the same `HttpOnly` cookie
validators get but with a 12-hour lifetime. Protected routes take a
`current_admin` dependency; the old `X-Admin-Token` header and the derived
bearer token it carried are gone. Trusted admins can create/delete admins and
toggle trust. An admin cannot delete their own account, change their own trust,
or delete the last admin. Deleting an admin or changing a password revokes that
account's sessions immediately.

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

The entries table supports filters for pending approval, review, repeated skips,
saved admin comments, validated, and excluded records, plus DOI/title search,
safe whitelisted sorting, agreement percentages, LLM-dissent markers,
validator/tier counts, skip counts, and pagination. The **Skipped** filter includes
a record after more than five distinct validators have skipped it for any reason,
or after at least two distinct validators have reported `eligibility_unclear` or
`data_quality`. Repeated skips by one validator remain auditable but cannot meet a
threshold by themselves.

The detail view includes raw/final values, independent reproduction axes and
their evidence, human/LLM summaries, queue rows, validator history counts, flags,
notes, quote-source controls, and correction fields. **Skip history** is collapsed
by default and lists every event's time, validator, controlled reason, and
comment. The independent **Automatic save recovery** ledger is also collapsed by
default and is not counted as skip activity.

### Approve, review, reject, and resolve

- **Approve** accepts only `consensus_reached`, marks the row `validated`, and
  inserts the effective final values into `validated` (using the validated
  identity conflict key for its upsert).
- **Flag for review** moves a pending approval back to `need_review` and can save
  an admin note.
- **Resolve** can correct type, both paper identities/titles/links, abstract,
  outcome/quote/source, published DOI, and alternative identifiers. A final type
  of `not_validation` rejects and deletes any `validated` row for that record.
- **Senior fast-reject** is a validator action, but the admin view exposes the
  resulting rejection and allows an override through resolve.

The authoritative identity key is
`(doi_r, study_r, title_r, original_key, study_o, title_o)`, where
`original_key` is the original DOI or, for a DOI-less original, its OpenAlex work
ID. This keeps study numbers separate from titles and prevents all blank original
DOIs from collapsing onto one identity.

Admin **Resolve** never silently overwrites a different validated record. A
colliding identity first returns a structured HTTP 409. The browser shows record
A (the duplicate) and record B (the authoritative survivor) and requires a second,
explicit confirmation. A confirmed merge keeps B unchanged in `validated`, marks
A rejected, preserves A's raw extraction, metadata, and judgements, and writes an
auditable `validated_record_merges` link with the administrator and resolution
snapshot. Replaying the same confirmed merge is idempotent; naming a different or
stale survivor is rejected.

### Extractor pipeline operations

The **Extractor Pipeline** tab exposes the routine two-stage operation and the
destructive maintenance action separately:

1. CSV download, safety validation, database import, promotion, and byte
   verification;
2. read-only orphan reporting; and
3. manually confirmed, guarded orphan cleanup.

The routine **Sync + report** operation never selects Part 3. Only a dedicated
cleanup request can delete, and it requires explicit confirmation. Only one
maintenance run can be queued or running across all workers/pods. The tab shows
the previous/candidate/add/remove counts, safety warnings, per-stage states,
requester, duration, and retained output. It always returns at least the latest
seven days (up to 90 when requested); summaries include a log tail and each run's
detail endpoint returns the complete database-retained log.

Manual requests persist their `queued` row before returning HTTP 202. They are
then claimed by the database-backed dispatcher rather than a process-local
background callback, so a web response or pod shutdown cannot silently discard
the job. The same page shows queued/running recovery state and the atomic cleanup
receipt retained after a destructive run.

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

1. normalizes replication outcomes through `outcome_alias` and derives the flat
   reproduction compatibility outcome directly from the two authoritative axes;
2. cleans DOI prefixes, case, whitespace, and known scraped suffixes;
3. applies `transform_exclusions` by replication DOI or URL;
4. removes `url_r` when it merely repeats the DOI resolver;
5. removes admin-confirmed duplicates and collapses remaining identifier
   duplicates, except rows marked distinct; and
6. writes the 20-column FLoRA projection, with the six reproduction-axis/evidence
   columns appended for positional compatibility with older consumers.

Reproduction quote text and quote sources are joined with ` || ` for the legacy
flat fields so neither axis is silently discarded, while each original axis,
quote, and source is also exported independently. Unknown replication aliases,
bad canonical aliases, or invalid axis values are all reported and abort the
write rather than producing a partial file. `DUMMY_...` identifiers are stripped
before output.

The output columns are:

```text
doi_o, ref_o, url_o,
doi_r, ref_r, url_r,
abstract_r,
outcome, outcome_quote, outcome_quote_source,
type, source,
alt_identifier_o, alt_identifier_r,
outcome_computation, outcome_computational_quote,
out_quote_computational_source,
outcome_robustness, outcome_robustness_quote,
out_quote_robust_source
```

## Database reference

`db_schema.sql` currently creates or evolves 20 application tables. It also
contains data migrations, indexes, helper functions, and triggers, so it should
be reviewed as executable migration history, not only fresh-install DDL.

### Validation tables

| Table | Purpose and important keys |
| --- | --- |
| `validators` | One row per validator; unique handle/email/code, tier, vote score, totals, onboarding/login/update/reminder state |
| `unvalidated` | One extractor pair per UUID; unique `pair_id`, raw paper fields, workflow flags, three JSONB summaries, `final_*`, admin/restriction/OpenAlex/published-ID fields |
| `validation_queue` | Unique `(record_id, validator_slot)` rows for `human_1`, `human_2`, `llm`; claims, checks, corrections, flags, notes, points, timestamps |
| `validation_skips` | Append-only skip events with record, validator, reusable-slot reference, controlled reason, comment, and timestamp |
| `submission_failure_releases` | Server-observed save failures, hashed one-time release stamps, and explicit recovery state; excluded from voluntary skip statistics |
| `validated` | Authoritative accepted output; UUID PK, source `record_id`, effective paper/outcome fields, unique identity `(doi_r, study_r, title_r, original_key, study_o, title_o)` |
| `validated_record_merges` | Explicit duplicate-resolution audit linking a merged record to the authoritative validated record |
| `record_metadata` | One-to-one extractor provenance and bibliography linked to `unvalidated.record_id` |
| `assignments` | One restricted record assignment; unique `record_id`, assignee, assigner, open/done timestamps |

### Accounts, communication, and serving

| Table | Purpose |
| --- | --- |
| `admins` | Plaintext handle/password and trusted flag |
| `site_banner` | Singleton public banner row (`id = 1`) |
| `validator_messages` | Bidirectional messages, parent threads, queue link, validator/admin read state |
| `serving_config` | Singleton priority-serving rule (`id = 1`) |
| `extractor_maintenance_runs` | Scheduled/manual status, stage results, CSV safety comparison, and complete log; a partial unique index prevents duplicate active reservations while an advisory lock excludes live processes |

### Source Records tables

| Table | Purpose |
| --- | --- |
| `source_records` | Insert-only sheet identity, promoted/raw fields, review/version state, duplicate decision |
| `source_record_edits` | Append-oriented changed-field and duplicate-decision audit rows |
| `source_sync_runs` | Per-source freshness, gate result, counts, and payload hash |
| `source_display_counters` | Last sequential number handed out per source |
| `transform_exclusions` | DOI/URL decisions omitted from transformed output |
| `outcome_alias` | Replication raw-to-canonical outcome lookup |

The former `reproduction_outcome_map` table is deliberately dropped by the
schema: reproductions retain both independent axes, and the flat compatibility
label is derived deterministically without a lossy lookup.

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

Identity comes from the `flora_session` cookie on every private route. All
`/api/admin/...` routes except admin login require an administrator session;
validator routes require a validator session. No route accepts a `coder_id` from
the caller. State-changing requests are refused unless they originate from
`APP_BASE_URL` or `ALLOWED_ORIGINS`.

### Public and validator routes

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/login` | Register/sign in with handle plus the account email; opens a session |
| POST | `/api/login/claim-code` | One-time: trade a pre-email personal code for an email address |
| POST | `/api/logout` | Revoke this session |
| GET | `/api/me` | Who the session cookie belongs to |
| GET | `/api/onboarding` | Return curated onboarding pairs |
| POST | `/api/onboarding/complete` | Stamp onboarding completion |
| POST | `/api/update-seen` | Record current update version |
| GET | `/api/my-judgements` | Latest 100 completed judgements for the signed-in validator |
| GET | `/api/my-judgements/{queue_id}` | One judgement, final record, and message thread |
| POST | `/api/next-pairs` | Resume/claim active and buffered pairs; normal/hard mode |
| POST | `/api/pairs/{queue_id}/start` | Promote a buffered claim to started |
| GET | `/api/health` | Process-only liveness check |
| POST | `/api/restricted` | Report inaccessible hard-mode article and release slot |
| GET | `/api/my-assignments` | Open restricted assignments for the signed-in validator |
| GET | `/api/assignment/{record_id}` | Load one assigned record |
| POST | `/api/assignment-judge` | Resolve assigned record and award double points |
| POST | `/api/judge` | Submit ordinary judgement and run consensus |
| POST | `/api/skip` | Release a claimed slot and atomically save its reason/comment history; `reason_code` is optional for rolling-deployment compatibility and answers `reason_recorded: true` |
| POST | `/api/submission-failures/release` | Consume a server-issued one-time stamp to recover one failed background submission; never counts as a skip |
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
| POST | `/api/admin/login` | Argon2id password check; opens an administrator session |
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
| POST | `/api/admin/entries/{record_id}/resolve` | Correct and accept/reject a record; a confirmed `merge_into_record_id` performs an explicit audited duplicate merge |
| POST | `/api/admin/queue/{queue_id}/flag` | Toggle judgement flag and optionally message validator |
| GET | `/api/admin/maintenance/runs` | At least seven days of pipeline summaries and warnings |
| GET | `/api/admin/maintenance/runs/{run_id}` | Complete retained log for one run |
| POST | `/api/admin/maintenance/run` | Queue the non-destructive full sync/report routine or one explicit stage; cleanup requires confirmation |

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
| 02:00 daily | `extractor_maintenance.run_scheduled()` | Locked sync/import → OpenAlex enrichment → read-only orphan report; never deletion |
| Every 10 seconds | `extractor_maintenance.run_queued()` | Claims a durable manual request or recovers work abandoned by a terminated pod |
| Every 2 minutes | `_reap_stale_slots()` | Releases 45-minute buffered and five-day started claims; marks elapsed failure stamps `expired` |

The OpenAlex backfill reads missing IDs from `unvalidated`, fetches DOI batches
of up to 50 without holding a DB connection, then bulk-updates `unvalidated`.
In the same write transaction it synchronizes matching missing IDs into existing
`validated` rows, including records filled by older backfill runs, so exports do
not wait for a later schema execution.

The extractor pipeline is fail-fast. Before import, it compares unique resolved
`pair_id`s with the baseline the orchestrator names from run history — the
previous run's immutable archive, verified by sha256, falling back to
`data/extracted_latest.csv` only while that file still holds those exact bytes.
A zero-resolved candidate is an extractor error. A candidate removing more than
`EXTRACTOR_MAX_REMOVAL_PERCENT` (10% by default) is blocked. So is a missing or
stale baseline on a database that already holds records: an empty volume is
reported as `missing_local_baseline`, never treated as a first deployment. In
every case the known-good CSV stays active and the orphan report is logged as
`SKIPPED`. Cleanup is not part of a routine run at all.
New resolved IDs are a visible non-blocking warning. Every scheduled or manual
run is retained in `extractor_maintenance_runs` for the admin **Extractor
Pipeline** tab; output also appends to the configured text log and stdout. A new
extractor commit is picked up at the next 02:00 UTC run, not through a webhook.

A session-level PostgreSQL advisory lock is held from before a run starts until
its final history update completes. Every pod and web worker therefore shares one
authoritative process mutex. A live but old process cannot be replaced merely
because its status timestamp is stale; a crashed pod releases the lock
automatically when PostgreSQL closes its connection. Each child stage is also
terminated after `EXTRACTOR_STAGE_TIMEOUT_SECONDS` so a hang cannot own the lock
indefinitely.

The OpenAlex backfill is no longer an independent 02:30 job. The 02:00 run
executes it inside the same advisory-lock lifetime after a successful import, so
it cannot overlap a slow sync or a manually started cleanup. Every cron trigger declares `UTC`
explicitly; the host or pod timezone cannot shift execution at daylight-saving
boundaries.

Manual `202 Accepted` responses are durable queue acknowledgements, not
in-process FastAPI callbacks. The request row is committed before the response;
all pods poll it, but the advisory lock elects one executor. If that pod exits,
a replacement requeues the same run. If cleanup had already committed its
receipt, recovery finalizes the run without repeating deletion.

Stage progression uses the durable run record, not only subprocess exit codes.
Part 1 becomes `SUCCESS` only after download, validation, database import, atomic
promotion, an exact post-promotion byte comparison, and a verified archive digest
all complete for that `run_id`. Part 2 starts only from that verified state. Part
3 is never chained automatically: a separately confirmed cleanup request starts
only after Part 2 has been committed as `SUCCESS`. Individually launched Part 2/3
runs inherit the newest verified prerequisite run IDs; the newest failed or
incomplete sync blocks them instead of falling back to an older success.

Run-scoped completion markers are paired with the snapshot's `archive_sha256`,
because a run ID proves only that some pod finished a sync. Both orphan stages
receive the archive path and `--expect-sha256` and verify the file before reading
it, and cleanup also compares that digest with the one PostgreSQL recorded for the
run. A pod whose `EXTRACTOR_DATA_DIR` lacks the archive blocks with
`snapshot_archive_unavailable`; one holding different bytes under the same name
blocks with `snapshot_archive_mismatch`.

Apply-mode orphan cleanup briefly requests `EXCLUSIVE NOWAIT` locks on all eight
affected tables (`unvalidated`, `validated`, queue, skip and automatic-failure audit,
metadata, assignments, and messages) before its safety scan. If validation is already writing, cleanup
fails and is logged instead of waiting; after the locks are acquired, new validation
writes wait. Skip history therefore cannot appear between classification and
deletion and abort the whole batch.

The cleanup child writes `safety_report.cleanup_receipt` and marks Part 3
`COMMITTED` in the **same transaction** as its DELETE statements. The receipt
contains exact record identities and per-table deletion counts. Parent progress
updates merge JSON rather than replacing it, so a crash after deletion cannot
erase the audit evidence.

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
doi_r_published, alt_identifier_r,
outcome_computation, outcome_computational_quote,
out_quote_computational_source,
outcome_robustness, outcome_robustness_quote,
out_quote_robust_source,
study_r, title_r, study_o, title_o,
work_id, release_id, screen_categories,
pdf_source, parse_method, outcome_reasoning, outcome_llm_model,
bibtex_ref_o, bibtex_ref_r
```

Newer columns are appended where possible for positional compatibility with older
consumers. The query reads provenance through a lateral `record_metadata` source;
`FROM record_metadata` is required inside that lateral subquery. OpenAlex citation
strings replace stored references when a lookup succeeds; stored references
remain as fallback. Responses are cached in committed `oa_ref_cache.json`.

`needs_manual_refs.csv` flags replication/original sides without a real DOI and
usable URL (including non-DOI URLs placed in DOI fields) and provides blank
reference-completion columns.

## Maintenance commands

Run dry modes before write modes and back up the database before destructive
operations.

### Extractor data

```bash
# Preview/import new rows and refresh extractor-owned fields on existing pair_ids
python csv_to_db.py --input data/extracted_latest.csv --dry-run
python csv_to_db.py --input data/extracted_latest.csv

# Download from configured extractor branch and import through the audited runner
python sync_csv.py

# Run the complete routine pipeline (sync + read-only orphan report; no deletion)
python extractor_maintenance.py

# Run one stage from the command line (admins can do the same in the UI)
python extractor_maintenance.py --stage sync
python extractor_maintenance.py --stage find
python extractor_maintenance.py --stage cleanup

# Find database rows missing from the current eligible CSV
python find_orphans.py --input data/extracted_latest.csv

# Preview orphan deletion using the current retention rules
python cleanup_orphans.py --input data/extracted_latest.csv

# Apply cleanup through the prerequisite-gated maintenance runner
python extractor_maintenance.py --stage cleanup

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

`update_outcomes.py` touches only extractor outcome/type fields when the record is
still `unvalidated` and no human slot was shown or completed. That includes both
reproduction axes and each axis's quote/source evidence. It validates the current
CSV contract and vocabulary, normalizes quote sources, derives the flat
reproduction outcome from the axes, and binds blank axes as SQL `NULL` rather than
the constraint-invalid empty string.

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
maps old judgements into free human slots. Malformed pair JSON aborts the
migration instead of being silently discarded. Legacy coder IDs are translated to
current validator IDs by matching handles, and reruns can repair rows written by
the older numeric-ID migrator. Migrated summaries are restored into
`validator_1`/`validator_2`; one completed human leaves the record
`validation_inprogress`, while two completed humans route it to `need_review` so
occupied slots cannot strand it. The migrator intentionally does not publish a
consensus result from incomplete legacy evidence; audit its printed counts and
admin-review queue after running it.

`db_reset.py` requires typing `YES`, but its docstring is wrong: it does not keep
validators. Its clear list includes `validated`, `submission_failure_releases`,
`validation_skips`, `validation_queue`, `record_metadata`, `unvalidated`, and
`validators`, each truncated with `CASCADE`.
Because of cascades, dependent application data can also be removed. Do not run it
against a database you have not backed up.

## Repository file map

### Runtime backend

| File | Role |
| --- | --- |
| `app.py` | FastAPI app, database context, startup, API routes, scheduler, and static mount |
| `db_schema.sql` | Current PostgreSQL schema, repeatable migrations, constraints, seed rows, triggers |
| `csv_to_db.py` | Strict extractor CSV importer, existing-row refresh/re-key logic, lineage mapping, and DOI-less ambiguity flag |
| `consensus_engine.py` | Two-human/LLM decision tree and final-row insertion |
| `llm_validator.py` | Gemini prompt, response schema, coercion, retry, error object |
| `source_records_service.py` | Source Records queries, review edits, versions, duplicate decisions |
| `email_templates.py` | Inline HTML/plaintext handle-reminder email |

### Synchronization, export, and maintenance

| File | Role |
| --- | --- |
| `sync_csv.py` | Staged extractor download, immutable archive, snapshot safety comparison, import, atomic promotion, and verification |
| `extractor_maintenance.py` | Locked CSV sync/report routine, separately requested guarded cleanup, and unified audit log |
| `sync_sources.py` | Gated insert-only Google entry-sheet synchronization |
| `transform_sources.py` | Source Records cleaning/dedup/projection CSV transform |
| `export_validated.py` | Validated CSV and manual-reference report, OpenAlex citation cache |
| `backfill_oa_work_ids.py` | Batched OpenAlex work-ID lookup for `unvalidated` with matching propagation into `validated` |
| `backfill_quote_source.py` | Dry-by-default quote-source classification on `validated` |
| `update_originals.py` | Dry-by-default raw original-reference refresh by `pair_id` |
| `update_outcomes.py` | Outcome/type/quote refresh for untouched rows |
| `find_orphans.py` | Read-only report of database records absent from the current resolved CSV |
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
| `data/extracted_latest.csv` | Last candidate whose import, atomic promotion, and post-promotion byte verification all completed |
| `data/extracted_YYYYMMDDTHHMMSSZ_<run-id>.csv` | Immutable extractor snapshots; exclusive creation adds a numeric suffix on an exact collision |
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
| `tests/test_consensus_engine.py`, `test_consensus_axes.py` | Consensus, uncertainty, paired quote/source selection, reproduction axes, senior decisions, and LLM branches |
| `tests/test_csv_to_db.py`, `test_current_extractor_contract.py`, `test_study_identifiers.py` | Strict CSV contract, importer identity, refresh/re-key behavior, study numbers, lineage, URL fallbacks, and DOI-less originals |
| `tests/test_reproduction_judgement.py`, `test_reproduction_axes.py` | Browser/API/schema reproduction-axis contract, evidence, type conversion, and history rendering |
| `tests/test_integrity_fixes.py`, `test_validated_identity.py` | Concurrent completion guards, migration mapping/status, explicit validated duplicate merge, source constraints, and export/backfill integrity |
| `tests/test_skip_history.py` | Skip audit/escalation, modal behavior, draft preservation, server-issued failure stamps, and automatic-release isolation |
| `tests/test_sync_csv.py`, `test_extractor_maintenance.py` | Download/archive safety, snapshot thresholds, promotion verification, stage gates, fail-fast behavior, advisory locks, timeout, logs, and cleanup authorization |
| `tests/test_llm_validator.py`, `test_quote_source.py` | Structured LLM responses, canonical vocabularies, uncertainty, malformed/error retry, and quote-source normalization |
| `tests/test_audit_regressions.py`, `test_extractor_vocab.py`, `test_console_encoding.py`, `test_transform_outcomes.py` | Cross-file regression contracts, shared vocabulary/schema parity, Windows console safety, and Source Records transformation |
| `tests/__init__.py` | Empty package marker |

### Documentation and historical material

| File | Status |
| --- | --- |
| `docs/README.md` | This implementation-based project guide |
| `docs/PROJECT.md` | Detailed validator/admin and maintenance narrative updated with the current workflow |
| `docs/SOURCE_RECORDS.md` | Detailed Source Records design and operating notes |
| `docs/SETUP.md` | Deployment-focused setup and environment guide |
| `docs/ARCHITECTURE.md` | High-level runtime, transaction, maintenance, and database architecture |
| `docs/CSV_SCHEMA.md` | Extractor-oriented historical schema; Stage 4 describes an obsolete Flask/SQLite design |
| `docs/VALIDATION_DB_SCHEMA.md` | Current validation-table schema and migration behavior, including skip and automatic-failure audits |
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

The current working tree collects 509 tests across 22 test modules. They run without a live
PostgreSQL or Gemini service by mocking cursors and external calls.

Useful focused commands:

```bash
python -m pytest -q tests/test_consensus_engine.py
python -m pytest -q tests/test_csv_to_db.py
python -m pytest -q tests/test_llm_validator.py
python -m pytest -q tests/test_sync_csv.py
python -m pytest -q tests/test_skip_history.py
node --check docs/app.js
```

The current tests do not exercise FastAPI routes end to end against a real
PostgreSQL database, live Gemini/OpenAlex/GitHub/Google Sheets services, or a real
browser. Most transaction, migration, importer, export, source-transform, admin,
and frontend contracts are regression-tested with mocked cursors or source-level
checks. A passing 328-test suite is useful evidence, not a substitute for staging
the PostgreSQL schema and critical user journeys.

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
8. verify `unvalidated`, queue-slot counts, `submission_failure_releases`, and
   admin login after startup; and
9. verify an actual Gemini consensus call before accepting validator traffic.

Application startup executes `db_schema.sql` idempotently. This working tree adds
`submission_failure_releases`; deploy/restart the backend before relying on the
new browser recovery flow and confirm the table and its two indexes exist. The
default stamp TTL is safe for the current retry window; set
`SUBMISSION_FAILURE_STAMP_TTL_MINUTES` explicitly if operations require a
different value. Old frontends cannot forge a new automatic-failure event because
`/api/skip` rejects the legacy reason, while new frontends against an old backend
retain pending work because no server stamp is returned.

### Rolling deployment and `/api/skip`

This release adds `reason_code` to `/api/skip`. Both directions of version skew
are handled, so no downtime window or forced reload is required:

| Situation | Behaviour |
| --- | --- |
| Old page → new backend | `reason_code` is optional and defaults to `prefer_another`, the only intent the old skip dialog offered. The record is released normally. Rejecting it with 422 would have been destructive: the old page clears its local draft *before* calling `/api/skip`, so a failed release loses unsent work and leaves the record claimed. |
| New page → old backend | The old backend answers `{"skipped": true}` with no `reason_recorded`. The page then reports only "Record skipped." instead of claiming the reason, comment, or restricted-access routing was stored. The record is still released. |
| Reload timing | `index.html` is served with `Cache-Control: no-cache, must-revalidate`, and its `app.js` / `style.css` links carry a `?v=<content hash>`. A changed asset gets a new URL and is fetched on the next load; an unchanged one keeps its URL and stays cached. |

The legacy default cannot distort operations: `prefer_another` never requires a
comment and never counts toward the issue-based escalation that surfaces records
in the admin Skipped panel. Once no old page can still be open, the default may
be removed and `reason_code` made required again.

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

**The one that still matters:**

- **Validator sign-in does not prove mailbox ownership.** A handle plus the
  address on the account opens a session, and the email that follows is only a
  notice. Handles are public on the leaderboard, so anyone who guesses a
  validator's address can act as them. This is a deliberate choice recorded in
  `login()` and in PROJECT.md section 19, not an oversight; the fix is one
  endpoint away, since `auth_links` already implements single-use emailed links.

**Resolved, listed so old reports are not read as current:**

- No endpoint accepts `coder_id` from the caller; identity is a server-issued
  session in an `HttpOnly; Secure; SameSite=Lax` cookie, stored only as a digest.
- Sessions expire (validators 30 days, admins 12 hours) and are individually
  revocable. Logout, a password change, and admin deletion all revoke at once.
- Admin passwords are Argon2id hashes. The derived `sha256(password + suffix)`
  bearer token is gone, so two admins with the same password no longer share one.
- Cross-site state-changing requests are refused, and sign-in attempts are
  throttled per identifier and per client address.
- There is no fallback admin password: an empty `admins` table with no
  `ADMIN_PASSWORD` fails startup rather than seeding a known account.

**Not done:** multi-factor authentication for trusted administrators. Admin
authentication is a single factor, so a leaked password is the whole defence
gone — and a trusted admin can create and delete other admins.

### Data integrity and concurrency

- Ordinary and assigned submissions lock their claim and conditionally complete
  only an open row before points are awarded. Skip, senior rejection, stale-slot
  reaping, and automatic failure release use the same record-before-queue lock
  order to avoid double credit and request/reaper deadlocks.
- The API rejects contradictory type corrections and validates/canonicalizes
  reproduction axes. Consensus keeps each chosen quote paired with its own source.
- Admin **Resolve** returns a structured conflict when its validated identity
  collides. The administrator must explicitly merge the duplicate into the
  existing survivor; the action is stored in `validated_record_merges`, and a
  resolve race uses `DO NOTHING` rather than overwriting the survivor.
- The ordinary **Approve** path and senior automatic publication still use
  `ON CONFLICT DO UPDATE` for the same identity key. The explicit two-record merge
  confirmation currently protects Admin Resolve, not those publication paths;
  investigate an unexpected identity collision before approving it.
- Source Records axes are constrained and normalized before transformation.
  Unknown combinations are reported rather than silently published as internally
  contradictory raw values.
- These guarantees are covered mainly with mocked/source-level tests. Verify
  PostgreSQL constraints, trigger order, and concurrent requests in staging after
  schema changes.

### Import, migration, and synchronization

- Nightly maintenance stages the candidate separately, validates schema/vocabulary
  and resolved identities, blocks zero-resolved or excessive-removal snapshots,
  imports first, atomically promotes second, verifies exact promoted bytes, and
  records a run-scoped Part 1 completion marker. Orphan stages cannot run after an
  incomplete Part 1 or failed report.
- Same-day archives use UTC timestamp plus maintenance run ID, so later syncs do
  not erase earlier evidence. On ephemeral hosts those files still require a
  persistent volume if rollback history must survive pod replacement.
- Existing pair IDs refresh extractor-owned fields and metadata; re-keying uses
  `(work_id, original_rank)`. Study numbers, lineage, full-text provenance,
  OpenAlex identity, independent axes, and axis evidence all have destinations.
- Bootstrap checks importer exit status. Blank axes bind as SQL `NULL` in both the
  importer and `update_outcomes.py`. Legacy migration aborts malformed JSON, maps
  validators by handle, and routes fully occupied migrated records to admin review.
- OpenAlex backfill synchronizes both `unvalidated` and existing `validated` rows
  in one transaction.
- The extractor maintenance pipeline has a PostgreSQL advisory lock and child
  timeouts across workers/pods. Other in-process APScheduler jobs are still
  instantiated per web worker, so one worker remains the recommended deployment
  unless those auxiliary schedules are externalized.
- Running `csv_to_db.py` directly validates one file but does not compare it with
  the previous snapshot. Use `sync_csv.py` or `extractor_maintenance.py` for the
  guarded recurring sync/report workflow; deletion remains a separate manual stage.

### Coverage and operations

- There is no live FastAPI+PostgreSQL integration suite or first-party browser
  automation. External-service and most database behavior is mocked or checked at
  the SQL/source-contract level.
- Requirements use lower bounds rather than a lock file, so future installs can
  resolve materially different dependency versions.
- `GET /api/health` does not check the database, scheduler, Gemini, Resend, GitHub,
  Google Sheets, or successful import freshness.
- Dated material under `docs/superpowers/`, `STAGE4_VALIDATE.md`, and parts of
  `CSV_SCHEMA.md` describe obsolete SQLite/Flask or pre-migration behavior. Read
  this implementation guide and current code before copying historical commands.

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

Check startup output from the `csv_to_db.py` subprocess. Bootstrap uses
`check=True`, so a failed seed import should fail application startup rather than
silently serving an empty deployment. The bundled archived snapshot is replayed
with `--allow-legacy-schema`; normal current CSV imports remain strict. If there
is no subprocess output, first confirm `data/extracted_latest.csv` exists. Run the
importer manually with `--dry-run`, inspect required headers, vocabulary, resolved
identity, and lineage, then run the real import and query:

```sql
SELECT COUNT(*) FROM unvalidated;
SELECT validator_slot, COUNT(*) FROM validation_queue GROUP BY validator_slot;
```

For each clean import, the three slot counts should match imported record count.

### CSV import rolls back

The whole run is transactional. Read the first importer/schema error rather than
the final rollback line. Current pre-write checks reject missing Stage-3 columns,
unknown vocabulary, blank or duplicate resolved `pair_id`s, missing numeric
replication work IDs, unstable DOI-less original identity, and ambiguous
`(work_id, original_rank)` re-keys. Legacy reproduction spellings are normalized,
and blank axes bind as SQL `NULL`. Dry-run validates the file contract and
identity but does not execute PostgreSQL constraints or triggers.

### A recurring import does not update a record

Current imports refresh extractor-owned raw fields and metadata for an existing
`pair_id` while preserving human summaries and `final_*` decisions. If nothing
changed, confirm that the row is resolved, the intended `pair_id` is present, and
the sync used the expected branch/file. If `pair_id` changed, inspect `work_id`
and `original_rank`; the importer re-keys only one unambiguous source-slot match.
Do not delete validated work merely to force a reimport.

### “My Judgements” shows no reproduction axes

The list and detail APIs now return independent computation/robustness axes; detail
also returns each axis's extracted, corrected, and final quote/source evidence.
Confirm the record's effective type is `reproduction`, restart the backend so
`db_schema.sql` has added the axis columns, and hard-refresh the frontend to avoid
an older cached `docs/app.js`. A genuinely uncoded axis remains blank.

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
or invalid values before refusing to write. For a reproduction, correct the
underlying `source_records.outcome_computation` or `outcome_robustness` value to a
codebook category; there is no `reproduction_outcome_map`. For a replication,
add or repair a deliberate `outcome_alias` row, review the resulting vocabulary,
and rerun.

### OpenAlex IDs or references are missing

Use `backfill_oa_work_ids.py --dry-run` for work IDs and inspect
`oa_ref_cache.json` for export-reference errors. DOI corrections deliberately
clear stale work IDs. The real backfill updates `unvalidated` and synchronizes
matching missing IDs into existing `validated` rows in the same transaction; if
an export remains blank, verify that the validated DOI still matches the paper
identity used for the backfill.

### Manual destructive cleanup

Run `find_orphans.py` first, then `cleanup_orphans.py` without `--apply`. The
cleanup utility preserves every admin-excluded row and any row with at least one
submitted judgement. It also preserves every fully validated row, checked both by
status and by presence in `validated`. An assignment-only
`validation_inprogress` status, skips, admin notes/checks, and restricted-access
state do not protect an otherwise untouched orphan; their dependent operational
rows are removed in the same cleanup transaction. Cleanup is never launched by
the nightly or routine full operation; an admin must start the dedicated cleanup
stage and confirm it. Avoid
`db_reset.py` unless the entire validation dataset and validator accounts are
intended to be truncated and a verified backup exists.
