# Save Note

Saves only the admin note, including who saved it and when. An empty note clears it.

[Open interactive guide](../index.html#view=admin&node=admin-entries--save-note)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Saves only the admin note, including who saved it and when. An empty note clears it.

## What is saved, and where

- unvalidated.admin_notes, note_saved_by, note_saved_at.

## When it updates

- After success; visible to all admins when the record reloads. It does not approve or publish the record.

## Find it in the code

- [docs/app.js:6483 — Save Note click handler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L6483) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5560 — admin_save_note](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5560) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
