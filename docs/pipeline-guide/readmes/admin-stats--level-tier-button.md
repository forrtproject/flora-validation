# Level / tier button

Cycles the contributor tier: regular (0), trusted (1), senior (2).

[Open interactive guide](../index.html#view=admin&node=admin-stats--level-tier-button)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Cycles the contributor tier: regular (0), trusted (1), senior (2).

## What is saved, and where

- validators.validator_tier and security_events audit.

## When it updates

- After success; later serving/consensus reads the saved tier.

## Find it in the code

- [docs/app.js:11606 — tier handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11606) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4361 — admin_set_tier](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4361) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
