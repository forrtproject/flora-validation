# Published versions of preprints (fred-data #120)

`published_versions.py` checks the prepared `output/flora.csv`, on both `doi_o`
and `doi_r`. It covers the preprint servers recognised by `preprint_dedup.py`
(OSF-hosted servers, bioRxiv/medRxiv, SSRN, Research Square, arXiv, and others)
and generic OSF `10.17605/` repository records. The latter are **not assumed to
be preprints**: most are reproduction projects with no journal version.

```sh
python3 published_versions.py
```

Set `OPENALEX_MAILTO` to supply a contact address in the generic client user-agent;
no personal address is hard-coded. Requests run serially, at least 0.5 seconds
apart, with bounded retries and `Retry-After` support. Each response, including
404/410 misses, expires after seven days. Failed requests are never cached as
misses. The command exits nonzero on lookup failures and writes the failed URLs
to the adjacent `.status.json`; partial candidates remain available.

The scheduled Sync entry sheets workflow runs this after preparing the current
snapshot, restores/saves the expiring API cache with Actions cache, and uploads
both CSV and status as a separate 90-day artifact. Discovery failures do not
block a validated FLoRA release. They are visible in step logs and the status
artifact. This enrichment proposes identifier decisions; it does not modify the
released CSV or confirmed decisions.

## Evidence and review

The [OSF API](https://developer.osf.io/) exposes `article_doi` on a preprint's
detail record. Versioned identifiers use the base OSF GUID to retrieve the latest
publication link. Crossref's [relation metadata](https://www.crossref.org/documentation/schema-library/markup-guide-metadata-segments/relationships/)
is checked in both directions: `is-preprint-of`/`is-version-of` on the preprint,
and `has-preprint`/`has-version` on journal records found through
`relation.object`. Reverse search is paginated. Each target must resolve in
Crossref as a `journal-article`, differ from the source, and not itself be a
recognised preprint or generic OSF repository record. A relation to a new
preprint version is not evidence of journal publication.

There is **no title-search fallback**. Titles or shared authors alone cannot
produce a publication candidate. Multiple journal targets are held for review
without a proposed action. Publication links are deposited assertions, not an
independent verification of the publisher's claim.

## Applying a reviewed candidate

`output/published_version_candidates.csv` uses the confirmed-decision columns:
`side`, `doi_1` (preprint), `doi_2` (journal version), and `action`. It also keeps
source evidence, URLs, affected FLoRA IDs, and `proposed_action`.

1. Review the evidence and the affected records. Only a single relation-backed,
   verified journal target receives `proposed_action=keep_2`; `action` stays blank.
2. Copy the reviewed fields into `cache/confirmed_preprint_duplicates.csv`,
   setting `action=keep_2`. Existing rulings, including `keep_both`, take priority
   during the next build; candidate discovery never overwrites them.
3. Keep the generated side: `original` or `replication published version`.
   The latter explicitly permits replacing a standalone replication DOI.
   Ordinary `replication` duplicate rulings retain their existing drop-only
   semantics.
4. Run the normal enrichment and preparation pipeline. Confirmed replication
   publication targets are included in metadata enrichment before the transform.
   The version of record becomes `doi_o`/`doi_r`, the former DOI is appended to
   `alt_identifier_o`/`alt_identifier_r`, and any resulting duplicate DOI pair
   goes through the existing merge machinery.

Candidates are not automatically confirmed. This retains the existing policy
that only confirmed equivalences enter alternate identifiers. A review can reject
an incorrect deposited relation without the next scan undoing that decision.
