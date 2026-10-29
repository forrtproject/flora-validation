# Publish to FLoRA

Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--publish-to-flora)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

## What is saved, and where

- source_records; adjudication.final publication fields; status=published.

## When it updates

- Source Records/derived grid after refresh; stored full CSV/API at next pipeline.

## Find it in the code

- [adjudication/review.py:404 — publish](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L404) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
