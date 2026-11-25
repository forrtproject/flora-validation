# Sheet edits → Keep FLoRA value / Undo

Records, with a required note, that FLoRA's value is right for one sheet edit, or that a record whose row left the sheet or its validated status stays in FLoRA. Undo withdraws the decision.

[Open interactive guide](../index.html#view=admin&node=admin-sources--sheet-edits-keep-flora-value-undo)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Records, with a required note, that FLoRA's value is right for one sheet edit, or that a record whose row left the sheet or its validated status stays in FLoRA. Undo withdraws the decision.

## What is saved, and where

- sheet_drift_findings kept_by, kept_at, kept_note and the sheet value it was made against; security_events records both the decision and its withdrawal.

## When it updates

- Immediately in the view. The decision holds while the sheet keeps that value; a later edit of the cell asks again.

## Find it in the code

- [docs/app.js:15945 — keepSourceDrift](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L15945) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:7591 — admin_sheet_drift_keep](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7591) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [sheet_drift_service.py:952 — keep](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sheet_drift_service.py#L952) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/sheet_drift_service.py)
