# Pool Priority

Controls which waiting records are more likely to be offered to validators. It changes assignment order, not a paper's outcome.

[Open interactive guide](../index.html#view=admin&node=admin-priority)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- Proposed outcome, year range, enabled flag and priority share; the currently servable unvalidated pool.

## What happens

1. Choose the desired priority group and preview the current counts.
2. Click Save to apply the rule to future record selection.

## What is saved, and where

- The single serving_config row (id = 1), including editor and timestamp.

## When it updates

- New record requests read the saved setting. Existing claimed assignments are not replaced by this button.
- Preview counts are fetched live but do not continue updating; Refresh counts recounts the current form.

## What comes out

- A weighted/random selection rule and a preview of matching waiting records.

## Keep in mind

- Reset to defaults changes the form only; press Save to persist it.
- Previewing a proposed rule does not activate it.

## Buttons and actions

### [Enable / outcome / year / share controls](admin-priority--enable-outcome-year-share-controls.md)

Edits a proposed rule in the browser. Changing outcome/year refreshes the preview.

**Saves:** No write until Save.

**Visible:** Form changes immediately.

### [Refresh counts](admin-priority--refresh-counts.md)

Counts currently servable waiting records matching the unsaved form.

**Saves:** No write.

**Visible:** On fetch; this is a snapshot.

### [Save](admin-priority--save.md)

PUT /api/admin/serving-config persists the selection rule; normalizes the share and reversed year bounds.

**Saves:** serving_config row.

**Visible:** Future record claims use it after success.

### [Reset to defaults](admin-priority--reset-to-defaults.md)

Repopulates the form with default settings.

**Saves:** Nothing until Save.

**Visible:** Immediately in the form.

## Find it in the code

- [docs/app.js:11215 — renderServingConfig](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L11215) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:3990 — put_serving_config](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L3990) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
- [app.py:1792 — _serving_config](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L1792) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
