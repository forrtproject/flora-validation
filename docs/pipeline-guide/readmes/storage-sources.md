# Source Records: the combined input

source_records is the shared input collection for final preparation. It combines published sheet rows, approved extractor reviews, separately published disagreement records and records added by hand in the FLoRA tab.

[Open interactive guide](../index.html#view=overview&node=storage-sources)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- Accepted Google entry-sheet rows.
- Approved validated rows projected by sync_validated.py.
- Explicitly published Observatory adjudication decisions, when that feature is enabled.
- Records added by hand in the FLoRA tab (source manual, MAN- display IDs), written by manual_records.py on every save.

## What happens

1. Keep source identity and original values so each combined record can be traced back to its input.
2. Apply exclusions, duplicate decisions, outcome rules and FLoRA corrections when building the derived dataset.

## What is saved, and where

- PostgreSQL source_records; source_sync_runs records refresh outcomes.
- Corrections belong in flora_record_changes, rather than overwriting imported sheet values.

## When it updates

- The Source Records grid reloads the imported input. The FLoRA grid derives the current combined view.
- The stored complete CSV and public API update through preparation, with eligible existing-row corrections applied immediately.

## What comes out

- Input to transform_sources.py and the identity registry.

## Keep in mind

- Source Records, the derived FLoRA grid and stored flora_data are three distinct views of data.

## Find it in the code

- [db_schema.sql:1223 — source_records](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/db_schema.sql#L1223) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/db_schema.sql)
- [sync_validated.py:138 — sync](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_validated.py#L138) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sync_validated.py)
- [transform_sources.py:184 — load_rules](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/transform_sources.py#L184) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/transform_sources.py)
