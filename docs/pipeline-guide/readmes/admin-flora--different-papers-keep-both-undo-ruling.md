# Different papers — keep both / Undo ruling

Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--different-papers-keep-both-undo-ruling)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

## What is saved, and where

- preprint_dedup_decisions insert/update or deletion, plus security_events audit.

## When it updates

- Grid after refetch; stored release after pipeline.

## Find it in the code

- [docs/app.js:14755 — undoFloraDedup](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L14755) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [flora_service.py:911 — undo_preprint_decision](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py#L911) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py)
