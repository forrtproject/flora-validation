# Dashboard

A read-only summary of the validation workload, completion, outcomes, disagreements and contributor activity.

[Open interactive guide](../index.html#view=admin&node=admin-dashboard)

Code snapshot: 25 November 2026, `589664a`.

## What goes in

- Aggregated unvalidated, validated, validation_queue and validator data.

## What happens

1. Opening the tab fetches aggregates and draws charts.
2. Use chart selectors/controls to explore the returned data.

## What is saved, and where

- No workflow or record writes.

## When it updates

- Fetches on opening the tab. The charts are a snapshot, not a continuous live stream.

## What comes out

- Charts and operational totals.

## Keep in mind

- A displayed count is current to its fetch; it does not itself run the extractor or publication pipeline.

## Buttons and actions

### [Open tab / chart controls](admin-dashboard--open-tab-chart-controls.md)

Reads dashboard aggregates and changes chart presentation.

**Saves:** No data mutation.

**Visible:** On opening the tab / in the browser.

## Find it in the code

- [docs/app.js:11187 — fetchAdminDashboard](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L11187) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [docs/app.js:11345 — renderAdminDashboard](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js#L11345) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/docs/app.js)
- [app.py:4043 — admin_dashboard](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py#L4043) · [complete script](https://github.com/forrtproject/flora-validation/blob/589664adc46254093eab9ede2c5c76c893eb19e9/app.py)
