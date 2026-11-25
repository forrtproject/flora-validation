# Level / tier button

Cycles the contributor tier: regular (0), trusted (1), senior (2).

[Open interactive guide](../index.html#view=admin&node=admin-stats--level-tier-button)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Cycles the contributor tier: regular (0), trusted (1), senior (2).

## What is saved, and where

- validators.validator_tier and security_events audit.

## When it updates

- After success; later serving/consensus reads the saved tier.

## Find it in the code

- [docs/app.js:11631 — tier handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L11631) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4394 — admin_set_tier](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4394) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
