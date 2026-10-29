# Export filtered CSV

Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

[Open interactive guide](../index.html#view=admin&node=admin-flora--export-filtered-csv)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

## What is saved, and where

- Browser file only.

## When it updates

- At request time; may differ from the full stored release.

## Find it in the code

- [flora_service.py:546 — export_csv](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L546) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [app.py:6070 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6070) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
