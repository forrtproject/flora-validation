# Different papers — keep both / Undo ruling

Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

[Open interactive guide](../index.html#view=admin&node=admin-flora--different-papers-keep-both-undo-ruling)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Keep both overrides automatic merging; Undo removes the explicit ruling so automatic rules apply again.

## What is saved, and where

- preprint_dedup_decisions insert/update or deletion, plus security_events audit.

## When it updates

- Grid after refetch; stored release after pipeline.

## Find it in the code

- [docs/app.js:14780 — undoFloraDedup](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L14780) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [flora_service.py:911 — undo_preprint_decision](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_service.py#L911) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_service.py)
