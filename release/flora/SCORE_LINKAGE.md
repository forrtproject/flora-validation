# SCORE publication linkage: pre-release finding

The [65-record membership review](SCORE_MEMBERSHIP_REVIEW.md) confirms additional
SCORE records across intake routes. Membership is resolved for those candidates;
the per-paper identifier and report-byline corrections described below remain
separate work. None has been silently applied to tracked outputs.

Checked 11 November 2026 against the live SCORE entry sheet and public OSF/COS
metadata. The snapshot's SCORE rows have individual-report URLs but no doi_r or
alt_identifier_r. The live sheet also has no nonmissing doi_r or link_r values:
ignoring link_r is not the cause of this snapshot's missing DOIs.

## Verified example and enrichment defect

`FLORA-002827` links to `https://osf.io/9hjxc`, which is the actual file
`Ritov_Stage2FinalReport.docx`, not an OSF project. OSF's GUID resolver returns its
parent report node `cwmb5`. That node now has the registered identifier
`10.17605/OSF.IO/CWMB5`, and its current APA/BibTeX citations contain that DOI.

Evidence:

- https://api.osf.io/v2/guids/9hjxc/
- https://api.osf.io/v2/nodes/cwmb5/identifiers/
- https://api.osf.io/v2/nodes/cwmb5/citation/apa/
- https://api.osf.io/v2/nodes/cwmb5/citation/bibtex/

`bibliographic_helpers.fetch_osf_reference` previously tried
`/v2/files/9hjxc/`; v2 file IDs are 24 characters, while these five-character file
links need `/v2/guids/9hjxc/`. The regression reproduces the empty reference result
using the real response shape. The fixed resolver reaches the report's citations,
including its verified DOI. Long file IDs continue to use the files endpoint.

This fixes citation enrichment; it does not silently replace source-owned doi_r or
alt_identifier_r. Cached failed lookups should be retried through the existing
reference-enrichment workflow after deployment. Existing manual reference overrides
retain their documented precedence and must also be reviewed for stale citations.

## Published SCORE papers

COS's official publication list links these papers:

- Replicability: Tyner et al., *Investigating the replicability of the social and
  behavioural sciences*: https://doi.org/10.1038/s41586-025-10078-y
- Reproducibility: Miske et al., *Investigating the reproducibility of the social and
  behavioural sciences*: https://doi.org/10.1038/s41586-026-10203-5
- Robustness: Aczel et al., *Investigating the analytic robustness of the social and
  behavioural sciences*: https://doi.org/10.1038/s41586-025-09844-9

Primary source: https://www.cos.io/score-evidence . The published replicability
article is also available at https://pmc.ncbi.nlm.nih.gov/articles/PMC13456834/ .
The project classifier now recognizes these verified publication identifiers
across all intake routes; the SCORE intake is an additional seed, not proof that
those records have no DOI or cannot be linked to a published paper.

## Required data correction review before production

`score-link-audit.json` records read-only OSF lookups for the snapshot's SCORE
records, with evidence URLs and registered report DOI candidates. It is a review
artifact, not an applied correction or a claim that every record belongs to the
same Nature paper. Registered report DOIs were verified for 59 records. Some other
lookups were rate-limited (HTTP 429); they remain unverified, not missing or broken
by inference. The audit also records explicit 410 responses and one unsupported
URL shape separately. Do not treat these different outcomes as the same failure.

Match each record to its relevant published SCORE paper using the paper's study/
claim identifiers or supporting dataset. Retain the individual-report link and its
verified identifier, and apply confirmed identifier/citation corrections through
flora-validation's append-only correction log. Do not blanket-assign a single
published DOI to all source=SCORE rows, edit outputs, or infer a paper's membership
from a title keyword alone. Then regenerate the authoritative publication and the
release bundle. This linkage review remains outstanding before production approval.
