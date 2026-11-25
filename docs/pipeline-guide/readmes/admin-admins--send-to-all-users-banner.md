# Send to all users (banner)

Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

[Open interactive guide](../index.html#view=admin&node=admin-admins--send-to-all-users-banner)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- The Admins tab and the record or settings selected there.

## What happens

1. Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

## What is saved, and where

- site_banner singleton row with message, active flag, editor and timestamp.

## When it updates

- Saved immediately; users see it on their next banner poll (up to about five minutes while visible).

## Find it in the code

- [docs/app.js:12156 — saveAdminBanner](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L12156) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:4253 — set_site_banner](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L4253) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
- [docs/app.js:2285 — banner polling](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L2285) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
