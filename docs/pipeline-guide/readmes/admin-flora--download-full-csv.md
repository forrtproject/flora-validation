# Download full CSV

Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

[Open interactive guide](../index.html#view=admin&node=admin-flora--download-full-csv)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- The FLoRA tab and the record or settings selected there.

## What happens

1. Downloads the live stored dataset: last pipeline's stored rows plus subsequent published corrections.

## What is saved, and where

- Browser file only; reads flora_data.

## When it updates

- At request time.

## Find it in the code

- [docs/app.js:12921 — downloadAdminArtifact](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js#L12921) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/docs/app.js)
- [app.py:6959 — admin_flora_export](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py#L6959) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/app.py)
