# Export CSV

Downloads rows matching the source filters.

[Open interactive guide](../index.html#view=admin&node=admin-sources--export-csv)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Source Records tab and the record or settings selected there.

## What happens

1. Downloads rows matching the source filters.

## What is saved, and where

- A file downloaded by your browser; no database change.

## When it updates

- At click time.

## Find it in the code

- [docs/app.js:15226 — source export handler](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L15226) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:6882 — admin_source_records_export](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L6882) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
