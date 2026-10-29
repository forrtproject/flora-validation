# Different papers — keep both / Undo ruling

Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--different-papers-keep-both-undo-ruling)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

## What is saved, and where

- preprint_dedup_decisions insert/update or deletion, plus security_events audit.

## When it updates

- Grid after refetch; stored release after pipeline.

## Find it in the code

- [docs/app.js:10252 — undoFloraDedup](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L10252) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [flora_service.py:873 — undo_preprint_decision](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L873) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
