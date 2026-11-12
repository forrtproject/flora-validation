# First Zenodo release: readiness after PR #25 review

Checked 12 November 2026. No production Zenodo deposit or DOI has been created.
The existing `release-bundles/2026.11.1` is an outdated offline preview, not an
approved candidate. Production credentials are present in the current process;
a sandbox token is not available. No credential was used for this review.

## Review findings

All ten inline findings were checked against the current implementation:

| Finding | Resolution |
| --- | --- |
| FAQ uses nonexistent alternative-identifier columns | Corrected names and the individual-report example; local errata documented. |
| Production command could target the old preview | Reserved the unused `release-bundles/production/2026.11.1` path for a fresh authoritative candidate. Approval must identify the candidate and checksum. No candidate is approved yet. |
| Locale-dependent text decoding | Explicit UTF-8 reads throughout the Zenodo client/assembler. |
| Scorer/export disagree on `and` and `&` bylines | Shared normalization for scoring and export, with round-trip regression tests. |
| Empty author entries disappear during export | Preserve unresolved entries in exported author JSON; validation retains unknown overlap. |
| Selector dark-mode contrast | Restrict its standalone browser canvas to its light palette; theme preview checked. |
| Outcome applicability described backwards | README now matches the serializer. |
| Unmarked local sources can pass production checks | Only canonical stored-publication manifests with `authoritative_publication` and a dataset revision/watermark pass. Unmarked, unknown and preview sources fail before network access. |
| Resumed draft may belong to another concept | Check remote deposit ID, saved/previous concept, version, title and published state before writes. |
| Nested OSF URL loses the file GUID | Match the file GUID and distinguish sibling files; direct node links retain their node GUID. |

The docstring warning was also reviewed; the release entry points and client
operations now document their purpose. CodeRabbit was paused and had not reviewed
the latest head when this audit began. Local regression tests establish the fixes;
no new automated-review approval is claimed.

Validation: the full repository suite passed 3,091 tests (421 skipped). A final
focused rerun after the last normalization and provenance tests passed 121 tests.
No Zenodo API deposit/upload action was called.

The branch was reconciled with current `main`; the conflict was in `.gitignore`.
Both the bundle archive and the new published-version cache ignore rules are kept.

A dry run against the current committed publication stops on 71 stale
`author_overlap_pct` values. No bundle was created. This confirms that the
authoritative rebuild is still required; correcting CSV cells is not a substitute.

## Work remaining before production

1. Merge/deploy PR #25. Its author normalization and provenance marker must reach
   the normal pipeline before a production candidate can be assembled.
2. Finish the separate SCORE report-byline correction work in PR #26 and apply
   the reviewed corrections through the log. Project and DOI linkage are already
   applied; report author attribution is separate and matters for author overlap.
   Unsigned/collective bylines must retain missing overlap, not a guessed zero.
3. Run the normal authoritative pipeline and Publish FLoRA. Check that the fresh
   manifest has the publication marker, current correction watermark and matching
   CSV hash. Assemble a new `2026.11.1` candidate at
   `release-bundles/production/2026.11.1`. Review all 13 bundle files, the 31-column
   codebook, applicability rules, source classification and initial changelog.
   Counts must come from this fresh snapshot, not the earlier preview.
4. Review the proposed title, 220-creator list, CC BY 4.0 licence and description
   in `first-release-review.md` and the actual candidate metadata. Confirm that
   this creates the intended FLoRA concept, separate from FReD. There is no known
   production receipt to reuse. If an existing FLoRA concept is supplied, use its
   actual latest published version as the baseline instead of creating a duplicate.
5. Obtain Lukas's first-production approval for that exact bundle path and
   checksum, including DOI reservation/upload. Only then run:

   ```bash
   python3 zenodo_flora.py --bundle release-bundles/production/2026.11.1 --upload production --publish --approve-production
   ```

A sandbox end-to-end trial is recommended before step 5, including a second
version and resume checks. This requires `ZENODO_SANDBOX_TOKEN` and a separate
sandbox bundle; the production token cannot substitute for it. Mock API tests
have passed, but no live sandbox versioning trial has been performed.

Zenodo reserves the version DOI during upload. The uploader inserts only that
returned DOI into CITATION.cff and refreshes its checksum before depositing it.
No DOI is invented or copied from the concept/old OSF citation. After publication,
archive the bundle and `zenodo-state.json` as the baseline for subsequent versions.
