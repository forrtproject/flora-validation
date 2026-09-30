# FLoRA Validation — Architecture Overview

## System Components

```text
flora-validation/
├── app.py               FastAPI server — all HTTP endpoints
├── consensus_engine.py  Decision logic: agree → validate; disagree → LLM tiebreak
├── llm_validator.py     Gemini Flash validator (sanity check & tiebreaker)
├── csv_to_db.py         Imports extracted.csv rows into the database
├── sync_csv.py          GitHub sync stage — downloads & imports latest CSV
├── extractor_maintenance.py  Locked sync → report → retire runner
├── db_schema.sql        DDL for fresh deployments (idempotent)
├── db_migrate.py        Migrates old pairs/coders/judgements schema to new schema
├── data/                extracted_latest.csv + immutable UTC/run-ID archives
│                        (EXTRACTOR_DATA_DIR; each also kept in extractor_snapshots)
├── tests/               pytest test suite
│   ├── test_llm_validator.py
│   ├── test_consensus_engine.py
│   └── test_sync_csv.py
└── docs/                Frontend static files + documentation
```

---

## Request Flow

### Login (`POST /api/login`)

```text
Client → handle + the email address on that account
       → validators table lookup / insert
       → opens a session, sets an HttpOnly cookie
       → returns handle, onboarding/tier profile
```

