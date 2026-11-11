# The stored FLoRA dataset

The stored dataset has its own PostgreSQL table, `flora_data`. It stores the
fields of `flora.csv` together with permanent IDs and publication positions.
The title/DOI search and ID-hash lookup APIs read this table without rebuilding
the dataset or changing its IDs.

Storage, Python lookup helpers, and public HTTP routes are implemented. See the
[public API guide](FLORA_API.md) for requests, responses, and browser access.
The production migration and initial data import have not been applied in this
session.

## Published CSV codebook

Fields are text; `NA` is missing. Suffix `_o` refers to the original study and
`_r` to its replication/reproduction report. Author arrays and citations may
contain quotes or newlines: read the file with a CSV parser. Encoding is UTF-8
with BOM. See the bundled FAQ for outcome definitions and coding decisions.

| Field | Definition |
| --- | --- |
| `id` | Permanent published record ID; stable through corrections and retirement. |
| `id_md5` | Full MD5 of the permanent id, for lookup; not a DOI hash. |
| `doi_o` | DOI of the publication, when available. Original study. |
| `alt_identifier_o` | Alternative publication identifiers, including preprints. Original study. |
| `doi_o_hash` | Short original-DOI hash; distinct from id_md5. |
| `title_o` | Publication title. Original study. |
| `author_o` | Author metadata as a JSON array. Original study. |
| `journal_o` | Publication journal or venue. Original study. |
| `year_o` | Publication year. Original study. |
| `volume_o` | Journal volume. Original study. |
| `issue_o` | Journal issue. Original study. |
| `pages_o` | Publication page range or article number. Original study. |
| `apa_ref_o` | Formatted APA reference. Original study. |
| `bibtex_ref_o` | BibTeX citation. Original study. |
| `url_o` | Publication URL. Original study. |
| `language_o` | Publication language code. Original study. |
| `doi_r` | DOI of the publication, when available. Replication/reproduction report. |
| `alt_identifier_r` | Alternative publication identifiers, including preprints. Replication/reproduction report. |
| `doi_r_hash` | Short report-DOI hash; distinct from id_md5. |
| `title_r` | Publication title. Replication/reproduction report. |
| `author_r` | Author metadata as a JSON array. Replication/reproduction report. |
| `journal_r` | Publication journal or venue. Replication/reproduction report. |
| `year_r` | Publication year. Replication/reproduction report. |
| `volume_r` | Journal volume. Replication/reproduction report. |
| `issue_r` | Journal issue. Replication/reproduction report. |
| `pages_r` | Publication page range or article number. Replication/reproduction report. |
| `apa_ref_r` | Formatted APA reference. Replication/reproduction report. |
| `bibtex_ref_r` | BibTeX citation. Replication/reproduction report. |
| `url_r` | Publication URL. Replication/reproduction report. |
| `language_r` | Publication language code. Replication/reproduction report. |
| `oa_url_o` | Open-access full-text URL, when known. Original study. |
| `oa_url_r` | Open-access full-text URL, when known. Replication/reproduction report. |
| `outcome` | Replication outcome. Legacy export rows for reproductions also contain a combined rendering of the two reproduction axes here; analyse reproduction outcomes using outcome_computation and outcome_robustness instead. See release-specific categories and FAQ. |
| `outcome_quote` | Supporting text for the outcome; for reproductions this combines the two axis quotes, with axis labels when needed. |
| `outcome_quote_source` | Location/source of the supporting text; reproduction sources can combine both axes. |
| `type` | Attempt type: replication (new observations) or reproduction (existing data). |
| `source` | Retained primary input route after deduplication, not a complete list of contributing sources or a validation-status field. Legacy label COS maps to the FReD Replication Success input; see source labels below. |
| `oa_work_id_o` | OpenAlex work identifier. Original study. |
| `oa_work_id_r` | OpenAlex work identifier. Replication/reproduction report. |
| `author_overlap` | Number of distinct shared family names in the original and report author lists, matched case-insensitively; not a yes/no flag or verified person identity. |
| `author_overlap_pct` | 100 × shared distinct family names / number of report authors with usable family names, rounded to one decimal. Missing if either list is absent, malformed, or contains any entry without a usable family name (including a collective author). Given names are optional. Plain bylines use the same normalization as export. Matching surnames can conflate different people. |
| `abstract_r` | Report abstract. |
| `outcome_computation` | Computational reproducibility outcome, coded separately from robustness. Category labels are specific to the released snapshot; see the release-specific vocabulary. |
| `outcome_computational_quote` | Supporting text for computational reproducibility. |
| `out_quote_computational_source` | Location/source for the computational quote. |
| `outcome_robustness` | Robustness outcome, coded separately from computational reproducibility. Category labels are specific to the released snapshot; see the release-specific vocabulary. |
| `outcome_robustness_quote` | Supporting text for robustness. |
| `out_quote_robust_source` | Location/source for the robustness quote. |
| `flora_id` | Pinned source-facing FLoRA identifier; distinct from published id. |
| `source_display_id` | Primary source record display identifier. |
| `source_record_id` | Primary source record UUID. |

