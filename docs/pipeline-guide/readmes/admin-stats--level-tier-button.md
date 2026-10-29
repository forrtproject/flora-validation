# Level / tier button

Cycles the contributor tier: regular (0), trusted (1), senior (2).

[Open interactive guide](../index.html#view=admin&node=admin-stats--level-tier-button)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Cycles the contributor tier: regular (0), trusted (1), senior (2).

## What is saved, and where

- validators.validator_tier and security_events audit.

## When it updates

- After success; later serving/consensus reads the saved tier.

## Find it in the code

- [docs/app.js:8068 — tier handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8068) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4067 — admin_set_tier](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4067) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
