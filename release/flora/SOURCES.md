# Release input provenance

`FAQ.md` and the authors/licence in `citation-template.cff` were copied from
forrtproject/fred-data at commit `42a22b635efd6ee4cc33d64f322742d0545d796a`:
`output/flora_faq.md` (the website's coding guidance) and `CITATION.cff`.
The template excludes the old version and DOI. Local FAQ errata use the published
alternative-identifier names, report precedence and the two reproduction axes.
Review the author list and licence before the first production release.

The codebook extracts maintained definitions from `docs/FLORA_DATA_TABLE.md` and
records categorical values from the actual release. Byte manifests and notes reuse
`release_helpers`; normal publication comes from `publish_flora.py`.

`projects.yml` contains report DOI anchors and historical qualification counts/hashes
for RP:P, the Boyce student collection and SCORE. Current SCORE records carry verified
paper alternative identifiers; the known SCORE intake also seeds report matching.
No exploratory membership dump or static preview is needed at runtime.

The already-applied identifier evidence is maintained separately in
[PR #30](https://github.com/forrtproject/flora-validation/pull/30).
Pre-split exploratory material is recoverable from
[479b12d](https://github.com/forrtproject/flora-validation/tree/479b12dc05a04f765f5ad1460dd0cad522f96331).
A private archive with SHA-256
`d9bfae927b94712ed214520529092b2070fbe0a46100fae74f518a49d076ab44`
is retained by the maintainer. These snapshots are not dataset release inputs.
