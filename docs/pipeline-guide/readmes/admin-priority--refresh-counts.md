# Refresh counts

Counts currently servable waiting records matching the unsaved form.

[Open interactive guide](../index.html#view=admin&node=admin-priority--refresh-counts)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Pool Priority tab and the record or settings selected there.

## What happens

1. Counts currently servable waiting records matching the unsaved form.

## What is saved, and where

- No write.

## When it updates

- On fetch; this is a snapshot.

## Find it in the code

- [docs/app.js:11263 — _refreshPriorityPreview](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11263) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:3986 — preview_serving_config](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L3986) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
