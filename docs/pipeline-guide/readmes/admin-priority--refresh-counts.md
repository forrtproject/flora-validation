# Refresh counts

Counts currently servable waiting records matching the unsaved form.

[Open interactive guide](../index.html#view=admin&node=admin-priority--refresh-counts)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Pool Priority tab and the record or settings selected there.

## What happens

1. Counts currently servable waiting records matching the unsaved form.

## What is saved, and where

- No write.

## When it updates

- On fetch; this is a snapshot.

## Find it in the code

- [docs/app.js:11288 — _refreshPriorityPreview](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L11288) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:4019 — preview_serving_config](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L4019) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
