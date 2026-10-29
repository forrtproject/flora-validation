# CSV exports

Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--csv-exports)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

## What is saved, and where

- Browser file only.

## When it updates

- At request time.

## Find it in the code

- [adjudication/api.py:174 — export route](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py#L174) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py)
- [adjudication/export.py:150 — export_csv](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/export.py#L150) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/export.py)
