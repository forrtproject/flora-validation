# Admins

Manages administrator accounts, their linked validator identities and the notice banner shown to validators.

[Open interactive guide](../index.html#view=admin&node=admin-admins)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- admins, validators, auth_links/sessions and site_banner.

## What happens

1. Trusted administrators can invite and manage other administrators.
2. The banner controls a notice in the signed-in validator interface.

## What is saved, and where

- admins, auth_links, sessions, site_banner and security_events.

## When it updates

- The account list refreshes after changes.
- Validator banner fetches on sign-in and then every five minutes while visible; it catches up after a hidden interval.

## What comes out

- Invitation/recovery email or fallback link, access changes, and a shared notice banner.

## Keep in mind

- Only trusted admins can manage admin accounts; you cannot delete yourself, delete the last admin, change your own trusted status, or change your own linked validator.
- The recipient chooses their own password; the inviter does not set it.

## Buttons and actions

### [Send invitation](admin-admins--send-invitation.md)

Creates an admin without a password and issues a single-use link, then attempts email delivery. If email fails the response provides a one-time fallback URL to share.

**Saves:** admins, auth_links token record, security_events.

**Visible:** Account exists after commit; it becomes usable after the invitee sets their password.

### [Resend invitation / Send recovery link](admin-admins--resend-invitation-send-recovery-link.md)

Issues a fresh invitation for an account without a password, or recovery for an account with one; uses its saved email address.

**Saves:** auth_links; possibly an email on an account that previously lacked one; security_events.

**Visible:** After issuance; it replaces the outstanding link of that kind.

### [Link / Unlink validator account](admin-admins--link-unlink-validator-account.md)

Connects the admin to that person's validator identity for the self-approval rule. Another trusted admin must set your own link.

**Saves:** admins.validator_id and security_events.

**Visible:** After success; future decisions enforce the link.

### [Trusted toggle](admin-admins--trusted-toggle.md)

Grants or removes account-management authority for another admin.

**Saves:** admins.trusted and security_events.

**Visible:** After success.

### [Remove admin](admin-admins--remove-admin.md)

Deletes another admin account and revokes its sessions in the same transaction.

**Saves:** admins deletion; sessions.revoked_at; security_events.

**Visible:** After confirmed deletion succeeds.

### [Send to all users (banner)](admin-admins--send-to-all-users-banner.md)

Saves and activates the banner text. This is a page notice rather than an inbox broadcast.

**Saves:** site_banner singleton row with message, active flag, editor and timestamp.

**Visible:** Saved immediately; users see it on their next banner poll (up to about five minutes while visible).

### [Clear banner](admin-admins--clear-banner.md)

Sets the banner inactive.

**Saves:** site_banner.active and timestamp.

**Visible:** After save; users remove it on their next banner poll.

## Find it in the code

- [docs/app.js:8372 — fetchAdminAdmins](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8372) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:8386 — renderAdminAdmins](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8386) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:4129 — list_admins](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L4129) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
- [auth_links.py:1 — single-use authentication links](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/auth_links.py#L1) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/auth_links.py)
