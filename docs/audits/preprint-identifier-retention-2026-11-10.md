# Preprint identifier retention during validation (fred-data #162)

The audit found **25 of 2,867 published FLoRA rows** missing a preprint DOI
after a recorded replacement: **23 replication-side and 2 original-side**.
Twenty-four arose in validation; one was a direct FLoRA editor correction
(`FLORA-000317`, change 135). These are 25 distinct rows and 25 distinct
row/side/identifier omissions. Generic OSF deposits are reported separately.

## What was missing

The validation UI stores original-DOI corrections separately from the extracted
DOI. Consensus and admin approval then put the corrected DOI in `validated`.
Replication publication links use `doi_r_published`; admin resolution also
supports `final_doi_r`. The raw extraction and validated source snapshots kept
evidence of the old identifier, but publication did not consistently put it into
an alternative-identifier field. In particular, `validated` has no
`alt_identifier_o` column. Ordinary FLoRA editor saves also replaced preprint
DOIs without adding an alias; the approved-publication CSV already did so.

The audit reads retained extracted/final values and validated source snapshots,
`admin_decisions`, `source_record_edits`, `manual_record_events` and
`flora_record_changes`. It follows recorded DOI transitions to the **current
published DOI** and tests its alternative identifiers after normalising DOI case
and resolver URLs. It counts current published rows, not obsolete records or
unaccepted validator proposals. Unlogged replacements predating retained history
cannot be reconstructed by this audit.

## Changes

| Path | Identifier preservation |
|---|---|
| UI correction, consensus, admin approval/resolution | Shared validated-to-source publication bridge retains the extracted preprint and intermediate preprints from accepted admin changes. An approved `doi_r_published` becomes the replication DOI. |
| Extractor refresh followed by validation | Prior validated source snapshots preserve preprints even when the raw extraction has since changed. |
| Daily validated CSV | Uses the same bridge policy, including `alt_identifier_o`. |
| FLoRA editor | Shared log append preserves a replaced preprint in the same change as its replacement, for saves, restores, reverts and type changes. |
| Hand-entered records before publication | The normal edit event includes the outgoing preprint in the appropriate alternative field. Published hand-entered records use the FLoRA editor. |
| Existing preprint deduplication | Already preserves the removed identifiers; this policy is unchanged. |

Preservation compares bare and resolver DOI forms, retains existing alternatives
and appends each replaced preprint once. Original-side alternatives are projected
into `source_records` and the CSV without adding columns to the raw validation
tables. Internal history joins are excluded from exported payloads.

## Bibliographic checks and generic OSF deposits

Crossref classifies all 25 old identifiers as `posted-content`.
The [case-by-case bibliography](preprint-identifier-bibliography-2026-11-10.csv)
includes old/new titles and author checks. Twenty-three pairs have matching
first-author surnames. The two exceptions also support the recorded replacement:

- `FLORA-002576`: Costa / Porfírio are the same first author, ORCID
  `0000-0002-6811-1407`, in both Crossref records; the title changed.
- `FLORA-002593`: the journal deposited the initial in `N. Williams` as part of
  the surname. Crossref explicitly links the preprint and article in both directions.

Five additional rows had a generic `10.17605` OSF identifier replaced:
`FLORA-002139`, `FLORA-002142`, `FLORA-002148`, `VAL-000271` and `VAL-000369`.
They are **review only**, excluded from the 25 and from automatic retention.
Generic OSF DOIs can identify projects and registrations rather than preprints.
For example, `VAL-000369`'s old DOI `10.17605/osf.io/hw3v6` identifies a 2016
preregistration, *Markers of Adulthood Subscale Development*, whereas its current
DOI `10.1177/2167696815585685` identifies a 2015 article, *Interinstitutional
Variation in Emerging Adulthood*, with a different coauthor. Automatically adding
the registration as the article's alternative DOI would create a false link.
The separate publication-discovery routine can still examine generic OSF DOIs
and requires publication evidence before proposing a correction.

## Repeatable audit and logged backfill

`backfill_preprint_identifiers.py` defaults to a read-only audit. Its `--apply`
mode groups missing alternatives by record and uses `flora_service.update_record`
and the existing FLoRA correction log, with version and primary-DOI checks.
It changes alternative identifiers only and verifies the live publication before
committing the whole batch. A rerun with no omissions creates no corrections.

The manual **Publish FLoRA** workflow offers `preprint_alias_history: audit` or
`backfill`, using the existing database secret and a public-identifier-only audit
artifact. The option defaults to `off`; no new Google Sheet is used.

Tests cover both sides, resolver/case deduplication, intermediate versions,
extractor refreshes, editor restores, atomic two-side backfills, concurrent DOI
changes, generic OSF exclusions, the source bridge and the daily CSV.
