# Download full CSV

Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

[Open interactive guide](../index.html#view=admin&node=admin-flora--download-full-csv)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

## What is saved, and where

- Browser file only; reads flora_data.

## When it updates

- At request time.

## Find it in the code

- [docs/app.js:9339 — downloadAdminArtifact](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L9339) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:6070 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6070) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
