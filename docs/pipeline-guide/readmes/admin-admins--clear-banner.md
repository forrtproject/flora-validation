# Clear banner

Sets the banner inactive.

[Open interactive guide](../index.html#view=admin&node=admin-admins--clear-banner)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Sets the banner inactive.

## What is saved, and where

- site_banner.active and timestamp.

## When it updates

- After save; users remove it on their next banner poll.

## Find it in the code

- [docs/app.js:12156 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12156) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4253 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4253) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
