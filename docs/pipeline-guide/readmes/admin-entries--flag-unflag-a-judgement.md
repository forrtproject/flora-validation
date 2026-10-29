# Flag / Unflag a judgement

Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-unflag-a-judgement)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

## What is saved, and where

- validation_queue.flagged and flag_reason; optional validator_messages.

## When it updates

- Immediately after the request; validator sees the message when their inbox refreshes.

## Find it in the code

- [docs/app.js:6549 — val-flag-btn handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6549) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5084 — admin_flag_queue](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5084) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
