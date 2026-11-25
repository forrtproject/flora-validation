# Export filtered CSV

Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

[Open interactive guide](../index.html#view=admin&node=admin-flora--export-filtered-csv)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the current tab view with its filters. Refused if included records still lack permanent IDs.

## What is saved, and where

- Browser file only.

## When it updates

- At request time; may differ from the full stored release.

## Find it in the code

- [flora_service.py:584 — export_csv](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_service.py#L584) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/flora_service.py)
- [app.py:6959 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L6959) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
