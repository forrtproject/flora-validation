# FLoRA Validation — complete technical reference

The human-validation layer of the [FORRT](https://forrt.org) FLoRA project. Replication
and reproduction studies extracted by an upstream pipeline arrive here as unverified
claims; volunteers check them in a gamified web app; a consensus engine and an LLM
resolve the results; validated records are exported back to the FLoRA dataset.

This document describes the system as it actually is. Where an older doc in this folder
disagrees, this one is current — see [§17](#17-documentation-map).

---

## Contents

1. [What this is](#1-what-this-is)
2. [Quick start](#2-quick-start)
3. [Stack and layout](#3-stack-and-layout)
4. [The record lifecycle](#4-the-record-lifecycle)
5. [The validator app](#5-the-validator-app)
6. [Scoring, tiers, and the leaderboard](#6-scoring-tiers-and-the-leaderboard)
7. [The consensus engine](#7-the-consensus-engine)
8. [The LLM validator](#8-the-llm-validator)
9. [The admin panel](#9-the-admin-panel)
10. [Database schema](#10-database-schema)
11. [API reference](#11-api-reference)
12. [Scheduled work](#12-scheduled-work)
13. [Scripts](#13-scripts)
14. [Local development](#14-local-development)
15. [Deployment](#15-deployment)
16. [Operations and troubleshooting](#16-operations-and-troubleshooting)
17. [Documentation map](#17-documentation-map)
18. [Design decisions and gotchas](#18-design-decisions-and-gotchas)
19. [Deferred security work](#19-deferred-security-work)

---

## 1. What this is

Upstream, a separate project (`forrtproject/flora-extractor`) searches the literature,
filters false positives, and extracts for each candidate paper: which original study it
replicates, and what the outcome was. That extraction is machine-made and imperfect.

This repo is the layer that makes it trustworthy:

- Volunteers ("validators") are shown one **paper pair** at a time — a replication and
  the original it claims to replicate — and answer three questions.
- **Two humans** must judge every record before anything is decided.
- A **consensus engine** compares their answers. An **LLM** checks every record once
  both humans are in (a senior reject aside), as sanity check or tiebreaker, but never
  overrules agreeing humans.
- Records that reach consensus are **approved by an admin** and written to `validated`.
- A nightly job exports `validated` to CSV, which the FLoRA R pipeline consumes.

There is also a **Source Records** tab that ingests the FLoRA entry sheets directly —
a parallel, simpler path documented separately in
[SOURCE_RECORDS.md](SOURCE_RECORDS.md).

### Live scale

| | |
|---|---|
| Records imported (`unvalidated`) | **1,818** |
| — awaiting validation | 1,228 |
| — in progress | 143 |
| — needs review | 140 |
| — consensus reached, awaiting admin | 15 |
| — validated | 239 |
| — rejected | 53 |
| Human judgements submitted | **1,034** (587 slot 1 + 447 slot 2) |
| Exported `validated` records | **238** |
| Registered validators | **34** (30 regular, 3 trusted, 1 senior) |
| Admins | 6 |
| Entry-sheet source records | **1,653** |

---

## 2. Quick start

```bash
git clone https://github.com/forrtproject/flora-validation
cd flora-validation

python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

cp .env.example .env            # then fill in DATABASE_URL and GEMINI_API_KEY

python -c "import app"          # creates/updates all tables via init_db()
uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. The admin panel is the same app — sign in with an admin
handle and password from the `admins` table.

To load data: `python csv_to_db.py --input data/extracted_latest.csv --dry-run` first,
then without `--dry-run`.

---

## 3. Stack and layout

**Backend** — FastAPI on Python 3.12, psycopg2 against PostgreSQL (Supabase in
production). Raw SQL throughout; there is no ORM. A `prisma/` folder and a `src/`
folder exist but are **empty vestiges of an abandoned Next.js scaffold** — ignore them.

**Frontend** — one HTML file, one JS file, one CSS file. No framework, no build step.
`docs/` is served directly by FastAPI's `StaticFiles` mount at `/`.

**LLM** — Google Gemini (`gemini-3.1-flash-lite`) via `google-genai`.

**Scheduler** — APScheduler, started in-process when `app.py` is imported.

```
app.py                     3,915 lines — every HTTP endpoint (59 of them)
consensus_engine.py          576     — the decision tree
llm_validator.py             454     — Gemini prompt and parsing
source_records_service.py    622     — Source Records data layer (no FastAPI types)
sync_sources.py              515     — entry-sheet ingest
transform_sources.py         397     — entry-sheet → FLoRA dataset
csv_to_db.py                 890     — extracted.csv → unvalidated
export_validated.py          288     — validated → CSV for the R pipeline
db_schema.sql              1,324     — all tables, idempotent
email_templates.py           174     — Resend transactional email

docs/index.html                687   — all five screens
docs/app.js                  7,177   — the whole frontend
docs/style.css               5,144

tests/                       509 tests, all passing
.github/workflows/           daily-export.yml · sync-sources.yml
```

### A caution about the database

The production database contains **27 tables after the current schema applies, but
only 18 belong to this repo.** The
nine `engine_*` tables (`engine_verdicts` alone has 40,052 rows) are written by a
different application sharing the same Postgres instance. Nothing here references them.
Do not assume the database is exclusively this app's — in particular, never run a
blanket `DROP` or a schema-wide migration.

---

## 4. The record lifecycle

```
  extracted.csv                      (upstream flora-extractor repo)
      │  csv_to_db.py — imports only rows where
      │  paper_type ∈ {replication, reproduction}
      │  AND link_method ∈ {10 current resolved methods}
      ▼
  unvalidated              ─────────────────────────┐
      │  + record_metadata (extraction provenance)   │
      │  + 3 × validation_queue (human_1, human_2, llm)
      │                                              │
      │  validators claim slots via /api/next-pairs  │
      ▼                                              │
  validation_queue                                   │
      │  each human submits 3 checks + corrections   │
      │  second submission triggers…                 │
      ▼                                              │
  consensus_engine.evaluate_consensus()              │
      │                                              │
      ├─ rejected ──────────────────────────────────►│
      ├─ need_review ───────────► admin resolves ────┤
      ├─ consensus_reached ─────► admin approves ────┤
      └─ validated (senior agreement, auto)          │
      ▼                                              │
  validated  ◄───────────────────────────────────────┘
      │  export_validated.py, nightly 04:00 UTC
      ▼
  data/validated_export.csv  →  committed  →  FLoRA R pipeline
```

### Import rules (`csv_to_db.py`)

Only **resolved** rows are imported. A row qualifies when:

- `paper_type` (or archived `filter_status`) is `replication` or `reproduction`, and
- `link_method` is one of the ten current resolved methods in
  `extractor_vocab.CURRENT_METHODS`.

Known unresolved and quarantined methods are skipped and counted. Unknown values,
missing current columns, duplicate `pair_id`s, and ambiguous source slots fail the
whole import.

Re-running refreshes `record_metadata`. An upstream correction that changes
`pair_id` is matched by stable `(work_id, original_rank)` and re-keyed; records with
validator activity are moved to `need_review`. A rejected record stays rejected, except
one whose original an admin removed, or withdrew before validation as the wrong
original, when the re-key ships a different original: it returns to `unvalidated` with
clean slots (see [Wrong paper, no replacement](#wrong-paper-no-replacement) and
[Withdrawing an entry before validation](#withdrawing-an-entry-before-validation)).

The boundary canonicalizes current replication labels and derives a reproduction's
flat outcome from its independently coded computation and robustness axes.

---

## 5. The validator app

Five screens, all in `docs/index.html`, switched by JS — there is no router.

| Screen | Purpose |
|---|---|
| `login-screen` | Handle + email, or handle + personal code |
| `update-screen` | "What's new" gate, shown when `updates.json` version is newer than the validator's `last_seen_update` |
| `onboarding-screen` | 5 calibration examples with known answers |
| `game-screen` | The main validation loop |
| `admin-screen` | The admin panel ([§9](#9-the-admin-panel)) |

### Login

No passwords, no JWT, no OAuth. A validator supplies a **handle** plus either an
**email** or a **four-part personal code**. The row in `validators` is created on first
use and looked up thereafter. Handles are `2–32` chars, `[A-Za-z0-9._-]`.

> **Known security debt:** this is currently identity lookup, not durable
> authentication of the mailbox. Login opens a server-side session and private
> endpoints read identity from it, but a handle plus the account email is all it
> takes to sign in.
> Email login proves knowledge of an address and handle, not control of the mailbox;
> personal codes are stored in plaintext and can be returned into browser storage.
> A caller who learns another validator's id can therefore act as that validator.
> This is intentionally documented, not fixed, in the current change set. See
> [§19](#19-deferred-security-work) before exposing the service to an untrusted network.

`/api/forgot-handle` emails a reminder via Resend, rate-limited per day by
`forgot_requests_today` / `forgot_requests_date`.

### Onboarding

Five pairs from `docs/onboarding.json` with expected answers. It calibrates judgement
before real records are served, and stamps `onboarded_at`.

### The validation loop

The client keeps a **prefetch buffer**. `/api/next-pairs` batch-claims up to 5 pairs:
the first is *active* (started, locked to this validator), the rest are buffered so the
next card renders instantly.

A validator answers three questions in one progressive form:

1. **Type check** — is this a replication / reproduction / neither?
2. **Original matching** — does the linked original study match? (correct / wrong / can't tell)
3. **Outcome coding** — is the outcome judgement right? (correct / mischaracterised / can't tell)

#### Multi-original replications

A replication paper may target several originals, and the extractor codes one row
per `(replication, original)` pair — so Gate II serves a validator exactly one of
them. That single row is not enough to answer the question: a paper replicating a
dominant original alongside secondary ones is legitimately coded against just the
dominant one, or against all of them, while a paper it never replicated is wrong.
All three look identical from one row.

Gate II therefore renders the **coded set**: every original coded for the same
`doi_r`, with the row under judgement marked (`_coded_originals` in `app.py`,
`_codedOriginalsBlock` in `docs/app.js`). The validator judges only the marked row
— correct if it is *among* the studies the paper set out to replicate.

The set is extractor output only. It deliberately carries no `validation_status`
and no judgement fields: a sibling's verdict would anchor the second validator,
which the two-human consensus design exists to prevent. Replications with a blank
`doi_r` are never grouped (`''` is not an identity) and simply show no set.

The match is case-insensitive (`lower(u.doi_r) = lower(%s)`, backed by a functional
index on `lower(doi_r)`). DOI names are case-insensitive by spec and `doi_r` is only
whitespace-stripped on import, so an exact match could split one paper's coded set
and hide originals — the failure this feature exists to prevent. This is scoped to
the lookup: the `UNIQUE (doi_r, study_r, title_r, …)` pair identity is still exact.

Rejected rows are excluded from the set. A duplicate resolved by an admin is
deliberately retained in `unvalidated` as `rejected` (see `validated_record_merges`)
and carries the *same* original as its survivor, so including it listed one original
twice and inflated the count; not-a-validation and wrong-original rejections are
dead pairs for the same reason. Filtering is not anchoring — the status gates the
`WHERE` clause and never reaches the payload.

The list is capped (`_CODED_ORIGINALS_LIMIT`) as a runaway guard rather than an
expected ceiling. The row under judgement is pinned into that window before any
other ordering, so truncation can never drop it and leave Gate II instructing the
validator to judge a highlight that isn't there; natural order is restored in
Python afterwards. `COUNT(*) OVER ()` runs before `LIMIT`, so a truncated list
still reports the true size of the set.

#### The paper review table

With `PAPER_REVIEW_ENABLED` on, a replication with a DOI in the normal pool opens in one
table when it has several coded originals, and "Add another original" opens the same table
from the one-record screen (`paper_review.py`, "Paper review" in `docs/app.js`).
Reproductions, the hard pool, assignments and onboarding keep the one-record screen; choosing
"Actually reproduction" in the table continues that paper one record at a time.

- `POST /api/paper-review/claim` reserves this validator's open records for the paper. It is
  safe to repeat. A sibling the validator cannot judge is listed for context, with no reason
  and no verdict. One validator can never hold both slots of a record (`_claim_free_slot`).
  It never waits for a row lock: a record locked elsewhere, for example by another validator's
  `/api/judge` during its Gemini call, is left out of that claim and lists as context.
- The claimed rows are buffered holds on the reaper's 45-minute lock, not started slots. Only
  the record the validator was served keeps its 5-day lock. `POST /api/paper-review/refresh`
  renews the holds every five minutes while the table is open and answers which records are
  still held, so an abandoned paper frees itself, and the served record later brings the
  validator back to their draft, where the claim takes the free rows again. Idle sign-out ends
  a forgotten tab.
- A hold can lapse with the table still open (a laptop asleep past 45 minutes). The refresh, and
  a check before submitting, notice. A row still free is retaken; a row another validator has
  now leaves the table, the validator is told by name, and every other answer stays. If no
  hold is left the answers stay as a draft and the validator is told.
- "Actually reproduction", and moving on while a claim is under way, put the rows the table
  reserved back at the front of the prefetch buffer, so they continue one at a time. In hard
  mode they lapse instead, like the normal buffer on a mode switch. A paper skip that fails
  partway takes the released rows out of the table, so what stays can still be submitted.
- The type is asked once. Each original gets its own "right paper?" answer. Originals share an
  outcome check only when their extracted outcome and quote both match; a row without a quote
  or with a replacement paper is never merged, and "outcome differs for this one" separates a
  row by hand.
- Submitting queues **one ordinary `/api/judge` per record**, so consensus, LLM checks, retries
  and recovery stay per record. `additional_checks.paper_review` records the outcome check each
  judgement answered and whether it was shared, for audit.
- Admins see the paper's coded originals on each entry, can approve those awaiting approval
  together, and can list shared answers with the "Shared outcome check" filter.
- The entry detail's `paper_differs_on` lists the fields (`url_r`, `abstract_r`) on which
  originals that are `consensus_reached` or `validated` differ, since each record publishes
  its own copy (`paper_review.paper_differs_on`). Admins see it as a warning, never a block;
  in Pending Approval it takes the second press like any other warning.

#### Wrong paper, no replacement

An original marked wrong with no replacement has no outcome to judge: `outcome_check` is
`not_applicable`, on both screens. A replacement has one, so it is asked for. Assignments and
onboarding still ask.

When both validators say so, the record waits in `need_review` with no final values
(`removal_proposed` in `consensus_engine.py`), listed under "Original to remove". The admin's
"Remove this original" rejects that record only, so nothing is published for it and its
siblings are untouched; correcting the original in the form and resolving remains possible.
It is refused while the entry has live added originals, which are published from it: the
banner says so, and the admin removes those first. If the extractor later ships a different
original at the same work and rank, the import sends the record back to `unvalidated` with
clean slots and archives its history in `retired_records`. The same holds for a pair an
admin withdrew before validation as the wrong original (scope `wrong_original`); a pair
withdrawn as a duplicate or with its whole article never comes back.

**After rolling this release back**, check these records. The previous release counts two
`not_applicable` answers as agreement and keeps the extracted original and outcome, so a
re-evaluated record can reach `consensus_reached` and be published with the original both
validators called wrong. The previous release also has no "Remove this original", so the
record stays on the admin to correct or reject through Mark as Resolved:

```sql
-- rollback-check: no-outcome judgements past review
SELECT u.record_id, u.validation_status, u.admin_checked, u.doi_r, u.doi_o, u.final_doi_o, u.title_o
FROM unvalidated u
WHERE u.validation_status IN ('consensus_reached', 'validated')
  AND EXISTS (SELECT 1 FROM validation_queue q
              WHERE q.record_id = u.record_id AND q.is_validated
                AND q.outcome_check = 'not_applicable');
```

Do not approve a `consensus_reached` row from this list. Flag it for review instead. A
`validated` row is fine only if an admin corrected its original (`final_doi_o` differs from
`doi_o`, or the title changed); otherwise send it back or reject it through Mark as Resolved.

An original the extractor *missed* is suggested with the judgement ("This study has
more than one original"). An admin adds it as a record of its own: at once on an entry
that is already validated and admin-checked, or, while reviewing an entry the LLM and
both validators have judged, drafted in its "Other originals" section and saved
together with it by Mark as Resolved (refused as a whole if one original is wrong).
See [VALIDATION_DB_SCHEMA.md, Other originals](VALIDATION_DB_SCHEMA.md).

Any answer can carry a correction: `corrected_doi_o`, `corrected_title_o`,
`corrected_outcome`, `corrected_type`, `corrected_title_r`, `corrected_url_r`,
`corrected_outcome_quote`, `corrected_abstract`. A validator can additionally supply
`doi_r_published` ("published DOI" button next to "suggest link") — for preprint
replications, the DOI of the published article, if it is available. It is net-new
data (no extracted counterpart) and counts as a correction in consensus, so a
one-sided or conflicting suggestion routes to admin review like a link suggestion.
Published DOIs are normalised at every entry point (resolver-link prefix and `doi:`
stripped; consensus compares case-insensitively), so pasting
`https://doi.org/10.1/X` vs typing `10.1/x` is agreement, not a conflict.

**"Can't tell" is not a hard vote.** It is recorded in `additional_checks` as
`was_unsure_original` / `was_unsure_outcome`, and the consensus engine routes any record
with an unsure answer straight to `need_review` rather than treating it as `incorrect`.

### Submit guards

`guardedSubmit()` runs a gauntlet before a judgement is accepted: unsaved title /
paper-suggestion / quote / abstract edits block with a shake-and-hint; the abstract
must be non-empty in normal mode; a sub-17-second submission gets an "are you sure"
dialog. In the review table that is 17 seconds per original, or 17 for a paper rejected as
a whole.

**Quote-in-abstract gate** (added Aug 2026): at submission the effective outcome quote
(the validator's edit, else the extracted one) is fuzzily matched against the
effective abstract. Matching mirrors the backend's normalisation (lowercase,
alphanumerics only) so case/punctuation/whitespace never mismatch, then falls back to
a best-window edit distance allowing ≤15% of the quote length — tolerant of
OCR/parsing fixes, still catching quotes that aren't there. Quotes spliced with an
ellipsis ("A … B") are checked fragment by fragment, so skipping non-adjacent
passages never counts against the match. A miss does **not** warn
or block the validator: the submission is silently stamped with
`additional_checks.quote_not_in_abstract`, the consensus engine routes the record to
`need_review` (see [§7](#7-the-consensus-engine) pre-checks — this also stops the
senior auto-validate shortcut), and the admin detail screen shows a banner naming who
triggered the flag, with the choice: legitimate full-text quote → set *Outcome Quote
Source* to "Full text", or mis-copied quote → fix it before resolving. The check is
skipped in hard mode, for assignments, for "not a replication" judgements, for records
already marked `out_quote_source = 'full_text'`, and when there is no quote or
abstract to compare. Server-side classification (`quote_source_for` in
`consensus_engine.py`) is unchanged and still auto-labels the published source.

The admin form shows that classification as a suggestion rather than a choice:
with no stored source, *Outcome Quote Source* selects what `quote_source_for` would
say (`suggestQuoteSource` in `docs/app.js` mirrors it, unlike the fuzzy check above),
marks it "(suggested)" and follows edits to the quote and abstract until the admin
picks a source. A suggestion left alone is sent as null, so the server detects it and
`out_quote_source_by` stays empty. A stored source stays selected: a stored `abstract`
or `full_text` the quote now contradicts gets a hint with a one-click way to the
suggestion, and a section name is left alone. The resolve form's reproduction axis
sources are not detected on save, so they get no suggestion.

The form sends a stored source back on every save, so that is not a choice either.
`admin_quote_source` (`consensus_engine.py`) compares normalised values: a different
source is used and stamped with the admin's handle; the stored one stays, with whatever
`out_quote_source_by` already holds, an earlier admin's handle or nothing. It is not
re-detected, which would replace a value such as `discussion` with a guess. Stamps
written before this rule cannot be told from real choices, so they stay.

An empty `out_quote_source_by` therefore does not mean nobody looked at the source, so
`backfill_quote_source.py --recompute-all` refreshes only computed values (`abstract`
and `full_text`, in any spelling `normalize_quote_source` maps to those) and never
replaces a section name or compound value such as `abstract|discussion`. A stamped row
is skipped as ever, and the default run fills only blanks.

### Modes

- **Normal mode** — records with an abstract
- **Hard mode** — records with no abstract, where the validator must open the paper

A validator's active pair is scoped to its mode, so switching modes parks the other
pair rather than dragging it along.

### Restricted access

A hard-mode validator who cannot open the article reports it. The record is pulled out
of circulation (`restricted_access = TRUE`) until an admin assigns it to someone with
access.

### Assignments

Admins can assign a specific record to a specific validator. Assignments appear in a
dedicated banner and are worth **double points**. An assignment takes the record's row
lock, so it cannot cross an admin's decision. A decided record (validated or rejected)
cannot be assigned, and an assignment on one answers 409: the validator is told their
answer was not saved and no points were added, and the screen moves on. A submitted
assignment frees any slot still held on the record, and an assignment that rejects it
takes its published row and added originals with it, like every other rejection.

### A decided record stays decided

An admin's decision (approve, resolve, reject, merge, remove an original, withdraw) closes
the record's open assignment and frees its unanswered slots. `/api/judge`, `/api/senior-reject`
and `evaluate_consensus` refuse a record in `validated` or `rejected`
(`consensus_engine.DECIDED_STATUSES`), so a late submission cannot flip it or overwrite its
final values; the browser's queue closes that item and says the record was already closed.
The test is the status, not `admin_checked`: a validated record the import re-keyed to
`need_review` keeps `admin_checked` and is open again, so its held slot and the tiebreaker
retry still work.

### Messages

A two-way inbox between validators and admins, threaded (`validator_messages`, 72 rows
live). Admins get a badge count; validators get an inbox icon.

---

## 6. Scoring, tiers, and the leaderboard

### Points

```python
points = validator.vote_score
       + 2  if original_check == "correct"
       + 2  if outcome_check  == "correct"
       + 1  if validator_notes is non-empty
```

A paper answered in the review table is scored as one piece of work: `vote_score` and the note
bonus are paid once per paper, each original answered pays 2, and each outcome check answered
pays 2, whatever the answer, so originals sharing a check share those 2 points (`paper_review.points`).

Assignment submissions are worth **double**. Skips score zero and increment
`skipped_count`. The validator must select a reason; eligibility, data-quality, and
"other" skips also require a short comment. Every successful slot release appends a
row to `validation_skips`. A retried request after the slot has already been released
returns HTTP 409 and cannot inflate the history or validator total.

Automatic recovery after a failed background save is not a Skip. The server can
mint a short-lived, one-time capability only after `/api/judge` fails before
commit and confirms that the validator still owns the queue slot. The browser
uses that stamp at `/api/submission-failures/release`; network-only failures have
no stamp and stay pending. Automatic releases add neither `validation_skips` rows
nor `skipped_count` and are visible to admins in a separate collapsed audit card.

`vote_score` is per-validator and stored on the row. The LLM's notional weight is 15
(`_LLM_VOTE_SCORE` in `llm_validator.py`).

### Tiers (`validators.validator_tier`)

| Tier | Name | Effect |
|---:|---|---|
| 0 | Regular | Standard validator (30 live) |
| 1 | Trusted | Agreements weighted higher in admin stats (3 live) |
| 2 | Senior | **Agreement auto-validates** without admin review; can use senior-reject (1 live) |

Senior powers are enforced server-side — `/api/senior-reject` rejects any caller with
`validator_tier < 2`.

**Senior reject is authoritative.** Once a senior has used it, neither the second human
nor the LLM can overturn it; the record becomes `rejected` regardless of submission
order. It is detected by a `senior_reject` marker in `additional_checks`, distinct from
an ordinary `not_validation` answer.

### Serving priority

`serving_config` lets admins bias which records are served — currently: enabled, favour
`outcome = failed`, years 2011–2021, at a 70% share. `/api/admin/serving-config/preview`
shows how many records a proposed config would match before saving it.

### Leaderboard

Top 10 by `total_points`, recalculated after each submission, shown in a sidebar.

---

## 7. The consensus engine

`consensus_engine.evaluate_consensus(cur, record_id)` runs after **every** human
submission and returns immediately if fewer than two humans have completed.

### Pre-checks, in order

1. **Senior reject present?** → `rejected`. Nothing else is consulted, not even the LLM.
2. **Either human unsure?** (`was_unsure_original` / `was_unsure_outcome`) → `need_review`.
3. **Quote flagged?** (`quote_not_in_abstract`, stamped by the frontend quote gate) →
   `need_review`. This also stops the senior auto-validate shortcut: a suspect quote
   never reaches the export table unreviewed.

Pre-checks 2 and 3 still run the LLM sanity check and store its answer for the admin;
whatever it says, the record goes to `need_review`.

### The decision tree

```
checks agree AND corrections agree
├── both said "not_validation"
│     └── LLM sanity check
│           ├── LLM agrees → rejected
│           └── LLM disagrees → need_review        (LLM thinks it IS a replication)
└── otherwise
      └── LLM sanity check (advisory only)
            ├── a senior was involved → validated        (auto, no admin step)
            └── otherwise            → consensus_reached (admin must approve)

checks agree AND corrections differ
└── LLM sanity check (stored for the admin only)
      └── need_review                              (humans already agree on the
                                                    judgement, they just wrote
                                                    different fixes)

checks differ
└── LLM tiebreaker
      ├── LLM errored          → need_review (is_tiebreaker = TRUE)
      ├── matches exactly one human
      │     ├── that human said "not_validation" → need_review
      │     └── otherwise → consensus_reached with that human's corrections
      └── matches both or neither → need_review
```

**Humans always win.** The LLM in branch 1 is advisory — its verdict is stored in
`unvalidated.llm_validator` for admin visibility but does not change the outcome. In
branch 2 and pre-checks 2 and 3 it is only stored: an agreeing or errored LLM leaves
the record at `need_review` all the same, and an errored call there does not replace a
clean answer the record already holds.

### Resolving final values

When a winner is chosen, `_resolve_final` builds the authoritative record:

- Each field takes the winner's correction, falling back to the extracted value.
- **`outcome_quote`**: if validators edited it, the **longest** edit wins — most context,
  and deterministic.
- **`abstract_r`**: same rule, longest edit wins.
- **`out_quote_source`**: recomputed against the *final* (possibly corrected) abstract.
  If the normalised quote is contained in the normalised abstract → `abstract`,
  otherwise → `full_text`. If validators did not touch the quote, the existing source is
  kept without re-checking.

Normalisation for these comparisons lowercases and strips everything non-alphanumeric,
so light formatting edits do not register as disagreements.

---

### Data-quality flags

Every terminal branch of `evaluate_consensus` routes through `_update_status`, and
that is where `record_checks.check_record()` runs — once all three judges are done
with a record. The flags are stored on `unvalidated.quality_flags` (JSONB) with
`quality_checked_at`, and shown in red at the top of the admin review panel, with a
count on the entry row and a **Data quality** filter in the admin list.

They are **advisory**. Nothing about where a record lands depends on them: a
flagged record still validates, is still approved, is still published. The banner
says the record *may need to be excluded* and an admin decides. That was a
deliberate choice — several of these checks have real false-positive rates, and an
automatic exclusion would be harder to notice than a red flag.

Checks are run against the values that would actually be published (`final`
overriding the raw record), so a correction a validator already made is never
reported as a problem.

#### What runs here, and what stays on cron

This answers issue #139. A check belongs in the final validation step if it can be
decided **from one row** and its answer **never changes on its own**:

| Runs at consensus (`record_checks.py`) | Stays on the pipeline's cron (`validate_flora*.py`) |
| --- | --- |
| Required fields present | Conflicting references for one DOI (cross-row) |
| DOI format `10.NNNN/…` | Duplicate records, and a paper pair under both types or entered both ways round (cross-row) |
| Literal `NA`/`N/A` values | Retracted papers and expressions of concern (time-varying) |
| URLs start with `http` | DOIs registered at doi.org (time-varying, network) |
| Type and outcome vocabularies | Links reachable (time-varying, network) |
| Year range, and `year_r >= year_o` | A DOI for an original without one (network) |

Retractions and link rot are the important half of that split: a paper can be
retracted, and a link can rot, long after a record was validated. Checking them
once at consensus would be worse than not checking — it would read as a clean bill
of health that silently goes stale.

The right-hand column runs in two places. The pipeline runs it on every build and
writes its report files beside `flora.csv`, as before. The website's scheduler also
runs it over the stored dataset (`entry_checks.py`): the structural checks whenever
the stored dataset changes (a pipeline run stored a snapshot, or a correction was
saved), the network checks once a day from 04:00 UTC within 45 minutes. Each
finding is kept in `flora_check_findings`, by FLoRA ID, until the value it flagged
changes; `flora_check_runs` records each run. Both only report: neither changes a
record. The FLoRA tab shows the findings: Problems and Possible problems filters,
and a Check findings section in each record, where a person can mark one "Not a
problem" with a comment. See [SOURCE_RECORDS.md §9.5](SOURCE_RECORDS.md#95-entry-checks-on-the-stored-dataset-entry_checkspy).

Two adaptations to this app's data model, without which the checks would be mostly
noise: a blank `doi_o` is legitimate for DOI-less originals and is only flagged when
there is no `oa_work_id_o` either; and years are parsed from the leading four digits
because `year_o`/`year_r` are TEXT holding `'2020'`, `'2020.0'` and `' 2020'`.

`backfill_quality_flags.py` applies the same checks to records that finished before
the checks existed. It never changes `validation_status` — flag, don't reopen — and
is safe to re-run, since flags are recomputed from current values each time.

## 8. The LLM validator

`llm_validator.run_llm_validation(record, context)` where context is `sanity_check` or
`tiebreaker`. Consensus calls it for every record once both humans have submitted,
except a senior reject: `tiebreaker` when the checks differ, `sanity_check` otherwise.

- Model: **`gemini-3.1-flash-lite`**
- Input: the replication abstract plus the extracted metadata — **nothing else**. The
  prompt explicitly instructs the model to use no external knowledge.
- Output: JSON with `type_check`, `original_check`, `outcome_check`, plus reasoning.
- **Each check is `"correct"`, `"incorrect"`, or `"uncertain"`** (changed Aug 2026 — it
  used to default to `"correct"` when unsure, which could make a guess accidentally
  win a tiebreak). The prompt instructs the model to answer `"uncertain"` rather than
  guess. No consensus-engine change was needed: `_llm_matches` is a plain equality
  check, and `"uncertain"` never equals a human's `"correct"`/`"incorrect"`, so an
  unconfident LLM automatically fails to match — same effect as a genuine
  disagreement, routing to `need_review`.
- **`corrected_outcome` is constrained to the real outcome vocabulary** (mirrors the
  `unvalidated_outcome_check` CHECK constraint, split by record type) through three
  independent layers: the prompt lists the categories with one-line definitions;
  Gemini's structured-output `response_schema` constrains the field to that enum;
  and `_coerce_outcome` re-validates server-side regardless, mapping common
  near-misses (e.g. `"inconsistent"` → `"failed"`) and dropping anything else rather
  than passing an invented label through. This fixes the LLM occasionally suggesting
  off-schema outcome labels like `"inconsistent"`.

Errors are captured rather than raised: the verdict dict carries an `error` key and the
engine treats it as "matches nobody". An errored tiebreaker or "not a replication"
check sends the record to `need_review`, and an agreement waits at `consensus_reached`
for an admin instead of auto-validating. Where the answer is only stored for the admin
(branch 2, pre-checks 2 and 3), an errored call keeps a clean answer stored earlier
(`_store_only_answer`). Everywhere else the stored value is the answer the decision
used, errors included: the nightly tiebreaker retry finds its records by that error.

The nightly job (§12) retries every open record whose stored answer is an error. A
tiebreaker is re-evaluated, since its answer decides the outcome. Any other record only
gets the new answer: consensus is not re-run, so the retry can never validate or reject
a record that is waiting for an admin. The saved answer does count later, though: if
`auto_validate_waiting.py --apply` is run by hand, an agreement whose sanity check now
agrees can pass its rules like any other.

---

## 9. The admin panel

Sign in at the same URL with a handle and password from `admins`. Auth is an
session cookie — an opaque token stored only as a digest — validated by
`_require_admin()`, which returns the admin's handle for stamping. Some operations
(creating/deleting admins) additionally require `trusted = TRUE`.

> **Partly remediated:** `admins.password_hash` holds an Argon2id hash, the plaintext
> column is gone, and there is no fallback password — startup fails when `admins` is
> empty and `ADMIN_PASSWORD` is unset. The frontend no longer persists the password
> through the login-prefill path. The token is still a deterministic function of the
> stored hash, with no independent expiry or revocation record, so it is not yet a
> session. See [§19](#19-deferred-security-work).

Eight tabs:

| Tab | What it does |
|---|---|
| **Entries** | Every record, filterable by All / Pending Approval / Needs Review / Skipped / Admin comments / Validated / Excluded. Shows which validators have judged the entry (handles in tooltips), distinct skip counts, human agreement % (with an LLM-dissent marker — see below), LLM errors, and who approved it (on the Validated filter only). Long outcomes and handles are cut, with the full text in a tooltip. Approve, flag for review and add notes in the Pending Approval filter, whose rows open in place; on the other filters the Pending approval status pill links to that row. Resolve conflicts, or withdraw an entry still open for validation, in the full review. |
| **Source Records** | The entry-sheet table — see [SOURCE_RECORDS.md](SOURCE_RECORDS.md) |
| **Validator Stats** | Per-validator throughput, accuracy, flagged judgements; set tier |
| **Admins** | Add/remove admins, toggle trusted, set the site banner |
| **Dashboard** | Aggregate progress and throughput |
| **Pool Priority** | Edit `serving_config` with a live preview of how many records match |
| **Restricted access** | Records reported as inaccessible; assign them to specific validators |
| **Messages** | Threaded inbox with validators |

The **Skipped** filter is an automatically derived review panel. A record appears
when either (a) more than five distinct validators have skipped it for any reason, or
(b) at least two distinct validators selected `eligibility_unclear` or
`data_quality`. Multiple skip events from the same validator remain in the audit
history but count once toward both rules. Opening the record shows a collapsed
**Skip history** ledger with timestamp, validator handle, reason, and comment; only
admin APIs return validator names and comments. Skips do not change consensus.

### The Study column

The Entries table's Study cell puts its title on one line, cut with an ellipsis, with
the study label beside it and the full title in the cell's tooltip. The row's flags,
trust badges and note marker get a line of their own, wrapping as they need, so a long
title cannot push them out of view; a row with none has no such line. The `↳` original
is on its own line too, cut, with its full text in a tooltip. Pending Approval lays
its Study cell out differently: its title wraps, and its flags have their own column.

### The Agreement column

The Entries table's **Agreement %** (headed **Agree**) measures the two *human* validators only: the share
of the three checks (type / original / outcome) on which V1 and V2 gave the same
answer — so 0 / 33 / 67 / 100%, and blank until both humans have submitted. An outcome
check of `not_applicable` is no answer: that record is scored on its other two checks,
and the dashboard's agreement rate leaves it out. The LLM is
**not** a voter. When the LLM ran cleanly and contradicts an answer the humans agree
on, the cell instead shows a 🤖✗ marker whose tooltip lists the dissenting checks —
that's the signal that a record like "both humans reject, LLM disagrees" is in review.
(Until Aug 2026 the % was 3-way unanimity *including* the LLM, so a record with full
human consensus could display as 0%.) Sorting the column sorts by the human-only %.

### Approval

`consensus_reached` records wait for an admin. Approving writes the row to `validated`
and stamps `admin_approved`. Flagging sends it to `need_review`. Every admin action is
attributed by handle.

Each validator card on the review screen shows the validator's track record next to
their name — lifetime judgements submitted (`validators.total_judgements`, so
assignment work counts) and how many of their judgements admins have flagged
(served as `validator_stats` on the entry-detail endpoint).

The resolution form also carries two identifier fields: **Published DOI**
(`doi_r_published` — pre-filled from a validator's suggestion the same way as the
proposed abstract, saved on resolve) and **Alternative Identifiers**
(`alt_identifier_r` — admin-curated, comma separated: another DOI, a different
version, a meta paper; same name as the `source_records` column). Unlike the other
form fields, clearing either of these to empty *deletes* the stored value on resolve.
Both are published to `validated` and included in the daily export.

Abstract edits: a record whose validators disagree only on an edited abstract lands in
`need_review` with **no** `final_*` values written. The admin detail screen therefore
pre-fills both the Final Preview and the resolution form's Abstract field with the
validators' proposed edit (longest wins when the two differ), flags it as a pending
change, and publishes it on resolve — no manual copy-paste needed.

The same pre-fill protects every other correction on a `need_review` record: when
**both** validators agreed on a corrected type/outcome/title/DOI/link, the form shows
the agreed value (with a hint that pre-filled corrections are saved on resolve)
instead of the raw extracted value — so resolving a record that landed in review for
an unrelated reason can no longer silently drop the corrections the validators agreed
on. Quote edits pre-fill with the longest edit, mirroring consensus.

If an admin resolution gives record **A** the natural identity already owned by
validated record **B**, the API returns a structured conflict instead of running an
overwriting upsert, and saves nothing. That identity is six fields, all identical
(replication title, DOI and study label; original title, key and study label), so of
what the window compares only type and outcome can differ. The window sets the two
records side by side and says what a merge does; the admin confirms **Merge into the
existing record** in a second request, or goes back to change a field. B stays
authoritative and unchanged; A is removed from validated output but retained with its
extraction metadata and judgements, and the corrections and note from the form are
written to A alone, so they never reach B. There is no undo: nothing removes a merge,
and resolving A again is refused (409 `record_already_merged`), so A's Admin Resolution
section shows no form or decision, only that A was merged into B and a way to open B.
A note can still be saved on it. `validated_record_merges` records the two record
ids, admin handle, timestamp, and proposed resolution snapshot. A concurrent insert
uses `ON CONFLICT DO NOTHING` and returns to the same confirmation
flow rather than mutating B. Every path that publishes does the same, so two duplicates
published at once can never leave one validated with no row: `admin_approve` answers
409 as its duplicate check does, and an unattended publish (`_insert_validated`, from
consensus or `auto_validate_waiting.py`) leaves the record waiting for an admin to merge.

### Withdrawing an entry before validation

An entry still open for validation (`unvalidated` or `validation_inprogress`) can be
taken out before its validators finish. **Withdraw from validation** on the entry screen
asks what is wrong with it and why, and calls `POST /api/admin/entries/{record_id}/withdraw`
with a required `scope` and `reason` (at most 1,000 characters). There are three scopes:

| Scope | Dialog label | What it withdraws | Comes back? |
|---|---|---|---|
| `wrong_original` | Wrong original: withdraw this pair | This pair only | Yes, when the extractor ships a different original at the same work and rank |
| `duplicate` | Correct pair already in FLoRA: withdraw this pair permanently | This pair only | Never |
| `not_replication` | Not a replication/reproduction: withdraw the whole article | This pair and every other pair of the same replication paper still open for validation | Never |

Each withdrawn record becomes `rejected` with `admin_checked`, so it is neither served
nor published; its open assignment closes and its unanswered slots are freed, so a
validator who held it is told it was already decided. Judgements already submitted stay,
with their points, as for any rejection. The reason goes at the head of the admin note
as `Withdrawn before validation (<scope label>): <reason>`, above any note already there.
Each is logged as `withdrawn`, with this in `admin_decisions.detail`:

```json
{"scope": "wrong_original | duplicate | not_replication",
 "reason": "<the reason as given, trimmed>",
 "withdrawn_with": "<record_id of the entry withdrawn from; only on the other pairs of an article>"}
```

The other pairs of an article are all the extractor records with the same replication
DOI, case-insensitive: the set the entry screen lists as "This paper has N coded
originals" (`paper_review.admin_paper_set`), which shows at most 50 of them. A
withdrawal is not limited to those 50; the dialog's preview counts and lists every pair
it takes along or leaves for review. Without a replication DOI they are the extractor records
of the same replication work (`record_metadata.work_id`, the import's key); an entry with
neither withdraws only itself, and the dialog says its other pairs cannot be found. For the
whole-article scope the dialog shows, from `GET …/withdraw-preview?scope=not_replication`,
how many open pairs go along and which pairs are already decided or waiting for an admin
(`validated`, `consensus_reached`, `need_review`). Those are not changed: the response
returns them as `review_records` and the panel lists them to review. Already rejected
pairs are left alone. Every open pair gets the same checks as the entry itself; if any
is blocked (merged, judged by this admin under the self-approval rule, or with live
added originals), the whole request is refused with 409 `withdraw_blocked_by_siblings`
and a `blocked` list, and nothing is withdrawn.

The entry itself is refused (409) when it is validated, rejected, `consensus_reached` or
`need_review`, which have their own actions, when it is a merged duplicate, and while it
has live added originals; 403 under the self-approval rule. A withdrawn entry can still
be published through **Review / edit fields** and Mark as Resolved. Withdrawing does not
re-link an entry to another original.

---

## 10. Database schema

All definitions are in [`db_schema.sql`](../db_schema.sql), which is **idempotent** and
re-executed by `init_db()` on **every application start**. Anything you add there must
survive repeated execution — use `IF NOT EXISTS`, `CREATE OR REPLACE`,
`ON CONFLICT DO NOTHING`, and guard `ALTER … RENAME` inside a `DO $$ … $$` block.
A non-idempotent statement takes the server down on the next restart.

### Core tables

**`validators`** (16 cols) — `handle`, `email`, `code`, `vote_score`, `validator_tier`,
`total_points`, `total_judgements`, `skipped_count`, `accuracy_score`, `onboarded_at`,
`last_login_at`, `last_seen_update`, forgot-handle rate limiting.

**`unvalidated`** (48 cols) — one row per resolved pair. Display columns for both sides
(`doi_r`/`study_r`/`title_r`/`year_r`/`url_r`/`ref_r`/`abstract_r` and the `_o`
equivalents). `study_r`/`study_o` are within-paper study numbers; titles never
occupy those fields,
classification (`type`, `outcome`, `outcome_quote`, `out_quote_source`), workflow state
(`validation_status`, `is_tiebreaker`, `restricted_access`, `admin_checked`,
`admin_override`), the three validator summaries as JSONB (`validator_1`,
`validator_2`, `llm_validator`), and the consensus-resolved `final_*` columns.

`validation_status` ∈ `unvalidated`, `validation_inprogress`, `validated`,
`need_review`, `consensus_reached`, `rejected`.

**`validation_queue`** (27 cols) — exactly three rows per record: `human_1`, `human_2`,
`llm`. Holds the three checks, all corrections, `additional_checks` JSONB, notes,
points, `flagged`/`flag_reason`, and the `is_shown` / `started_at` / `validated_at`
timing that drives claiming and reaping. `UNIQUE (record_id, validator_slot)`.

**`validation_skips`** — append-only history for released validator claims. Stores
`record_id`, `validator_id`, the stable `queue_id`, a controlled `reason_code`, an
optional comment (maximum 1,000 characters), and `skipped_at`. It is deliberately
separate from `validation_queue` because a released queue slot is reusable.
Historical `submission_failed` values remain valid so old audit rows can be read,
but `/api/skip` no longer accepts that reason.

**`submission_failure_releases`** — server-controlled save-recovery audit. Stores
the browser's unique `submission_id`, exact queue/record/validator binding, a
SHA-256 digest of the one-time stamp (never the raw stamp), safe failure metadata,
expiry, and state: `save_failed`, `released`, `slot_closed`, or `expired`. These
events are deliberately excluded from Skip counts and escalation thresholds.

**`validated`** — final consensus records. Pair identity includes DOI/OpenAlex paper
identity, `study_r`/`study_o` within-paper study numbers, and title fallbacks for
legacy rows.
Includes `doi_r_published` and `alt_identifier_r` (added Aug 2026).
This is what gets exported.

**`record_metadata`** — upstream extraction provenance: filter/link evidence and
confidence, models, author lists, OpenAlex ids, `doi_o_verification`, full-text
`pdf_source`/`parse_method`, screen categories, outcome reasoning, BibTeX references,
`work_id`/`release_id`, and multi-original rank/count. Not shown in the validation UI.

### DOI-less originals

Some replication targets have no registered DOI at all — books, book chapters,
pre-DOI-era papers. The extractor marks these `doi_o_verification = 'no_doi'` and
ships `doi_o = ''` with identity carried in columns that already exist: `title_o`,
`oa_work_id_o`, and a `url_o` pointing at OpenAlex instead of doi.org. **`doi_o`
stays `''`, never `NULL`**, on these rows — `validated`'s natural key
pair-identity constraint and its `ON CONFLICT` clause rely on equality,
and Postgres never treats two NULLs as equal, so a NULL `doi_o` would silently
break upsert/dedup for these records.

What honors this end to end:
- **Import** (`csv_to_db.py`, `update_originals.py`) — `_url_o()` prefers the CSV's
  own `url_o` over the derived `https://doi.org/{doi_o}` link, so the OpenAlex
  identity survives instead of being overwritten by an empty derived link.
  `update_originals.py` additionally exempts `doi_o` from its "a blank CSV cell
  never overwrites a good value" rule — but **only** when that row carries
  `doi_o_verification == 'no_doi'`, i.e. the extractor explicitly asserts there is
  no DOI. Without that exemption the script could never correct a wrong DOI *to*
  blank; without the `'no_doi'` condition an incomplete CSV pull could wipe good
  DOIs wholesale.
- **Validator app** — Gate II falls back to an "OpenAlex ↗" link (`url_o`, or one
  built from `oa_work_id_o`) when `doi_o` is blank, with a "No registered DOI —
  verify using title, author, and year" note, instead of rendering with no link
  at all.
- **Admin review** — the Final Preview's Original DOI row does the same fallback,
  **but only when the raw extracted `doi_o` was itself blank**. When a non-blank
  `doi_o` was deliberately cleared as wrong, it shows "DOI cleared — no verified
  link available" and no link: `url_o` is mechanically derived from `doi_o` at
  import for any row whose CSV didn't supply its own, and `oa_work_id_o` is
  backfilled from a DOI lookup (`backfill_oa_work_ids.py`), so **both fallback
  fields are contaminated by a wrong DOI** and would silently relink to the very
  page the admin just rejected.
  The **Original DOI** field in the resolution form treats `''` as a deliberate,
  persisted correction (distinct from `null` = "never resolved, use the raw
  extracted value") — the same clear-to-empty pattern as `doi_r_published` /
  `alt_identifier_r` — so an admin can correct a wrongly-linked DOI-less original
  (e.g. the extractor matching a book to an unrelated review's DOI) back to blank,
  and it stays blank on a later re-resolve instead of reverting.
- **LLM validator** — a blank `doi_o` renders as an explicit
  `"(none — original has no registered DOI, e.g. a book or pre-DOI-era paper)"`
  sentinel in the prompt rather than a bare blank line, so the model doesn't read
  the absence as a data-quality problem.
- **Export** — `export_validated.py`'s manual-references flagging already treats a
  non-empty `url_o` as resolvable-enough (`is_real_doi(doi_o) OR url_o present`),
  so once import carries the real `url_o` through, these rows stop being flagged
  for manual reference-filling automatically — no export change was needed.

**The `'' or NULL` trap.** Everywhere `final_doi_o` is read, use an explicit
`is not None` / `!= null` check, never the `x or y` truthy fallback this codebase
uses for every other field — `''` (deliberately cleared) and `None` (never touched)
are different states, and collapsing them silently reverts an admin's correction to
the wrong extracted DOI. Sites that must honor this: `admin_resolve`,
`assignment_judge`, `admin_approve`, the dashboard disagreement counter, and
`consensus_engine._insert_validated`'s `wid_o` guard (dormant today — nothing can
currently reach it with a `''` — but kept consistent so a future re-validation path
can't reintroduce the bug). On the frontend: `storedDoiO` / `finalDoiO` in
`renderAdminDetail`, the Final Preview's changes-table row, and `renderHistDetail`'s
`val_doi_o` fallback.

**Ambiguous DOI-less originals (warning, not a block).** Because `doi_o` is `''` for
*all* DOI-less originals, the OpenAlex work id is the primary fallback; title and
within-paper study number provide legacy fallback identity. If two such originals
share both (titles compared loosely — casefolded, whitespace-collapsed) or have a
blank title, the second could otherwise overwrite the first via
`ON CONFLICT DO UPDATE`. `csv_to_db._flag_ambiguous_doi_o_titles()` detects this;
both import paths call it and **still import the row** — this is deliberately a
warning, not a hard constraint, so a legitimately odd row can never break the
pipeline. Each flagged record is surfaced twice, because an unattended nightly import
makes console output easy to miss:
1. a console summary listing the affected `pair_id`s, and
2. an `⚠ Auto-flagged:` line appended to the record's `admin_notes`, which renders at
   the top of the admin review screen — so the next person to open that record sees
   it regardless of who was watching the import.

Note this is scoped to a single replication's originals: two *different* replications
citing the same DOI-less book is fine (`doi_r` differs, so the rows stay distinct) and
is not flagged. As of the current extractor CSV, 38 DOI-less originals exist and none
are ambiguous.

### Supporting tables

`admins` · `assignments` · `validator_messages` · `validated_record_merges` ·
`site_banner` · `serving_config`

### Source Records tables

`source_records` · `source_record_edits` · `source_sync_runs` ·
`source_display_counters` · `outcome_alias` · `reproduction_outcome_map` ·
`transform_exclusions` — documented in [SOURCE_RECORDS.md](SOURCE_RECORDS.md#7-database-schema).
Source records are never edited by hand; corrections are made to FLoRA records and
logged in `flora_record_changes` — see
[SOURCE_RECORDS.md §9.4](SOURCE_RECORDS.md#94-correcting-a-flora-record-flora_editspy).

### Triggers

- `trg_clear_stale_oa_work_id` on `unvalidated` — nulls the OpenAlex work id when its
  DOI changes, so the backfill re-fetches for the correct paper
- `trg_clear_stale_source_oa_work_id` on `source_records` — same guard
- `trg_set_source_content_fingerprint` on `source_records` — recomputes the duplicate
  fingerprint on insert and on any DOI/URL change

---

## 11. API reference

119 endpoints (method and path under `/api`, counted from the app's routes). All admin routes require an administrator session. Validator routes
currently accept `coder_id` as identity; this is a known authorization vulnerability,
not an API guarantee. The planned authenticated API will derive the validator id from
the server-side session and remove these parameters (see [§19](#19-deferred-security-work)).

### Validator

```
POST /api/login                        handle + (email | code)
GET  /api/onboarding                   the 5 calibration pairs
POST /api/onboarding/complete
POST /api/update-seen                  dismiss the "what's new" gate
GET  /api/next-pairs                   batch-claim (count, mode, buffered_only)
POST /api/pairs/{queue_id}/start
POST /api/paper-review/claim           reserve a paper's open records as buffered holds
POST /api/paper-review/refresh         renew an open table's holds; returns those still held
POST /api/judge                        submit a judgement
POST /api/skip                        release claim + save reason/comment history
                                      (reason_code optional: old pages default to prefer_another)
POST /api/submission-failures/release  consume one server-issued recovery stamp
POST /api/senior-reject                tier ≥ 2 only
POST /api/restricted                   report an inaccessible article
GET  /api/my-judgements                own history
GET  /api/my-judgements/{queue_id}
GET  /api/my-assignments
GET  /api/assignment/{record_id}
POST /api/assignment-judge             double points
GET  /api/stats · /api/leaderboard · /api/banner · /api/health
GET  /api/messages · POST /api/messages/{id}/read · /api/messages/{id}/reply
POST /api/forgot-handle
```

### Admin

```
POST   /api/admin/login
GET    /api/admin/stats · /dashboard · /validators
GET    /api/admin/entries                        list + filter + search + sort
GET    /api/admin/entries/{record_id}
POST   /api/admin/entries/{record_id}/approve · /flag-review · /note · /resolve
POST   /api/admin/entries/{record_id}/remove-original · /withdraw
GET    /api/admin/entries/{record_id}/extra-originals · POST (add at once)
POST   /api/admin/entries/{record_id}/extra-originals/suggestions/{id}/dismiss
POST   /api/admin/entries/{record_id}/extra-original/remove
POST   /api/admin/queue/{queue_id}/flag
GET    /api/admin/serving-config · PUT · GET /preview
GET    /api/admin/restricted · POST /api/admin/assign
GET    /api/admin/validators/{id}/flagged · POST /{id}/set-tier
GET    /api/admin/admins · POST · DELETE /{id} · POST /{id}/toggle-trusted
POST   /api/admin/banner
GET    /api/admin/messages · /thread/{id} · POST /thread/{id}/reply · /message
```

### Extractor maintenance

```
GET  /api/admin/maintenance/runs
GET  /api/admin/maintenance/runs/{run_id}
POST /api/admin/maintenance/run              full | sync | find
```

### Source Records

```
GET   /api/admin/source-records                  list + counts
GET   /api/admin/source-records/export.csv
GET   /api/admin/source-records/sync-status
GET   /api/admin/source-records/duplicates
GET   /api/admin/source-records/{record_id}
POST  /api/admin/source-records/{record_id}/duplicate
```

Read-only apart from duplicate rulings. Records are corrected in the FLoRA tab:

```
GET   /api/admin/flora/{flora_id}             record + editable fields + corrections
PATCH /api/admin/flora/{flora_id}             save a change {fields, revert, version, notes}
POST  /api/admin/flora/{flora_id}/restore     restore a logged change {change_id, version, note}
POST  /api/admin/flora/{flora_id}/type        change replication <-> reproduction {type, fields, version, notes}
```

**Route ordering matters.** Literal paths (`export.csv`, `sync-status`, `duplicates`)
are declared *before* `{record_id}`, because FastAPI matches in declaration order.
New literal paths must go above the parameterised ones.

---

## 12. Scheduled work

### In-process (APScheduler, starts with the app)

All cron triggers specify UTC explicitly; Kubernetes node timezone and daylight
saving changes do not alter these times.

| Job | Schedule | What |
|---|---|---|
| `extractor_maintenance.run_scheduled` | 02:00 UTC | Under one lock: sync/import, OpenAlex enrichment, read-only orphan report, then retire of the pairs flora-extractor withdrew |
| `extractor_maintenance.run_queued` | every 10 seconds | Claim durable admin requests and recover work after pod termination |
| `_nightly_llm_retry` | 00:22 UTC | Retry stored LLM errors: re-run consensus on errored tiebreakers (`_retry_tiebreakers`), then ask Gemini again for every other open record holding an error and save only the answer (`_retry_llm_errors`) |
| `_reap_stale_slots` | every 2 min | Release queue slots claimed but abandoned, so records return to circulation |

The reaper matters: without it, a validator who closes the tab mid-record would lock
that record indefinitely.

The 00:22 job fires in every process (the scheduler starts at import and production
runs two replicas), so it first takes a PostgreSQL advisory lock
(`NIGHTLY_LLM_RETRY_LOCK_ID`); a process that does not get it logs a line and skips.
`_retry_llm_errors` covers `need_review` and `consensus_reached` records that are not
tiebreakers. It writes `llm_validator` and nothing else: not the status, the final
values, the quality flags or `validated`. It holds no row lock while Gemini answers;
it saves the answer only if, under the lock, the row still holds the error it read and
is still open and not a tiebreaker, so an answer a submission saved meanwhile is kept.
Each record has its own transaction, and a call that fails again leaves the error for
the next night.

The 02:00 extractor job is a fail-fast sync → report → retire operation with
OpenAlex enrichment sequenced inside the same process lock. A failed CSV sync
marks the orphan report and the retire stage `SKIPPED`. Child output and stage
statuses append to `logs/extractor_maintenance.log` (configurable through
`EXTRACTOR_MAINTENANCE_LOG`) and are mirrored to stdout. It is a nightly poll, not
an immediate cross-repository upload trigger. Records leave the database only
through the retire stage, for the pairs flora-extractor names in
`data/retired_pairs.csv` that nobody has worked on, each archived in
`retired_records` first; nothing is deleted for mere absence from the CSV.
Each stage transition is also committed to `extractor_maintenance_runs`. Part 1 is
complete only after import, CSV promotion, an exact post-promotion byte check, and
a verified archive digest for the same run. A standalone report must inherit the
newest verified sync run ID, so a failed newer sync cannot fall back to an older
success.

Run IDs alone are not enough under pod replacement, so the run history also stores
the `archive_file`/`archive_sha256` of the snapshot Part 1 imported. The orphan
report and the retire stage read that immutable archive and verify its sha256
first; the retire stage additionally requires the digest it read to equal the one
PostgreSQL recorded for the run. Every imported snapshot is also kept in the
database (`extractor_snapshots`), so a pod whose `EXTRACTOR_DATA_DIR` lost the
archive, or the previous import the counts compare against, restores it from
there.

Before import, the resolved pair IDs the candidate adds and drops are counted
against the baseline named by run history (the previous run's archive, verified
by digest). The counts never stop a run: nothing is deleted by absence, so the
old removal limit (`EXTRACTOR_MAX_REMOVAL_PERCENT`) was removed on 2026-10-01. A
baseline that cannot be found only loses the counts. Zero resolved IDs still
blocks the run and leaves the known-good CSV untouched.
`extractor_maintenance_runs` retains complete logs and structured counts for the
admin Extractor Pipeline tab. A session-level PostgreSQL advisory lock prevents
overlapping live processes across web workers, while a partial unique index
prevents duplicate active reservations. The admin's **Run full sync** action
starts the same routine as the nightly run.

Manual HTTP 202 responses persist only a durable `queued` row; they do not rely on
an in-process web callback. Any pod may poll the row, but the advisory lock elects
one executor. A replacement pod requeues an abandoned running job immediately
after proving the former session lock is gone; repeating a run is safe.
Each retire batch takes a short `EXCLUSIVE NOWAIT` lock on `unvalidated`,
`validation_queue`, `validated`, `validation_skips`, `submission_failure_releases`,
`record_metadata`, `assignments`, and `validator_messages` and re-plans under it.
This prevents concurrent validation/audit writes from invalidating the plan; if
validation is already writing, the batch aborts immediately and the failure is logged.

### GitHub Actions

| Workflow | Schedule | What |
|---|---|---|
| `sync-sources.yml` | 03:00 UTC | Entry-sheet sync + FLoRA dataset build |
| `daily-export.yml` | 04:00 UTC | `export_validated.py`, commits `data/validated_export.csv`, `data/needs_manual_refs.csv`, `oa_ref_cache.json` |

Both need the `DATABASE_URL` secret. The hour gap is deliberate so they never contend.

---

## 13. Scripts

| Script | Purpose |
|---|---|
| `csv_to_db.py` | Import `extracted.csv` → `unvalidated` + `record_metadata` + 3 queue slots. `--dry-run` supported |
| `export_validated.py` | `validated` → `data/validated_export.csv`, plus `needs_manual_refs.csv` listing entries whose identifiers CrossRef/DataCite cannot resolve. Replaces references with OpenAlex data where available, cached in `oa_ref_cache.json` |
| `sync_csv.py` | Fetch `extracted.csv` from the extractor repo (nightly job calls `sync_once`) |
| `extractor_maintenance.py` | Locked sync → orphan report → retire routine, with a database copy of each imported snapshot and one combined log |
| `sync_sources.py` | Entry-sheet ingest → `source_records` |
| `transform_sources.py` | `source_records` → FLoRA column set |
| `backfill_oa_work_ids.py` | Fill `oa_work_id_o/_r` from DOIs via OpenAlex |
| `backfill_quote_source.py` | Populate `out_quote_source` on existing rows; `--recompute-all` refreshes only computed values |
| `fetch_oa.py` | Unpaywall open-access status for every DOI |
| `update_originals.py` | Refresh original-study references on existing rows |
| `update_outcomes.py` | Update outcome classification from a newer `extracted.csv` |
| `find_orphans.py` | Read-only summary of rows in the DB no longer present in the CSV |
| `build_static.py` | Generate `docs/pairs.json`, `hard_pairs.json`, `onboarding.json` for the static GitHub Pages demo |
| `db_migrate.py` | Migrate an older schema forward |
| `db_reset.py` | **Destructive** — wipes everything except `validators` |

---

## 14. Local development

### Environment

```bash
DATABASE_URL=postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres
GEMINI_API_KEY=AIzaSy...
GITHUB_TOKEN=                       # only if the extractor repo is private
GITHUB_REPO=forrtproject/flora-extractor
GITHUB_BRANCH=main
ADMIN_PASSWORD=                     # first-run admin seed; MUST be set in any deployment
RESEND_API_KEY=                     # transactional email
EMAIL_FROM="Flora Validator <noreply@forrt.org>"
OPENALEX_MAILTO=                    # OpenAlex/Unpaywall polite-pool contact
```

`ADMIN_PASSWORD` is read only when the `admins` table is empty. Setting or changing
the environment variable does **not** rotate an existing database administrator.
Until the authentication redesign in §19 lands, use a unique high-entropy value and
rotate the stored admin credential separately.

### Running

```bash
uvicorn app:app --reload            # app + frontend on :8000
```

`init_db()` runs `db_schema.sql` on import, so schema changes apply on restart.

The scheduler starts automatically. When running several local instances against the
same database, be aware they will all run the nightly jobs.

### Tests

```bash
python -m pytest tests/ -q          # 509 tests
```

Coverage is on the logic that is hardest to reason about: `test_consensus_engine.py`
(the full decision tree), `test_llm_validator.py` (prompt/parse/error handling),
`test_sync_csv.py`. They use mocked cursors and do not need a database.

### Frontend

No build step. Edit `docs/app.js` / `docs/style.css` and reload. Check syntax with
`node --check docs/app.js`.

---

## 15. Deployment

`Procfile`: `web: uvicorn app:app --host 0.0.0.0 --port $PORT` · `runtime.txt`:
`python-3.12`. Any Procfile-aware host works; production runs on Supabase for the
database.

Only `DATABASE_URL` is technically required to boot, but that is **not a safe
production configuration**: without `ADMIN_PASSWORD`, a fresh database is seeded with
the known fallback password described in §19. Set a unique value before first start.
Without `GEMINI_API_KEY` every LLM call records an error, so nothing is auto-validated
or rejected on the LLM's word (a senior reject still works): agreements wait at
`consensus_reached`, the rest at `need_review` —
degraded, not broken.

There is also a **static demo mode**: `build_static.py` writes JSON fixtures into
`docs/`, which can be served from GitHub Pages with no backend at all.

---

## 16. Operations and troubleshooting

**Server won't start after a schema edit.** `init_db()` executes all of
`db_schema.sql` on import. A non-idempotent statement (a bare `ALTER … RENAME`, a
`CREATE TABLE` without `IF NOT EXISTS`) fails on the second run. Guard it and restart.

**Records stuck in `validation_inprogress`.** A validator claimed a slot and vanished.
`_reap_stale_slots` releases these every 2 minutes; check the scheduler is running.

**A record sat at `consensus_reached` for weeks.** That status means *humans agreed,
admin has not approved*. It is a queue for people, not a bug — Entries → Pending Approval.

**Lots of `need_review`.** Expected. It is the destination for unsure answers,
conflicting corrections, three-way splits, and LLM errors. 140 live.

**LLM errors.** The 00:22 UTC job (`_nightly_llm_retry`) retries every open one
nightly: an errored tiebreaker is re-evaluated, any other record only gets the new
answer. A call that fails again keeps the error for the next night. Filter Entries by
LLM errors to see the backlog. Check `GEMINI_API_KEY` and the free-tier rate limit.

**Export is missing references.** `export_validated.py` also writes
`data/needs_manual_refs.csv` — entries whose DOI/URL cannot be resolved by
CrossRef/DataCite and which the R pipeline's title filter would otherwise drop. Fill in
the blank columns and add them to `manual_references.xlsx` upstream.

**Entry-sheet sync problems** — see
[SOURCE_RECORDS.md §12](SOURCE_RECORDS.md#12-troubleshooting).

---

## 17. Documentation map

| Doc | Status |
|---|---|
| **PROJECT.md** (this file) | Current |
| [SOURCE_RECORDS.md](SOURCE_RECORDS.md) | Current — the entry-sheet tab and pipeline |
| [SETUP.md](SETUP.md) | Mostly current — environment and Supabase setup |
| [CSV_SCHEMA.md](CSV_SCHEMA.md) | Reference for the upstream `extracted.csv` columns |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current focused overview — not exhaustive |
| [VALIDATION_DB_SCHEMA.md](VALIDATION_DB_SCHEMA.md) | Current abridged schema guide; `db_schema.sql` remains authoritative |
| [STAGE4_VALIDATE.md](STAGE4_VALIDATE.md) | ⚠ **Stale** (May 2026) |
| [README.md](README.md) | Describes the whole four-stage FLoRA Extractor project, of which this repo is stage 4 |

**On the remaining stale stage document:** it dates from 2026-05-14 and predates the
validator tier system, assignments, serving priority, restricted access, messages,
admin trust levels, Source Records, reproduction axes, and the current extractor
contract. Treat it as history; verify against this document and `db_schema.sql` before
relying on anything in it.

---

## 18. Design decisions and gotchas

**Two humans before anything is decided.** No record is resolved on one judgement, ever.
This is the core quality guarantee and everything else is built around it.

**Humans always beat the LLM.** When both humans agree, the LLM is a sanity check whose
verdict is recorded for admin visibility but changes nothing. The LLM only decides when
humans conflict — and then only by matching exactly one of them.

**"Can't tell" is not "incorrect".** Collapsing the two would let uncertainty
masquerade as a judgement. Any unsure answer routes to `need_review`; the LLM's answer
is stored for the admin but cannot settle it.

**Corrections differing is enough to block.** If two validators agree on *what is wrong*
but write different fixes, the record goes to review. The LLM's sanity check is stored
for the admin, but the engine will not pick a fix on its own.

**Longest edit wins for quotes and abstracts.** Deterministic and reproducible, and
longer text carries more context. Not a quality judgement — just a tie-break rule you
can predict.

**Raw SQL, no ORM.** Deliberate. The queries are the interesting part of this app,
especially claiming and consensus, and an ORM would obscure them. It also means
`db_schema.sql` is the single source of truth for the schema — there are no migration
files to reconcile.

**The frontend has no framework.** One HTML, one JS, one CSS, no build. Contributors are
researchers as often as engineers; a `npm install` between them and a fix is a real cost.

**`docs/` is both the frontend and the documentation folder.** FastAPI mounts it at `/`,
so the Markdown files are also web-served. Slightly unusual, and it means the static
mount must stay the *last* route registered — anything added after it is unreachable.

**Nine tables in the database are not ours.** See [§3](#3-stack-and-layout).

**The Source Records path is deliberately simpler.** It has no consensus, no LLM, and no
two-human requirement, because its input is already human-validated in the Google
Sheets. Do not model new work on it if the input is machine-extracted — that is what the
main pipeline is for.

---

## 19. Deferred security work

**Status: partly implemented 2026-09-12.**

Closed. Identity is a server-issued session rather than a client-supplied `coder_id`;
administrator passwords are Argon2id hashes with no fallback; administrator accounts
are created by emailed invitation and the recipient chooses their own password;
sessions expire and are individually revocable; cross-site writes are refused;
sign-in attempts are throttled.

**NOT closed: validator authentication.** Sign-in requires a handle plus the email
address on that account, and nothing more. The email that follows is a notice sent
after the session already exists, so it cannot prevent anything. Handles are public on
the leaderboard and academic addresses are frequently guessable, so anyone who knows
or guesses both obtains a genuine session for that validator, with that validator's
tier and history.

This is a deliberate product decision taken on 2026-09-12 in favour of a
frictionless sign-in, not an oversight. It is recorded here, and in the docstring of
`login()` in `app.py`, so that it stays a known and revisitable choice. The fix, if
it is revisited, is small: `auth_links` already implements single-use emailed links
for administrator invitations, and the same mechanism covered validator sign-in
before this decision.

**Resolved (audit trail).** `security_events` records privileged actions with the
server's view of the actor, written in the same transaction as the action and kept
for 365 days. `GET /api/admin/security-events` reads it.

**Resolved (admin sign-in field).** Admins had to type their password into the login
screen's Email field, and anything containing `@` was routed to the validator
endpoint — storing the password in `validators.email` in plaintext and opening a
validator session. Admins now have a dedicated password form.

Also not done: multi-factor authentication for trusted administrators. Admin auth
remains single-factor.

### Validator impersonation

The login response contains `coder_id`, and validator endpoints accept that id in a
query parameter or request body. Database ownership checks compare queue,
assignment, judgement, and message rows to the submitted id, but they do not prove
that the caller owns that identity. The vulnerable surface includes more than
`/api/judge`: queue claim/start, onboarding completion, update acknowledgement,
restricted reports, skip, senior reject, assignments, stats, judgement history, and
message read/reply operations all use client-selected identity.

The email login path also authenticates with handle plus email address without an
email challenge. The personal-code path is stronger only while that code remains
secret; today it is stored in plaintext, returned in the login profile, and the full
profile is persisted in `localStorage` as `flora.coder`.

### Administrator credentials and tokens

**Resolved (item 4, partially).** `admins.password_hash` holds an Argon2id hash, and
the plaintext `password` column is dropped once every row has been migrated. Existing
passwords were hashed in place rather than reset, so no admin had to change anything.
`_seed_admin_if_empty()` has no fallback password: with an empty `admins` table and no
`ADMIN_PASSWORD`, startup raises instead of creating a publicly known account. The
handle comes from `ADMIN_HANDLE` (default `flora_muenster`), which is not a secret and
may live in code. `admin_password.py` is the operator recovery path when nobody can
sign in. The frontend no longer writes the admin password into `flora.lastLogin`, and
discards any value an older build left there.

**Resolved (invitations).** A trusted admin creates an account with a handle and an
email; `password_hash` starts NULL so the account cannot be signed into, and a
single-use `auth_links` row is emailed. The recipient chooses their own password, so
the inviter never learns it. Only the token digest is stored, issuing supersedes any
outstanding link, and `admin_password.py --invite` prints one when email is
unavailable.

**Resolved (sessions).** Login now mints an opaque random token stored only as a
digest in `sessions`, delivered in an `HttpOnly; Secure; SameSite=Lax` cookie, with
expiry and revocation as columns. Two logins with the same password receive unrelated
sessions; logout, a password change, and admin deletion revoke immediately. The old
deterministic bearer token and the `X-Admin-Token` header are gone.

### Approved remediation direction

Implement both identities together rather than putting a signature around
`coder_id` or replacing the deterministic admin hash with another deterministic
token:

1. Add server-side, opaque sessions for validators and admins. Generate a fresh
   cryptographically random token at login, store only its hash with principal,
   creation, expiry, last-used, and revocation timestamps, and deliver the raw token
   only in a `Secure`, `HttpOnly`, `SameSite` cookie.
2. Add a `current_validator` / `current_admin` dependency. Every protected endpoint
   derives identity and tier/trust from that session. Remove `coder_id` from private
   request bodies and query strings; never keep an insecure compatibility fallback.
3. Prove validator identity. Email users receive a short-lived, single-use email
   challenge before account creation or login. Code-only users authenticate with a
   sufficiently random personal code stored as an Argon2id hash. Stop returning codes
   and clear legacy credential-bearing browser storage.
4. **(Done.)** Replace `admins.password` with an adaptive password hash (Argon2id),
   migrate or reset every existing password, remove the hard-coded fallback, and
   require either an explicit one-time bootstrap secret or an operator command when
   no admin exists. Delivered as hash-in-place migration, `ADMIN_PASSWORD` with no
   default, and `admin_password.py`. The remaining clause below is not yet done:
   Password changes, admin deletion, and logout revoke sessions. Trusted admins should
   receive MFA as a follow-up hardening step.
5. Protect cookie-authenticated writes against CSRF with strict cookie attributes plus
   Origin/Fetch-Metadata validation or a CSRF token. Convert state-changing GETs—most
   importantly `/api/next-pairs`, which claims queue rows—to POST.
6. Add account/IP login throttling, generic login errors, security-event audit logs,
   session cleanup, and `Cache-Control: no-store` on authenticated responses.

### Cutover and acceptance criteria

Deploy schema, backend, and frontend changes together and force every user to log in
again. All legacy admin tokens must stop working at cutover. There must be no period
where a valid session is optional and a caller can fall back to `coder_id`.

The work is complete only when: a request with another user's id but no session gets
`401`; validator A cannot read or mutate B's data; tier 0 cannot senior-reject; two
logins using the same password receive unrelated revocable sessions; password changes
and logout revoke sessions immediately; cross-origin state-changing requests fail;
fresh deployment without explicit bootstrap credentials fails closed; and the
database/browser contain no plaintext passwords, personal codes, or raw session
tokens.

Until then, set and rotate a unique admin password, restrict administrator access at
the hosting layer where possible, and do not describe `coder_id` as authentication.
