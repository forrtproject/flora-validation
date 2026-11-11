# Published SCORE paper identifiers

Applied 11 November 2026 through `flora_edits.save` and `flora_edits.publish`:
301 additions to `alt_identifier_r`, recorded as changes 446–746. Individual
report DOIs, URLs, existing alternatives, bibliographic fields and outcomes are
preserved. The [receipt](score_paper_identifier_receipt.json) records each before/
after value and correction-log ID. No tracked output file was edited.

| FLoRA record type | Records | Added alternative identifier |
| --- | ---: | --- |
| replication | 166 | [10.1038/s41586-025-10078-y](https://doi.org/10.1038/s41586-025-10078-y) |
| reproduction | 135 | [10.1038/s41586-026-10203-5](https://doi.org/10.1038/s41586-026-10203-5) |

These are the distinct replication and reproduction papers in the
[official COS publication list](https://www.cos.io/score-evidence). Their registered
DOIs and titles were also checked against Crossref. The robustness paper was not
added by this batch. An aggregate-paper DOI is an additional publication link;
it does not replace the identity or authorship of the individual report.

## Matching evidence

The [batch](score_paper_identifiers.json) records a paper ID, attempt ID (`rr_id`),
report-component identifier and published outcome-table report IDs for every
correction. The source packages are the papers' linked OSF datasets:

- [Replicability package](https://osf.io/g5sny/): `rr_internal_tracking` and
  `repli_outcomes` in `analyst data.RData`.
- [Reproducibility package](https://osf.io/ed8pj/): `rr_internal_tracking` and
  `repro_outcomes` in `analyst data.RData`.

Both package and RData SHA-256 hashes are pinned in the batch. Tracking-table
presence alone is insufficient: the matched attempt must occur in the appropriate
paper's outcome table. For components represented in both papers, the FLoRA record
type chooses the relevant paper; this batch does not change study types.

Matching used 269 pinned file-to-report-component mappings, 17 report-citation OSF
identifiers, 10 project links in report headers, four paired paper/attempt codes
in reports, and one direct report identifier. The pinned mappings/headers come
from the exact record-ID/report-URL matches in the independent byline audit,
[PR #26](https://github.com/forrtproject/flora-validation/pull/26), commit
`17b84324ae024c6e840361173c19b335c05190da`. Report codes, not an author's name or
the keyword SCORE, establish the extra links. Paired-code matches also require
the original DOI to agree with the paper's metadata.

Three exact component matches use a preprint DOI for the original in SCORE and a
journal DOI in FLoRA (FLORA-001530, FLORA-001539, FLORA-001546). Their report
components and attempt IDs establish membership independently; original-study
identifiers were not changed. FRED-000997 already had an OSF alternative, retained
before appending its paper DOI with a semicolon.

The published datasets identify additional SCORE records across intake routes,
including reproductions whose citations did not contain the keyword SCORE.
All 153 previously labelled SCORE records are covered; the identifier additions
allow the release classifier to identify 301 SCORE records automatically.

## Application and verification

```bash
python3 scripts/apply_score_paper_identifiers.py
# With DATABASE_URL supplied by the configured environment or ignored .env:
python3 scripts/apply_score_paper_identifiers.py --apply --admin-id 3
```

The default validates the batch without opening the database. Application checks
every record's published identity, study type, report URL and DOI baseline before
writing anything. It preserves current alternatives, refuses a conflicting SCORE
paper DOI, uses the correction log's version checks, and commits the whole batch
together. Reapplication is a no-op when the DOI is already present.

All 301 saved log entries were checked to change only `alt_identifier_r`.
Stored-table content hashes excluding that field and update/version timestamps
were identical before and after. The authoritative API snapshot receives the
changes immediately; repository outputs follow the normal publication workflow.
Report-byline corrections in PR #26 remain separate.
