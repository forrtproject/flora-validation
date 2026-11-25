# Save Note

Saves only the admin note, including who saved it and when. An empty note clears it.

[Open interactive guide](../index.html#view=admin&node=admin-entries--save-note)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Entries tab and the record or settings selected there.

## What happens

1. Saves only the admin note, including who saved it and when. An empty note clears it.

## What is saved, and where

- unvalidated.admin_notes, note_saved_by, note_saved_at.

## When it updates

- After success; visible to all admins when the record reloads. It does not approve or publish the record.

## Find it in the code

- [docs/app.js:9842 — Save Note click handler](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L9842) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:6354 — admin_save_note](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L6354) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
