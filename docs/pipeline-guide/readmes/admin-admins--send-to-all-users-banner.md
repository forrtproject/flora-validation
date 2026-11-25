# Send to all users (banner)

Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-to-all-users-banner)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

## What is saved, and where

- site_banner singleton row with message, active flag, editor and timestamp.

## When it updates

- Saved immediately; users see it on their next banner poll (up to about five minutes while visible).

## Find it in the code

- [docs/app.js:12156 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12156) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4253 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4253) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
- [docs/app.js:2285 — banner polling](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L2285) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
