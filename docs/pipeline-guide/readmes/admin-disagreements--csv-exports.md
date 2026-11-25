# CSV exports

Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements--csv-exports)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Disagreements tab and the record or settings selected there.

## What happens

1. Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

## What is saved, and where

- Browser file only.

## When it updates

- At request time.

## Find it in the code

- [adjudication/api.py:186 — export route](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/api.py#L186) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/api.py)
- [adjudication/export.py:151 — export_csv](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/export.py#L151) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/adjudication/export.py)
