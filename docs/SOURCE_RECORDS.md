# Source Records — entry-sheet ingest and review

The **Source Records** tab in the admin panel is a spreadsheet-like view of the FLoRA
entry sheets, backed by a Postgres table that a nightly GitHub Action fills. Admins
browse, search, and correct those rows in the browser; a separate script turns the
corrected rows into the FLoRA dataset.

This document is the complete picture: what it does, why it is built this way, how to
operate it, and what goes wrong.

---

## Contents

1. [Why this exists](#1-why-this-exists)
2. [The whole flow in one picture](#2-the-whole-flow-in-one-picture)
3. [The two entry sheets](#3-the-two-entry-sheets)
4. [Prerequisite: the sheet UUID](#4-prerequisite-the-sheet-uuid)
5. [The registry (`sources.yml`)](#5-the-registry-sourcesyml)
6. [The sync (`sync_sources.py`)](#6-the-sync-sync_sourcespy)
7. [Database schema](#7-database-schema)
8. [The admin tab](#8-the-admin-tab)
9. [The transform (`transform_sources.py`)](#9-the-transform-transform_sourcespy)
10. [The GitHub Action](#10-the-github-action)
11. [Common tasks](#11-common-tasks)
12. [Troubleshooting](#12-troubleshooting)
13. [Design decisions and why](#13-design-decisions-and-why)
14. [Known limits](#14-known-limits)

---

## 1. Why this exists

The FLoRA preparation pipeline (an R notebook, mirrored at
[`docs/FLoRA_Preparation_Pipeline.r`](FLoRA_Preparation_Pipeline.r)) reads the entry
sheets straight from Google, filters and deduplicates them inline, and emits
`flora.csv`. That works, but it means:

- nobody can see a source row unless they open the Google Sheet
- a malformed DOI or a mis-coded outcome can only be fixed by editing the sheet
- corrections have no author, no timestamp, and no history
- the cleaning rules are buried in an R notebook

This system puts the source rows in a database with a review UI on top. Corrections
are made in the web app, attributed and timestamped, and they flow into every
subsequent build automatically.

**It does not replace the R pipeline.** It replaces the *sheet-reading front end* of
it, and produces the same column shape.

---

## 2. The whole flow in one picture

```
  Google Sheets (2 tabs)
    │   Apps Script stamps a static UUID in the `id` column
    ▼
  sync_sources.py            ← nightly, 03:00 UTC
    │   6 integrity gates → filter to accepted → INSERT ONLY
    ▼
  source_records  (Postgres, 1,653 rows)   ◄── authoritative from here on
    │
    ├─► Admin panel "Source Records" tab
    │      browse · search · filter · review panel · duplicate review
    │      every edit stamped with reviewer + timestamp + history
    │
    ▼
  transform_sources.py       ← nightly, right after the sync
    │   merge outcome → clean DOIs → exclude → strip url → dedup → project
    ▼
  output/flora_entry_sheets.csv  (1,590 rows × 14 FLoRA columns)
```

The single most important property: **the sync only ever inserts.** Once a row is in
the database, the Google Sheet can never change it again. Corrections made in the web
app are permanent and do not need to be mirrored back to the sheet.

---

## 3. The two entry sheets

Both are tabs of one published Google spreadsheet, so they share a single sharing
permission — if it breaks, both break together.

| Sheet | gid | Raw rows | Accepted | Prefix |
|---|---|---:|---:|---|
| replications | `863031634` | 4,043 | **1,635** | `REPL-` |
| reproductions | `984458430` | 157 | **18** | `REPRO-` |

"Accepted" means `validation_status` (replications) or `validation` (reproductions) is
one of:

```
validated - chosen        validated - changed        validated - unchanged
```

Everything else — blank, `help needed`, `on hold`, `awaiting validation`,
`validated - discarded` — is left in the sheet and never ingested.

### Columns taken from each sheet

Seven columns are shared. The rest differ, which is why one table carries both with
`type` as the discriminator and unused columns left NULL.

| | replications (14) | reproductions (15) |
|---|:---:|:---:|
| `ref_o` `doi_o` `url_o` | ✅ | ✅ |
| `ref_r` `doi_r` `url_r` | ✅ | ✅ |
| `abstract_r` | ✅ | ✅ |
| `validation_status` | ✅ | ✅ *(sheet calls it `validation`)* |
| `outcome` `outcome_quote` `out_quote_source` | ✅ | — |
| `year_r` | ✅ *(sheet calls it `year`)* | — |
| `alt_identifier_o` `alt_identifier_r` | ✅ | — |
| `study_o` | — | ✅ |
| `outcome_computation` + `_quote` + `out_quote_computational_source` | — | ✅ |
| `outcome_robustness` + `_quote` + `out_quote_robust_source` | — | ✅ |

Two renames happen at ingest, and nothing else:

- `validation` → `validation_status` (same concept, different sheet name)
- `year` → `year_r` (verified: it matches the year in `ref_r` 58.5% of the time versus
  3.1% for `ref_o`, so it is the replication-side year)

Every other sheet column — `prep_notes`, `quote validated`, `target_match_liberal`,
`Coder`, `validator`, `validator_notes`, `id challenge` — is preserved verbatim in the
`raw` JSONB column. Nothing a coder typed is ever discarded.

**Reproductions do not have an `outcome` column.** They carry a two-dimensional
outcome, and the single FLoRA label is derived downstream (see [§9](#9-the-transform-transform_sourcespy)).

---

## 4. Prerequisite: the sheet UUID

**This is the load-bearing part of the whole design. Read it before changing anything.**

Each sheet has an `id` column holding a static UUID written by Apps Script
(`Utilities.getUuid()`). The sync keys on `(source, sheet_row_id)`, so that UUID *is*
the row's identity.

Three rules the Apps Script must keep:

1. **Static value, never a formula.** A positional formula like `=ROW()` would shift
   every id below an inserted row, and the next sync would treat all of them as new —
   inserting hundreds of duplicates permanently.
2. **Never regenerate an id that already exists.** The fill function must skip a
   populated cell.
3. **Never reuse an id from a deleted row.** `ON CONFLICT DO NOTHING` would silently
   swallow the new record.

Two gaps to be aware of:

- **`onEdit()` does not fire for every row-creation path.** Rows added by a script, an
  add-on, the Sheets API, or an `IMPORTRANGE` refresh get no UUID. Run the backfill
  function on a **time-driven trigger** as well, shortly before the nightly sync. It
  is idempotent because it refuses to overwrite a populated cell.
- **Copy-pasting a row copies its UUID.** Two sheet rows then share one id. The sync
  detects this and **skips every row involved**, reporting it — because storing one and
  silently discarding the other would be unrecoverable data loss under insert-only.

A row with a blank `id` is skipped and reported. The sync never invents a fallback id;
doing so would reintroduce exactly the duplicate problem the UUID solves.

---

## 5. The registry (`sources.yml`)

[`sources.yml`](../sources.yml) is the single description of where the sheets are and
how their columns map. Adding or repointing a sheet is a config change here, never a
code change.

```yaml
document: "2PACX-1vT0VnLyrf9GC…"          # the published spreadsheet
url_template: "https://docs.google.com/spreadsheets/d/e/{document}/pub?gid={gid}&single=true&output=csv"

accepted_values: ["validated - chosen", "validated - changed", "validated - unchanged"]
row_count_floor: 0.5                       # abort a source if it loses >50% of its rows

sources:
  - key: replications
    gid: 863031634
    validation_column: validation_status
    type_label: replication
    display_prefix: REPL
    id_column: id
    expected_columns: [...]                # gate 4 checks these exist
    column_map: {year: year_r}             # sheet name -> source_records name
    promoted: [...]                        # which columns become real columns
    enabled: true
```

R can read this file too (`yaml::read_yaml()`), so the R pipeline and the sync can
share one definition rather than two copies that happen to match today.

`enabled: false` parks a broken source without touching code or reverting a commit.

---

## 6. The sync (`sync_sources.py`)

```bash
python sync_sources.py                      # normal run
python sync_sources.py --dry-run            # read-only; runs every gate, writes nothing
python sync_sources.py --source replications
```

### The six gates

Each source passes all six before anything reaches the database. A source that fails
any gate is **skipped entirely** — its existing rows are untouched, and the other
source still syncs.

| # | Gate | What it catches |
|---|---|---|
| 0 | Registry validation | A `promoted` column that `source_records` does not have — which would insert as a permanently NULL column |
| 1 | Fetch | Network failure, timeout, **truncated response** (3 retries with backoff) |
| 2 | **Is it actually CSV?** | An unshared sheet returns **HTTP 200 with an HTML sign-in page**. The request "succeeds" and pandas parses the HTML into a one-column frame |
| 3 | Parse | Malformed CSV |
| 4 | Column contract | A renamed column upstream — fails loudly instead of producing a silently NULL column for months |
| 5 | Row-count floor | A truncated or partially-loaded sheet. Sheets grow and shrink by tens of rows; they do not halve overnight |
| 6 | Payload hash | Identical bytes to the last successful run → skip the source entirely and report "unchanged" |

Gate 2 matters more than it looks. Under insert-only, a bad parse does not *delete*
data — it **inserts permanent garbage**, and insert-only never cleans up.

Gate 6 makes the run summary honest: "unchanged" is distinguishable from "0 new rows",
which otherwise looks identical to a silent failure.

### The merge

For each accepted row:

1. Blank `sheet_row_id`? → skip, count it
2. `sheet_row_id` duplicated within this batch? → skip **all** rows sharing it, report
3. `(source, sheet_row_id)` already in the table? → skip, it exists
4. Otherwise → assign a `display_id` and `INSERT … ON CONFLICT DO NOTHING`

That is the entire merge. Nothing is ever updated or deleted.

### Sample output

```
── Replications (replications) ──────────────────────────
  fetched 4043 rows × 21 columns
  accepted: 1635
  inserted: 9   already present: 1624   skipped (no id): 2
  ⚠ 62 paper(s) appear under more than one sheet id — flagged for review

── Reproductions (reproductions) ────────────────────────
  unchanged since last run (157 rows) — skipping
```

Every run writes a row to `source_sync_runs`, which feeds the freshness banner above
the grid. Raw payloads are written to `snapshots/` (git-ignored) and uploaded as a
90-day workflow artifact — **not** in a dry run, so reaching for a dry run while
debugging a bad sync cannot destroy the evidence.

---

## 7. Database schema

All definitions live in [`db_schema.sql`](../db_schema.sql), which is idempotent and
re-executed by `init_db()` on every app start. **Any statement you add there must be
safe to run repeatedly** — guard `ALTER … RENAME` in a `DO` block, use
`IF NOT EXISTS`, `CREATE OR REPLACE`, `ON CONFLICT DO NOTHING`.

### `source_records`

One row per accepted sheet row.

| Group | Columns |
|---|---|
| Identity | `record_id` (UUID PK) · `source` · `sheet_row_id` · `display_id` · `type` |
| Shared | `ref_o` `doi_o` `url_o` `ref_r` `doi_r` `url_r` `abstract_r` |
| Replication-only | `outcome` `outcome_quote` `out_quote_source` `year_r` `alt_identifier_o` `alt_identifier_r` |
| Reproduction-only | `study_o` · `outcome_computation`(+`_quote`, +`out_quote_computational_source`) · `outcome_robustness`(+`_quote`, +`out_quote_robust_source`) |
| Merged | `validation_status` |
| Derived | `oa_work_id_o` `oa_work_id_r` · `content_fingerprint` |
| Preserved | `raw` (JSONB — the complete sheet row, verbatim) |
| Review state | `reviewed_by` `reviewed_at` `version` |
| Duplicates | `duplicate_status` `duplicate_of` `duplicate_reviewed_by` `duplicate_reviewed_at` |
| Timestamps | `first_seen_at` `updated_at` |

Key constraints:

- `UNIQUE (source, sheet_row_id)` — the insert-only guarantee, enforced by the database
- `UNIQUE (display_id)`
- `CHECK` — `duplicate_status='duplicate'` requires `duplicate_of`; `'distinct'` forbids it
- `CHECK` — a row cannot be a duplicate of itself

Two triggers:

- `trg_clear_stale_source_oa_work_id` — nulls the OpenAlex work id when its DOI changes,
  so the backfill re-fetches for the correct paper
- `trg_set_source_content_fingerprint` — recomputes the duplicate-detection fingerprint
  on insert and whenever `doi_o`/`doi_r`/`url_r` changes

**Values are stored dirty on purpose.** A DOI like
`10.1002/pits.22106digital object identifier (doi)` and a year of `0` are stored exactly
as the sheet has them, because those are precisely the rows a human should see and fix.
Cleaning happens only in the transform.

### `source_record_edits`

Append-only history: `record_id`, `display_id`, `field`, `old_value`, `new_value`,
`edited_by`, `edited_at`, `note`.

There is deliberately **no cascading foreign key** — an audit trail whose whole purpose
is to outlive its record must not be deleted with it. `display_id` is denormalised so
history stays readable even if the row is gone.

### `source_sync_runs`

One row per source per run: status, gate failure reason, row counts, payload hash.
Feeds the freshness banner.

### `source_display_counters`

`source` → `last_value`. Note the name: it stores the **last value handed out**, not
the next one. A reader taking "next" at face value would reissue the id just used and
collide on the `display_id` index.

### Rule tables (editable without touching code)

| Table | Purpose |
|---|---|
| `outcome_alias` | Replication outcome spelling → canonical value (8 rows) |
| `reproduction_outcome_map` | `(computational, robustness)` → single label (12 rows) |
| `transform_exclusions` | Rows dropped from the output, by `doi_r` or `url_r`, with a reason |

These replace values hardcoded in the R notebook. They are read at transform time, so
changing one is a row edit and a re-run — never a data migration.

---

## 8. The admin tab

Sign in to the admin panel and pick **Source Records**.

### The grid

Server-side paging at 50 rows. Columns: ID, type, original, replication, outcome,
status, reviewed, open.

- **Filter chips** — All · Replications · Reproductions · Not reviewed · Reviewed · Duplicates
- **Search** — reference, DOI, `display_id`, `study_o` (press Enter)
- **Status dropdown** — the three accepted values
- **Sortable headers** — click to sort, click again to reverse
- **Export CSV** — every row matching the current filter, not just the page
- **Freshness banner** — "Last sync — replications 6h ago · +9 new · 2 skipped"

One outcome column serves both types. Replications show `successful`; reproductions
show `computationally reproducible / robustness challenges`. Eighteen rows never needed
their own grid.

A `⚑` next to an ID means that paper appears under more than one sheet UUID.

### The review panel

Click any row. Fields are grouped in the order a reviewer checks them — Original,
Replication (or Reproduction / Computational / Robustness), Outcome, Status.

- Text fields are inputs; abstracts and quotes are textareas
- Fields with a known vocabulary are dropdowns, and the options come **from the data**,
  so a new value appearing upstream needs no code change. The current value always
  stays selectable even if it is not in the list
- `oa_work_id_o/_r` are read-only — they are derived from the DOI and a manual edit
  would be overwritten by the next enrichment pass
- The raw sheet row is available in a collapsed section
- Edit history is shown at the bottom, including the reviewer's note

**Save** writes changed fields, stamps `reviewed_by` and `reviewed_at`, and appends one
log entry per changed field.

**Save also counts when nothing changed.** "I looked at this and it is correct" is a
real review outcome and the most common one — without the stamp, a reviewer cannot tell
*unchecked* from *checked and fine*, and would re-review the same rows forever.

**Save & next** advances to the next row in the *current filter* without returning to
the grid. So "review all 18 reproductions" is: set the type filter → open the first →
Save & next ×18. The counter reads `412 / 1653`.

If two admins open the same record and both save, the second gets a **409** and the
panel reloads rather than silently overwriting the first.

### Duplicate review

Click the **Duplicates** chip. Members of each group are shown side by side with their
identifiers, references and outcomes, so the differences are scannable.

Groups where members **disagree about the outcome** are sorted first — those are the
ones worth opening. Groups spanning both sheets are badged.

For each row: **Keep — distinct** (genuinely a different record) or **Duplicate of
`<other>`**. Marking a row a duplicate automatically records the other as the surviving
`distinct` row, so a two-member group resolves in one click.

The transform honours both decisions: `duplicate` rows are excluded, and `distinct`
rows are exempt from automatic dedup.

### API

All endpoints require the `X-Admin-Token` header.

```
GET   /api/admin/source-records                    list + counts (filters, sort, paging)
GET   /api/admin/source-records/export.csv         CSV of the current filter
GET   /api/admin/source-records/sync-status        freshness banner
GET   /api/admin/source-records/duplicates         grouped duplicate candidates
GET   /api/admin/source-records/vocabularies       dropdown options
GET   /api/admin/source-records/{record_id}        full record + raw + history + neighbours
PATCH /api/admin/source-records/{record_id}        save a review  {fields, version, note}
POST  /api/admin/source-records/{record_id}/duplicate   {status, duplicate_of}
```

The literal paths are declared **before** `{record_id}` so FastAPI matches them first.
If you add another literal path, put it above the parameterised routes.

All SQL and business logic lives in
[`source_records_service.py`](../source_records_service.py), which imports no FastAPI
types at all. The routes are thin wrappers. This is deliberate — the planned Lambda
handlers call the identical functions.

---

## 9. The transform (`transform_sources.py`)

```bash
python transform_sources.py                 # writes output/flora_entry_sheets.csv
python transform_sources.py --stats-only    # report only, writes nothing
python transform_sources.py --output path/to/file.csv
```

A **pure function of the database** — it writes nothing back. Six operations, in order:

1. **Derive the outcome.** Replications: normalise the spelling via `outcome_alias`.
   Reproductions: look up `(computational, robustness)` in `reproduction_outcome_map`.
   The two quotes and two sources are joined with ` || `, de-duplicated.
2. **Clean DOIs.** Strip `https://doi.org/`, `doi:`, and trailing garbage like
   `digital object identifier (doi)` or `get rights and content`. DOIs cannot contain
   whitespace, so everything from the first space is dropped.
3. **Apply exclusions** from `transform_exclusions`. Runs *before* step 4, because an
   operator registering a url-keyed exclusion copies the URL as it appears in the UI.
4. **Strip redundant `url_r`.** 1,403 of the replication rows carry
   `https://doi.org/<doi_r>` — the DOI written twice. In FLoRA, `url_r` means "a link
   to something that is not the DOI".
5. **Deduplicate.** Key = `type | doi_o | coalesce(doi_r, url_r)`. Rows a reviewer
   ruled `distinct` are exempt.
6. **Project** to the 14 FLoRA columns and strip `DUMMY_*` placeholder DOIs.

### Ordering matters

The order above is not arbitrary. Cleaning before exclusions would make url-keyed
exclusions unmatchable. Deduplicating before cleaning would miss rows that only look
identical after normalisation.

### Current output

```
loaded 1653 → outcome derived 1652 → DOIs cleaned 179 → excluded 0
→ url_r stripped 1403 → deduplicated 63 → DUMMY_* stripped 11
final: 1590 rows × 14 columns
```

Columns: `doi_o` `ref_o` `url_o` `doi_r` `ref_r` `url_r` `abstract_r` `outcome`
`outcome_quote` `outcome_quote_source` `type` `source` `alt_identifier_o`
`alt_identifier_r` — the same set the R notebook calls `flora_cols`.

---

## 10. The GitHub Action

[`.github/workflows/sync-sources.yml`](../.github/workflows/sync-sources.yml) —
**03:00 UTC daily**, plus manual runs from the Actions tab.

```
checkout → Python 3.12 → pip install → sync_sources.py → transform_sources.py
         → upload snapshots (artifact) → upload flora dataset (artifact)
```

Needs one secret: `DATABASE_URL`.

Deliberately at 03:00, an hour ahead of the existing 04:00 `daily-export.yml`, so the
two never contend and the night's new rows are in place before the export runs.

The transform step carries `if: always()` — the transform reads only the database, so a
temporarily unshared sheet should not also cost you the nightly dataset built from rows
already stored.

`output/flora_entry_sheets.csv` is **not committed**. It changes every night, so it is
uploaded as a 90-day artifact instead. If the R pipeline should ever read it from a
`raw.githubusercontent` URL the way it reads `validated_export.csv`, add a commit step.

---

## 11. Common tasks

### Run a sync by hand

```bash
python sync_sources.py --dry-run   # always do this first
python sync_sources.py
```

The dry run is read-only at the database-session level and exercises every gate,
including the row-count floor and the payload-hash comparison. Against an already-synced
database it should report **"unchanged"**.

### Add a new outcome combination

A reproduction whose `(computational, robustness)` pair is not in the map produces a
blank outcome and a warning. Add the row and re-run — no migration:

```sql
INSERT INTO reproduction_outcome_map (computational, robustness, canonical)
VALUES ('computational issues', 'partially robust', 'computational issues, partially robust');
```

### Exclude a paper from the output

```sql
INSERT INTO transform_exclusions (doi_r, reason, added_by)
VALUES ('10.31234/osf.io/jfmsz', 'Withdrawn paper', 'lukas');
```

The row stays in `source_records` — it is a true record of what the sheet said — but
drops out of the transform output.

### Add or repoint a sheet

Edit `sources.yml`. Set `enabled: false` to park a broken source. If you add a new
promoted column, add it to `DATA_COLUMNS` in `sync_sources.py` and to `source_records`
in `db_schema.sql` first — gate 0 will refuse the sync otherwise.

### Promote a column that is currently only in `raw`

1. Add it to `source_records` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`)
2. Add it to `DATA_COLUMNS` in `sync_sources.py` and to `promoted` in `sources.yml`
3. Backfill from `raw` — **do not** expect a re-sync to fill it. Insert-only means
   existing rows are never revisited:

```sql
UPDATE source_records SET new_col = raw->>'sheet_column_name' WHERE new_col IS NULL;
```

### Apply schema changes

```bash
psql "$DATABASE_URL" -f db_schema.sql
```

Safe to re-run. The app also executes it on every start.

---

## 12. Troubleshooting

### "FAILED — response was HTML, not CSV"

The spreadsheet's sharing settings changed, or the publish-to-web link was revoked.
Both sources fail together because they share one document. Re-publish the sheet
(File → Share → Publish to web) and re-run. No data is lost — a failed source is
skipped, not emptied.

### "missing expected column(s): …"

Someone renamed a column in the sheet. Either rename it back, or update
`expected_columns` and `promoted` in `sources.yml`. Gate 4 exists so this fails loudly
instead of producing a permanently NULL column nobody notices for months.

### "row count N is below 50% of last successful run"

The sheet is truncated, still loading, or was genuinely halved. Check the sheet, then
re-run. If the drop is real, the floor is `row_count_floor` in `sources.yml`.

### "N rows skipped (no id)"

Rows with a blank `id`. Run the Apps Script fill function, then re-sync. They will be
picked up as new rows.

### "⚠ N duplicated sheet id(s)"

Someone copy-pasted a row including its `id` cell. **All** rows sharing that id are
skipped. Fix it in the sheet — clear the `id` on the copied row and let the Apps Script
generate a fresh one — then re-sync.

### "display_id collision"

`source_display_counters.last_value` has fallen behind `source_records`. Usually a
partial restore, or a source `key` renamed while keeping its `display_prefix`. Fix:

```sql
UPDATE source_display_counters c SET last_value = (
  SELECT COALESCE(MAX(SUBSTRING(display_id FROM '[0-9]+$')::int), 0)
  FROM source_records WHERE source = c.source);
```

### A record's outcome is blank in the output

Two different causes, and the transform now distinguishes them:

- *"blank in the source sheet"* — the coder left it empty; fix it in the review panel
- *"no `reproduction_outcome_map` entry"* — add the mapping row and re-run

### The grid shows stale data

Check the freshness banner. If the last run says `failed`, read `failure_reason` in
`source_sync_runs`, or the Actions run log.

---

## 13. Design decisions and why

**Insert-only.** The sync never updates or deletes. Once a row lands, the database is
authoritative and edits made in the web app never need to go back to the sheet. This
removed an entire override layer, a conflict queue, and tombstone/rekey logic. The
trade-off: a bad parse inserts *permanent* garbage, which is why the gates are strict.

**Identity is the sheet's UUID, not a content hash.** An earlier design hashed
`doi_o + doi_r/url_r`. That breaks the moment someone fixes a DOI typo — the hash
changes, the row looks new, and you get the duplicate row you were trying to avoid. A
static UUID makes identity explicit and survives edits to every other field.

**Values stored dirty, cleaned only in the transform.** Three reasons. Under
insert-only, cleaning at ingest would freeze today's rules into every row forever —
improving a rule later would need a migration, because a re-sync skips existing rows.
The transform is a pure function, so improving a rule means editing it and re-running.
And a DOI that *looks* clean in the grid is a DOI nobody will ever fix at the source.

**One table, not two.** Replications and reproductions share 7 of their columns and are
`bind_rows()`-ed immediately downstream. Sparse NULL columns cost essentially nothing in
Postgres, and 18 reproduction rows do not justify a second table, a second API, and a
union view to put them back together.

**Reproduction outcome dimensions are stored, not merged.** `outcome_computation` and
`outcome_robustness` are kept as-is; the single label is derived in the transform. So an
unseen combination is a lookup-table row to add, not stored data to migrate — and the
two-dimensional structure the coders actually recorded is never lost.

**Row-level review, not inline cell editing.** Abstracts and quotes are far too long to
edit in a grid cell, and reviewers work record by record, not cell by cell.

**`content_fingerprint` is computed by the database.** It was originally computed in
Python at insert and frozen forever, so a reviewer correcting a DOI left a stale
fingerprint — hiding real duplicates *and* leaving false flags a reviewer might act on
by deleting a good row. A trigger keeps it correct.

---

## 14. Known limits

- **The 63 duplicate groups are real.** 127 rows (~7.6% of accepted replications) share
  a paper with another row under a different sheet UUID, and 14 of those groups disagree
  about the outcome. That is a property of the source sheets, not of this pipeline. Work
  through the Duplicates queue.
- **Rows that leave accepted status stay.** Insert-only never removes anything, so a row
  flipped back to `help needed` in the sheet remains in the table. That is intentional —
  its edits and history are worth more than the row's absence.
- **The R pipeline still reads the sheets directly.** Nothing here changes it yet. If it
  should consume `flora_entry_sheets.csv` instead, that is a commit step in the workflow
  plus a URL change in the notebook.
- **`year_r` is 99.5% clean, not 100%.** Eight of the 1,635 replication rows carry `0`,
  `2366`, `3272`, or a blank. They are visible in the grid so a reviewer can fix them.
  (All 18 reproductions have `year_r` NULL — that sheet has no year column at all.)
- **Enrichment is not wired in.** `oa_work_id_o/_r` columns and their staleness trigger
  exist, but nothing populates them yet. The machinery to do it —
  `backfill_oa_work_ids.py`, `fetch_oa.py`, `oa_ref_cache.json` — already exists in the
  repo.

---

## File map

| File | Role |
|---|---|
| [`sources.yml`](../sources.yml) | Source registry — gids, columns, accepted statuses |
| [`sync_sources.py`](../sync_sources.py) | Download, gates, insert-only merge |
| [`transform_sources.py`](../transform_sources.py) | Six-operation transform to the FLoRA columns |
| [`source_records_service.py`](../source_records_service.py) | All SQL and logic; no FastAPI types |
| [`app.py`](../app.py) | Thin routes (search for `Source records — entry-sheet datatable`) |
| [`db_schema.sql`](../db_schema.sql) | Tables, constraints, triggers, rule-table seeds |
| [`docs/index.html`](index.html) | `admin-tab-sources`, `src-detail-modal` |
| [`docs/app.js`](app.js) | Grid, review panel, duplicate view (`ADMIN: SOURCE RECORDS`) |
| [`docs/style.css`](style.css) | `SOURCE RECORDS` sections |
| [`.github/workflows/sync-sources.yml`](../.github/workflows/sync-sources.yml) | Nightly job |
