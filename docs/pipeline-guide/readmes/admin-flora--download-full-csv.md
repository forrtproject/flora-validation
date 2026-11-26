# Download full CSV

Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

[Open interactive guide](../index.html#view=admin&node=admin-flora--download-full-csv)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

## What is saved, and where

- Browser file only; reads flora_data.

## When it updates

- At request time.

## Find it in the code

- [docs/app.js:12896 — downloadAdminArtifact](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12896) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:6926 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6926) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
