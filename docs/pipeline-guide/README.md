# FLoRA pipeline field guide

[Open the clickable guide](index.html)

Double-click `index.html` to open it in a browser. The guide, explanations and code excerpts work offline. Full script links open GitHub at the reviewed commit.

Verified against local code on 29 October 2026, commit `2f3a89c56a44bd7b6270eb0fa94548f6d93512f8`. This documents configured behavior, not the live deployment's health.

## Start here

1. Open **System map** for the complete pipeline, or use **Start walkthrough** to follow one extracted paper.
2. Open **Inputs**, **Processing**, and **Outputs** for separate diagrams.
3. Choose **Admin tabs**, pick a tab, and click a button card for its simple README, save location and exact code.
4. Use **Save & update** for timing and persistence, or **Code index** to open a whole script.
5. Search for any tab, action, table or filename. Each element has a shareable link and a plain Markdown README.

## Diagram pictures for slides

SVG files stay sharp when resized; PNG files can be pasted into PowerPoint or Google Slides. SVG boxes link back to the interactive guide when opened alongside this folder.

- **Complete system:** [PNG](diagrams/overview.png) · [SVG](diagrams/overview.svg)
- **Source Records vs FLoRA:** [PNG](diagrams/comparison.png) · [SVG](diagrams/comparison.svg)
- **Input schema:** [PNG](diagrams/inputs.png) · [SVG](diagrams/inputs.svg)
- **Processing schema:** [PNG](diagrams/processing.png) · [SVG](diagrams/processing.svg)
- **Output schema:** [PNG](diagrams/outputs.png) · [SVG](diagrams/outputs.svg)
- **Storage schema:** [PNG](diagrams/storage.png) · [SVG](diagrams/storage.svg)

## Source Records vs FLoRA

