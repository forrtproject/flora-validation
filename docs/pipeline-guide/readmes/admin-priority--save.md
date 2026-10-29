# Save

PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

[Open interactive guide](../index.html#view=admin&node=admin-priority--save)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Pool Priority tab and the record or settings selected there.

## What happens

1. PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

## What is saved, and where

- serving_config row.

## When it updates

- Future record claims use it after success.

## Find it in the code

- [docs/app.js:7768 — _saveServingConfig](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7768) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3685 — put_serving_config](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3685) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
