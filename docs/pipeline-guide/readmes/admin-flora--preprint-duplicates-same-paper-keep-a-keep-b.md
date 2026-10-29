# Preprint duplicates → Same paper, keep A / keep B

Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

[Open interactive guide](../index.html#view=admin&node=admin-flora--preprint-duplicates-same-paper-keep-a-keep-b)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Records which DOI should survive; the discarded DOI becomes an alternative identifier. For original-study DOIs, replications move under the kept DOI.

## What is saved, and where

- preprint_dedup_decisions and security_events.

## When it updates

- Grid after refetch; stored CSV/API after next pipeline.

## Find it in the code

- [docs/app.js:10232 — decideFloraDedup](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10232) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [flora_service.py:831 — decide_preprint_pair](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L831) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [app.py:6128 — admin_flora_preprint_decide](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6128) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
