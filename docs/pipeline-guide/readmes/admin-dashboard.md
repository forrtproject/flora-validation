# Dashboard

A read-only summary of the validation workload, completion, outcomes, disagreements and contributor activity.

[Open interactive guide](../index.html#view=admin&node=admin-dashboard)

Code snapshot: 24 November 2026, `3cc13da`.

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

- [docs/app.js:11162 — fetchAdminDashboard](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11162) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [docs/app.js:11320 — renderAdminDashboard](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js#L11320) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/docs/app.js)
- [app.py:4010 — admin_dashboard](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py#L4010) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/app.py)
