# Published-version identifier corrections (fred-data #120)

`published_versions.py` checks the prepared `output/flora.csv`, on both DOI
sides, for journal versions of preprints. It recognises OSF-hosted servers,
bioRxiv/medRxiv, SSRN, Research Square, arXiv, Preprints.org, JMIR preprints,
and generic OSF repository DOIs. OSF projects are not assumed to be preprints.

```sh
python3 published_versions.py
```

## Evidence and confidence

The [OSF API](https://developer.osf.io/) supplies `article_doi`. Crossref's
[relation metadata](https://www.crossref.org/documentation/schema-library/markup-guide-metadata-segments/relationships/)
is checked in both directions: is-preprint-of/is-version-of on a preprint, and
has-preprint/has-version on journal records found through paginated relation.object
queries. Preprint-to-preprint version relations never count as publication.

An OpenAlex lookup of the preprint DOI can return a work with a different primary
DOI. This is an additional signal, not proof of equivalence. A journal target
must be verified as a Crossref journal-article or a DataCite JournalArticle.
DataCite is needed for Replication Research articles. The match is high confidence
when backed by an explicit relation, or when the OpenAlex DOI merge is confirmed
by a title similarity of at least .90 and matching first-author surnames against
the target's registered bibliography. Prefer the source's Crossref bibliography;
use the input title and authors when it has none. No title-search fallback is
used, and OpenAlex merges to another preprint, repository or dissertation cannot
create a journal candidate.

An unconfirmed OpenAlex merge or conflicting journal targets goes to review only,
with no proposed action. Renamed papers may need manual verification of the
source and publisher bibliographies. The two shortened cited-author lists in the
seven-case audit remain review-only in the routine; their separately approved
corrections document the manual verification.

## Apply changes through the FLoRA correction log

The companion `output/published_version_candidates.corrections.csv` has one row
per FLoRA record and DOI side. It contains record_id, flora_id, side (o/r),
old_identifier, new_doi, confidence, evidence, evidence_url, note, and approved.
The routine leaves approved blank. Review the evidence and set approved=yes only
for corrections you approve. Weaker candidates require manual verification before
a reviewer can attest to high confidence in an approved CSV.

```sh
# Dry run: validate every approved row and show the exact edits.
python3 correct_publication_identifiers.py output/published_version_candidates.corrections.csv --admin-handle "$USER"
# Apply the reviewed batch in one transaction.
python3 correct_publication_identifiers.py output/published_version_candidates.corrections.csv --admin-handle "$USER" --apply
```

DATABASE_URL is required. The script uses flora_service.update_record, hence the
same flora_edits.save audit log, concurrency checks, exclusion checks, metadata
rebuild and live publication as the editor. It changes doi_o/r and appends the old
identifier to alt_identifier_o/r, preserving existing aliases. URL-only I4R
reports can use their handle as old_identifier. If the journal DOI is already
primary, a missing preprint alias can still be added. An already-correct record
is a no-op; stale identifiers or concurrent edits abort the whole batch.

Missing target reference metadata is fetched before applying corrections; failure
to obtain a title aborts the transaction. The script verifies the stored live
fields before committing. The log retains every field's before/after values,
actor, timestamp and note. Record IDs, source rows and dedup decisions stay intact.
Normal subsequent pipeline builds reapply these corrections and enrich their DOIs
through flora_edits.identifier_values.

For an administrator without a local database connection, Publish FLoRA has an
explicit workflow_dispatch checkbox, apply_approved_identifier_corrections. It
applies only the reviewed cache/approved_publication_corrections.csv, using the
existing database secret, and records github-actions plus the triggering actor.
The checkbox defaults to false and is never enabled by scheduled publication.
The current approved file contains the seven verified recent cases and the two
user-specified fixtures. Subsequent runs safely skip already-correct records.

## Schedule, cache and failure handling

Sync entry sheets scans the current prepared snapshot and uploads both candidate
CSVs and the status JSON as a separate 90-day artifact. Set OPENALEX_MAILTO to
supply the contact address in the generic client user-agent. Requests run serially
at least .5 seconds apart, with bounded retries and Retry-After handling. Responses
including 404/410 misses expire after seven days; transient failures are not
cached as absence. Actions cache preserves successful results between runs.
Discovery failures remain visible in logs and the status artifact and do not block
a validated release. Discovery never automatically writes a correction.

See the [10 November census and seven-case list](audits/published-versions-2026-11-10.md)
and its evidence CSVs. Seda et al. remains a negative control with no journal
candidate. No Google Sheet is involved.
