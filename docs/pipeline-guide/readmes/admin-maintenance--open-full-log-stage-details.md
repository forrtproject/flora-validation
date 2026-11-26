# Open full log / stage details

Fetches the chosen run's complete log and shows stage progress/results.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--open-full-log-stage-details)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. Fetches the chosen run's complete log and shows stage progress/results.

## What is saved, and where

- No database changes.

## When it updates

- At click time; an active log is refreshed when its run version changes.

## Find it in the code

- [docs/app.js:10980 — _loadMaintenanceLog](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L10980) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:7672 — admin_maintenance_run_detail](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L7672) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