### Source labels

| Export value | Input route |
| --- | --- |
| replications | Replication entry sheet |
| reproductions | Reproduction entry sheet |
| COS | Legacy alias for the FReD Replication Success input |
| SCORE | SCORE 2025 Validation input |
| validated | Website validation workflow |
| manual | Records added manually in the FLoRA editor |

A deduplicated record retains one route label even when merged records contribute
other values. These labels do not establish all project memberships or whether a
record is validated: the entry sheets also contain accepted validation decisions.
The `type` column identifies replication versus reproduction independently of route.

### Release source labels

Established projects take priority over intake route, including records from the
search pipeline. `release/flora/projects.yml` names projects using existing report
identifiers or a known project-specific intake. Normalized `doi_r`, the multiple
identifiers in `alt_identifier_r`, and direct report links in `url_r` match records
across intake routes. Original-study identifiers are never used for this purpose.

A project qualifies at strictly more than 50 distinct active released record IDs,
after deduplication/exclusions. Qualification is recorded against a snapshot; its
label persists if later retirements reduce the count. Matching records also seed
one-hop report aliases; matches do not recursively join unrelated identifier chains.
Conflicting project matches require review. New shared-identifier groups above the
threshold appear in `project_classification.json` for naming/review and block
production until mapped. Text mentions are review hints only, not memberships.

| Registered project label | Established report anchor or intake |
| --- | --- |
| RP:P | 10.1126/science.aac4716 in report or alternative report identifiers |
| Student replication projects (Boyce et al.) | 10.1098/rsos.231240 in report or alternative report identifiers |
| SCORE | Dedicated SCORE intake and shared identifiers of its reports |

For records without an established project match, the intake labels are:

| Release value | Internal export values | Meaning |
| --- | --- | --- |
| manual entry | replications, reproductions | Entries from the manual entry sheets |
| FReD | COS | FReD Replication Success input |
| SCORE | SCORE | Dedicated SCORE project intake |
| search pipeline | validated, manual | Website validation and manual-entry workflow; not necessarily automated discovery |

The field records a project where identifiable, otherwise an intake category. It
is not a validation-quality level and does not retain all contributing routes in
one value. No prefix-based discovery-origin inference is used. Raw route keys stay
in the internal snapshot; the manifest records the project registry and the
classification report records matched IDs, counts and identifiers.

### Author-overlap release validation

The overlap calculation rejects partial individual/collective lists as well as
collective-only lists. Final export recalculates against the normalized bylines,
so a plain organisation name cannot be treated as a surname before it becomes a
literal collective author in the CSV. Existing stored snapshots require a normal
pipeline rebuild to incorporate this derivation fix; release assembly verifies the
stored percentage and refuses stale values rather than editing them.

## Relationship to the existing tables

