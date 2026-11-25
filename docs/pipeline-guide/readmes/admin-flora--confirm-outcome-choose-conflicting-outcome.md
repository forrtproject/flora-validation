# Confirm outcome / Choose conflicting outcome

Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

[Open interactive guide](../index.html#view=admin&node=admin-flora--confirm-outcome-choose-conflicting-outcome)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

## What is saved, and where

- flora_record_changes outcome decision, and live flora_data row where applicable.

## When it updates

- After success; pipeline retains this correction.

## Find it in the code

- [docs/app.js:13557 — confirmFloraOutcome](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L13557) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:7377 — admin_flora_confirm_outcome](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7377) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [flora_edits.py:735 — confirm_outcome](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_edits.py#L735) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_edits.py)
