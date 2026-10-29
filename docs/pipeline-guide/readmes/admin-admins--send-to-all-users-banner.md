# Send to all users (banner)

Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-to-all-users-banner)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

## What is saved, and where

- site_banner singleton row with message, active flag, editor and timestamp.

## When it updates

- Saved immediately; users see it on their next banner poll (up to about five minutes while visible).

## Find it in the code

- [docs/app.js:8593 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8593) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3930 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3930) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [docs/app.js:2272 — banner polling](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L2272) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
