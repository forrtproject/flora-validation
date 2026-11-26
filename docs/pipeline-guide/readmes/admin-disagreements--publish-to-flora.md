# Publish to FLoRA

Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--publish-to-flora)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

## What is saved, and where

- source_records; adjudication.final publication fields; status=published.

## When it updates

- Source Records/derived grid after refresh; stored full CSV/API at next pipeline.

## Find it in the code

- [adjudication/review.py:527 — publish](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/adjudication/review.py#L527) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/adjudication/review.py)
