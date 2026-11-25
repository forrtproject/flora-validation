# Not a problem / Undo dismissal

Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

[Open interactive guide](../index.html#view=admin&node=admin-flora--not-a-problem-undo-dismissal)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Adds a reasoned dismissal to a check finding, or reopens it for review. It does not change the scientific record.

## What is saved, and where

- flora_check_findings dismissal fields and security_events audit.

## When it updates

- After success; matching finding retains its dismissal across later check runs.

## Find it in the code

- [docs/app.js:13505 — markFloraFinding](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L13505) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:7086 — admin_flora_finding_dismiss](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L7086) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
- [app.py:7103 — admin_flora_finding_undismiss](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L7103) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
