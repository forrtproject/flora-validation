# Applied SCORE identifier corrections

These are the durable patches/evidence split from PR #25. They are already applied
in the correction log and public CSVs; merging this PR does not rerun them.
No author, outcome or report URL correction belongs to these batches.

| Table | Records | Meaning |
| --- | ---: | --- |
| score_paper_identifiers.csv | 301 | Append the appropriate SCORE paper DOI to alternative identifiers (166 replications, 135 reproductions). |
| score_report_dois.csv | 166 | Fill a missing primary DOI with the registered individual-report DOI. |
| score_report_doi_unfilled.csv | 14 | Report-level endpoints had no registered DOI; primary fields stay blank. |
| score_identifier_receipts.csv | 467 | Before/after values and correction-log IDs: paper links 446–746; report DOIs 747–912. |

CSV tables are UTF-8 with a BOM and one record per row. Shared source descriptions,
package hashes, application dates and verification notes are recorded once in
`score_identifier_provenance.json`. Report component/attempt IDs and API proof remain
in the record tables. The conversion was checked to preserve every original patch.

Paper links were matched to the appropriate outcome table in the official SCORE
[replicability](https://osf.io/g5sny/) and [reproducibility](https://osf.io/ed8pj/)
packages. Both DOI/title pairs were checked with Crossref and the
[official publication list](https://www.cos.io/score-evidence). Original report DOIs,
URLs and alternatives were preserved; an aggregate DOI never replaces a report DOI.
Report DOI additions use registered OSF identifiers, including verified report roots
for files in subcomponents. The metadata rebuild preserved bylines and citations.

Application scripts default to offline validation:

```bash
python3 scripts/apply_score_paper_identifiers.py
python3 scripts/apply_score_report_dois.py
```

Explicit application requires `--apply --admin-id "$FLORA_ADMIN_ID"` and DATABASE_URL in the
configured environment/ignored .env. Export `FLORA_ADMIN_ID` in the calling shell
with the intended operator's verified administrator ID. The scripts preflight current identities,
refuse conflicting changes, save through flora_edits, and commit atomically.
Already-applied corrections are no-ops, including paper links whose report DOI
was subsequently filled. No token or database credential is stored here.

Exploratory previews and superseded audits are outside this PR. Their original
version remains recoverable at public commit
[479b12d](https://github.com/forrtproject/flora-validation/tree/479b12dc05a04f765f5ad1460dd0cad522f96331).
A private archival copy is also retained by the maintainer. The separate report-
writer correction batch is PR #26; the author/export fix is PR #29.
