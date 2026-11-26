# Save

PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

[Open interactive guide](../index.html#view=admin&node=admin-priority--save)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Pool Priority tab and the record or settings selected there.

## What happens

1. PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

## What is saved, and where

- serving_config row.

## When it updates

- Future record claims use it after success.

## Find it in the code

- [docs/app.js:11300 — _saveServingConfig](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11300) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:3957 — put_serving_config](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L3957) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
