# Mark as Resolved

POST /resolve validates the edited type, original, outcome and evidence; saves the final decision. For reproductions both axes are required. Other originals drafted in the entry are created in the same transaction; if one is refused, nothing is saved and the message names the original, the field and what is wrong.

[Open interactive guide](../index.html#view=admin&node=admin-entries--mark-as-resolved)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. POST /resolve validates the edited type, original, outcome and evidence; saves the final decision. For reproductions both axes are required. Other originals drafted in the entry are created in the same transaction; if one is refused, nothing is saved and the message names the original, the field and what is wrong.

## What is saved, and where

- unvalidated.final_* and admin fields; replaces this record's validated row; admin_decisions; drafted other originals as new unvalidated + validated rows.

## When it updates

- After commit; then downstream sync/preparation is required.

## Find it in the code

- [docs/app.js:10188 — submitAdminResolve](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L10188) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:6393 — admin_resolve](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L6393) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
