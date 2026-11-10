# First FLoRA Zenodo release: 2026.11.1

Prepared offline on 10 November 2026 from the existing repository publication.
No production draft, DOI reservation, upload or publication was performed. No
explicit sandbox token was available, so no sandbox upload was attempted.

Source CSV SHA-256: `053a2a92536d694f42d78947a5f7d3d081fb041b2f144387179295d105c51e99`.
The CSV remains byte-identical, including its UTF-8 BOM. Source correction watermark:
346. Reassemble from this exact source publication to reproduce the reviewed data.

## First changelog

Added: **2,851**; changed: **0**; retired: **0**. Current records: **2,851**.

First Zenodo release: all current records are additions to this release baseline.
These counts do not reconstruct additions or retirements before this baseline.
`changes.json` enumerates all 2,851 IDs.

## Proposed metadata for Lukas's approval

- Title: **FORRT Library of Reproduction and Replication Attempts (FLoRA)**.
- Creators: **the 220 authors, in their existing order**, from fred-data's
  CITATION.cff at commit `42a22b635efd6ee4cc33d64f322742d0545d796a`.
  Full names and existing ORCIDs are in [citation-template.cff](citation-template.cff)
  and the assembled `zenodo-metadata.json`. No emails are included.
- Licence: **Creative Commons Attribution 4.0 International (CC BY 4.0)**,
  inherited from that citation file; API identifier `cc-by-4.0`.
- Type/access: dataset, open access.
- Version: `2026.11.1`.
- Description: A comprehensive database of reproduction and replication studies
  in the social and behavioural sciences. Each row links an original study to a
  replication or reproduction attempt. Added: 2,851; changed: 0; retired: 0.
  Current records: 2,851. Includes coding guidance, record-level changelog and
  reuse guidance.
- DOI: none assigned. Upload will use only a DOI actually reserved by Zenodo.

## Bundle file list

1. `flora.csv`
2. `CODEBOOK.md`
3. `FAQ.md`
4. `README.md`
5. `CHANGELOG.md`
6. `changes.json`
7. `REUSE.md`
8. `CITATION.cff`
9. `flora_release_manifest.json`
10. `flora_release_notes.md`
11. `zenodo-metadata.json`
12. `bundle-checksums.json`

The completed local bundle is `release-bundles/2026.11.1` (gitignored archive).
No token is in the bundle. `ZENODO_TOKEN` must be exported in the invoking process;
`ZENODO_SANDBOX_TOKEN` is needed for the separately assembled sandbox bundle.

## Exact production command, only after approval

From the repository root, using the reviewed bundle:

```bash
python3 zenodo_flora.py --bundle release-bundles/2026.11.1 --upload production --publish --approve-production
```

Manual releases and `YYYY.MM.N` versions support deliberate review and more than
one release per month. The deposited CITATION.cff is frozen for each version;
future author/version updates cannot change citations in an older deposit.
See [the release guide](../../docs/FLORA_ZENODO_RELEASES.md) for recovery and subsequent releases.

## Codebook verification and outstanding data review

All 51 CSV columns are documented in order. The bundle codebook now includes every
observed type, source and outcome/axis label. Author fields with nonmissing values
all parse as JSON arrays. Definitions clarify that author overlap is a shared-surname
count with the report author count as the percentage denominator, and that reproduction
outcomes/quotes combine both axes.

Recomputing author overlap found three exceptions: `FLORA-000662`, `FLORA-000896`,
`FLORA-001262`. Each stores 0 / 0.0 despite a collective-author-only list with no
usable family names on one side; the current calculation returns missing. The other
2,848 records agree with that calculation. Resolve these through the correction log
and regenerate the publication before approving production; the reviewed CSV has
not been edited. These checks validate documented structure and the overlap formula,
not the substantive correctness of every study's coding.
