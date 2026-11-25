# Clear banner

Sets the banner inactive.

[Open interactive guide](../index.html#view=admin&node=admin-admins--clear-banner)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Sets the banner inactive.

## What is saved, and where

- site_banner.active and timestamp.

## When it updates

- After save; users remove it on their next banner poll.

## Find it in the code

- [docs/app.js:12156 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L12156) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4253 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4253) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
