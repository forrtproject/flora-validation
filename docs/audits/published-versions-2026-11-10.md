# Published-version census: 10 November 2026

The live check found **43 candidate DOI pairs affecting 48 FLoRA records**:
14 original-side candidates, and 34 replication-side occurrences. Automatic
verification gives **41 high-confidence correction rows** (14 original-side,
27 replication-side); seven weaker replication-side matches are review-only.
No action is automatically approved. This census describes the input before the
nine specifically requested corrections are applied.

Input: `output/flora.csv` at e4a98ee, 2,851 rows, SHA-256
`053a2a92536d694f42d78947a5f7d3d081fb041b2f144387179295d105c51e99`.
The scan checked **255 distinct eligible DOIs**, covering 262 side occurrences,
including JMIR preprints and 155 generic OSF project DOIs. The final scan completed
without lookup errors. Explicit OSF/Crossref relations, OpenAlex primary-DOI
merges, and independent Crossref/DataCite bibliography verification are described
in the [operational guide](../published-versions.md).

## Seven recent OpenAlex cases

| FLoRA ID | Previous doi_r | Journal doi_r | Routine verification |
|---|---|---|---|
| REPL-001390 | 10.1101/2025.03.13.643168 | 10.1177/25152459261443491 | Review: cited authors contracted to “et al.”; Nave first author confirmed manually |
| REPL-001400 | 10.31234/osf.io/4uy2c | 10.1016/j.cortex.2024.12.017 | Crossref title + first author |
| REPL-001332 | 10.31234/osf.io/zeux9 | 10.1177/25152459241267902 | Review: shortened cited-author list; Hall first author confirmed manually |
| REPL-000503 | 10.31234/osf.io/ydgh9_v2 | 10.1016/j.jbct.2026.100641 | Crossref title + first author |
| REPRO-000009 | 10.48550/arxiv.2208.07395 | 10.17879/replicationresearch-2026-9414 | DataCite JournalArticle, matching title and Wang first author |
| REPL-000658 | 10.21203/rs.3.rs-8351701/v1 | 10.1016/j.ynirp.2026.100376 | Crossref relations; also OpenAlex merge |
| VAL-000456 | 10.2196/preprints.45481 | 10.2196/45481 | Crossref relations; also OpenAlex merge |

The raw routine keeps the two shortened titles in review. Their manually verified
corrections are approved separately in cache/approved_publication_corrections.csv.
The other automatically high-confidence candidates remain unapproved.

## Named fixtures

- REPRO-000363 / FLORA-002771: Neubauer, Rose and Ankel-Peters' 2023 I4R
  Discussion Paper 92 is identified by https://hdl.handle.net/10419/280689.
  The [publisher](https://jcr-econ.org/public-infrastructure-and-economic-development-evidence-from-postal-systems-replication/)
  confirms the journal version, 10.18718/81781.44. The approved correction moves
  doi_r to the journal DOI and appends the handle to alt_identifier_r.
- REPL-001618 / FLORA-002723: the snapshot already has Meta-Psychology
  10.15626/mp.2025.4708 as doi_r but has no preprint alias. Crossref and the
  [publisher](https://open.lnu.se/index.php/metapsychology/article/view/4708)
  confirm Schaffer et al.'s title and authors against 10.31234/osf.io/84bfy_v2.
  The approved correction adds that preprint without replacing the journal DOI.

These are identifier corrections through flora_edits, not duplicate merges.
The correction log is the source of truth; source records and permanent IDs are
preserved. See [approved batch](../../cache/approved_publication_corrections.csv).

Examples:

| Side | Preprint | Journal version |
|---|---|---|
| Replication | [10.31234/osf.io/3472y_v4](https://doi.org/10.31234/osf.io/3472y_v4) | [10.1016/j.cortex.2025.05.014](https://doi.org/10.1016/j.cortex.2025.05.014) |
| Replication | [10.21203/rs.3.rs-8351701/v1](https://doi.org/10.21203/rs.3.rs-8351701/v1) | [10.1016/j.ynirp.2026.100376](https://doi.org/10.1016/j.ynirp.2026.100376) |
| Original | [10.31235/osf.io/xatcn](https://doi.org/10.31235/osf.io/xatcn) | [10.1093/jopart/muz044](https://doi.org/10.1093/jopart/muz044) |

**Seda et al.: no candidate.** FLoRA uses `10.31219/osf.io/x394e_v3`.
The live OSF detail record returns latest version x394e_v4 with article_doi=null;
Crossref supplies no verified journal relation. Relations between preprint
versions do not count as publication evidence.

[All candidates and evidence](published-versions-2026-11-10.csv).
[Record-level correction queue](published-version-corrections-2026-11-10.csv).
For confirmation and scheduled operation, see the
[published-version guide](../published-versions.md).
