# Export filtered CSV

Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

[Open interactive guide](../index.html#view=admin&node=admin-flora--export-filtered-csv)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

## What is saved, and where

- Browser file only.

## When it updates

- At request time; may differ from the full stored release.

## Find it in the code

- [flora_service.py:584 — export_csv](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py#L584) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/flora_service.py)
- [app.py:6926 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6926) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
