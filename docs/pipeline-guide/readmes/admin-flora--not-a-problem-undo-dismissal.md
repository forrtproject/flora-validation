# Not a problem / Undo dismissal

Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

[Open interactive guide](../index.html#view=admin&node=admin-flora--not-a-problem-undo-dismissal)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

## What is saved, and where

- flora_check_findings dismissal fields and security_events audit.

## When it updates

- After success; matching finding retains its dismissal across later check runs.

## Find it in the code

- [docs/app.js:13480 — markFloraFinding](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L13480) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:7053 — admin_flora_finding_dismiss](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7053) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
- [app.py:7070 — admin_flora_finding_undismiss](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7070) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
