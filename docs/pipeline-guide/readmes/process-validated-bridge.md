# Approved records join Source Records

sync_validated.py copies the application's approved records into the same input collection as the entry sheets, so one final preparation can combine both routes.

[Open interactive guide](../index.html#view=overview&node=process-validated-bridge)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- Rows in the validated table after human/admin publication decisions.

## What happens

1. Shape each validated record into Source Records columns and preserve the approved row's identity as its sheet_row_id.
2. Insert missing records; update changed, unreviewed rows while retaining their display IDs.
3. Protect legacy rows already marked as human-reviewed.
4. Mark source rows deleted when their original validated row no longer exists; keep their identities and provenance.

## What is saved, and where

- source_records under the app-validation source, plus source_sync_runs counters.

## When it updates

- Runs during Source Records → Run pipeline and the nightly 03:00 UTC Sync entry sheets workflow.
- Approving a validation record does not itself run this bridge or build the final FLoRA CSV.

## What comes out

- The app's approved data is available to the combined FLoRA transform.

## Keep in mind

- This is different from export_validated.py, which writes a separate validated-only CSV.
- A protected reviewed row may differ from its current validated origin and needs human investigation.

## Find it in the code

- [sync_validated.py:145 — sync](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_validated.py#L145) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_validated.py)
- [sync_validated.py:233 — retire_missing](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_validated.py#L233) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_validated.py)
- [source_sync_runner.py:60 — STAGES](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/source_sync_runner.py#L60) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/source_sync_runner.py)
- [db_schema.sql:2148 — retire_validated_source_row](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/db_schema.sql#L2148) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/db_schema.sql)
