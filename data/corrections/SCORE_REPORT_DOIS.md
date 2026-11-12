# Individual SCORE report DOIs

Applied 12 November 2026 as correction-log entries 747–912. The
[receipt](score_report_doi_receipt.json) records all 166 additions. A normal
rebuild preserved report metadata, and a repeat application made zero changes.

The SCORE papers remain in `alt_identifier_r`. Primary `doi_r` values identify the
individual report wherever OSF has registered a DOI. Reports without a registered
DOI retain their individual URLs and the appropriate SCORE paper alternative;
no aggregate-paper fallback is inserted into `doi_r`.

On 12 November 2026, all 180 missing primary DOI fields among the 301 previously
verified SCORE records were checked. Registered report DOIs were verified for
166 records; 14 report-level identifier endpoints returned no DOI. No lookup was
left unresolved. The [batch and unfilled cases](score_report_dois.json) retain the
OSF identifier API evidence for each decision. Registered identifiers from the
11 November audit were reused; previously unchecked nodes were queried directly.

Fourteen report files sit in subcomponents without their own DOI, while their
parent report has one. These additions require both the parent's registered DOI
and a verified parent/root relationship from the file component to that specific
report. The published SCORE outcome datasets independently identify those report
roots. No DOI was inferred merely by constructing an OSF DOI prefix.

Application uses the append-only correction log, then rebuilds through the normal
`flora_service` path before publishing to the stored dataset. It fills missing
values only, preserves the SCORE alternatives, and refuses changes to report
bylines, citation text, outcomes or report URLs. Existing nonmissing primary DOIs
are not replaced. The derived DOI hash and OpenAlex ID may follow a new DOI.
Any failed check rolls back the whole transaction. Reapplication is a no-op.

```bash
python3 scripts/apply_score_report_dois.py
# With DATABASE_URL in the configured environment or ignored .env:
python3 scripts/apply_score_report_dois.py --apply --admin-id 3
```

The Publish FLoRA export completed successfully on 12 November 2026:
https://github.com/forrtproject/flora-validation/actions/runs/43205377210 .
No tracked output is edited by hand. Repository CSVs are refreshed through the existing
Publish FLoRA and downstream sync workflows. This correction does not apply the
separate report-byline proposals in PR #26 or publish anything to Zenodo.
