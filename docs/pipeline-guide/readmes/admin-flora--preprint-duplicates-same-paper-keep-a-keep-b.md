# Preprint duplicates → Same paper, keep A / keep B

Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

[Open interactive guide](../index.html#view=admin&node=admin-flora--preprint-duplicates-same-paper-keep-a-keep-b)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

## What is saved, and where

- preprint_dedup_decisions and security_events.

## When it updates

- Grid after refetch; stored CSV/API after next pipeline.

## Find it in the code

- [docs/app.js:14760 — decideFloraDedup](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L14760) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [flora_service.py:869 — decide_preprint_pair](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_service.py#L869) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_service.py)
- [app.py:7017 — admin_flora_preprint_decide](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L7017) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
