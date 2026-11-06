# Entries

The review desk for papers imported from flora-extractor. Compare the extracted record, human judgements, and AI result; decide which records belong in the approved validation dataset.

[Open interactive guide](../index.html#view=admin&node=admin-entries)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- unvalidated records, validation_queue judgements, record_metadata, skip history, and any existing validated row.

## What happens

1. Filter or search the table, then open Review to see the evidence and current decision.
2. In the Pending Approval filter, open a row to see what approving publishes and what still needs a look, then Approve, Flag for review or leave a note there. To change anything, open Review, edit the fields and choose Mark as Resolved.
3. Other originals adds other original studies of a replication or reproduction, each as a validated record of its own: at once on an entry that is validated and admin-checked, or drafted while reviewing an entry the LLM and both validators have judged, and saved with Mark as Resolved.

## What is saved, and where

- Approved/resolved values: unvalidated.final_* plus the validated table.
- Admin decision history: admin_decisions. Notes: unvalidated.admin_notes, note_saved_by, note_saved_at.

## When it updates

- The Entries list changes after a successful request and refetch.
- Approving a validation entry does not rebuild flora.csv: sync_validated.py brings approved records into Source Records during the next FLoRA pipeline; preparation then updates the stored dataset.

## What comes out

- An approved validation row, an excluded row, or a row returned for review.

## Keep in mind

- Changing fields in the form is not a save.
- An admin who judged the record may be prevented from deciding it; the server checks the self-approval policy.
- Skip in the admin review modal only moves to the next entry; it is different from a validator recording a skip.

## Buttons and actions

### [Filters, search, sorting, pagination, Review](admin-entries--filters-search-sorting-pagination-review.md)

Reads matching entries and their details; no study decision is changed.

**Saves:** No database write.

**Visible:** On fetch.

### [Approve](admin-entries--approve.md)

In an open Pending Approval row: re-reads the entry and stops if it changed, asks a second time if there is any warning, such as a paper's originals differing on the link or abstract, then POST /api/admin/entries/{id}/approve accepts the agreed values and marks the entry validated. Other filters link to the row instead.

**Saves:** unvalidated status/admin fields; validated row; admin_decisions.

**Visible:** Entries immediately; FLoRA input after sync_validated, stored output after a successful pipeline.

### [Save Note](admin-entries--save-note.md)

Saves only the admin note, including who saved it and when. An empty note clears it.

**Saves:** unvalidated.admin_notes, note_saved_by, note_saved_at.

**Visible:** After success; visible to all admins when the record reloads. It does not approve or publish the record.

### [Mark as Resolved](admin-entries--mark-as-resolved.md)

POST /resolve validates the edited type, original, outcome and evidence; saves the final decision. For reproductions both axes are required. Other originals drafted in the entry are created in the same transaction; if one is refused, nothing is saved and the message names the original, the field and what is wrong.

**Saves:** unvalidated.final_* and admin fields; replaces this record's validated row; admin_decisions; drafted other originals as new unvalidated + validated rows.

**Visible:** After commit; then downstream sync/preparation is required.

### [Merge duplicate (conflict dialog)](admin-entries--merge-duplicate-conflict-dialog.md)

If the proposed identity already belongs to another validated record, explicitly keep that survivor and link this duplicate to it. Its added and drafted other originals move to the survivor; if the survivor cannot take them, nothing is saved.

**Saves:** validated_record_merges; duplicate is rejected in unvalidated, removed from validated, and open assignments completed; survivor preserved.

**Visible:** After confirmed resolve succeeds; downstream source retirement is triggered on deletion.

### [It's a replication / It's a reproduction](admin-entries--it-s-a-replication-it-s-a-reproduction.md)

Changes the edit form to the other FLoRA type and shows its fields.

**Saves:** Nothing until Mark as Resolved.

**Visible:** Form changes immediately; database only on resolve.

### [Reject — not in FLoRA](admin-entries--reject-not-in-flora.md)

After confirmation, resolves as not_validation: neither a replication nor a reproduction.

**Saves:** unvalidated becomes rejected; existing validated row removed; decision audit written.

**Visible:** Entries immediately; derived FLoRA view follows source retirement; stored release waits for pipeline.

### [Flag for Review / Send back for review](admin-entries--flag-for-review-send-back-for-review.md)

Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

**Saves:** unvalidated status/admin note; validated deletion for auto-validated rows; admin_decisions.

**Visible:** After success; the validated-source trigger marks its source row deleted at commit.

### [Flag / Unflag a judgement](admin-entries--flag-unflag-a-judgement.md)

Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

**Saves:** validation_queue.flagged and flag_reason; optional validator_messages.

**Visible:** Immediately after the request; validator sees the message when their inbox refreshes.

### [Skip → / Cancel](admin-entries--skip-cancel.md)

Moves to the next entry, or closes the detail window.

**Saves:** No resolution is saved; Save Note is a separate action.

**Visible:** Immediately in the browser.

## Find it in the code

- [docs/app.js:5418 — fetchAdminEntries](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L5418) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:5670 — renderAdminDetail](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L5670) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4716 — admin_entries](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4716) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:4915 — admin_entry_detail](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4915) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [db_schema.sql:2114 — admin_decisions](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L2114) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
