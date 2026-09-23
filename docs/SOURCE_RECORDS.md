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
3. [The entry sheets](#3-the-entry-sheets)
   - [3.1 What is different about spreadsheet B](#31-what-is-different-about-spreadsheet-b)
   - [3.2 Our own validated records](#32-our-own-validated-records-validated)
4. [Prerequisite: the sheet UUID](#4-prerequisite-the-sheet-uuid)
5. [The registry (`sources.yml`)](#5-the-registry-sourcesyml)
6. [The sync (`sync_sources.py`)](#6-the-sync-sync_sourcespy)
7. [Database schema](#7-database-schema)
8. [The admin tab](#8-the-admin-tab)
9. [The transform (`transform_sources.py`)](#9-the-transform-transform_sourcespy)
   - [9.1 FLoRA record ids](#91-flora-record-ids-flora_registrypy)
   - [9.2 Identifiers: what actually exists](#92-identifiers-what-actually-exists)
   - [9.3 The FLoRA tab](#93-the-flora-tab)
10. [The GitHub Action](#10-the-github-action)
    - [10.1 The "Run sync now" button](#101-the-run-sync-now-button)
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

## 3. The entry sheets

Sheets come from **two** published Google spreadsheets. Tabs of the same spreadsheet
share one sharing permission — if it breaks, they break together — while the two
spreadsheets fail independently.

**Spreadsheet A — the original entry sheets** (registry key `document`):

| Sheet | gid | Raw rows | Accepted | Prefix |
|---|---|---:|---:|---|
| replications | `863031634` | 4,043 | **1,635** | `REPL-` |
| reproductions | `984458430` | 157 | **18** | `REPRO-` |

**Spreadsheet B — "Validating FReD replication success - Brinna"** (registry key
`fred_document`). All three are replications, and all three differ structurally from
spreadsheet A — see [§3.1](#31-what-is-different-about-spreadsheet-b):

| Sheet | gid | Raw rows | Accepted | Prefix | |
|---|---|---:|---:|---|---|
| replication success | `984458430` | 1,979 | **983** | `FRED-` | |
| replication success - BACKUP | `513210700` | 1,968 | *(974)* | `FREDB-` | **disabled** |
| SCORE 2025 validation | `1421575264` | 88 | **88** | `SCORE-` | no validation column |

The two remaining tabs of spreadsheet B — `Data validation` (`593748463`) and
`File links` (`177801713`) — are working sheets, not entry sheets, and are not
registered.

Note that gid `984458430` appears in both spreadsheets. gids are only unique within a
spreadsheet, and identity here is `(source, sheet_row_id)`, so the collision is
harmless — but it does mean a gid alone never identifies a sheet.

"Accepted" means `validation_status` (replications) or `validation` (reproductions and
the FReD tabs) is one of:

```
validated - chosen        validated - changed        validated - unchanged
```

Everything else — blank, `help needed`, `on hold`, `awaiting validation`,
`validated - discarded` — is left in the sheet and never ingested.

### 3.1 What is different about spreadsheet B

Three differences, each absorbed by a registry key rather than by code:

**The header is not the first row.** Both `replication success` tabs open with a
banner row (`should include success criterion` over the outcome columns). Parsed
normally, pandas takes that banner as the header and every real column comes back as
`Unnamed: N`, so gate 4 reports the entire sheet missing instead of the header being
off by one. `header_row: 1` moves the header down.

**The UUID is in `flora_id`, not `id`.** These tabs *do* have a column named `id` — it
holds `doi_o` and `doi_r` concatenated. Pointing `id_column` at it would key every row
on a DOI pair. `_validate_registry` now refuses any source whose `id_column` is not
also in `expected_columns`, so this fails once at startup rather than once per row.

**`SCORE 2025 validation` has no validation column.** It is an already-curated list, so
there is no per-row status to gate on. Its registry entry declares `validation_column:`
explicitly empty, every fetched row is accepted, and `validation_status` lands NULL —
which is what distinguishes these rows from ones a coder actively marked accepted.

The BACKUP tab is registered but `enabled: false`. It is ~98.5% the same studies as
`replication success` measured on `doi_o`+`doi_r`, but it shares **zero** `flora_id`
values with it, so nothing dedupes the two. Under insert-only those ~974 duplicate
rows would be permanent. It stays in the registry so the tab is documented and one
flag away, rather than silently forgotten.

Columns these tabs carry that `source_records` has no home for — `fred_id`, `link_r`,
`study_r`, `entries_per_rep`, `frq`, `validator`, `validator_notes`, and the sheet's
own `id` — are not promoted and stay recoverable in `raw`. Neither tab has a `year`
column, so `year_r` is NULL for every FReD row.

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

### 3.2 Our own validated records (`validated`)

Not a sheet. [`sync_validated.py`](../sync_validated.py) projects the project's own
`validated` table into the same grid under the source key `validated`, prefix `VAL-`,
so everything feeding the FLoRA dataset is reviewable in one place — and so the
duplicate detector can show where the entry sheets already cover a record this project
validated itself (currently **42** such groups).

Identity is `validated.validated_record_id`, which is already a UUID, so it fits
`(source, sheet_row_id)` with nothing invented.

**This sync is not insert-only, and that is deliberate.** Insert-only exists because an
entry sheet is external, its edits are unattributed, and the database should win once a
row lands. None of that holds for a table inside this same database that we own and
that legitimately changes when an admin re-opens a record. A permanently stale copy in
the grid would be a bug, not a safeguard — so a changed validated record refreshes its
grid row and bumps `version`.

**With one hard exception:** a row whose `reviewed_at` is set has been reviewed by a
human in the grid, and is never updated. Overwriting it would silently discard a
reviewer's correction, which is exactly what insert-only was protecting against. Those
rows are counted and reported by the run instead, so a divergence stays visible rather
than being resolved by whoever wrote last.

Two fields are deliberately *not* written:

- **`validation_status` stays NULL.** That column holds the sheet coders' vocabulary
  (`validated - chosen`). These rows never passed through a sheet, so reusing one of
  those values would claim a provenance that does not exist. `source = 'validated'`
  carries the meaning instead.
- **`oa_work_id_o` / `oa_work_id_r` are left to the OpenAlex backfill**, which owns
  them; writing them here would fight `trg_clear_stale_source_oa_work_id`, the trigger
  that clears them whenever the DOI beside them is written.

For reproductions, `validated.outcome` holds the two axes joined for display
(`computationally reproducible, robust`). The grid keeps them apart in
`outcome_computation` / `outcome_robustness`, which are copied directly, so the joined
string is dropped rather than stored as a third spelling of the same data.

Everything with no column of its own — `title_o`, `title_r`, `admin_approved`,
`original_key`, `record_id`, `validated_at` — is preserved in `raw`.

It runs nightly in the same GitHub Action, after the sheet sync and with `if: always()`
so an unshared sheet cannot also stop our own records from refreshing.

```
python sync_validated.py --dry-run
python sync_validated.py
```

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
document: "2PACX-1vT0VnLyrf9GC…"          # default spreadsheet
fred_document: &fred_document "2PACX-1vSlPVImL7kjz…"   # second spreadsheet
url_template: "https://docs.google.com/spreadsheets/d/e/{document}/pub?gid={gid}&single=true&output=csv"

accepted_values: ["validated - chosen", "validated - changed", "validated - unchanged"]
row_count_floor: 0.5                       # abort a source if it loses >50% of its rows

sources:
  - key: replications
    gid: 863031634                         # no `document` -> the default one
    validation_column: validation_status
    type_label: replication
    display_prefix: REPL
    id_column: id
    expected_columns: [...]                # gate 4 checks these exist
    column_map: {year: year_r}             # sheet name -> source_records name
    promoted: [...]                        # which columns become real columns
    enabled: true

  - key: score_2025
    document: *fred_document               # override -> the other spreadsheet
    gid: 1421575264
    header_row: 0                          # 0-based row the real header sits on
    validation_column:                     # explicitly empty -> accept every row
    id_column: flora_id
    ...
```

Three keys carry the multi-spreadsheet support:

| Key | Default | Meaning |
|---|---|---|
| `document` | top-level `document` | Which spreadsheet this tab belongs to |
| `header_row` | `0` | 0-based row the real header sits on; skips banner rows |
| `validation_column` | — | Empty means the sheet is pre-curated: accept every row |

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
| `preprint_dedup_decisions` | Admin rulings on preprint duplicate pairs (`keep_1` / `keep_2` / `keep_both`), made in the FLoRA tab |

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

**What a "duplicate" actually means here.** A row's `content_fingerprint` is
`md5(normalised doi_o + "|" + normalised doi_r, falling back to url_r)`, set by a
database trigger. Two rows sharing it describe **the same pair of papers**. It is a
detector, not an identity key — identity is `(source, sheet_row_id)` — and it never
blocks an import.

The fingerprint deliberately **excludes `study_o` and `study_r`**, and that single
fact explains most groups. One replication paper often covers several effects from
one original, each a legitimate row with its own outcome. Measured on the current
table, of 174 groups:

| | Groups | Meaning |
|---|---:|---|
| Every member is a different study pair | **94** (54%) | Not errors — separate effects from one paper pair |
| Every member is the same study pair | **71** (41%) | Genuine re-entry of the same effect |
| Mixed | 9 (5%) | Distinct effects plus a true repeat inside one group |

So roughly half the count is the fingerprint being intentionally coarse. Widening it to
include the study numbers was rejected: study numbering is free text across the sheets
(`1`, `1a`, `2`, blank), so it would miss real duplicates that a coder numbered
differently — and a detector that under-reports is worse than one that over-reports
into a review queue.

Groups are also worth reading by **which sources collide**. Within one sheet means a
coder entered the pair twice; across sheets means two coders reached the same pair
independently — including a `validated` row overlapping a sheet row, which says the
entry sheets already cover something this project validated itself.

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

All endpoints require an administrator session (the `flora_session` cookie set
by `POST /api/admin/login`).

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

### 8.9 The R notebook, ported

The FLoRA preparation pipeline used to live in an R notebook that read the Google
Sheets directly. It is now reproduced here, so a dataset build depends on this
repository alone.

| R notebook step | Here |
|---|---|
| Download 5 sources (replications, reproductions, COS, SCORE, validated export) | `sync_sources.py` + `sync_validated.py` — all five are registry sources |
| Step 4 exclusions sheet + manual lists | [`sync_exclusions.py`](../sync_exclusions.py) |
| Outcome normalisation | `extractor_vocab` + the `outcome_alias` table |
| DOI cleaning, redundant-URL stripping | `transform_sources.clean_doi` / `strip_redundant_url` |
| Dedup | `transform_sources` step 5 + reviewer duplicate rulings |
| Step 6b/7 references and metadata | [`enrich_works.py`](../enrich_works.py) via OpenAlex |
| Step 7c text cleaning | `transform_sources.clean_text` |
| Step 8 DOI hashes | `transform_sources.doi_hash` |
| Step 9 language, 9c OA urls | `enrich_works.py` (same fetch) |
| Step 9b COS non-replications | `sync_exclusions.COS_NON_REPLICATIONS` — 16 pairs |
| Step 9d meta-paper annotation | `transform_sources.META_PAPER_DOIS` — 12 DOIs |
| Step 7b OpenAlex work-id lookup | [`enrich_works.enrich_work_ids`](../enrich_works.py) |
| Step 8b title from reference text | [`apa_references.py`](../apa_references.py) — not called by the notebook |
| COS outcome-quote shorthand | [`cos_quote_rewrite.py`](../cos_quote_rewrite.py) |
| Author overlap (`augmentation.R`) | [`author_overlap.py`](../author_overlap.py) — from cached authors, no network |
| Step 10 title filter + export log | `transform_sources.missing_title_report` |
| `validate_flora.R` | [`validate_flora.py`](../validate_flora.py) |
| `validate_flora_network.R` | [`validate_flora_network.py`](../validate_flora_network.py) |

**Two deliberate differences.**

*The title filter drops rows from the export but not from the FLoRA tab.* The filter
itself now matches the notebook and is on by default: rows with no title on one side
are logged to `output/flora_export_log.csv` and left out of the export, and
`--keep-untitled` turns that off.

It was off while our title coverage was worse than R's. R backs a title with three
fallbacks — `manual_references.xlsx`, OpenAlex lookups for OSF urls, and synthesis
from structured fields — where we had only the OpenAlex DOI lookup, leaving 331 rows
blank for reasons of **our** coverage rather than the data's. Two of those fallbacks
now exist (OpenAlex work-id lookups at Step 7b, title recovery from the reference
text at Step 8b), which closed it to 24 genuinely untitled rows — Google Docs links,
DOIs OpenAlex does not hold.

The FLoRA tab still lists those 24. It is the screen someone fixes such a row on, and
a row hidden there is a row nobody fixes; `flora_service.counts()` reports them as
`untitled` and the grid shows the number above the table.

*`url_o` is excluded from the network URL check.* It holds titles rather than links on
a large number of rows — which the structural validator reports — and checking them
would be thousands of guaranteed failures burying the real ones.

**Preprint deduplication** (notebook Steps 6a and 7d) is
[`preprint_dedup.py`](../preprint_dedup.py), a port of `R/preprint_dedup.R`. It runs at
two points, as the notebook does:

- `apply_confirmed()` **before** enrichment, applying only the `keep_1`/`keep_2` rows
  of [`cache/confirmed_preprint_duplicates.csv`](../cache/confirmed_preprint_duplicates.csv).
  Running it first is what makes the metadata fetch use canonical DOIs instead of DOIs
  it is about to discard.
- `resolve()` **after** enrichment, because detection needs titles and authors.

Detection uses all four of the notebook's routes: replication-side pairs under one
`doi_o`, identical normalised titles, DOI-format variants, and fuzzy title matches
within a first-author block — at the same 0.80 similarity threshold. Verified against
the confirmed file's own recorded values: the Pennycook pair scores **0.991** here, the
number R wrote into that file.

`merge_doi_pair_dups()` is the piece our dedup previously lacked. Step 5 *drops* a
colliding row; this *combines* them — study numbers joined with `; `, quotes with
` || `, `COS` preferred as the source, and disagreeing outcomes collapsed to `mixed`
or, when they cannot be mixed, retained as `A || B` so `validate_flora` reports them.
Absorbed `record_id`s are carried through so `flora_records` provenance survives.

**A record validated on this website wins — its values, not its identity.** When a
`source = 'validated'` row shares a key with entry-sheet or FReD rows of the same
type, its judgement (`outcome`, both reproduction axes) and `source` are what the
row publishes; when the judgements disagree, its quotes and quote sources win too.
A field it leaves blank falls back to the other rows. Which row the group becomes is
chosen exactly as before (the first), so its published FLoRA ID never moves because
of this rule, and a row a reviewer ruled *distinct* is never merged or dropped and
never speaks for another. Rows of different types are left alone. Conflicts it settles
— in the step 5 drop as well as the merges — are logged in `dup_outcome_conflicts.csv`
with `resolved_by = website record`, so the entry sheet can be corrected.

Absorbed `record_id`s are recorded by every merge, including the step 2b one that
runs before step 5; they used to be lost there, leaving the registry unable to
recognise a row whose survivor later changed.

**`type` is part of every dedup key**, as in step 5: a replication and a reproduction
of one paper are two records. The merge groups on `(type, doi_o, doi_r)`; the preprint
dedup pairs only rows of one type, and drops a losing DOI's row only where the kept DOI
has a row of the same type under the same original. Merging on `(doi_o, doi_r)` alone
had published reproductions as replications with a joined `A || B` outcome
(FLORA-001148, FLORA-002617); the next run splits them, the replication keeping its ID.
The Source Records duplicate detector still keys on the paper alone, so it can catch a
report entered in the wrong sheet; mixed-type groups are labelled there and a
cross-type *duplicate* ruling asks for confirmation. Existing cross-type rulings are
listed in `cross_type_duplicate_rulings.csv` and warned about on every run.

Confirmed decisions write the discarded DOI into `alt_identifier_o`/`alt_identifier_r`;
automatic ones do not. That asymmetry is the original's: recording an alias is a claim
of equivalence, and the automatic rule is a guess until a human agrees with it.

**The review nudge is ported too.** `maybe_open_review_issue()` files — or comments on
— a GitHub issue when pairs are being auto-resolved and
`cache/confirmed_preprint_duplicates.csv` has not been touched for 7 days. Three
outcomes, as in R: no open issue carrying `[preprint-dedup-review]` → file one; an open
one active inside the window → leave it alone; an open one itself stale → comment.

It is **off by default**, and that default is load-bearing: `build()` runs on every
FLoRA tab load, so a default of `True` would file GitHub issues when somebody opens a
page. Only `transform_sources.py --review-issue` turns it on, which is what the nightly
workflow passes. Every failure — no `gh`, no token, no network — is reported and
ignored: a dataset build that already succeeded must not fail on a nudge.

### 8.9.1 What a run writes

Every run also appends to **`logs/flora_pipeline.log`**, whichever way it was
started — the sync button, the nightly Action, cron, or a shell on the server.
[`pipeline_logging.py`](../pipeline_logging.py) tees stdout and stderr into that
file, timestamped and labelled with the component:

```
[2026-09-13 14:52:37] [sync-exclusions] START (pid 31152)
[2026-09-13 14:52:42] [sync-exclusions]   sheet rows: 5
[2026-09-13 14:52:42] [sync-exclusions] END (5.5s)
```

**Teed rather than converted to `logger.info()`.** The console output is read by
humans watching a sync and captured verbatim by `source_sync_runner` into
`source_sync_jobs.log_text`; rewriting 300 `print()` calls would have changed what
both of them see, for no gain.

**`start()` is never called at import.** `transform_sources` is imported by the web
app — `flora_service` builds the FLoRA tab from it — so a module-level call would
redirect the entire web process's output into a pipeline log the moment somebody
opened a page. Every script calls it as the first statement of `main()` instead, and
a test walks the module-level AST of every file to keep it that way.

The file rotates at 5 MB keeping one previous copy, and `logs/` is gitignored. If the
log cannot be opened the run says so on stderr and carries on unlogged: a pipeline
that refuses to run because it cannot write a log file is worse than one that runs
without it.

| Started by | Where the output goes |
|---|---|
| Sync button | `source_sync_jobs.log_text` **and** `logs/flora_pipeline.log` |
| Nightly Action | the Action run log, the artifacts, **and** the file (ephemeral in CI) |
| Terminal / cron | console **and** `logs/flora_pipeline.log` |

| File | Contents |
|---|---|
| `output/flora_entry_sheets.csv` | the dataset, 45 columns |
| `output/flora_export_log.csv` | rows missing a title, with the reason |
| `output/dup_outcome_conflicts.csv` | merged groups whose outcomes disagreed, and how each resolved |
| `output/preprint_dedup_candidates.csv` | every detected pair and the action taken |
| `output/cross_type_duplicate_rulings.csv` | rows ruled a duplicate of a record of the other type (only when there are any) |

The logs are written beside the dataset, so in the nightly job they land in
`output/prepared/` and are uploaded with it by the workflow.

Preprint duplicate pairs nobody has ruled on (`needs_review`, or an unconfirmed
`auto_keep_*`) also become a warning in the preparation report and on the pipeline
job, and are listed for a decision under **FLoRA tab → Preprint duplicates**. A pair
is held as `needs_review`, with both rows kept, when its first authors differ or when
both DOIs are preprints without a matching first author. A ruling there is stored in
`preprint_dedup_decisions`, wins over `cache/confirmed_preprint_duplicates.csv` for
the same pair, shows in the tab at once and reaches the published CSV at the next
run. The run also prints, per step: how many
outcome spellings were recoded and to what, DOI values that are not DOIs, rows excluded
by each rule, cells changed per text-cleaned column, conflicting outcomes, and a closing
coverage table for every column of the output contract.

`dup_outcome_conflicts.csv` currently holds **55** groups — the same paper, the same
`url_r`, disagreeing outcomes. Those are source-data mistakes, not pipeline bugs.

---

### 9.0 Output columns: the FLoRA contract

Two column sets, easy to confuse:

- **`FLORA_COLUMNS`** — the R notebook's `flora_cols`, its *input* selection.
- **`FLORA_OUTPUT_COLUMNS`** — its `output_cols`, the shape of `flora.csv`. **This is
  what both exports now emit.**

All 35 now carry data. 15 come from our own records; the other 20 are bibliographic
enrichment, cached per DOI in `work_metadata` by
[`enrich_works.py`](../enrich_works.py).

**One API replaces the R pipeline's three.** The notebook fetches references from
CrossRef (Step 6b), language from OpenAlex (Step 9) and OA links from Unpaywall
(Step 9c). OpenAlex carries all of it — `biblio` holds volume/issue/pages, `language`
is a field, and `open_access.oa_url` is the Unpaywall data OpenAlex already ingests —
and it answers **50 DOIs per request**: ~100 requests for the whole corpus instead of
~15,000.

Measured coverage over 4,951 unique DOIs: **4,841 works cached (98%)**, 77 not held by
OpenAlex. Per column, across the 3,032 product rows:

| | Original side | Replication side |
|---|---:|---:|
| title / author / year | 98% | 89% |
| journal | 97% | 82% |
| volume / pages | 94% / 95% | 76% / 71% |
| issue | 89% | 67% |
| language | 98% | 82% |
| `oa_url` | 36% | 58% |
| bibtex | 98% | 89% |

The replication side is lower throughout because 9% of rows have no `doi_r` at all —
they are OSF links, which are not OpenAlex works.

| Filled by us | How |
|---|---|
| `doi_o` `doi_r` `url_o` `url_r` `alt_identifier_o` `alt_identifier_r` | source_records |
| `outcome` `outcome_quote` `outcome_quote_source` `type` `source` | the transform |
| `apa_ref_o` `apa_ref_r` | our `ref_o`/`ref_r`, the sheet's own reference strings (100% / 99.9%) |
| `title_*` `author_*` `journal_*` `year_*` `volume_*` `issue_*` `pages_*` `language_*` `oa_url_*` `bibtex_ref_*` | OpenAlex, cached in `work_metadata` |
| `doi_o_hash` `doi_r_hash` | `substr(md5(doi), 1, 3)` — same 3-char bucket as `openssl::md5` in R |

`apa_ref_o`/`apa_ref_r` deliberately keep the **sheets'** reference strings rather than
a synthesised one. The R pipeline prefers CrossRef's formatted citation and falls back
to the sheet (`coalesce(ref_o_clean, ref_o)`); with no CrossRef APA to prefer, the
fallback is simply the value — and it is already real APA at 100% coverage. `bibtex_ref`
IS synthesised from the structured fields, which is what the R side does via
`synthesise_missing_refs_from_fields` when CrossRef returns none.

Seven columns follow the 35 rather than being dropped: `abstract_r`, the six
reproduction axis columns, and the three provenance columns. `abstract_r` feeds
downstream classification and the axes are what the flat `outcome` is *derived from* —
discarding either to match a column list exactly would lose data the database is the
only copy of. Readers select by name, so trailing columns cost nothing;
`to_output_shape(frame, keep_extras=False)` gives an exact 35-column file if something
ever needs one.

---

### 9.1 FLoRA record ids (`flora_registry.py`)

The transform is a pure function, which makes its output reproducible but
**anonymous**: nothing in a produced row can be cited, and nothing links it back to
the record it came from. [`flora_registry.py`](../flora_registry.py) supplies both,
writing to `flora_records` — the only thing that does.

```
flora_id "FRED-000001"  →  primary_source_record_id  →  source_records (FRED-000001)
                           merged_source_record_ids  →  rows dedup collapsed into it
```

**The id is the source record's own `display_id`, pinned.** There is no separate
`FLORA-` series: a FLoRA row carries the id already visible in the Source Records tab
(`REPL-000397`, `FRED-000001`, `VAL-000012`), so there is one namespace to learn and
tracing a published row back needs no lookup.

"Pinned" is the whole point — the id is copied **once**, at first assignment, and then
lives in `flora_records`. It does not follow the primary source record afterwards.
Reading `display_id` live instead would change 207 rows' identity the moment a reviewer
ruled a survivor a duplicate, which is precisely what an identifier must not do.

Collisions are possible but rare, and only via that same route: a record pins
`REPL-000001`, later re-points elsewhere, and `REPL-000001` then returns as its own row
because a reviewer ruled it `distinct`. The newcomer takes `REPL-000001-R2` rather than
stealing an id that may already have been cited.

**Identity follows provenance, not content.** A FLoRA row is "the same record" when
it derives from the same `source_records` row — *not* when its `doi_o|doi_r` key
matches. That key changes the moment a reviewer corrects a DOI, which would silently
mint a new id for a record that has not changed. Source record ids are UUIDs on an
insert-only table, so they are the one thing in the system that genuinely does not
move.

**The survivor can change.** The transform collapses duplicates and keeps the first
row by `display_id`. A reviewer ruling that survivor a duplicate promotes a different
row — same paper, same FLoRA record, different primary source id. Matching therefore
falls back to any overlap between the row's source ids and those an existing record
already claims, and **re-points** the record instead of issuing a new id. Verified
against live data: ruling `FRED-000001` a duplicate re-pointed that record's primary
source to `REPL-000481` while the row kept `FRED-000001` as its id. The grid shows the
new source beside the id whenever the two have diverged, so it is visible rather than
hidden.

**Retirement, never deletion.** A row that stops appearing is stamped `retired_at`.
Ids are never reused and rows are never deleted — a published id must keep resolving
to what it meant.

> **The `::text[]` cast in `_load_existing` is load-bearing.** psycopg2 has no
> `uuid[]` parser registered, so a bare `uuid[]` arrives as the raw `'{a,b}'`
> **string**. Iterating that yields single characters, which silently builds an index
> of punctuation — and every survivor change then looks like a brand-new record. This
> bug was live until the survivor-change test caught it.

### 9.2 Identifiers: what actually exists

`source_records.oa_work_id_o` / `oa_work_id_r` are **empty** — nothing populates them.
[`backfill_oa_work_ids.py`](../backfill_oa_work_ids.py) targets the `unvalidated`
table, and it resolves work ids **by DOI**, so a record with no DOI gets nothing from
it either. Measured coverage:

| | Count |
|---|---:|
| Missing `doi_o` | 29 (0.9%) — none have an OpenAlex id |
| Missing `doi_r` | 300 — but 298 have a `url_r` |
| No replication-side identifier at all | **2** |

So "no DOI but has an OpenAlex work id" is currently a set of **zero** records. This
is why `flora_id` is the pinned `display_id` rather than a DOI/OpenAlex cascade: it
works for all 3,032 rows today, including the 2 with no usable identifier at all.

---

### 9.3 The FLoRA tab

The admin panel's **FLoRA** tab shows the prepared product — the transform's output
with `flora_id` attached — with search, filters, sorting, a detail view and a CSV
export honouring the current filter.

Nothing is materialised. [`flora_service.py`](../flora_service.py) derives the dataset
on demand (~1s) and caches the frame until the underlying tables actually change,
detected by a cheap signature query (row counts plus the newest `updated_at`) rather
than a timer — so an edit in the Source Records tab shows up here immediately rather
than after an arbitrary delay.

| Route | Purpose |
|---|---|
| `GET /api/admin/flora` | One page of the grid, plus filter counts |
| `GET /api/admin/flora/stats` | Row totals and when ids were last assigned |
| `GET /api/admin/flora/export.csv` | Every row matching the filter, all columns |
| `GET /api/admin/flora/{flora_id}` | One full row for the detail panel |

The export uses the same column order as the nightly artifact — `FLORA_COLUMNS` then
`PROVENANCE_COLUMNS` — so the two files are interchangeable rather than subtly
different. `flora_id` is **appended, not prepended**: §9 is explicit that positional
readers of the existing export must keep working.

The read path never writes. `attach_ids()` only joins ids that already exist, so
opening a page cannot assign one. Rows a sync has landed but no refresh has reached
show a blank id, and the tab says so in a banner rather than hiding it.

---

## 10. The GitHub Action

[`.github/workflows/sync-sources.yml`](../.github/workflows/sync-sources.yml) —
**03:00 UTC daily**, plus manual runs from the Actions tab or from the **Run sync
now** button in the admin panel ([§10.1](#101-the-run-sync-now-button)).

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

### 10.1 The "Run sync now" button

The Source Records tab has a sync panel: a **Run sync now** button, live run status,
and the complete console output of each run.

The button **runs the same two scripts on the server** — `sync_sources.py` then
`sync_validated.py` — rather than calling out to GitHub. No token, no API, nothing to
configure: if the app can reach the database and the sheets, the button works.

```
button → POST /api/admin/source-sync/dispatch  (persists a queued row, returns a job id)
       → scheduler on every pod polls the queue every 5s
       → PostgreSQL advisory lock elects ONE executor
       → stages run as subprocesses, log streamed into source_sync_jobs.log_text
       → panel polls /api/admin/source-sync/status every 3s and tails the log
       → grid and freshness banner reload when the run ends
```

**Why a queue and not an inline call.** A sync takes a minute or two, which an HTTP
request cannot hold open. A queued row is also durable: if the pod that accepted the
click dies, another picks the job up instead of the click being silently lost. This
is the same shape as the extractor pipeline in `extractor_maintenance.py`.

**Why subprocesses and not imports.** Both scripts call `sys.exit`, parse `argv` and
hold module-level state. Importing them into a long-lived web process would leak that
state between runs, and a hard crash in either would take the web server down.

**One at a time.** `queue_run()` refuses (409) while a job is queued or running, and
the advisory lock means that holds across pods, not just within one process. A job
left behind by a dead pod is reaped after an hour by `_housekeeping`, so a crash
cannot block the button forever.

**What it does not guard.** The nightly GitHub Action runs the same scripts with its
own connection and cannot see this lock. That overlap is tolerated rather than
prevented: `sync_sources.py` is insert-only with `ON CONFLICT DO NOTHING`,
`sync_validated.py` upserts by a unique key, and display ids come from an atomic
counter — so a concurrent run duplicates effort but not data.

| Route | Purpose |
|---|---|
| `POST /api/admin/source-sync/dispatch` | Queue a run. 409 if one is active. Audited as `source_sync.dispatched`. |
| `GET /api/admin/source-sync/status` | Recent runs with log tails, **and** the per-source results of the last sync |
| `GET /api/admin/source-sync/jobs/{id}` | The complete log for one run |

**Two logs, deliberately.** `source_sync_jobs` records one *run of the button*, with
everything the scripts printed. `source_sync_runs` records what each *source* did —
and the nightly Action writes to it too, so the per-sheet list covers scheduled runs
that never appear in the job list. The job log says what the scripts said; the
per-sheet list says what landed.

**Requirements: none beyond what the app already has.** The server needs
`DATABASE_URL` and outbound access to `docs.google.com`. There is no token and no
GitHub API involved.

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