Private endpoints now derive identity from that cookie; none accepts a
`coder_id` from the caller. Sign-in still does **not** challenge mailbox
ownership — the email that follows is a notice — so anyone who knows a public
handle and guesses the address can obtain a session. That remaining gap is a
deliberate choice recorded in
[PROJECT.md §19](PROJECT.md#19-deferred-security-work).

### Get next pairs (`POST /api/next-pairs`)

```text
Client → session cookie + mode/count/buffer options
       → unvalidated JOIN validation_queue JOIN record_metadata
       → find record not yet assigned to this validator, with a free human slot
       → assign slot (is_shown=TRUE, validator_id=X)
       → returns pair data + OA URL enrichment
```

### Submit judgment (`POST /api/judge`)

```text
Client → session cookie + record_id + type_check + original_check + outcome_check + corrected_*
       → update validation_queue (is_validated=TRUE, store checks)
       → write JSONB summary → unvalidated.validator_1 / validator_2
       → update validators.total_points / total_judgements
       → call consensus_engine.evaluate_consensus()
       → returns points_earned, total_points, rank
```

### Skip a record (`POST /api/skip`)

```text
Client → session cookie + record_id + reason_code + optional comment
       → lock the unvalidated record row so concurrent releases serialize
       → conditionally release the validator's unfinished human slot
       → append one validation_skips event in the same transaction
       → increment validators.skipped_count (no points and no consensus vote)
       → return the record to the pool when no other slot is active or complete
       → route inaccessible records to the existing Restricted-access queue
```

The admin **Skipped** filter is derived from the append-only history. A record is
listed after more than five distinct validators have skipped it for any reason, or
after at least two distinct validators report `eligibility_unclear`/`data_quality`.
Repeated skips by one validator remain visible in the audit history but count once
toward either threshold.

Automatic save recovery does **not** use `/api/skip`. Every durable background
submission gets a browser-generated `submission_id`. If `/api/judge` observes a
failure before commit while that validator still owns the slot, the server writes
a `submission_failure_releases` row and returns a short-lived random capability
stamp. PostgreSQL stores only the stamp's SHA-256 digest. The browser may consume
the stamp once at `POST /api/submission-failures/release`; the token is bound to the
exact submission, queue slot, record, and validator. Recovery states are
`save_failed`, `released`, `slot_closed`, and `expired`; the two-minute stale-slot
reaper also materialises elapsed stamp TTLs as `expired` for accurate auditing.

An automatic release writes no `validation_skips` event and does not increment
`skipped_count`, because it was not a validator choice. Network-only failures and
request-validation failures outside `/api/judge` receive no stamp, so their local
pending judgement remains intact. The browser removes pending data only after a
successful save, a consumed `released` stamp, or authoritative `slot_closed` state.

The same ordering protects a validator who chooses **Skip record** manually: the
browser retains the in-memory answers and localStorage draft until `POST /api/skip`
confirms release. Network/server failure leaves both the assignment and draft in
place for retry; only a confirmed release clears them.

---

## Consensus Engine

`consensus_engine.evaluate_consensus(cur, record_id)` is called after every human
submission. It reads both completed human rows from `validation_queue` and applies
the following decision tree:

| Condition | LLM called? | Outcome |
| --- | --- | --- |
| Checks agree + corrections agree, senior involved | Yes (sanity) | `validated` — humans always win |
| Checks agree + corrections agree, no senior | Yes (sanity) | `consensus_reached` — admin approval |
| Checks agree + corrections differ | No | `need_review` |
| Checks differ + LLM matches H1 | Yes (tiebreak) | `validated` with H1 verdict |
| Checks differ + LLM matches H2 | Yes (tiebreak) | `validated` with H2 verdict |
| Checks differ + 3-way split | Yes (tiebreak) | `need_review` |
| Checks differ + LLM error | Yes (tiebreak) | `need_review` |

When a winner is selected, the engine sets the coherent `unvalidated.final_*` shape,
including separate reproduction computation/robustness axes and their evidence.
Senior consensus may write `validated` immediately; other winners stop at
`consensus_reached` for admin approval.

---

## LLM Validator

`llm_validator.run_llm_validation(record, context)` calls Gemini Flash
(`gemini-3.1-flash-lite`) via the `google-genai` SDK.

- Prompts the model with the abstract + extracted metadata
- Default behaviour: "correct" when uncertain (conservative)
- Returns structured JSON with `type_check`, `original_check`, `outcome_check`,
  `corrected_*` fields, and `notes`
- Retries once on transient failure; returns `{"error": "..."}` on persistent failure

The LLM is an advisory sanity check when humans agree and a tiebreaker when they do
not. It does not receive or award validator points.

---

## Nightly Extractor Maintenance

`extractor_maintenance.run_scheduled` is scheduled via APScheduler at 2:00 AM UTC
every night (started in `app.py`). Every `CronTrigger` declares UTC explicitly.
The scheduled operation runs its stages as subprocesses under one advisory lock:
sync, OpenAlex enrichment, the read-only orphan report, then the retire stage.
Enrichment failure is recorded as a warning; the sync and report gates remain
fail-fast. Records leave the database only through the retire stage, which acts
on what flora-extractor states it withdrew, never on mere absence from the CSV.
(An orphan-cleanup stage that deleted by absence was removed on 2026-09-30.)

Before import or promotion, `sync_csv.py` counts the unique resolved `pair_id`s
the candidate adds and drops against the snapshot of the last import. The counts
are information only: a large drop never stops a run, because nothing is deleted
by absence (a removal limit, `EXTRACTOR_MAX_REMOVAL_PERCENT`, did this until
2026-10-01). Zero resolved IDs is still an extractor error that stops the run.

1. Resolves `GITHUB_BRANCH` to a commit and fetches `extracted.csv` at that commit
   from `GITHUB_REPO` (`source_commit` in the safety report)
2. Exclusively creates an immutable archive such as
   `data/extracted_20260901T140532Z_7b42ecc5.csv`; an exact collision receives
   `_2`, `_3`, and so on rather than overwriting an earlier download
3. Imports a temporary candidate with `csv_to_db.run_import()`, which inserts new
   rows, refreshes existing metadata, and re-keys corrected pairs by
   `(work_id, original_rank)`
4. Promotes the candidate to `data/extracted_latest.csv` only after import succeeds,
   compares the promoted bytes with the downloaded candidate, and then commits
   Part 1 completion for the same maintenance `run_id`
5. Stores the archive in the database (`extractor_snapshots`, gzip, keyed by
   sha256), together with the baseline it was compared against, and prunes
   copies beyond `EXTRACTOR_SNAPSHOTS_KEPT` (10)
6. For the scheduled full run, executes `backfill_oa_work_ids.py` while the same
   cross-pod process lock remains held
7. Runs the read-only `find_orphans.py` report against the **archive** from step 2
   rather than the mutable `extracted_latest.csv`, passing `--expect-sha256` so the
   child verifies the bytes it reads. It prints counts by status, lists only the
   records still `unvalidated`, and hands the same summary to the admin panel
8. Runs the retire stage (below)
9. Appends every stage's output and each `SUCCESS`/`FAILED`/`SKIPPED` status to
   `logs/extractor_maintenance.log` (or `EXTRACTOR_MAINTENANCE_LOG`) and stdout;
   the complete log of each run is kept in `extractor_maintenance_runs.log_text`

The pipeline is fail-fast. If CSV download/import/promotion fails, the orphan
report and the retire stage are recorded as `SKIPPED`. The job polls nightly; it
is not triggered immediately by an extractor repository upload.

**Retire stage.** `retire_superseded` (`EXTRACTOR_AUTO_RETIRE`, on by default)
reads flora-extractor's `data/retired_pairs.csv` at the commit Part 1 imported and
runs `csv_to_db.py --retire --apply` against the archived snapshot. It acts only on
pair ids the manifest names and the imported CSV no longer carries. Records nobody
has touched are archived whole in `retired_records`, then deleted. Touched records
are flagged in `admin_notes`, and rejected ones are left alone. It is idempotent,
because a retired pair id is absent the next time. The child does not take the
advisory lock, which the parent's session holds. Instead it proves, through
`extractor_maintenance_runs`, that it belongs to the live run whose Parts 1 and 2
verified this snapshot at this commit (`csv_to_db.require_maintenance_gate`). A
plan over `EXTRACTOR_MAX_RETIRE_PERCENT` (15) of `unvalidated` retires nothing
(stage `BLOCKED`, warning `retire_cap_exceeded`). Any retire outcome is a
warning, because the import stands. Manual: `python csv_to_db.py --input
data/extracted_latest.csv --retire github` (dry run), then `--apply
--expect-retire N`.

Manual one-stage runs use the same durable completion gate: a standalone report
(`find`) requires the newest sync attempt to prove import plus verified
promotion. Finding "the newest sync attempt" filters on `requested_stage`, not on
`stage_status` alone: a manual `find` inherits the gating sync's
`"sync_csv": "SUCCESS"` into its own `stage_status` for the audit trail, so a
search by that key alone would return the `find` run and mistake it for the sync.
A newer incomplete attempt always blocks downstream work; the code never falls
back to an older successful baseline.

The digest is what makes the gate sound under pod replacement. A run ID proves
that some pod completed a sync; it says nothing about which bytes the pod running
a later stage can see. Every stage therefore resolves the recorded archive and
verifies its sha256 before reading it. A pod whose `EXTRACTOR_DATA_DIR` lost the
archive restores it from `extractor_snapshots` first; one that holds a file of that
name with different bytes, or finds no copy, is blocked
(`snapshot_archive_mismatch` / `snapshot_archive_unavailable`). A restore never
overwrites a file that is already there.

Part 1 is told which baseline to count against: the orchestrator passes the
previous run's recorded archive, and restores it from the database first if the
directory has lost it. Failing that, `sync_csv.py` looks for the same bytes in any
other archive on the host, then in the newest extractor commits touching
`data/extracted.csv` up to the archive's timestamp. Only bytes matching the
recorded digest are accepted. Without them the run still imports; it only goes
without the added/dropped counts (warning `baseline_unavailable`). (When a
removal limit still depended on this, a Railway redeploy on 2026-09-12 blocked
every nightly run until 2026-09-30.)

To run the complete routine manually: `python extractor_maintenance.py`; one
stage: `--stage sync` or `--stage find`.

Every scheduled/admin run stores its status, stage results and durations,
snapshot counts, orphan and retire summaries, warnings, and complete output in
`extractor_maintenance_runs`. A PostgreSQL session advisory lock permits only one
live operation across Kubernetes workers; the partial unique index separately
prevents duplicate history reservations. The admin **Extractor Pipeline** tab
explains the three steps, offers one **Run full sync** button (single stages
remain a command-line option), and shows the last seven days as run cards
(counts, a step timeline, plain-language notes) with the complete log grouped by
step.

An admin HTTP 202 response only confirms that a `queued` row committed. A
ten-second scheduler poll claims that row; it does not rely on a FastAPI
background task surviving response completion. When a pod disappears PostgreSQL
releases its session lock, and another pod requeues the same run; repeating it is
safe. A queued run of the removed `cleanup` stage is closed as failed instead of
blocking later runs.

---

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `GITHUB_REPO` | No | Source repo for CSV (default: `forrtproject/flora-extractor`) |
| `GITHUB_BRANCH` | No | Branch name (default: `main`) |
| `GITHUB_TOKEN` | No | Personal access token for private repos |
| `ROUTING_RELEASE_ID` | No | Filter-engine release stored with each nightly-imported row |
| `EXTRACTOR_DATA_DIR` | No | Snapshot working directory (default `data/`); need not survive a redeploy, the database keeps a copy |
| `EXTRACTOR_SNAPSHOTS_KEPT` | No | Imported snapshots kept in `extractor_snapshots` (default `10`, minimum 2) |
| `EXTRACTOR_AUTO_RETIRE` | No | Run the retire stage after each full sync (default on; `0`/`off` disables) |
| `EXTRACTOR_MAX_RETIRE_PERCENT` | No | Cap on one automatic retire, % of `unvalidated` (default `15`) |
| `EXTRACTOR_MAINTENANCE_LOG` | No | Combined pipeline log path (default `logs/extractor_maintenance.log`) |
| `EXTRACTOR_STAGE_TIMEOUT_SECONDS` | No | Per-child timeout; default 7200 seconds |
| `EXTRACTOR_LOCK_WAIT_SECONDS` | No | Advisory-lock retry for a reserved run; default 15 seconds |
| `SUBMISSION_FAILURE_STAMP_TTL_MINUTES` | No | One-time automatic-release capability lifetime; default 30 minutes |
| `TRUSTED_PROXY_HOPS` | No | Reverse proxies in front of this app; default `1`. Decides how far from the right of `X-Forwarded-For` the client address is read — see README "Which address the server believes" |
| `ADMIN_PASSWORD` | First run only | Password for the bootstrap administrator. There is **no** fallback: with no administrator in the database and this unset, startup fails rather than seeding a known password |

---

## Database Quick Reference

See [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md) for full DDL and JSONB shapes.

| Table | Key columns |
| --- | --- |
| `validators` | `id`, `handle`, `vote_score`, `total_points`, `validator_tier` |
| `unvalidated` | `record_id`, `pair_id`, `validation_status`, `validator_1/2` JSONB, `llm_validator` JSONB |
| `validation_queue` | `queue_id`, `record_id`, `validator_slot`, `is_validated`, all check fields |
| `validation_skips` | application-append-only `record_id`/validator/reason/comment/timestamp history; removed only with a retired record (archived in `retired_records`) |
| `submission_failure_releases` | server-observed save-failure audit, hashed one-time capability, explicit recovery state; never counted as a validator skip |
| `validated` | `record_id`, study_r/title_r, study_o/title_o, and final DOI/outcome/type values |
| `validated_record_merges` | explicit duplicate A→B audit link, admin, timestamp, resolution snapshot |
| `record_metadata` | `record_id`, provenance + extraction metadata |
| `extractor_maintenance_runs` | trigger/stage/status, safety report JSON, full log, timestamps |
| `extractor_snapshots` | every imported `extracted.csv` (gzip) by sha256, with its extractor commit |
| `retired_records` | records the retire stage removed, archived whole so they can be restored |

---

## Fresh Deployment

> **Security warning:** set a unique `ADMIN_PASSWORD` before first start. The current
> application otherwise seeds a known fallback. This is an interim operational
> requirement until the fail-closed bootstrap/session redesign in PROJECT.md §19.

```bash
# 1. Apply schema
python -c "import psycopg2; conn=psycopg2.connect(DATABASE_URL); conn.cursor().execute(open('db_schema.sql').read()); conn.commit()"

# 2. Import initial data
python csv_to_db.py --input data/extracted.csv

# 3. Start server (scheduler starts automatically)
uvicorn app:app --host 0.0.0.0 --port 8000
```

---

## Authentication Boundary

One server-derived principal per request. A login mints a random opaque token,
stores only its SHA-256 digest in `sessions`, and returns it in a
`Secure; HttpOnly; SameSite=Lax` cookie. Expiry and revocation are columns the
database evaluates at the moment of use, so logout, a password change and admin
deletion take effect immediately. Administrator passwords are Argon2id hashes
with no fallback. Cross-site state-changing requests are refused by middleware.
No private endpoint accepts a `coder_id` as authority, and no credential is kept
in browser storage.

What this boundary still does **not** do: prove that a validator holds the
mailbox they name at sign-in. Handle plus the account's email is sufficient, and
the email that follows is only a notice. See
[PROJECT.md §19](PROJECT.md#19-deferred-security-work).

## Migrating from Old Schema

```bash
python db_migrate.py   # copies pairs/coders/judgements → new tables
uvicorn app:app --host 0.0.0.0 --port 8000
```
