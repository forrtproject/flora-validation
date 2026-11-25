# Open full log / stage details

Fetches the chosen run's complete log and shows stage progress/results.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--open-full-log-stage-details)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. Fetches the chosen run's complete log and shows stage progress/results.

## What is saved, and where

- No database changes.

## When it updates

- At click time; an active log is refreshed when its run version changes.

## Find it in the code

- [docs/app.js:11005 — _loadMaintenanceLog](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L11005) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:7705 — admin_maintenance_run_detail](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L7705) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
