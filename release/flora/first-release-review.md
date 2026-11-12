# First FLoRA Zenodo release: 2026.11.1

CalVer `YYYY.MM.N` is agreed. The release now uses the 30 columns selected by Lukas,
plus the tested `author_overlap_pct` field, in the order in [columns.json](columns.json).
Only the percentage is released; neither author lists nor the overlap count are
included in the deposit. The replication outcome and each reproduction outcome sit next to their quote and source. The full
internal export remains in the repository.

## Prepared offline preview (11 November 2026)

- Source: the stored authoritative publication after correction-log changes 446–746,
  with 2,884 records, SHA-256
  `a4a65a57c12327dfbde6769fe2bb181c7827ed5bfe0b34b6049bc28901441b6c`.
  The tracked repository output is unchanged;
  the local publication uses the normal `publish_flora.publish` path.
- First changelog: 2,884 added; 0 changed; 0 retired (initial release baseline).
- Release CSV: 31 selected columns, quoted UTF-8 with BOM.
- The preview regenerates the derived percentage through `author_overlap.augment`
  and the existing `final_export.csv_text` serializer. Release projection also applies
  the agreed source labels and clears inapplicable outcome/evidence fields to NA.
  Applicable evidence and identifiers are preserved. Preview assembly writes
  neither tracked outputs nor the database; identifier additions above are logged
  corrections applied separately through the normal correction service.
- The codebook lists exactly the 31 release columns, in release order, and observed
  categorical values. Source/full-export and release hashes are recorded separately.

This bundle is an **offline pipeline preview**, not a production-ready publication.
Its source manifest is marked `offline_pipeline_preview`; production upload refuses
it. Merge/deploy the fix, run the normal authoritative pipeline/publication, then
assemble and review a fresh release. Assembly refuses stale overlap percentages;
it does not patch source outputs. Other data corrections still use the correction log.

## Author-overlap verification

The earlier pipeline could interpret a plain group name as a surname before final
normalization converted it to a literal collective author. Scoring now uses the
same normalization as export, and final export recalculates the derived fields
before normalization can discard an incomplete byline entry.

Both overlap measures are missing when either byline is absent, malformed, or has
any collective/unresolved entry, including mixed person/group lists. A family name
without a given name remains usable. Group membership is not inferred.

On the current published author metadata, 71 previously numeric percentages become
missing; no numeric percentage changes to another numeric value. This includes
`FLORA-000662`, `FLORA-000896`, `FLORA-001262`. Detailed affected IDs are in
[author-overlap-audit.json](author-overlap-audit.json). These are deterministic
changes to a derived field, not manual changes to study coding.

The percentage remains a surname proxy: distinct shared family names / report
author count × 100, rounded to one decimal. Homonyms and repeated surnames limit
interpretation. It is not a verified percentage of shared people.

## Proposed metadata for Lukas's approval

- Title: **FORRT Library of Reproduction and Replication Attempts (FLoRA)**.
- Creators: the existing 220 authors, in order, from fred-data at commit
  `42a22b635efd6ee4cc33d64f322742d0545d796a`; see
  [citation-template.cff](citation-template.cff). No emails are included.
- Licence: **CC BY 4.0**, inherited from that citation file.
- Version/access/type: `2026.11.1`, open access, dataset.
- Description: a comprehensive database of reproduction and replication studies
  in the social and behavioural sciences, linking original studies to reports,
  with coding guidance, record-level changes and reuse guidance. The generated
  metadata includes the actual record/change counts.
- DOI: none assigned. CITATION.cff receives only a DOI actually reserved by Zenodo.

## Bundle file list

`flora.csv`, `CODEBOOK.md`, `FAQ.md`, `README.md`, `CHANGELOG.md`, `changes.json`,
`REUSE.md`, `CITATION.cff`, `flora_release_manifest.json`, `flora_release_notes.md`,
`zenodo-metadata.json`, `project_classification.json`, `bundle-checksums.json`.

The local preview is archived at `release-bundles/2026.11.1`. It supersedes the
previous 51-column candidate. Superseded bundles without the agreed projection are
also rejected for upload. No production draft, DOI reservation, upload or publication
has been performed. No explicitly identified sandbox token is available.

## Production command after the authoritative rebuild and approval

No approved production candidate exists. Keep the offline preview separate.
After the authoritative rebuild, assemble a fresh candidate at
`release-bundles/production/2026.11.1` using the command in
[the release guide](../../docs/FLORA_ZENODO_RELEASES.md). Approval must identify that
exact candidate and its checksum manifest. Only then, with `ZENODO_TOKEN` exported:

```bash
python3 zenodo_flora.py --bundle release-bundles/production/2026.11.1 --upload production --publish --approve-production
```

Sandbox uses `ZENODO_SANDBOX_TOKEN`; credentials are never stored in the bundle.

## Outcome and source interpretation

Replication outcome/quote/source are NA on reproduction rows; reproduction axes and
associated evidence are NA on replication rows. Legacy combined outcome strings stay
in the full internal export but are not repeated in the release.

Source is now project-first, without search-pipeline protection. RP:P matches
normalized report identifiers in doi_r/alt_identifier_r (99 current records); the
Boyce student-replication collection matches its report identifier (140); SCORE is
matched through verified, type-specific published-paper alternative identifiers
(301 total: 166 replications and 135 reproductions), its known intake, and the
65 earlier explicit memberships. Shared report identifiers extend matching across
routes. All qualify at strictly >50 distinct active released record IDs. Existing
labels persist after retirements; the registry records qualification evidence.

Unmapped large identifier groups require naming/review and block production;
conflicting project matches stop assembly. Title mentions alone remain unassigned
review candidates. No additional CSV column is added. For unmatched records the
fallbacks remain manual entry, FReD, SCORE and search pipeline. Raw internal routes
are unchanged. The previous source-distribution page documents the raw snapshot.

All 65 earlier unassigned SCORE mentions are confirmed; no unmatched project-name
mentions remain. See the [membership review](SCORE_MEMBERSHIP_REVIEW.md).
Three of these records still lack a verified report byline, a separate limitation.

## SCORE publication links and remaining metadata work

The [paper-identifier batch](../../data/corrections/SCORE_PAPER_IDENTIFIERS.md)
adds the replication-paper DOI to 166 replications and the reproduction-paper DOI
to 135 reproductions. Every attempt is matched to the relevant published outcome
dataset. These are logged corrections, not output edits. Existing alternatives
and the individual report identifiers/URLs are preserved. The refreshed preview
changes only 301 alternative-identifier cells and 148 project source labels from
the preceding preview; all other release data cells are unchanged.

On 12 November, the [report DOI batch](../../data/corrections/SCORE_REPORT_DOIS.md)
filled 166 verified missing `doi_r` values through log entries 747–912, preserving
SCORE paper alternatives and existing report metadata. Fourteen reports have no
registered DOI and retain blank primary fields. This dated preview predates those
primary-DOI corrections and must be reassembled from a fresh publication.

The short-file GUID resolver defect is fixed. Report-byline corrections in PR #26
and the authoritative pipeline rebuild remain outstanding before production. See
[SCORE_LINKAGE.md](SCORE_LINKAGE.md). Nothing has been sent to Zenodo.
