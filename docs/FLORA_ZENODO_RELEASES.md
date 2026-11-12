# Versioned FLoRA deposits on Zenodo

FLoRA and FReD have separate concept records. This pipeline covers FLoRA only;
it does not upload FReD. The first production release requires Lukas's approval.
No production deposit has been created or published as part of implementation.

## Release policy

Use [Calendar Versioning (CalVer)](https://calver.org/): year, zero-padded month
and a within-month release counter (`YYYY.0M.MICRO` in CalVer terminology).
This labels the data snapshot; changes to data and documentation are described in
the changelog rather than classified as semantic minor/patch releases.

Use `YYYY.MM.N`, starting at `2026.11.1`. The date identifies a living dataset's
release month without implying API/schema compatibility; N increases for another
release in the same month. Versions must increase relative to the previous bundle.
Use **manual dispatch**, after reviewing the changelog, authors and licence. Daily
repository publication continues unchanged; scheduling immutable DOI releases would
create unnecessary versions and bypass this review.

The `Prepare FLoRA Zenodo release` Actions workflow assembles and retains an offline
artifact. Its optional previous record input is the numeric **version record ID**
from the preceding published FLoRA release, never the concept ID. It downloads that
release as the comparison baseline. Keep an independent archive of published bundles
and receipts because Actions artifacts expire after 90 days.

## Inputs and history

`publish_flora.py` already exports the authoritative stored dataset and corrections.
Use the committed `output/` produced by that pipeline. Assembly validates the CSV
against its existing `flora_release_manifest.json` and reuses
`release_helpers.prepare_release` for byte manifests and notes. Release CSV fields are selected and ordered by `release/flora/columns.json` (31
columns, including only `author_overlap_pct` for overlap). IDs and applicable
outcome/evidence values are preserved. Release source labels map replications and
reproductions to `manual entry`, COS to `FReD`, and validated/manual to `search pipeline`;
Established projects take precedence over these fallbacks, including search-pipeline
records. SCORE is seeded by its known intake. The search-pipeline label means the website discovery/curation
workflow, including manual entries, not necessarily automated search. Replication
outcome and evidence fields are NA on reproduction rows; reproduction axes and their
evidence are NA on replication rows. These explicit representation rules are stored
in the release manifest, without editing the internal data or correction log; the release serializer writes quoted UTF-8 CSV with a BOM. The source
and release hashes therefore differ and are both preserved in the manifest.
Before projection, assembly checks the percentage against both source author lists;
stale derived values stop assembly. Regenerate via the fixed normal pipeline rather
than patching exports. A preview of the deterministic overlap fix can be assembled
locally, but production accepts only `source_kind=authoritative_publication` with
a stored dataset revision/correction watermark. The normal stored-publication paths
write this marker through `release_helpers.add_data_version`; unmarked manifests,
unknown source kinds and offline previews are rejected.

The existing `flora_history.csv`, `flora_dataset_history`, and
`backfill_flora_history.py` hold **daily aggregate counts**, not historical record
values. `flora_store` retains permanent IDs, current/retired records and version
counters, but not every past value. `flora_record_changes` is the append-only
correction log; source refreshes can also change records without a correction entry.
It is therefore insufficient by itself to describe a whole release.

The release changelog uses the **published record history**: the previous Zenodo
CSV compared with the current authoritative snapshot, matched on permanent `id`.
It enumerates additions, retirements and changed field names in `changes.json`,
with unique-record counts in `CHANGELOG.md`. Row ordering alone is ignored; schema
changes are explicit. Returning retired IDs count as additions to this release.
Intermediate edits which cancel out, including a retire/reactivate between releases,
are not counted. The first release establishes a baseline, with all current records
added and no claim to reconstruct pre-release changes. The embedded source manifest
preserves the exact correction watermark and database revision of the publication.
No admin identities, private notes or personal emails are exported from the log.
Corrections continue through that log, never through editing outputs.

The codebook extracts the published column dictionary from the maintained
`docs/FLORA_DATA_TABLE.md` and appends the exact categorical labels in the CSV.
Assembly rejects columns without definitions. Coding FAQs and citation authors/licence are pinned
from fred-data; provenance is in `release/flora/SOURCES.md`. Review these for each
release. The metadata proposes the existing CC BY 4.0 licence and author order.

## Commands from the project root

Python 3.12 with `requests`, `pyyaml` and `pandas` is sufficient for release assembly/upload:

```bash
python3 -m pip install requests pyyaml pandas
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/2026.11.1
```

The default is an offline dry run: no API calls and no token read. It refuses to
overwrite an existing bundle. Review every file and `zenodo-metadata.json`.

An upload without `--publish` leaves a draft. Use a separate assembled bundle for
sandbox, with `ZENODO_SANDBOX_TOKEN` exported in your local environment:

```bash
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/sandbox-2026.11.1
python3 zenodo_flora.py --bundle release-bundles/sandbox-2026.11.1 --upload sandbox
# Optional sandbox publication to exercise versioning end to end:
python3 zenodo_flora.py --bundle release-bundles/sandbox-2026.11.1 --upload sandbox --publish
```

No approved production candidate exists yet. The existing `release-bundles/2026.11.1`
is an offline preview. After deploying the fixes and rebuilding the authoritative
publication, assemble a fresh candidate into the dedicated, currently unused path:

```bash
python3 zenodo_flora.py --version 2026.11.1 --bundle release-bundles/production/2026.11.1
```

Review that candidate and its checksum manifest. Lukas's approval must identify
this exact directory and its checksum; do not copy or rename the old preview here.
**Only after that approval**, the first production publication command is:

```bash
python3 zenodo_flora.py --bundle release-bundles/production/2026.11.1 --upload production --publish --approve-production
```

Set `ZENODO_TOKEN` in the invoking process's environment. This name was already
present in the implementation environment, but was not documented in this repo.
No sandbox token was available. Tokens are read only from environment variables;
this script does not load `.env`. Use deposit write/action scopes. Never put tokens
in arguments, source files or artifacts. The assembly workflow needs no Zenodo secret;
if a future upload job is added, store its token in a protected GitHub environment
secret of the same name. No token values or server error bodies are logged.

For the next release, recover the preceding published bundle using its version
record ID (the command accepts either host through `--environment`), or use your
archived bundle including `zenodo-state.json`:

```bash
# After the first publication, keep its release-bundles/2026.11.1 directory.
python3 zenodo_flora.py --version 2026.11.2 --bundle release-bundles/2026.11.2 --previous release-bundles/2026.11.1
```

`--fetch-previous RECORD_ID --bundle DIRECTORY` downloads a public baseline without
a token; replace RECORD_ID with the **actual** preceding version record ID. This
validates the deposited checksums and creates a local published receipt. Assembly
requires that receipt for subsequent releases. Upload calls `newversion` on that
preceding version ID, follows `links.latest_draft`, and checks the concept ID.
Zenodo requires the latest version ID rather than the concept ID; see its
[REST API documentation](https://developers.zenodo.org/#new-version).

## Citation consistency and recovery

The repository template has no version DOI or old OSF DOI. Offline assembly fills
in the version but omits DOI. Upload asks Zenodo to reserve a DOI and writes **only
that returned version DOI** into the bundle's CITATION.cff before uploading files
and refreshed checksums. The API's `prereserve_doi` reservation is not DOI
registration; that happens on publication. Later releases receive their own citation
file; previous deposits are never rewritten. Cite the downloaded version's file,
not a citation template from a moving branch. A concept DOI can be used to refer to
the collection in prose once one exists, but must not replace the version DOI in
a version-specific citation file.

`bundle-checksums.json` protects the reviewed bundle against accidental changes.
Upload records `zenodo-state.json` immediately after creating/finding a draft and
resumes that ID on retry. The state receipt is local, not deposited. Inherited files
are removed from new drafts; every uploaded file's returned MD5 is checked before
publication. HTTP failures stop the run. Re-run the upload command with the same
bundle to resume. If a draft creation request times out **before a receipt is
written**, inspect the account on Zenodo and recover its draft ID into a receipt
before retrying; do not blindly create another concept record. Keep the receipt with
the bundle. Production always requires the explicit approval flag, including drafts.

The upload's only planned local mutation is the reserved DOI in CITATION.cff and
its checksum. Metadata and data remain the reviewed inputs. Production approval
must cover this DOI reservation and the first upload. Never run the production
command until approved: published records/DOIs cannot simply be deleted.

## Agreed public columns

`release/flora/columns.json` is the ordered release contract: the 30 fields selected
by Lukas plus `author_overlap_pct`. The original-study and report bibliographies
are grouped separately. Each outcome is followed by its quote and source, and
provenance `source` ends the CSV. The full pipeline export remains available in the
repository; it is not a second dataset file in the deposit.

[The interactive selector](../release/flora/column-selector.html) now defaults to
this agreed selection. Browser changes remain proposals; they do not modify the
contract or the pipeline. The deposit codebook contains only the agreed columns,
in their release order.

## Project-first classification

`flora_release_projects.py` uses `release/flora/projects.yml` to match project
anchors in `doi_r`, `alt_identifier_r` and report URLs. DOI prefixes, case,
comma/semicolon/pipe-separated alternatives and OSF project URL/DOI forms are
normalized. Original-study IDs and title keywords never establish membership.
Project-specific intake rows (currently SCORE) can also seed report identifiers.
Reviewed permanent record IDs can establish membership where report identifiers
are missing. Each such registry entry must link to its membership evidence;
the [65-record SCORE review](../release/flora/SCORE_MEMBERSHIP_REVIEW.md) documents
the current explicit assignments independently of the report-byline corrections.
Matches seed one-hop aliases; there is no recursive transitive merge of projects.

Projects take priority over every intake route, including search pipeline. The
strict >50 threshold counts distinct active released record IDs after deduplication
and exclusions, once each regardless of the number of matching identifiers. A
registry may link multiple reports to the same project. Established qualification
records the count and source-snapshot hash so labels persist after retirements.

The current registered projects are RP:P, the student replication collection in
Boyce et al., and SCORE. Shared report identifier groups over 50 that are not yet
mapped are reported for naming/review; production refuses unresolved large groups.
Conflicting matches stop assembly rather than arbitrarily choosing a project.
Text mentions without identifier evidence are included as review hints only.

`project_classification.json` in the bundle documents project counts, member IDs,
seed IDs, normalized identifiers, unmatched large groups and unmatched project-name
mentions. The manifest preserves the registry and review status. Changing the
registry requires reassembly before upload. No additional CSV column is introduced;
source means an identifiable project, otherwise intake route. It does not retain
simultaneous route and project membership in one value.

The [SCORE paper-identifier batch](../data/corrections/SCORE_PAPER_IDENTIFIERS.md)
adds the replication-paper DOI to replication records and the reproduction-paper
DOI to reproduction records, after checking the published outcome datasets.
It appends to `alt_identifier_r` through the correction log, preserving individual
report identifiers and existing alternatives. The associated aggregate paper is
an additional publication link, not a replacement report citation.

The [individual-report DOI batch](../data/corrections/SCORE_REPORT_DOIS.md)
fills verified missing `doi_r` values while retaining those paper alternatives.
Reports without a registered DOI keep a blank primary field and their report URL;
the aggregate paper is not substituted as their primary DOI. A rebuild is checked
to preserve the report's byline, citation text and outcomes.
