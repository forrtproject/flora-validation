# Save

PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

[Open interactive guide](../index.html#view=admin&node=admin-priority--save)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Pool Priority tab and the record or settings selected there.

## What happens

1. PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

## What is saved, and where

- serving_config row.

## When it updates

- Future record claims use it after success.

## Find it in the code

- [docs/app.js:11325 — _saveServingConfig](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L11325) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:3990 — put_serving_config](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L3990) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
