# Approve / Save the answer

Validates the selected original and outcome, then records the admin's final answer.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--approve-save-the-answer)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Validates the selected original and outcome, then records the admin's final answer.

## What is saved, and where

- adjudication.final and adjudication.records.status=approved.

## When it updates

- After success; does not yet publish a source row.

## Find it in the code

- [adjudication/review.py:340 — approve](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/review.py#L340) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/adjudication/review.py)
- [docs/adjudication.js:1415 — approve](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/adjudication.js#L1415) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/adjudication.js)
