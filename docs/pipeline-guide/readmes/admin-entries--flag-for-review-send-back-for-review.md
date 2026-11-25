# Flag for Review / Send back for review

Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

[Open interactive guide](../index.html#view=admin&node=admin-entries--flag-for-review-send-back-for-review)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Moves an eligible record to need_review. Sending back an auto-validated record also removes its approved row.

## What is saved, and where

- unvalidated status/admin note; validated deletion for auto-validated rows; admin_decisions.

## When it updates

- After success; the validated-source trigger marks its source row deleted at commit.

## Find it in the code

- [docs/app.js:10046 — Send back handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L10046) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:6308 — admin_flag_review](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L6308) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
