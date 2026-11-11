# First FLoRA Zenodo release: 2026.11.1

CalVer `YYYY.MM.N` is agreed. The release now uses the 30 columns selected by Lukas,
plus the tested `author_overlap_pct` field, in the order in [columns.json](columns.json).
Only the percentage is released; neither author lists nor the overlap count are
included in the deposit. The replication outcome and each reproduction outcome sit next to their quote and source. The full
internal export remains in the repository.

## Prepared offline preview (11 November 2026)

- Source: the repository publication with 2,884 records, SHA-256
  `5826464541891f83c594ccc295a0512cf20329373c3b90a2fafc56e0a97e807c`.
- First changelog: 2,884 added; 0 changed; 0 retired (initial release baseline).
- Release CSV: 31 selected columns, quoted UTF-8 with BOM.
- The preview regenerates the derived percentage through `author_overlap.augment`
  and the existing `final_export.csv_text` serializer. Release projection also applies
  the agreed source labels and clears inapplicable outcome/evidence fields to NA.
  Applicable evidence and identifiers are preserved; no tracked output or database
  record was edited.
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

First archive the preview and assemble a fresh bundle from the regenerated,
authoritative `output/`. Then, with `ZENODO_TOKEN` exported in the invoking process:

```bash
python3 zenodo_flora.py --bundle release-bundles/2026.11.1 --upload production --publish --approve-production
```

Sandbox uses `ZENODO_SANDBOX_TOKEN`; credentials are never stored in the bundle.

## Outcome and source interpretation

Replication outcome/quote/source are NA on reproduction rows; reproduction axes and
associated evidence are NA on replication rows. Legacy combined outcome strings stay
in the full internal export but are not repeated in the release.

Source is now project-first, without search-pipeline protection. RP:P matches
normalized report identifiers in doi_r/alt_identifier_r (99 current records); the
Boyce student-replication collection matches its report identifier (140); SCORE is
seeded from its known intake (88). Shared report identifiers extend matching across
routes. All qualify at strictly >50 distinct active released record IDs. Existing
labels persist after retirements; the registry records qualification evidence.

Unmapped large identifier groups require naming/review and block production;
conflicting project matches stop assembly. Title mentions alone remain unassigned
review candidates. No additional CSV column is added. For unmatched records the
fallbacks remain manual entry, FReD, SCORE and search pipeline. Raw internal routes
are unchanged. The previous source-distribution page documents the raw snapshot.
