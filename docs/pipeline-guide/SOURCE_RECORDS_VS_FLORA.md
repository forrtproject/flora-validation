# Source Records vs FLoRA

[Open interactive comparison](index.html#view=comparison)

Source Records shows the imported inputs. FLoRA shows a current combined view built from those inputs, rules, metadata and saved corrections. The full CSV and public API read a third layer: the stored prepared dataset, flora_data.

| Question | Source Records tab | FLoRA tab |
| --- | --- | --- |
| [What am I looking at?](readmes/compare-tabs.md) | Imported input rows, with source labels, original values and sync history. | The current combined view after transformation, deduplication, metadata and corrections. |
| [What is behind the screen?](readmes/storage-sources.md) | PostgreSQL source_records, read by source_records_service.py. | flora_service.py derives the view from inputs and saved decisions. It is not simply reading flora_data. |
| [What does one row mean?](readmes/review-identities.md) | One imported source record, identified by its source/row identity and display ID. | A combined FLoRA record. Several source rows may feed it; an excluded source may feed none. |
| [Where do I fix a DOI or outcome?](readmes/process-save.md) | Inspect the read-only detail, then follow its linked FLoRA record. Imported cells stay available as provenance. | Open the record and Save changes, confirm its outcome, or restore a recorded version. |
| [Which other decisions live here?](readmes/admin-flora.md) | Duplicate imports: keep distinct or identify the duplicate's surviving record. Run pipeline and view source/job history. | Review preprint duplicates, outcome conflicts and check findings; inspect correction history. |
| [What does Export download?](readmes/output-downloads.md) | Export CSV downloads the filtered imported source rows. History downloads belong to a specific pipeline run. | Export filtered CSV uses the tab's current view. Download full CSV uses the stored flora_data snapshot plus eligible later corrections. |
| [When do the outputs catch up?](readmes/schedule-updates.md) | A duplicate ruling affects the derived view on reload. Run pipeline reconciles the full stored dataset. | Eligible field saves update an existing stored row immediately; new rows and whole-snapshot changes need preparation. GitHub changes on publication. |
| [How do I trace a record?](readmes/compare-tabs.md) | Open the row and follow its linked FLoRA records, including cases where it was absorbed by deduplication. | Inspect source provenance and merged sources; use the registry to connect source IDs to the permanent FLoRA identity. |

## Follow an example

These are illustrative scenarios, not live production records.

### I correct a DOI

Illustrative example: an imported row has an incorrect DOI. Its derived FLoRA record already has an eligible active row in the stored dataset.

**Action:** In FLoRA, open the record, change the DOI and click Save changes.

- **Source Records:** The originally imported DOI remains visible. FLoRA corrections do not rewrite source_records or the Google Sheet.
- **FLoRA view:** The corrected DOI and its derived consequences appear on the next record fetch. The change is logged in flora_record_changes.
- **Full CSV and API:** The eligible active flora_data row updates after commit. A fresh server request sees the revision; public HTTP caches can lag.
- **GitHub:** The repository CSV waits for publication. If no eligible stored row exists yet, run the pipeline before publishing.

[Explanation and exact code](readmes/process-save.md)

### I mark a duplicate

Illustrative example: two imported source rows describe the same record and the administrator chooses one survivor.

**Action:** In Source Records → Duplicates, mark the redundant record as Duplicate of the survivor.

- **Source Records:** Both source rows remain traceable. The duplicate ruling and edit history are saved with the input records.
- **FLoRA view:** The next derived view applies the ruling. The redundant record is excluded from that combined view; row counts may change.
- **Full CSV and API:** The stored full CSV and public API retain their earlier snapshot until the next successful preparation reconciles it.
- **GitHub:** Publish after preparation, or wait for the scheduled release, to update output/flora.csv in GitHub.

[Explanation and exact code](readmes/admin-sources--duplicates-duplicate-of.md)

### A new sheet row arrives

Illustrative example: a published sheet gains a new accepted row with a new valid UUID, and it passes the import gates.

**Action:** Use Source Records → Run pipeline, or let the configured 03:00 UTC nightly workflow run.

- **Source Records:** The sheet stage inserts the row into source_records. Editing an already-imported UUID does not overwrite its existing cells.
- **FLoRA view:** The row can enter the derived view if the rules retain it. The identity registry and preparation stages give it its output identity.
- **Full CSV and API:** After successful preparation/storage, an eligible retained record is included in the complete stored dataset. An earlier failed stage can prevent that final update.
- **GitHub:** The website run does not commit GitHub. Use Publish FLoRA, or let the nightly release workflow update the repository.

[Explanation and exact code](readmes/process-queue.md)

[Complete comparison README and code references](readmes/compare-tabs.md)

[Slide PNG](diagrams/comparison.png) · [Slide SVG](diagrams/comparison.svg)
