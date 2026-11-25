# Open full log / stage details

Fetches the chosen run's complete log and shows stage progress/results.

[Open interactive guide](../index.html#view=admin&node=admin-maintenance--open-full-log-stage-details)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Extractor Pipeline tab and the record or settings selected there.

## What happens

1. Fetches the chosen run's complete log and shows stage progress/results.

## What is saved, and where

- No database changes.

## When it updates

- At click time; an active log is refreshed when its run version changes.

## Find it in the code

- [docs/app.js:11005 — _loadMaintenanceLog](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11005) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:7705 — admin_maintenance_run_detail](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L7705) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
