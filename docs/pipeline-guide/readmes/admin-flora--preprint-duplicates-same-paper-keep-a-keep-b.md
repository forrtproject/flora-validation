# Preprint duplicates → Same paper, keep A / keep B

Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

[Open interactive guide](../index.html#view=admin&node=admin-flora--preprint-duplicates-same-paper-keep-a-keep-b)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

## What is saved, and where

- preprint_dedup_decisions and security_events.

## When it updates

- Grid after refetch; stored CSV/API after next pipeline.

## Find it in the code

- [docs/app.js:14735 — decideFloraDedup](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L14735) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [flora_service.py:869 — decide_preprint_pair](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py#L869) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py)
- [app.py:6984 — admin_flora_preprint_decide](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6984) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
