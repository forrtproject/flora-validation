# Clear banner

Sets the banner inactive.

[Open interactive guide](../index.html#view=admin&node=admin-admins--clear-banner)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Sets the banner inactive.

## What is saved, and where

- site_banner.active and timestamp.

## When it updates

- After save; users remove it on their next banner poll.

## Find it in the code

- [docs/app.js:12131 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L12131) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4220 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4220) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
