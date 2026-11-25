# Flag / Unflag a judgement

Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-unflag-a-judgement)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Toggles the flag on one person's submitted judgement. A nonempty reason also creates an inbox message for that validator.

## What is saved, and where

- validation_queue.flagged and flag_reason; optional validator_messages.

## When it updates

- Immediately after the request; validator sees the message when their inbox refreshes.

## Find it in the code

- [docs/app.js:9982 — val-flag-btn handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L9982) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:5538 — admin_flag_queue](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L5538) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
