# Versioned FLoRA deposits on Zenodo

FLoRA has its own concept record; this pipeline covers FLoRA only. Each
production release requires Lukas's approval of the exact candidate bundle.

| Version | Published | Record | Records |
| --- | --- | --- | --- |
| 2026.11.2 | 2026-11-23 | [10.5281/zenodo.24477135](https://doi.org/10.5281/zenodo.24477135) | 2,884 |
| 2026.11.1 | 2026-11-23, withdrawn | [10.5281/zenodo.24461023](https://doi.org/10.5281/zenodo.24461023) | 2,884 |

2026.11.1 deposited internal release files and had a one-line description; it was
replaced the same day by 2026.11.2 (identical records) and deleted on Zenodo.

All versions: concept DOI [10.5281/zenodo.24461022](https://doi.org/10.5281/zenodo.24461022).
Published bundles and their `zenodo-state.json` receipts are archived in the
maintainer's private storage (`flora-validation/zenodo-releases/production/`).

## Policy and inputs

Use [CalVer](https://calver.org/) `YYYY.MM.N`, starting at `2026.11.1`: year,
zero-padded month and a counter starting at 1 each month. Versions must increase.
This identifies a living dataset's snapshot date; the changelog describes data
and schema changes. Releases use **manual dispatch** so each immutable deposit
receives a review. Daily repository publication continues separately.

`publish_flora.py` produces the authoritative `output/`. Assembly checks its CSV
against the existing manifest and reuses `release_helpers.prepare_release` for
hashes and notes. `release/flora/columns.json` selects and orders the 31 deposited
columns. The release applies the source labels and outcome applicability rules in
the [codebook](FLORA_DATA_TABLE.md), writing quoted UTF-8 CSV with a BOM. Both
source and release hashes are retained. Corrections go through the correction log
and a fresh publication; never edit outputs.

Assembly recalculates overlap from the source bylines to detect stale percentages.
Production requires `source_kind=authoritative_publication` and a stored dataset
revision/correction watermark, written by `release_helpers.add_data_version`.
Unmarked manifests and offline previews are rejected.

The codebook reuses `docs/FLORA_DATA_TABLE.md` and lists the exact categories in
this snapshot. Missing definitions stop assembly. Citation authors and licence are
pinned from fred-data; [SOURCES.md](../release/flora/SOURCES.md) records their
provenance. Review them for each release.

## What is deposited

Zenodo receives five files: `flora.csv`, `README.md` (rendered from
`release/flora/README.md`), `CODEBOOK.md`, `CHANGELOG.md` and `CITATION.cff`. The
record description is generated with them. The README and codebook link to the coding
guidance in `release/flora/FAQ.md` on GitHub rather than depositing it.

The bundle also holds files the next release and the audit trail need, which are
archived but not deposited: `flora_release_manifest.json` (source and release hashes,
projection, previous release), `project_classification.json`, `changes.json`
(record-level changes), `zenodo-metadata.json`, `bundle-checksums.json` and the
`zenodo-state.json` receipt.

## Changelog and project classification

Existing `flora_history.csv`, `flora_dataset_history` and
`backfill_flora_history.py` retain daily aggregate counts. `flora_store` retains
permanent IDs and current/retired records. The append-only `flora_record_changes`
logs corrections, but source refreshes can change records without a log entry.
These sources cannot reconstruct every historical record value.

The changelog therefore compares the current release CSV with the previous
published Zenodo CSV by permanent `id`. `changes.json` lists added/retired IDs and
changed field names; `CHANGELOG.md` counts unique records. Each `CHANGELOG.md` adds
the new version's section above the previous version's history, so every deposit
carries the full history. Row order is ignored;
schema changes are explicit. Returning IDs count as additions. Intermediate edits
that cancel out, including retire/reactivate cycles, are excluded. The first
release establishes a baseline: all current IDs are additions. The embedded source
manifest retains the publication's database revision and correction watermark;
private correction-log details are excluded.

`flora_release_projects.py` matches the anchors in `release/flora/projects.yml`
against report-side DOIs, alternative identifiers and OSF URLs. Project intake
rows can seed aliases too. Matching expands one hop; it never recursively merges
projects. Original-study identifiers and titles cannot establish membership.
Projects qualify above **50 distinct released record IDs** and override every
intake route, including search pipeline. Recorded qualification keeps labels
stable after retirements. Registered projects are RP:P, the Boyce et al. student
replication collection, and SCORE.

`project_classification.json` records counts, seed/member IDs, identifiers and
unmapped groups above the threshold. Conflicting memberships stop assembly;
unmapped large groups block production pending review. The manifest preserves the
registry, and changing it requires reassembly. The single `source` column names
an established project, otherwise an intake route; it cannot retain both.

## Assemble and upload

From the project root, with Python 3.12 and the listed dependencies:

```bash
python3 -m pip install requests pyyaml pandas
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/2026.11.1
```

Default assembly is an offline dry run: no API calls or token reads. Existing
bundle directories are refused. Review every file, especially the initial
changelog, codebook and `zenodo-metadata.json` (220 proposed creators, CC BY 4.0).
The bundle file list and interpretation guidance are in its README.

Use a separate sandbox bundle with `ZENODO_SANDBOX_TOKEN` in the environment:

```bash
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/sandbox-2026.11.1
python3 zenodo_flora.py --bundle release-bundles/sandbox-2026.11.1 --upload sandbox
# Optional: publish in sandbox to exercise versioning.
python3 zenodo_flora.py --bundle release-bundles/sandbox-2026.11.1 --upload sandbox --publish
```

An upload without `--publish` leaves a draft. Tokens are read only from environment
variables: `ZENODO_SANDBOX_TOKEN` or `ZENODO_TOKEN` for production. The script does
not load `.env`; use deposit write/action scopes. No token values or server error
bodies are logged. The assembly workflow needs no secret. A future
upload job would need that name in a protected GitHub environment secret.

Assemble each candidate from current `main` after the nightly Publish FLoRA run:

```bash
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/production/2026.11.1
```

Lukas must approve this exact directory and checksum manifest, including DOI
reservation and upload. **Only after that approval**, with `ZENODO_TOKEN` set:

```bash
python3 zenodo_flora.py --bundle release-bundles/production/2026.11.1 --upload production --publish --approve-production
```

Never copy or rename an earlier preview into a candidate path; assemble afresh.
Superseded audits remain at [commit 479b12d](https://github.com/forrtproject/flora-validation/tree/479b12dc05a04f765f5ad1460dd0cad522f96331).

## Subsequent versions and recovery

Keep each published bundle and its local `zenodo-state.json` receipt. For the next
release, supply that directory through `--previous` (restore it from the archive,
or recover it with `--fetch-previous 24477135` as described below):

```bash
python3 zenodo_flora.py --version 2026.12.1 --bundle release-bundles/production/2026.12.1 --previous release-bundles/production/2026.11.2
```

Alternatively, `--fetch-previous RECORD_ID --bundle DIRECTORY` recovers a public
baseline (its `flora.csv` and `CHANGELOG.md`, checked against Zenodo's MD5s) and
creates its published receipt. Use the actual
preceding **version record ID**, never the concept ID; `--environment sandbox`
selects sandbox. Upload calls `newversion` on that record and checks the concept;
see the [Zenodo REST API](https://developers.zenodo.org/#new-version).

The manual Actions workflow assembles offline, optionally recovering the previous
record. Archive bundles independently: its artifacts expire after 90 days.

Offline `CITATION.cff` contains the version and no DOI. Upload reserves a version
DOI through Zenodo's `prereserve_doi`, writes only the returned DOI into the file,
and refreshes checksums before uploading. Reservation becomes registration only
on publication. Cite the published version's citation file. Subsequent releases
get their own files and DOIs; earlier deposits remain unchanged. A concept DOI
may refer to the collection in prose once it exists.

Checksum verification protects the reviewed files. Upload saves the draft ID in
`zenodo-state.json` immediately, checks identity/concept/version on retry, removes
inherited files and checks each uploaded MD5. Re-run the same upload command to
resume after an HTTP failure. If draft creation times out **before a receipt is
saved**, inspect your Zenodo drafts and copy the intended draft ID from its URL.
Add `--resume-deposit` followed by that numeric ID to the same upload command.
Recovery validates the remote title/version/concept and saves a receipt before
any mutation; it does not create another draft. The receipt stays local. Production always requires the approval flag, including draft uploads.
Approval is an operator attestation, not authenticated authorization. It covers
the reviewed payload and the sole planned mutation: the returned version DOI in
CITATION.cff and its refreshed checksum. Data and other files remain unchanged. Published records
and DOIs cannot simply be deleted. Within 30 days of publication the owner can delete
a version on its Zenodo page (Manage → Delete record); its DOI then resolves to a
tombstone. To replace a faulty version, publish its successor with
`--previous OLD --replaces` (the changelog drops the withdrawn version's section and
says it was replaced), then delete the old version.
