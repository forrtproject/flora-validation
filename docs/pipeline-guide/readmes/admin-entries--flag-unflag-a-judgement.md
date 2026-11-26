# Flag / Unflag a judgement

Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-unflag-a-judgement)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

## What is saved, and where

- validation_queue.flagged and flag_reason; optional validator_messages.

## When it updates

- Immediately after the request; validator sees the message when their inbox refreshes.

## Find it in the code

- [docs/app.js:9957 — val-flag-btn handler](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L9957) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:5505 — admin_flag_queue](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L5505) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
