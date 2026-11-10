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
DataCite is needed for Replication Research articles. A matching bibliography
requires title similarity of at least .90 and matching
first-author surnames against the target's registered bibliography. An explicit
relation with that match is eligible for automatic resolution. A matching
OpenAlex DOI merge without an explicit relation still requires review. Prefer
the source's Crossref bibliography;
use the input title and authors when it has none. No title-search fallback is
used, and OpenAlex merges to another preprint, repository or dissertation cannot
create a journal candidate.

An OpenAlex-only merge, renamed title, missing/inconsistent first author, or
conflicting journal targets requires review and has no automatic action. Renamed papers may need manual verification of the
source and publisher bibliographies. The two shortened cited-author lists in the
seven-case audit remain review-only in the routine; their separately approved
corrections document the manual verification.

## Automatic resolution

```sh
# Preview against the current database; no changes.
python3 resolve_publication_versions.py
# Apply only the verified explicit-link subset.
python3 resolve_publication_versions.py --apply
# Materialize queued merges through normal preparation and validation.
python3 prepare_flora.py --output-dir output --network-minutes 5
```

Automatic resolution requires one journal target, an explicit OSF/Crossref link,
title similarity >= .90, and consistent nonblank first-author surnames (ignoring
case and diacritic differences). It rechecks
the current record against that bibliography before applying anything. Any API
lookup failure prevents all automatic writes for that run. Human preprint rulings
and confirmed-file decisions take priority; reviewer-confirmed distinct rows are
never merged automatically.

A standalone version uses the existing FLoRA correction log to update doi_o/r
and append its previous identifier to alt_identifier_o/r. Both versions under
the same other DOI and study type use preprint_dedup_decisions and the existing
pair merger at final preparation. The resolver never bypasses that validation
gate to publish a newly rebuilt dataset. The publisher record leads, absorbed source IDs and aliases are
retained, and outcome/quote conflicts follow existing merge rules. Replication
and reproduction records, and reports targeting different originals, are not
collapsed. A previously corrected DOI's preserved preprint alias can still
identify a pair that needs merging.

`auto_eligible=yes` appears in the discovery and per-record review CSVs. The
standalone discovery command itself remains read-only; the resolver recomputes
candidates from the live database and applies the eligible subset. OpenAlex-only
matches and renamed/conflicting records stay in the review artifact. The existing
FLoRA editor can undo corrections; the Preprint duplicates tab can revise or
withdraw an automatic duplicate ruling. To keep a standalone preprint after
reverting its correction, add an explicit keep_both ruling for the DOI pair to
cache/confirmed_preprint_duplicates.csv; that prevents automatic reapplication.

## Apply changes through the FLoRA correction log

The companion `output/published_version_candidates.corrections.csv` has one row
per FLoRA record and DOI side. It contains record_id, flora_id, side (o/r),
old_identifier, new_doi, confidence, evidence, evidence_url, note, and approved.
The exported review CSV leaves approved blank; automatic eligibility is separate
from manual approval. Review the evidence and set approved=yes only
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
The checkbox defaults to false. A second manual option, resolve_published_versions, runs automatic resolution against the live database.
The current approved file contains the seven verified recent cases and the two
user-specified fixtures. Subsequent runs safely skip already-correct records.

## Schedule, cache and failure handling

Sync entry sheets runs automatic resolution after enrichment and ID assignment,
before final preparation and validation. It uploads both candidate CSVs and the
status JSON as a separate 90-day artifact. Set OPENALEX_MAILTO to
supply the contact address in the generic client user-agent. Requests run serially
at least .5 seconds apart, with bounded retries and Retry-After handling. Responses
including 404/410 misses expire after seven days; transient failures are not
cached as absence. Actions cache preserves successful results between runs.
Discovery failures remain visible in logs and the status artifact and do not block
a validated release. Failed automatic batches roll back; review-only candidates
never enter the automatic write path.

See the [10 November census and seven-case list](audits/published-versions-2026-11-10.md)
and its evidence CSVs. Seda et al. remains a negative control with no journal
candidate. No Google Sheet is involved.
