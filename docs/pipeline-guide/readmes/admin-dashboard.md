# Dashboard

A read-only summary of the validation workload, completion, outcomes, disagreements and contributor activity.

[Open interactive guide](../index.html#view=admin&node=admin-dashboard)

Code snapshot: 29 October 2026, `2f3a89c`.

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

- [docs/app.js:7630 — fetchAdminDashboard](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7630) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [docs/app.js:7788 — renderAdminDashboard](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js#L7788) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/app.js)
- [app.py:3738 — admin_dashboard](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L3738) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
