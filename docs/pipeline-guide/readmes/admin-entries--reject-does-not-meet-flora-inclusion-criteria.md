# Reject — does not meet FLoRA inclusion criteria

After confirmation, resolves as not_validation: the record does not meet FLoRA inclusion criteria, whatever the paper calls itself.

[Open interactive guide](../index.html#view=admin&node=admin-entries--reject-does-not-meet-flora-inclusion-criteria)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. After confirmation, resolves as not_validation: the record does not meet FLoRA inclusion criteria, whatever the paper calls itself.

## What is saved, and where

- unvalidated becomes rejected; existing validated row removed; decision audit written.

## When it updates

- Entries immediately; derived FLoRA view follows source retirement; stored release waits for pipeline.

## Find it in the code

- [docs/app.js:9878 — Reject click handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L9878) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:6426 — admin_resolve](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L6426) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
