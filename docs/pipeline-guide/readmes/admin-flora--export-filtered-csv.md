# Export filtered CSV

Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

[Open interactive guide](../index.html#view=admin&node=admin-flora--export-filtered-csv)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

## What is saved, and where

- Browser file only.

## When it updates

- At request time; may differ from the full stored release.

## Find it in the code

- [flora_service.py:584 — export_csv](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_service.py#L584) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/flora_service.py)
- [app.py:6959 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L6959) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
