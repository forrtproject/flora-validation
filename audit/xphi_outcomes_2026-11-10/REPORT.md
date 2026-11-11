# XPhi outcomes: ready-to-apply corrections

All **40 distinct original–replication pairs** have an accessible individual report.
The replication authors’ statements support **31 successful and 9 failed** outcomes.
No existing outcome label needs changing. The existing dataset-derived explanations
should be replaced with quotations and links to the individual reports.

**Nothing has been applied to production.** Neither this checkout nor the main local
checkout has a `.env`, and `DATABASE_URL` is absent from the process environment.
This is a handoff for the logged editor workflow, not a change to source records
or to the daily CSV copy.

## Decisions to review

- Follow the authors’ assessment of the target effect. Secondary discrepancies do
  not by themselves change an outcome to mixed. This matters for May & Holton,
  Liao et al., Reuter, and Murray & Nahmias. Their individual reports explicitly
  describe replication of the main finding despite other differences.
- Retain the successful outcome for Nadelhoffer (2004) and failed outcome for
  Machery et al. (2004). The archived paper’s Appendix 1 labels these differently,
  but the individual reports explicitly support the existing FLoRA labels.
- Clear `art00005` from `FLORA-002388.doi_o`. Retain Reuter’s existing title and
  citation. The [original-DOI audit](https://gist.github.com/LukasWallrich/ef8fab72ffb6d94126cf902a387543e5)
  identifies this as an article fragment rather than a DOI. No substitute DOI is invented.

## Proposed changes

| Operation | Existing records | Field edits |
| --- | ---: | ---: |
| Replace quotations and/or quote sources; clear pseudo-DOI; supply missing Machery report URL | 37 | 75 |
| Retire duplicate pairs through Source Records rulings | 2 | separate workflow |
| Add missing originals through manual records | 3 new | separate workflow |
| Existing outcome labels changed | 0 | 0 |
| XPhi pairs needing human evidence adjudication | 0 | — |

Starting from 39 rows, remove two duplicate rows and add three missing originals:
**40 rows** after the next successful rebuild. No published IDs are reassigned.
All 39 current rows and all three additions are accounted for in
[pair_evidence.csv](pair_evidence.csv), including the duplicate rows.

The missing originals are:

- McCann (2005), [10.1080/09515080500355236](https://doi.org/10.1080/09515080500355236): successful.
- Nahmias et al. (2006), [10.1111/j.1933-1592.2006.tb00603.x](https://doi.org/10.1111/j.1933-1592.2006.tb00603.x): failed.
- Nichols & Knobe (2007), [10.1111/j.1468-0068.2007.00666.x](https://doi.org/10.1111/j.1468-0068.2007.00666.x): successful.

These identifiers come from the corrections CSV linked in
[fred-data#152](https://github.com/forrtproject/fred-data/issues/152).
Their citations and inclusion were checked against Appendix 1 and their individual
reports. A fresh Crossref API check was rate limited (HTTP 429); the earlier audit’s
DOI verification is reused rather than claimed as a new check.

## Evidence and coding

Applied the [FLoRA FAQ](https://github.com/forrtproject/fred-data/blob/main/output/flora_faq.md):
use the authors’ account in the individual report, concentrating on the central
finding the replication tested. Reports with no abstract were read at their results
and discussion/success-assessment sections. No outcome was inferred from a p-value
or the project’s binary success indicator. Quotations are short continuous excerpts;
only whitespace and PDF zero-width spacing are normalized. Short excerpts such as
“Yes on both counts” refer to the report’s explicit success-assessment question.

The [XPhi project](https://osf.io/dvkpr/) provides a central
[Post-replication forms & Final reports component](https://osf.io/fmynh/).
Several FLoRA `url_r` values use file GUIDs as if they were project nodes; those
return 404 from the nodes API. The central archive recovers the corresponding files.
Additional reports were found through Appendix 1 in the
[archived paper](https://osf.io/download/6gkcy/), including
[Machery et al.](https://osf.io/download/qx6t5/),
[Greene et al.](https://osf.io/download/zx3mt/),
[Sytsma & Machery](https://osf.io/download/udhk6/) and
[Nahmias et al. (2007)](https://osf.io/download/dgc9w/).
The new quote-source links point to working file downloads, including where `url_r`
is stale; existing `url_r` values are otherwise preserved. Machery’s absent `url_r`
is supplied. The later Nahmias (2006) follow-up also explicitly reports failure.

[report_manifest.json](report_manifest.json) records the exact filenames, download
URLs, sections and SHA-256 hashes of the 40 report files used. The pair ledger
links every quotation to one of those files. Original reports are not vendored.
The input snapshot’s hash and accounting are in [counts.json](counts.json).

## Applying through the supported mechanisms

1. Run `python3 audit/xphi_outcomes_2026-11-10/verify.py` from the repository root.
   This checks the expected values against `output/flora.csv`; repeat comparisons
   against the current production editor before writing. A newer daily snapshot
   may require rebasing expected values.
2. [corrections_to_apply.csv](corrections_to_apply.csv) uses the exact six-column
   precedent from [fred-data PR #156](https://github.com/forrtproject/fred-data/pull/156):
   `flora_id,source_record_id,column,expected_current_value,new_value,note`.
   Here `flora_id` is the permanent published `FLORA-…` ID, as in that precedent.
   There is no automatic CSV ingestion just because this file is merged.
   Group rows by ID. Load the current record with `GET /api/admin/flora/{id}`,
   compare every expected value, then save a single
   `PATCH /api/admin/flora/{id}` with `fields`, current `version`, and per-field
   `notes`. This uses `flora_edits.save()` and the append-only correction log.
   The empty DOI cell means deliberately clear the field (JSON null or empty
   string), not skip it. Preserve the existing source records.
3. [manual_additions.json](manual_additions.json) contains three complete request
   bodies for `POST /api/admin/flora/manual`, with `fields`, `note`,
   `keep_distinct: false`, and deterministic client tokens for safe retries.
   First check the current dataset for the DOI pairs and use the manual duplicate
   check route. These are new relationships, not field edits on existing IDs.
   `out_quote_source` is the manual-record field name; it is deliberately shorter
   than the ledger description to meet the 100-character input limit.
4. [duplicate_rulings.csv](duplicate_rulings.csv) identifies the source UUIDs and
   published IDs. Keep `FLORA-002180` (FRED-000302), retire `FLORA-002181`
   (FRED-000335); keep `FLORA-002233` (FRED-000314), retire `FLORA-002234`
   (FRED-000299). Inspect the current provenance registry for additional contributing
   rows before ruling each duplicate. Use the Source Records duplicate action
   (`POST /api/admin/source-records/{source_uuid}/duplicate`, with
   `status: "duplicate"`, `duplicate_of: survivor_source_uuid`, and the note).
   Do not delete records or edit their source fields.
5. Run the preparation pipeline. Confirm 40 distinct XPhi relationships, the
   corrected quote sources and blank Reuter DOI, three new permanent IDs, and
   retirement of the duplicate IDs. Publish via the existing workflow so the daily
   export reaches Git. Recheck live data rather than importing a modified snapshot.

The field changes can be undone through the editor’s logged restore operation.
Duplicate rulings can be undone through Source Records; manual additions can be
withdrawn. Production write-through, duplicate retirement and rebuild behavior
have not been exercised in this offline handoff.

## Other multi-study papers requiring review

The wider scan covers all nonmissing replication DOIs appearing at least twice
in the same snapshot. It identifies **208 rows across four papers** with explicit
dataset-indicator explanations. Replacing these requires reading many individual
reports and is left as follow-up under the task’s “otherwise report with counts”
option. No speculative corrections are proposed for them.

| Paper DOI | Total rows | Explicit dataset-indicator rows | Additional table/summary candidates |
| --- | ---: | ---: | ---: |
| [10.1126/science.aac4716](https://doi.org/10.1126/science.aac4716), psychology reproducibility project | 97 | 71 | 0 |
| [10.1098/rsos.231240](https://doi.org/10.1098/rsos.231240), student replication projects | 140 | 107 | 0 |
| [10.1038/s41562-018-0399-z](https://doi.org/10.1038/s41562-018-0399-z), Nature/Science social science replications | 21 | 12 | 2 |
| [10.1126/science.aaf0918](https://doi.org/10.1126/science.aaf0918), experimental economics | 18 | 18 | 0 |
| [10.1007/s40279-025-02201-w](https://doi.org/10.1007/s40279-025-02201-w), sports/exercise science | 25 | 0 | 23 |
| [10.1038/s41562-024-02062-9](https://doi.org/10.1038/s41562-024-02062-9), decision-market experiments | 26 | 0 | 24 |
| [10.1177/2515245920958687](https://doi.org/10.1177/2515245920958687), Many Labs 5 | 9 | 0 | 9 |
| [10.1027/1864-9335/a000178](https://doi.org/10.1027/1864-9335/a000178), Many Labs | 8 | 0 | 1 |

The additional **59 candidates across five papers** cite tables or supplements;
that alone does not establish incorrect coding or the existence of separate
reports. The FAQ permits the meta-paper’s main criterion when individual reports
are unavailable. The 208 explicit cases also need a report-availability check
before concluding that their outcomes are wrong. Counts concern current wording,
not the number of outcome labels that should change.

[other_multi_study_rows.csv](other_multi_study_rows.csv) retains all 267 flagged
rows, their IDs, quotations, sources, links and classification.
[other_multi_study_summary.csv](other_multi_study_summary.csv) provides counts.
The screen recognizes dataset phrases (`Coded as … replicated`, subjective
replication ratings, `Replication success =`, `Replicated =`, `Rep. s1`) and,
separately, sources mentioning tables, figures or supplements without identifying
report discussions/conclusions. It is a targeted wording screen, not an exhaustive
manual audit; DOI-less reports and papers with only one current row are excluded.

## Verification performed

- Every proposed field’s expected value and source UUID matches the 2026-11-10
  repository snapshot; all 39 existing XPhi rows and three additions are accounted for.
- All 40 downloaded report hashes and all 42 evidence-row quotations verified by
  re-extracting PDF, DOCX or ODT text; duplicate rows share the same report evidence.
- All 75 field edits pass `flora_edits.clean_value()` and all three manual additions
  pass `manual_records.clean()` and note validation using canonical outcome aliases
  supplied offline. No production connection was used.
- Existing editor regression suite: `tests/test_flora_edits.py`, **138 passed**.
- Local verification command:
  `python3 audit/xphi_outcomes_2026-11-10/verify.py`.
  With the downloaded report originals in one directory, add
  `--reports /path/to/report-files` to reproduce hash and quotation checks.
