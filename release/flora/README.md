# FLoRA dataset release

The FORRT Library of Reproduction and Replication Attempts links original studies
to replication and reproduction attempts in the social and behavioural sciences.
Each row represents a reference pair; a report may contribute multiple rows.

- `flora.csv`: complete dataset, UTF-8 with BOM for Excel; `NA` denotes missing data.
- `CODEBOOK.md`: column definitions and permanent identity/storage guidance.
- `FAQ.md`: the team's coding guidance.
- `CHANGELOG.md` and `changes.json`: net added, changed and retired permanent IDs.
- `REUSE.md`: interpretation, licence and reuse guidance.
- `CITATION.cff`: citation for this specific version.
- `flora_release_manifest.json`: exact data hashes, source build and correction watermark.
- `flora_release_notes.md`: release summary from the existing preparation helper.
- `zenodo-metadata.json`: proposed descriptive metadata.
- `bundle-checksums.json`: SHA-256 hashes of the deposited files.

The bundle projects the authoritative publication onto the 31 agreed columns in
`release/flora/columns.json`, preserving permanent IDs and applicable outcome/evidence
values. Source values use the four documented release labels. Inapplicable outcome
and evidence fields are NA: replication outcomes on replication rows; the two
reproduction axes on reproduction rows.
Columns are ordered by record identity, original study, replication/reproduction
report, author overlap percentage, outcomes and evidence, then source. The full
internal export is not deposited. Output CSV is quoted UTF-8 with a BOM.
Corrections must go through flora-validation's correction log, followed by a fresh publication and release; never edit this CSV.
The manifest's `source_manifest` identifies the input publication and its correction
watermark. `dataset_version` is the dated Zenodo version; the helper's legacy
`version` remains null because its semantic version scheme describes another layer.

A local dry run has no DOI. On upload, the citation file receives the version DOI
reserved by Zenodo. Reservation does not mean publication or DOI registration.
Use the citation file in the published deposit when citing the data you analysed.

Author overlap is a surname-based proxy, not verified person identity. Percentages
are missing if either byline is absent, malformed, or contains an unresolved or
collective author. The final pipeline export recalculates from normalized bylines;
release assembly blocks stale percentages.

Source and maintenance: https://github.com/forrtproject/flora-validation

Source labels are `bulk entry` (both entry sheets), `FReD` (the legacy COS input),
`SCORE`, and `search pipeline` (website-validated and manually entered records).
Search pipeline includes the broader discovery/curation workflow, not necessarily
automated searching. These are coarse import categories, not a project registry or
a validation-quality scale. Project-size rules are not inferred automatically.
