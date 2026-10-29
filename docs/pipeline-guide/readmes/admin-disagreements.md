# Disagreements

Optional review lane for disagreements between FLoRA and the Metascience Observatory. It uses its own records and two trusted/senior judgements before an admin decision.

[Open interactive guide](../index.html#view=admin&node=admin-disagreements)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Pinned disagreement CSV from fred-data PR #143; metadata from OpenAlex/Europe PMC; adjudication judgements.

## What happens

1. Preview and import the disagreement rows.
2. Two eligible people judge each row; admin reviews and approves the final answer.
3. Publish the approved answer into Source Records, then use the FLoRA pipeline to include it in stored outputs.

## What is saved, and where

- adjudication.records, adjudication.judgements, adjudication.final; publication writes source_records.

## When it updates

- The tab appears only when ADJUDICATION_ENABLED is on.
- Approval changes the adjudication lane immediately; Publish updates source inputs immediately; stored public release waits for the FLoRA pipeline.

## What comes out

- Approved disagreement decisions; new/updated Source Records; optional CSV exports.

## Keep in mind

- Approve and Publish to FLoRA are separate actions.
- Published answers must be withdrawn before changing the answer.
- An admin cannot approve a disagreement they judged themselves.

## Buttons and actions

### [Preview import](admin-disagreements--preview-import.md)

Reads the pinned source and looks up metadata; reports what an import would add/update/keep.

**Saves:** No adjudication database rows are written by the preview; metadata lookups may be cached.

**Visible:** After the read/lookup completes.

### [Import rows](admin-disagreements--import-rows.md)

Adds missing rows and refreshes only rows nobody has judged; preserves judged rows and deletes nothing.

**Saves:** adjudication.records.

**Visible:** After import succeeds; counts reload.

### [Review / Next / filters](admin-disagreements--review-next-filters.md)

Reads a disagreement, both judgements and any final answer/source linkage.

**Saves:** No write.

**Visible:** On fetch.

### [Approve / Save the answer](admin-disagreements--approve-save-the-answer.md)

Validates the selected original and outcome, then records the admin's final answer.

**Saves:** adjudication.final and adjudication.records.status=approved.

**Visible:** After success; does not yet publish a source row.

### [Undo approval](admin-disagreements--undo-approval.md)

Removes an approved final answer that has never been published and returns it to awaiting approval.

**Saves:** Deletes adjudication.final and changes adjudication.records status.

**Visible:** After confirmation succeeds.

### [Publish to FLoRA](admin-disagreements--publish-to-flora.md)

Checks eligibility/conflicts and creates or updates its Source Records row; records the publication identity.

**Saves:** source_records; adjudication.final publication fields; status=published.

**Visible:** Source Records/derived grid after refresh; stored full CSV/API at next pipeline.

### [Withdraw from FLoRA](admin-disagreements--withdraw-from-flora.md)

Marks the source row deleted, preserves history and returns the disagreement to approved for editing.

**Saves:** source_records.deleted_at/deleted_reason; adjudication.final withdrawal fields; status=approved.

**Visible:** Derived view after refresh; stored release at next pipeline.

### [CSV exports](admin-disagreements--csv-exports.md)

Downloads the adjudication final-answer or judgement data through its dedicated export endpoint.

**Saves:** Browser file only.

**Visible:** At request time.

## Find it in the code

- [docs/adjudication.js:26 — initAdmin](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js#L26) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/docs/adjudication.js)
- [adjudication/api.py:60 — create_router](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py#L60) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/api.py)
- [adjudication/review.py:266 — approve](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L266) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
- [adjudication/review.py:404 — publish](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py#L404) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/adjudication/review.py)
