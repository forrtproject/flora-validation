# + New → Send

Validates subject/body and inserts a message for the selected validator.

[Open interactive guide](../index.html#view=admin&node=admin-messages--new-send)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Messages tab and the record or settings selected there.

## What happens

1. Validates subject/body and inserts a message for the selected validator.

## What is saved, and where

- validator_messages.

## When it updates

- After save; recipient sees it when inbox refreshes.

## Find it in the code

- [docs/app.js:12216 — openComposePanel](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12216) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:5805 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L5805) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
