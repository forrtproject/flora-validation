# Release guidance provenance

`FAQ.md` and the authors/licence in `citation-template.cff` were copied from
forrtproject/fred-data at commit `42a22b635efd6ee4cc33d64f322742d0545d796a`:
`output/flora_faq.md` (the website's coding guidance) and `CITATION.cff`.
FAQ line endings and trailing whitespace were normalized.
The template intentionally excludes its old version, DOI and OSF URLs. Review the
author list and licence before the first production release. Refresh these pinned
sources deliberately for later releases; assembly never fetches mutable URLs.

`CODEBOOK.md` extracts the maintained published column dictionary from
`docs/FLORA_DATA_TABLE.md`, with the exact categorical labels in each release. Release summaries and byte manifests
reuse `release_helpers.prepare_release`; source publication uses publish_flora.py.

`projects.yml` anchors RP:P and the Boyce student-replication collection to report
DOIs already in the snapshot and the existing META_PAPER_DOIS list. Labels reflect
the report titles/citations in that snapshot. SCORE is anchored by its dedicated
intake; no DOI is invented. Qualification records the matching count, snapshot
SHA-256 and date. Additional aliases or project groups require an explicit registry
update; count-based candidates alone do not invent names or memberships.

The 65 explicit SCORE record IDs have report/component evidence in
`score-project-membership-review.json`, summarized in `SCORE_MEMBERSHIP_REVIEW.md`.
File-to-component mappings reuse the report-byline audit in flora-validation PR
#26 at commit `17b84324ae024c6e840361173c19b335c05190da`; live parent-project checks
and one same-component preregistration independently confirm SCORE membership.
Authorship corrections and membership decisions remain separate.

The pinned FAQ has local release errata: alternative-identifier names match the
public schema, and the individual-report example distinguishes primary and
alternative identifiers. Its underlying coding guidance is unchanged.
