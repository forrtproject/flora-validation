# Approve / Save the answer

Validates the selected original and outcome, then records the admin's final answer.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--approve-save-the-answer)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Validates the selected original and outcome, then records the admin's final answer.

## What is saved, and where

- adjudication.final and adjudication.records.status=approved.

## When it updates

- After success; does not yet publish a source row.

## Find it in the code

- [adjudication/review.py:339 — approve](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/review.py#L339) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/review.py)
- [docs/adjudication.js:1415 — approve](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/adjudication.js#L1415) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/adjudication.js)
