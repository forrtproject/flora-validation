# Save a FLoRA correction

Save records a correction with its author and history, then updates the prepared view. If that record already has an eligible active stored row, it also updates the data served by the API and full CSV immediately.

[Open interactive guide](../index.html#view=overview&node=process-save)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- Edited fields, optional per-field notes and the version of the FLoRA record opened in the editor.

## What happens

1. Validate field values and refuse a stale editor version if another person saved first.
2. Append the effective corrections and change history under the permanent FLoRA identity.
3. Rebuild the record's derived values, including consequences such as metadata for a corrected DOI.
4. Write changed fields to its active flora_data row and move the public dataset revision when eligible.

## What is saved, and where

- flora_record_changes is the append-only correction history.
- flora_data and flora_data_metadata update for eligible already-stored records.
- Raw imported sheet cells and upstream extractor output remain their original inputs.

## When it updates

- The corrected record appears in the next current FLoRA view request.
- Eligible stored corrections are visible to a fresh API server request after commit, subject to downstream HTTP caches.
- Repository output/flora.csv updates only after a nightly build or the Publish FLoRA Action.

## What comes out

- An auditable correction that persists through future builds, and an updated stored record where applicable.

## Keep in mind

- Save does not write back to Google Sheets or commit GitHub.
- If the row is not stored yet, is retired, or becomes untitled, direct stored publication does not happen; run the pipeline to reconcile the whole snapshot.
- Restore is another logged change; history is not erased.

## Find it in the code

- [flora_edits.py:637 — save](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L637) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
- [flora_edits.py:692 — restore](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L692) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
- [flora_service.py:513 — _changed](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py#L513) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py)
- [flora_edits.py:921 — publish](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L921) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
- [app.py:7313 — admin_flora_record_update](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7313) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
