# Send to all users (banner)

Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-to-all-users-banner)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

## What is saved, and where

- site_banner singleton row with message, active flag, editor and timestamp.

## When it updates

- Saved immediately; users see it on their next banner poll (up to about five minutes while visible).

## Find it in the code

- [docs/app.js:12131 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12131) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4220 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4220) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
- [docs/app.js:2285 — banner polling](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L2285) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
