# Not a problem / Undo dismissal

Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

[Open interactive guide](../index.html#view=admin&node=admin-flora--not-a-problem-undo-dismissal)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

## What is saved, and where

- flora_check_findings dismissal fields and security_events audit.

## When it updates

- After success; matching finding retains its dismissal across later check runs.

## Find it in the code

- [docs/app.js:9715 — markFloraFinding](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9715) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6197 — admin_flora_finding_dismiss](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6197) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [app.py:6214 — admin_flora_finding_undismiss](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6214) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
