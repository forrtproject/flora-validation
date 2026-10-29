# Open full log / stage details

Fetches the chosen run's complete log and shows stage progress/results.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--open-full-log-stage-details)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. Fetches the chosen run's complete log and shows stage progress/results.

## What is saved, and where

- No database changes.

## When it updates

- At click time; an active log is refreshed when its run version changes.

## Find it in the code

- [docs/app.js:7448 — _loadMaintenanceLog](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7448) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6509 — admin_maintenance_run_detail](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6509) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
