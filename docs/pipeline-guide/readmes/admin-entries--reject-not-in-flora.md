# Reject — not in FLoRA

After confirmation, resolves as not_validation: neither a replication nor a reproduction.

[Open interactive guide](../index.html#view=admin&node=admin-entries--reject-not-in-flora)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. After confirmation, resolves as not_validation: neither a replication nor a reproduction.

## What is saved, and where

- unvalidated becomes rejected; existing validated row removed; decision audit written.

## When it updates

- Entries immediately; derived FLoRA view follows source retirement; stored release waits for pipeline.

## Find it in the code

- [docs/app.js:6515 — Reject click handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6515) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5606 — admin_resolve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5606) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
