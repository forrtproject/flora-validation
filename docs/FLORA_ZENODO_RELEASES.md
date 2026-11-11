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
`release_helpers.prepare_release` for byte manifests and notes. CSV cells and IDs
are unchanged; a UTF-8 BOM is added only when missing.

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

Python 3.12 with `requests` and `pyyaml` is sufficient for release assembly/upload:

```bash
python3 -m pip install requests pyyaml
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

**After Lukas approves**, the exact first production publication command is:

```bash
python3 zenodo_flora.py --bundle release-bundles/2026.11.1 --upload production --publish --approve-production
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