[Open the comparison slide](index.html#view=comparison) · [Read the comparison](SOURCE_RECORDS_VS_FLORA.md)

A side-by-side comparison explains inputs, corrections, duplicate decisions, downloads, update timing and provenance. Three illustrative examples follow a DOI correction, a duplicate ruling and a new sheet row across the tabs, stored CSV/API and GitHub.

## Important distinctions

- Draft answers stay in the browser until submitted. Advancing to the next card does not prove the server saved the previous review.
- `validated` is the approved extractor-review table. It is not the complete prepared dataset.
- `source_records` is the combined input. The FLoRA grid derives a current view from it and saved decisions.
- `flora_data` is the stored prepared dataset used by the full CSV and public API.
- Saving eligible existing FLoRA fields updates their stored row immediately; duplicate rulings require preparation to update the full snapshot.
- Run artifacts, the current full export, and the GitHub CSV can differ because they capture different moments.
- The separate upstream extractor's internal discovery scripts are outside this checkout. This guide covers its input contract and everything downstream here.

## Admin tabs and actions

### [Entries](readmes/admin-entries.md)

The review desk for papers imported from flora-extractor. Compare the extracted record, human judgements, and AI result; decide which records belong in the approved validation dataset.

- [Filters, search, sorting, pagination, Review](readmes/admin-entries--filters-search-sorting-pagination-review.md)
- [Approve](readmes/admin-entries--approve.md)
- [Save Note](readmes/admin-entries--save-note.md)
- [Mark as Resolved](readmes/admin-entries--mark-as-resolved.md)
- [Merge duplicate (conflict dialog)](readmes/admin-entries--merge-duplicate-conflict-dialog.md)
- [It's a replication / It's a reproduction](readmes/admin-entries--it-s-a-replication-it-s-a-reproduction.md)
- [Reject — not in FLoRA](readmes/admin-entries--reject-not-in-flora.md)
- [Flag for Review / Send back for review](readmes/admin-entries--flag-for-review-send-back-for-review.md)
- [Flag / Unflag a judgement](readmes/admin-entries--flag-unflag-a-judgement.md)
- [Skip → / Cancel](readmes/admin-entries--skip-cancel.md)

### [Source Records](readmes/admin-sources.md)

The input ledger: imported entry-sheet records, approved app records, and published disagreement decisions. Open a row to see its provenance; run the FLoRA pipeline here.

- [Filters / Search / Open record / Previous / Next](readmes/admin-sources--filters-search-open-record-previous-next.md)
- [Export CSV](readmes/admin-sources--export-csv.md)
- [Run pipeline](readmes/admin-sources--run-pipeline.md)
- [Refresh](readmes/admin-sources--refresh.md)
- [History: CSV / report / full log](readmes/admin-sources--history-csv-report-full-log.md)
- [Duplicates → Keep — distinct](readmes/admin-sources--duplicates-keep-distinct.md)
- [Duplicates → Duplicate of …](readmes/admin-sources--duplicates-duplicate-of.md)
- [Show resolved / Back to grid](readmes/admin-sources--show-resolved-back-to-grid.md)

### [FLoRA](readmes/admin-flora.md)

The prepared view of the current source records: cleaned identifiers, derived outcomes, exclusions, duplicate decisions and saved corrections. This view can be newer than the stored public release.

- [Filters / Search / Refresh / Open record](readmes/admin-flora--filters-search-refresh-open-record.md)
- [Save changes](readmes/admin-flora--save-changes.md)
- [Revert field to derived value](readmes/admin-flora--revert-field-to-derived-value.md)
- [Restore this version / Restore original derived values](readmes/admin-flora--restore-this-version-restore-original-derived-values.md)
- [Confirm outcome / Choose conflicting outcome](readmes/admin-flora--confirm-outcome-choose-conflicting-outcome.md)
- [Not a problem / Undo dismissal](readmes/admin-flora--not-a-problem-undo-dismissal.md)
- [Download full CSV](readmes/admin-flora--download-full-csv.md)
- [Export filtered CSV](readmes/admin-flora--export-filtered-csv.md)
- [Preprint duplicates → Same paper, keep A / keep B](readmes/admin-flora--preprint-duplicates-same-paper-keep-a-keep-b.md)
- [Different papers — keep both / Undo ruling](readmes/admin-flora--different-papers-keep-both-undo-ruling.md)

### [Validator Stats](readmes/admin-stats.md)

Shows how contributors are progressing, their judgement timing and flags, and their review tier.

- [Level / tier button](readmes/admin-stats--level-tier-button.md)
- [Flagged count](readmes/admin-stats--flagged-count.md)
- [DM](readmes/admin-stats--dm.md)

### [Admins](readmes/admin-admins.md)

Manages administrator accounts, their linked validator identities and the notice banner shown to validators.

- [Send invitation](readmes/admin-admins--send-invitation.md)
- [Resend invitation / Send recovery link](readmes/admin-admins--resend-invitation-send-recovery-link.md)
- [Link / Unlink validator account](readmes/admin-admins--link-unlink-validator-account.md)
- [Trusted toggle](readmes/admin-admins--trusted-toggle.md)
- [Remove admin](readmes/admin-admins--remove-admin.md)
- [Send to all users (banner)](readmes/admin-admins--send-to-all-users-banner.md)
- [Clear banner](readmes/admin-admins--clear-banner.md)

### [Dashboard](readmes/admin-dashboard.md)

A read-only summary of the validation workload, completion, outcomes, disagreements and contributor activity.

- [Open tab / chart controls](readmes/admin-dashboard--open-tab-chart-controls.md)

### [Pool Priority](readmes/admin-priority.md)

Controls which waiting records are more likely to be offered to validators. It changes assignment order, not a paper's outcome.

- [Enable / outcome / year / share controls](readmes/admin-priority--enable-outcome-year-share-controls.md)
- [Refresh counts](readmes/admin-priority--refresh-counts.md)
- [Save](readmes/admin-priority--save.md)
- [Reset to defaults](readmes/admin-priority--reset-to-defaults.md)

### [Extractor Pipeline](readmes/admin-maintenance.md)

Imports the latest candidate pairs from flora-extractor into the human-review pool. This is separate from building the final FLoRA release.

- [Run full sync](readmes/admin-maintenance--run-full-sync.md)
- [Refresh history](readmes/admin-maintenance--refresh-history.md)
- [Open full log / stage details](readmes/admin-maintenance--open-full-log-stage-details.md)

### [Restricted access](readmes/admin-restricted.md)

Routes papers that a validator could not access to a chosen person who can review them.

- [Assign / Reassign](readmes/admin-restricted--assign-reassign.md)

### [Disagreements](readmes/admin-disagreements.md)

Optional review lane for disagreements between FLoRA and the Metascience Observatory. It uses its own records and two trusted/senior judgements before an admin decision.

- [Preview import](readmes/admin-disagreements--preview-import.md)
- [Import rows](readmes/admin-disagreements--import-rows.md)
- [Review / Next / filters](readmes/admin-disagreements--review-next-filters.md)
- [Approve / Save the answer](readmes/admin-disagreements--approve-save-the-answer.md)
- [Undo approval](readmes/admin-disagreements--undo-approval.md)
- [Publish to FLoRA](readmes/admin-disagreements--publish-to-flora.md)
- [Withdraw from FLoRA](readmes/admin-disagreements--withdraw-from-flora.md)
- [CSV exports](readmes/admin-disagreements--csv-exports.md)

### [Messages](readmes/admin-messages.md)

In-app conversations with validators, including replies and messages sent to everyone. These are stored in the application inbox.

- [Open conversation](readmes/admin-messages--open-conversation.md)
- [+ New → Send](readmes/admin-messages--new-send.md)
- [Send to everyone / broadcast](readmes/admin-messages--send-to-everyone-broadcast.md)
- [Reply](readmes/admin-messages--reply.md)

## Pipeline and storage READMEs

- [Extractor CSV: candidate study pairs](readmes/input-extractor.md)
- [Extractor maintenance: sync, report, retire](readmes/process-extractor-maintenance.md)
- [Published entry sheets](readmes/input-sheets.md)
- [Approved records join Source Records](readmes/process-validated-bridge.md)
- [Exclusions, outcomes and paper metadata](readmes/input-rules-metadata.md)
- [Run pipeline: the background job](readmes/process-queue.md)
- [Clean, combine and deduplicate](readmes/process-transform.md)
- [Permanent FLoRA IDs](readmes/process-identities.md)
- [Prepare and check the exact CSV](readmes/process-prepare.md)
- [Stored dataset: flora_data](readmes/storage-dataset.md)
- [Save a FLoRA correction](readmes/process-save.md)
- [Automatic checks after updates](readmes/process-entry-checks.md)
- [Three different CSV downloads](readmes/output-downloads.md)
- [Public FLoRA API](readmes/output-api.md)
- [Publish the repository copy](readmes/output-publication.md)
- [Separate validated-only export](readmes/output-validated-export.md)
- [When updates happen](readmes/schedule-updates.md)
- [Get a study to review](readmes/review-claim.md)
- [Draft answers: saved in this browser](readmes/review-draft.md)
- [Submit a judgement](readmes/review-submit.md)
- [Two reviews, Gemini, and the approval decision](readmes/review-consensus.md)
- [Skip or report an inaccessible article](readmes/review-skip.md)
- [Complete an assigned restricted study](readmes/review-assignment.md)
- [Senior override: reject](readmes/review-senior.md)
- [My Judgements and corrections after submission](readmes/review-history.md)
- [Observatory disagreements: separate review lane](readmes/review-adjudication.md)
- [Where the review data lives](readmes/review-storage.md)
- [Which ID belongs to which record?](readmes/review-identities.md)
- [What a successful server save means](readmes/review-transaction.md)
- [Source Records vs FLoRA: how the tabs connect](readmes/compare-tabs.md)
- [Source Records: the combined input](readmes/storage-sources.md)

## Maintain the guide

The audited content lives in `admin-map.json`, `pipeline-map.json`, `review-map.json` and `comparison-map.json`. The build script pins source links to the reviewed commit; update both content and that commit after a new code audit. JSON content does not automatically stay accurate when the application changes.

```powershell
python docs/pipeline-guide/build_guide.py
node docs/pipeline-guide/render_and_check.cjs
```

The build reads source as text and never imports `app.py`, connects to PostgreSQL, runs a pipeline or edits production data. The renderer uses the repository's Playwright dependency to check navigation and create PNGs. Keep the complete folder for image downloads and individual Markdown files; `index.html` alone retains the interactive text and diagrams.
