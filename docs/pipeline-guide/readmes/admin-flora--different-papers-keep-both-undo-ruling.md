# Different papers — keep both / Undo ruling

Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--different-papers-keep-both-undo-ruling)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

## What is saved, and where

- preprint_dedup_decisions insert/update or deletion, plus security_events audit.

## When it updates

- Grid after refetch; stored release after pipeline.

## Find it in the code

- [docs/app.js:14780 — undoFloraDedup](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L14780) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [flora_service.py:911 — undo_preprint_decision](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_service.py#L911) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_service.py)
