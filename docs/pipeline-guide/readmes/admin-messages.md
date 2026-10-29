# Messages

In-app conversations with validators, including replies and messages sent to everyone. These are stored in the application inbox.

[Open interactive guide](../index.html#view=admin&node=admin-messages)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- validator_messages conversations, validators, subject/body/recipient or broadcast flag.

## What happens

1. Select a conversation to read its thread, or choose + New.
2. Send a reply, a message to one validator, or a broadcast to all validators.

## What is saved, and where

- validator_messages with direction, parent thread, sender, body and read flags.

## When it updates

- Messages load when opening the tab; opening a thread marks incoming messages read by the admin.
- The validator inbox polls about every 30 seconds while the inbox is open and the page is visible; messages are also fetched at validator sign-in. There is no continuous admin-thread polling.

## What comes out

- A persisted conversation and updated unread badges.

## Keep in mind

- This is the application inbox; sending here does not itself send an email.
- Broadcast inserts one message for each current validator.

## Buttons and actions

### [Open conversation](admin-messages--open-conversation.md)

Reads the thread and marks its inbound messages read by the admin.

**Saves:** validator_messages.is_read_by_admin.

**Visible:** After opening; unread badge refreshes.

### [+ New → Send](admin-messages--new-send.md)

Validates subject/body and inserts a message for the selected validator.

**Saves:** validator_messages.

**Visible:** After save; recipient sees it when inbox refreshes.

### [Send to everyone / broadcast](admin-messages--send-to-everyone-broadcast.md)

Creates a separate in-app message for each current validator.

**Saves:** One validator_messages row per validator.

**Visible:** After successful send; each recipient's next inbox poll.

### [Reply](admin-messages--reply.md)

Adds an outbound reply linked to the conversation root, retaining any related judgement link.

**Saves:** validator_messages with parent_id and direction=outbound.

**Visible:** After successful reply; thread updates.

## Find it in the code

- [docs/app.js:8205 — fetchAdminMessages](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8205) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:8273 — openThread](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8273) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:8653 — openComposePanel](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L8653) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:5351 — admin_send_message](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L5351) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
