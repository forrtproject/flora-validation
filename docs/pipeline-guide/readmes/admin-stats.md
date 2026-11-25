# Validator Stats

Shows how contributors are progressing, their judgement timing and flags, and their review tier.

[Open interactive guide](../index.html#view=admin&node=admin-stats)

Code snapshot: 25 November 2026, `f0cb8de`.

## What goes in

- validators and validation_queue joined with validation outcomes.

## What happens

1. Open the tab to fetch totals and a table of validators.
2. Review flagged judgements or change a validator's tier when appropriate.

## What is saved, and where

- Only tier changes and sent messages write data.

## When it updates

- Statistics load when the tab opens; tier cells update after success.

## What comes out

- Contributor statistics, tier levels, flagged-judgement links and direct messages.

## Keep in mind

- Tier changes affect future eligibility/weighting; they do not automatically rerun old decisions.

## Buttons and actions

### [Level / tier button](admin-stats--level-tier-button.md)

Cycles the contributor tier: regular (0), trusted (1), senior (2).

**Saves:** validators.validator_tier and security_events audit.

**Visible:** After success; later serving/consensus reads the saved tier.

### [Flagged count](admin-stats--flagged-count.md)

Opens the list of that validator's flagged judgements, with reasons and links to the entries.

**Saves:** No write.

**Visible:** On fetch.

### [DM](admin-stats--dm.md)

Composes and sends an in-app message to this validator.

**Saves:** validator_messages.

**Visible:** After Send; the recipient sees it when their inbox refreshes.

## Find it in the code

- [docs/app.js:11162 — fetchAdminStats](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11162) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [docs/app.js:11588 — renderAdminStats](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js#L11588) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/docs/app.js)
- [app.py:3728 — admin_stats](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py#L3728) · [complete script](https://github.com/forrtproject/flora-validation/blob/f0cb8de088ea65f429125505b68908990d9d2f76/app.py)
