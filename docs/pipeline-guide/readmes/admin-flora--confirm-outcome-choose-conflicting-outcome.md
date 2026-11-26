# Confirm outcome / Choose conflicting outcome

Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

[Open interactive guide](../index.html#view=admin&node=admin-flora--confirm-outcome-choose-conflicting-outcome)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Settles the conflicting outcomes of merged source rows by confirming the shown outcome or choosing one of the candidates.

## What is saved, and where

- flora_record_changes outcome decision, and live flora_data row where applicable.

## When it updates

- After success; pipeline retains this correction.

## Find it in the code

- [docs/app.js:13532 — confirmFloraOutcome](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L13532) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:7344 — admin_flora_confirm_outcome](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7344) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
- [flora_edits.py:738 — confirm_outcome](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py#L738) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_edits.py)