| Table | Purpose |
| --- | --- |
| `source_records` | Imported source records, never edited by hand, and duplicate rulings |
| `manual_records` | Records an admin added by hand in the FLoRA tab; each is projected into `source_records` as a `MAN-` row ([details](SOURCE_RECORDS.md#33-records-added-by-hand-manual)) |
| `flora_records` | Permanent identity and provenance registry for transformed records |
| `flora_record_changes` | Change log of FLoRA records: who, note, before → after; re-applied by every build ([details](SOURCE_RECORDS.md#94-correcting-a-flora-record-flora_editspy)) |
| `flora_data` | Complete stored preparation output, including retired published records |
| `flora_data_metadata` | Export column order and SHA-256 of the latest imported snapshot |

`flora_data` has no source foreign key or cascading delete. A published record
retains its own data and identity independently of the source review tables.
The existing website preview remains a view of the current transformation;
the new table changes when a complete preparation snapshot is stored, and when an
admin saves a change to a FLoRA record: that rewrites the record's row at once,
increases its `record_version` and moves `flora_data_metadata.updated_at`.
`snapshot_sha256` then still names the last imported snapshot, which no longer
equals the table byte for byte until the next run stores one with the change applied.
A pipeline run notes the newest change as it starts (part of `dataset_revision`); when
only changes have landed since, its store keeps those records' rows as they are
instead of failing. Another snapshot stored meanwhile still fails it.

## Permanent identity and changing data

| Field | Meaning |
| --- | --- |
| `id` | Permanent primary key copied from `flora.csv` |
| `id_md5` | Full 32-character lowercase MD5 of the UTF-8 `id`, generated by PostgreSQL |
| `export_position` | Permanent ordering position; never renumbered |
| `record_version` | Starts at 1; increases when stored values change, or a record retires or reactivates |
| `created_at` | Time the record first entered this table |
| `updated_at` | Time its stored data or retirement state last changed |
| `retired_at` | Null for active records; timestamp for records absent from a later snapshot |

A title, DOI, citation, or outcome correction updates the existing record while
preserving its `id` and `id_md5`. Repeating an unchanged import leaves its
`record_version` and `updated_at` unchanged. The version is a change counter;
this table does not store a separate copy of every past record version.

The supplied 2,914-row snapshot already has IDs `FLORA-000001` through
`FLORA-002914`. The normal preparation pipeline pins further IDs in the identity
registry before producing its CSV. `flora_store.py` never creates IDs from
titles, DOIs, or other editable content. An imported CSV must retain the existing
ID when correcting an existing record: an unfamiliar ID means a new record.

`id_md5` is distinct from the existing short `doi_o_hash` and `doi_r_hash`
fields. It is a lookup key, not a password or authentication token. Its database
uniqueness constraint rejects a conflicting hash instead of allowing an
ambiguous lookup.

Database triggers prevent changing stored IDs or publication positions and
prevent deletion or truncation of the permanent identity tables. Registry and
source IDs receive corresponding protections. Normal updates use retirement
instead of deletion; a previously published ID continues to resolve.

## Complete fields and ordering

The first 37 CSV fields are stored explicitly: `id`, `id_md5`, then all 35
original FLoRA columns in the supplied order. Original data fields use SQL
`TEXT`, preserving values such as years, citation markup, and JSON author strings
without converting their formats. Missing `NA` values are stored as nulls and
exported as `NA` again.

Additional prepared fields go into `extra_fields` as JSONB. Their column names
and order are those of the latest stored snapshot, kept in `flora_data_metadata`:
a column the snapshot no longer has is no longer exported.
CSV export expands these fields back into normal columns after the original 37.
Database metadata such as `record_version` is excluded from this CSV.

Reference records retain the positions reserved by the supplied snapshot.
The table also honors positions already assigned by `flora_records`, including
gaps for records temporarily omitted from an export because a title is missing.
Existing stored records keep their positions; IDs with no assigned position
append after every reference, registry, and stored position. Registry allocation
also reserves IDs and positions already present in `flora_data`, so a direct
CSV import cannot have its identity reassigned by a later registry refresh.
Retired positions remain reserved. Reads and exports explicitly sort by
`export_position` because SQL tables have no implicit row order.

## Complete-snapshot import semantics

Every import represents the entire current dataset, not a partial
batch or filtered download:

- An existing ID updates its stored values in place.
- A new ID inserts one row with a permanent position.
- An active ID missing from the snapshot is retired, retaining its last data.
- A retired ID appearing again reactivates its existing row, ID, and position.
- Ordinary lists and exports include active rows. ID and hash lookups can also
  resolve retired rows and identify their status.

Empty snapshots are rejected by default. Missing, repeated, or malformed IDs,
incorrect hashes, duplicate headers, and incompatible column layouts fail
before database changes. The import locks the table while applying its updates;
the caller commits or rolls back the whole database transaction. A failure in
that transaction preserves the previously committed table.

`flora_data_metadata.snapshot_sha256` identifies the incoming CSV.
The export serializer uses UTF-8 with a BOM, quoted fields, and the stored
publication order. A manually reordered input or different CSV serialization
can therefore have a different byte hash from a subsequent table export even
when its parsed record values match.

## Initial import

Configure `DATABASE_URL` for the intended PostgreSQL database in the existing
environment or `.env`. From the repository root, import the supplied prepared
snapshot and apply the idempotent schema:

```powershell
.\.venv\Scripts\python.exe flora_store.py --input output/flora.csv --init-schema
```

The command applies `db_schema.sql` and imports the snapshot in one transaction.
It prints inserted, updated, unchanged, retired, and reactivated counts plus the
incoming snapshot hash. Subsequent full-snapshot imports can omit
`--init-schema` once the schema is current.

## Pipeline integration

Normal database preparation stores its final candidate automatically after
identity validation, structural/network checks, and release preparation:

```powershell
.\.venv\Scripts\python.exe prepare_flora.py --output-dir output
```

The preparation report contains a **store dataset** stage and a
`storage` object with row counts, the snapshot hash, and `status: "committed"`
after the database transaction succeeds. A separate **write output files**
stage then replaces `flora.csv` in the output folder. Review findings give the
status `needs_attention` (shown as *needs review*) and the dataset is still stored;
an execution failure prevents successful completion of its affected stage.

An existing CSV remains an offline input by default. To validate and explicitly
store a complete CSV snapshot, use:

```powershell
.\.venv\Scripts\python.exe prepare_flora.py --input output/flora.csv --output-dir output --network-checks none --store-data
```

`--network-checks none` skips network checks and records that limitation in the
report. `--no-store-data` prepares files without updating `flora_data`.
The direct `flora_store.py` import validates the storage/identity contract;
use `prepare_flora.py` when structural and network validation reports are needed.

The nightly job runs the first command and commits `output/` when `flora.csv`
changed. After it, the Publish FLoRA workflow writes the stored dataset to `output/`
with `publish_flora.py` and commits it the same way when it changed; it can also be
started by hand, for instance after a website **Run pipeline**, which stores its
dataset but cannot commit. The repository's `output/flora.csv` is therefore the
stored snapshot as of the last nightly run or manual publish. The stored columns are
always the latest snapshot's, in its order, so this export and that file have the same
header.
**Download full CSV** in the FLoRA tab exports `flora_data` with
`flora_store.export_csv`: that snapshot, plus any corrections saved in the tab
since, which rewrite their rows in `flora_data` at once. New source records and
duplicate decisions reach the download at the next pipeline run, and the repository
file after the next nightly job (after a website run, sooner by starting Publish FLoRA
by hand).

### Recovery when flora.csv cannot be replaced

Database commit and filesystem replacement are separate operations. If replacing
`flora.csv` fails after the database commit, the run reports a failed
**write output files** stage while `storage.status` remains `"committed"`.
The database contains the new snapshot even though `flora.csv` may still be
the preceding file. This condition does not roll back the database import.

The committed CSV bytes are retained as `flora_committed_recovery.csv` in the
output directory. If writing that recovery file also fails, they remain at
`.flora.candidate.csv`. The JSON report's `recovery_csv` field and the Markdown
report identify the retained file. Preserve that file, resolve the filesystem
problem, and restore `flora.csv` from those bytes. In particular, copy
the fallback `.flora.candidate.csv` before starting another pipeline run,
which normally clears that temporary path. Do not treat this failure as an
uncommitted database import when deciding whether to retry.

For website runs, the runner saves these bytes in `source_sync_jobs.recovery_csv`
before removing its temporary workspace. The failed job exposes a separate
**Download recovery CSV** button through the authenticated
`/api/admin/source-sync/jobs/{job_id}/artifacts/recovery.csv` endpoint. The report
sets `recovery_artifact` to `recovery.csv`; that run has no **Download flora.csv**
button. If saving the run's files fails, the workspace is kept and its path appears
in the runner error (`Run files kept at …`) for local recovery.

Preparations targeting the same output directory wait on a shared operating-system
file lock before touching candidates or reports. The persistent
`.flora-preparation.lock` file is normal; ownership is automatically released when
the process closes or exits. Runs targeting separate folders can proceed
concurrently, with the database revision check preventing stale publication.

## Python storage helpers and HTTP APIs

The helpers in [`flora_store.py`](../flora_store.py) use a PostgreSQL
`RealDictCursor`. They perform read-only queries:

```python
import flora_store

flora_store.list_records(cur, title="sentence memory", limit=100, offset=0)
flora_store.list_records(cur, doi="https://doi.org/10.1016/0010-0285(72)90003-5")
flora_store.get_record_by_hash(cur, "2de91c651bc82ac3d5e43031660a22b6")
flora_store.get_record(cur, "FLORA-000001")
flora_store.export_csv(cur)
```

Title search uses PostgreSQL full-text search across original and replication
titles. DOI lookup normalizes prefixes, whitespace, and case on both the query
and indexed DOI fields, preserving the original CSV cells. Providing both filters
requires both to match. Lists return `records`, `total`, `limit`, and `offset`, with a maximum
page size of 500. Pass `include_retired=True` to lists or exports to include
retired records.

Hash lookup requires the complete 32-character MD5 and accepts uppercase input.
An unknown ID/hash returns `None`. Returned records contain all publication
fields plus `_meta` with position, version, timestamps, and `active` status.

These storage helpers expose stored relationship rows. The public `/v1` DOI
routes build paper aggregates from the same active snapshot, while
`/v1/id-lookup` returns a row by its full permanent ID hash, including retired
rows. The [API guide](FLORA_API.md) describes their distinct shapes, search
ranking, and public field allowlist. API reads preserve the existing CSV.

## Dataset size history

Each pipeline run stores the day's size in `flora_dataset_history`: one row per
UTC day with the total, replications, reproductions and source records, marked
`measured_by = 'pipeline'`. `GET /api/flora/history` serves it and the public
data report, [`docs/dataset.html`](dataset.html), charts it. A day without a run
carries the previous figure forward and is marked as carried.

The pipeline first counted on 13 September 2026. For the days before, from the
validator launch (the first validator account), `backfill_flora_history.py`
counts the `output/flora.csv` that the FReD data repository published each day
and stores it with `measured_by = 'flora.csv'`:

```bash
git clone --no-checkout https://github.com/forrtproject/fred-data.git ../fred-data
python backfill_flora_history.py --fred-data ../fred-data            # dry run
python backfill_flora_history.py --fred-data ../fred-data --apply
```

These are counts of what was published on each day, not older records re-counted
under today's rules, so the line can step where the pipeline takes over. The
script writes nothing from the pipeline's first count on, even with `--until`, and
only ever inserts: a day that already has a row is never changed. fred-data's
history has since been rewritten (`output/flora.csv` now starts on 23 Oct 2026), so
a re-run could only make the counts already written worse. `--apply` refuses until
the deployed schema has the `measured_by` column. The pipeline writes `measured_by`
once the column exists and leaves it out until then, so the nightly job does not
depend on the site being redeployed first. All days are UTC calendar days, whatever
the database session's time zone.

`--before-launch` reaches back to the old fred-data pipeline: the root `flora.csv`
it published from 11 Nov 2025 until the 13 Jan 2026 restructure moved it to
`archive/`. It writes only days before the launch that have no row, marked
`measured_by = 'fred-data'`, and refuses `--apply` until the deployed schema takes
that value. `release/latest_data/flora.csv` and `archive/flora.csv` only hold
copies of root versions and are not read. Versions before 13 Jan 2026 have no type
column, so their replications and reproductions are NULL:

```bash
python backfill_flora_history.py --fred-data ../fred-data --before-launch            # dry run
python backfill_flora_history.py --fred-data ../fred-data --before-launch --apply
```

Between the two, `output/flora.csv`'s early versions are gone from fred-data's
branches. Two records of them survive, and two more modes count them. Both write
`measured_by = 'flora.csv'`, since they measure the same file, and both write only
days before the launch that have no row:

- `--pull-snapshots` counts the versions still reachable from
  `refs/pull/102/head`, which GitHub keeps: from the first version (20 Jan 2026) to
  the day before the README log starts (15 Mar 2026), dated like the default mode.
  Those versions contain records that were deleted on purpose. So the ref is
  fetched, without file contents, into a temporary bare repository that is deleted
  when the run ends. It is never fetched into `../fred-data`, and only counts leave
  the temporary repository. `--pull-remote` and `--pull-ref` override the source.
- `--readme` takes the counts from the update log in fred-data's `README.md`: the
  table between `<!-- FLORA_UPDATE_LOG_START -->` and
  `<!-- FLORA_UPDATE_LOG_END -->`, one row per day from 16 Mar 2026. It writes
  16 Mar to 20 May 2026. The log lacks 25 to 28 Apr, so those days carry 24 Apr's
  count. It stops if the markers or the table are missing.

```bash
python backfill_flora_history.py --fred-data ../fred-data --readme                 # dry run
python backfill_flora_history.py --fred-data ../fred-data --pull-snapshots         # dry run
python backfill_flora_history.py --fred-data ../fred-data --readme --apply
python backfill_flora_history.py --fred-data ../fred-data --pull-snapshots --apply
```

The root `flora.csv` moved away on 13 Jan 2026, and `output/flora.csv` first
appeared on 20 Jan. So 14 to 19 Jan had no published file and have no counts. The
old pipeline's last count is not carried into them, and the data report draws that
stretch as a dotted line. The "fred-data pipeline" band runs from the first count to
the launch line, because every count before the launch is that pipeline's.

`latest` in the history payload carries what the page needs from beyond the
400-day daily window: the count at the validator launch (`launch_total_rows`), the
first count (`first_counted_on`), the first pipeline count after flora.csv
counts (`pipeline_from`) and the stretch with no counts (`no_counts_from`,
`no_counts_until`).

The data report shows FLoRA only by default. **Show source records** adds the
source-record line, table column and CSV column. Once flora.csv days exist, the
summary also shows the growth since the validator launched.

## Verification

On 16 September 2026, the complete Python suite passed **1,268 tests**, including
the opt-in PostgreSQL checks. Browser pipeline checks and JavaScript syntax checks
also passed. The single Python warning came from an existing Google SDK deprecation.

Checks against a disposable local PostgreSQL database imported the complete
2,914-row supplied snapshot and exported it with byte-for-byte equality to
`output/flora.csv`. Reimporting the same snapshot left its records unchanged.
Database checks also covered permanent identity during edits, retirement and
reactivation, and title/DOI/hash lookups. These checks did not use or modify the
production database.

## Keeping IDs permanent across operations

Back up the complete PostgreSQL database, including `source_records`,
`flora_records`, `flora_data`, and `flora_data_metadata`. Restore these tables
together so the record identities, provenance, reserved positions, and retired
records remain available. A current CSV alone omits retired rows and registry
history and is not a complete identity backup.

The guards protect ordinary database operations; database owners can still
drop databases or disable triggers. Operational permanence therefore also
depends on retaining and restoring the database rather than rebuilding IDs
from edited data.
