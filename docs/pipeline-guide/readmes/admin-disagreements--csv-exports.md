# CSV exports

Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--csv-exports)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

## What is saved, and where

- Browser file only.

## When it updates

- At request time.

## Find it in the code

- [adjudication/api.py:186 — export route](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/api.py#L186) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/api.py)
- [adjudication/export.py:151 — export_csv](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/export.py#L151) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/adjudication/export.py)
