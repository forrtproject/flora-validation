# Source Records vs FLoRA: how the tabs connect

Source Records shows the imported inputs. FLoRA shows a current combined view built from those inputs, rules, metadata and saved corrections. The full CSV and public API read a third layer: the stored prepared dataset, flora_data.

[Open interactive guide](../index.html#view=overview&node=compare-tabs)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Accepted published-sheet records, approved app records after synchronization, and explicitly published disagreement records.
- Exclusions, duplicate decisions, outcome mappings, paper metadata, the identity registry and FLoRA corrections.

## What happens

1. Open Source Records to inspect an imported row, its raw source values and its provenance. Its detail panel links to registered FLoRA records that it feeds.
2. The FLoRA service builds a combined view: transform the source records, apply rules and deduplication, attach permanent identities and apply correction history. It reuses cached results only while its inputs remain unchanged.
3. Open FLoRA to correct the derived record, restore changes, settle outcome conflicts, inspect checks and decide preprint duplicates. Source Records also has its own duplicate-import decisions.
4. Run pipeline in Source Records to refresh inputs, prepare and check the candidate CSV, and store the complete dataset in flora_data.
5. Download full CSV and the public API read active stored rows. Export filtered CSV reads the current FLoRA tab view. A pipeline-run download preserves that particular run's snapshot.
6. Publish FLoRA or the successful nightly release updates the repository copy separately.

## What is saved, and where

- Imported input: source_records. Sync evidence: source_sync_runs.
- Source duplicate rulings: source_records and source_record_edits. Preprint rulings: preprint_dedup_decisions.
- FLoRA field corrections: flora_record_changes, plus an eligible existing active row in flora_data and its revision.
- Prepared full dataset: flora_data and flora_data_metadata. Website job, logs and exact run artifacts: source_sync_jobs.
- A correction in FLoRA does not write back to Google Sheets or overwrite the imported input cells.

## When it updates

- A saved input ruling or correction appears in the derived FLoRA view on its next fetch/rebuild; Refresh only rereads data.
- An eligible saved field correction updates its already-stored active row immediately after commit. Public HTTP caches may still show an older response.
- Duplicate rulings and newly available records need preparation to reconcile the full stored dataset.
- Google sheet sync is insert-only for an existing source/UUID: changing its spreadsheet cells is not a way to overwrite an imported record.
- The nightly combined pipeline is configured for 03:00 UTC. Manual Run pipeline can run earlier. GitHub publication is a separate step.

## What comes out

- Source Records Export CSV: filtered imported rows.
- FLoRA Export filtered CSV: the current derived view under its filters.
- FLoRA Download full CSV and public API: stored prepared active rows.
- Run-history CSV: an immutable snapshot for that run; GitHub output/flora.csv: the committed release.

## Keep in mind

- The two tabs do not need to have the same row count: several inputs can be merged into one derived record, and excluded inputs can disappear from the final view. Filters and retirement also affect counts.
- The FLoRA tab is not a direct table viewer for flora_data. Its view can include data that the next preparation run will add to the stored snapshot.
- The pipeline uses multiple transactions. If it fails, inspect its report: some earlier stages may already have saved changes, and a late failure can occur after dataset storage.
- Examples in this comparison are illustrations of code behavior, not actual production records.

## Find it in the code

- [source_records_service.py:179 — list_records](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py#L179) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py)
- [source_records_service.py:265 — get_record: raw values, history and FLoRA links](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py#L265) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_records_service.py)
- [flora_service.py:159 — _current: build the derived FLoRA view](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L159) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [flora_service.py:484 — _changed: correction without editing source inputs](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L484) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [flora_service.py:531 — merged_sources: provenance in FLoRA](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L531) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [flora_edits.py:759 — publish: immediate eligible stored-row correction](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L759) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
- [app.py:6070 — admin_flora_export: current filtered vs stored full export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6070) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [source_sync_runner.py:269 — _execute: refresh, prepare and store](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py#L269) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/source_sync_runner.py)
- [sync_sources.py:325 — sync_source: sheet import](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/sync_sources.py#L325) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/sync_sources.py)
