# Published-version census: 10 November 2026

The live check found **18 relation-backed candidates affecting 18 current FLoRA
rows: 12 on doi_r and six on doi_o**. Each has one verified Crossref journal-article
target and receives proposed_action=keep_2; action remains blank for confirmation.
There are no conflicting journal targets and no title-only candidates. No FLoRA
records or confirmed decisions were changed.

Input: `output/flora.csv` at e4a98ee, 2,851 rows, SHA-256
`053a2a92536d694f42d78947a5f7d3d081fb041b2f144387179295d105c51e99`.
The scan checked all **254 distinct eligible DOIs**, covering 261 side occurrences
(238 replication-side, 23 original-side). These include 155 generic OSF project
DOIs, none of which produced a publication candidate. The remaining 106 side
occurrences are recognised preprint-server DOIs. All final lookups completed
without errors; temporary rate-limit responses were retried, never recorded as
absence. Successful and missing responses were reused from the seven-day cache.

All 18 candidates in this snapshot come from Crossref relations. Sixteen have
both is-preprint-of and has-preprint evidence; one has only has-preprint and one
only is-preprint-of. OSF article_doi and generic version relations were also
checked, but added no further verified journal targets in this snapshot.

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

[All candidates, evidence URLs, titles and affected FLoRA IDs](published-versions-2026-11-10.csv).
For confirmation and scheduled operation, see the
[published-version guide](../published-versions.md).
