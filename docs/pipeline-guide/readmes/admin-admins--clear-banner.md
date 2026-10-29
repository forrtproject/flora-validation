# Clear banner

Sets the banner inactive.

[Open interactive guide](../index.html#view=admin&node=admin-admins--clear-banner)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Sets the banner inactive.

## What is saved, and where

- site_banner.active and timestamp.

## When it updates

- After save; users remove it on their next banner poll.

## Find it in the code

- [docs/app.js:8593 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8593) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3930 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3930) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
