# Publish to FLoRA

Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--publish-to-flora)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

## What is saved, and where

- source_records; adjudication.final publication fields; status=published.

## When it updates

- Source Records/derived grid after refresh; stored full CSV/API at next pipeline.

## Find it in the code

- [adjudication/review.py:520 — publish](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py#L520) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/review.py)
