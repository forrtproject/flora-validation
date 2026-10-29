# Approve / Save the answer

Validates the selected original and outcome, then records the admin's final answer.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--approve-save-the-answer)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Validates the selected original and outcome, then records the admin's final answer.

## What is saved, and where

- adjudication.final and adjudication.records.status=approved.

## When it updates

- After success; does not yet publish a source row.

## Find it in the code

- [adjudication/review.py:266 — approve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L266) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
- [docs/adjudication.js:1184 — approve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js#L1184) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js)
