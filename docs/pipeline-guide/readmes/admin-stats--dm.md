# DM

Composes and sends an in-app message to this validator.

[Open interactive guide](../index.html#view=admin&node=admin-stats--dm)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Validator Stats tab and the record or settings selected there.

## What happens

1. Composes and sends an in-app message to this validator.

## What is saved, and where

- validator_messages.

## When it updates

- After Send; the recipient sees it when their inbox refreshes.

## Find it in the code

- [docs/app.js:11722 — openComposeDialog](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L11722) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:5805 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L5805) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
