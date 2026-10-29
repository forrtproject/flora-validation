# Confirm outcome / Choose conflicting outcome

Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

[Open interactive guide](../index.html#view=admin&node=admin-flora--confirm-outcome-choose-conflicting-outcome)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

## What is saved, and where

- flora_record_changes outcome decision, and live flora_data row where applicable.

## When it updates

- After success; pipeline retains this correction.

## Find it in the code

- [docs/app.js:9767 — confirmFloraOutcome](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9767) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6308 — admin_flora_confirm_outcome](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6308) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [flora_edits.py:626 — confirm_outcome](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L626) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
