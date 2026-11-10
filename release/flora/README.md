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

The bundle preserves the published CSV's cell values and permanent `id`s. It adds
an encoding BOM only if absent. Corrections must go through flora-validation's
correction log, followed by a fresh publication and release; never edit this CSV.
The manifest's `source_manifest` identifies the input publication and its correction
watermark. `dataset_version` is the dated Zenodo version; the helper's legacy
`version` remains null because its semantic version scheme describes another layer.

A local dry run has no DOI. On upload, the citation file receives the version DOI
reserved by Zenodo. Reservation does not mean publication or DOI registration.
Use the citation file in the published deposit when citing the data you analysed.

Source and maintenance: https://github.com/forrtproject/flora-validation
