# Release guidance provenance

`FAQ.md` and the authors/licence in `citation-template.cff` were copied from
forrtproject/fred-data at commit `42a22b635efd6ee4cc33d64f322742d0545d796a`:
`output/flora_faq.md` (the website's coding guidance) and `CITATION.cff`.
FAQ line endings and trailing whitespace were normalized.
The template intentionally excludes its old version, DOI and OSF URLs. Review the
author list and licence before the first production release. Refresh these pinned
sources deliberately for later releases; assembly never fetches mutable URLs.

`CODEBOOK.md` is copied at assembly time from the maintained
`docs/FLORA_DATA_TABLE.md` in this repository. Release summaries and byte manifests
reuse `release_helpers.prepare_release`; source publication uses publish_flora.py.
