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
