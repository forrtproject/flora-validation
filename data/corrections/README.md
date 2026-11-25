# SCORE report authors

This batch corrects bibliographic metadata through the existing FLoRA correction
log. It prepares 290 named report bylines and explicitly marks 12 unestablished
report bylines unknown. Project contributors are never substituted for report
writers. `score_report_audit.csv` retains the reasons (four deleted files, eight
unsigned/unestablished reports); its disposition column separates named bylines
from explicit unknown handling. One unrelated candidate is excluded.

The corrections also replace titles that are SCORE's internal component codes
("Wurst_JournPerSocPsy_2017_qPxQ - Edlund - New Data Replication - 69136"). Those
codes are the titles OSF and DataCite hold for the report DOIs, so every metadata
refresh brings them back; only a correction keeps the descriptive title.

On 25 November (fred-data #179) the reference baselines of 153 rows were refreshed
to the live values, after their references had reverted to DataCite's code titles;
the reviewed report URL, original DOI and type of all 289 rows were unchanged. The
13 individual attempts FRED-000984 to FRED-000997 were added from the bylines
verified on 10 November in fred-data's
`cos_score_integration/report_references/audit.csv`.

For unknown authors, author_r is null, citations/BibTeX do not credit contributors,
and author overlap recalculates to missing. Titles in brackets are supplied
original-study descriptions. Eight dates were verified from OSF report file deposit
metadata; the four deleted files have no established date and use n.d. No author or
year is invented. Report DOI and SCORE paper alternatives remain unchanged.

```bash
python3 scripts/apply_score_report_authors.py
# DATABASE_URL from the configured environment or ignored .env:
python3 scripts/apply_score_report_authors.py --check-live
# Only when applying the reviewed batch:
python3 scripts/apply_score_report_authors.py --apply --admin-id "$FLORA_ADMIN_ID"
```

Before application, export `FLORA_ADMIN_ID` in the calling shell with the verified
administrator ID of the operator. Apply outside the sync window: the 302
corrections rebuild the dataset while holding a single transaction.

The default opens no database. --check-live runs with a read-only connection.
Application checks every identity and reference baseline before any save, uses
versioned flora_service.update_record / flora_edits.save, rechecks identity after
the updates, and commits atomically.
Reapplication is a no-op, including null author/date fields. Identifier, report
URL and outcome fields are excluded from the batch. Any baseline drift aborts.

REPRO-000101's baseline was refreshed on 12 November only for the verified
`OSF .` → `OSF.` whitespace change. Its report URL and DOI identity were unchanged;
the strict reference guard is retained. Live preflight on 25 November: all 302
ready, none already applied.

The source evidence includes OSF report links, report bylines, file hashes and
explicit date provenance. Full original methods and spot checks are in
[fred-data PR #167](https://github.com/forrtproject/fred-data/pull/167).
Companion work: #29 handles author/export correctness; #30 retains the already-
applied identifier corrections; #25 contains only the dependent release pipeline.

# Identifier, link and duplicate corrections from fred-data #179

Three batches, applied in this order after
`cache/approved_publication_corrections.csv` (journal versions of preprints, OSF
projects, theses and working papers; `correct_publication_identifiers.py`):

1. `fred_data_179_fields.csv` (`scripts/apply_field_corrections.py`): record-level
   field corrections. A missing thesis DOI, a wrong original link, OpenAlex records
   that stood in for a report link, and the identifiers of absorbed duplicates
   carried to the record that keeps the report. `set` refuses a record whose field
   no longer holds `before`; `append` adds an alternative identifier once.
2. `fred_data_179_duplicates.csv` (`scripts/apply_duplicate_rulings.py`): records
   that are another version of a report FLoRA already holds (a preprint, OSF
   project, Zenodo copy or thesis version beside the article). Each absorbed
   record's source row is ruled a duplicate of the survivor's, as Source Records
   does, and the next build retires its published id.
3. `score_report_authors.json` (`scripts/apply_score_report_authors.py`, above).

Works with no DOI keep their OpenAlex record as the key to their metadata: when
`url_r` is corrected from the OpenAlex link to the work's own page, the build
looks the work up by the record the correction replaced, or by an OpenAlex link
in `alt_identifier_r` (`transform_sources.join_metadata`).

`fred_data_179_published_version_review.csv` lists every candidate the search
found that is not in the approved file, with the decision taken on each: ruled a
duplicate, kept distinct, rejected, or left unchanged for want of evidence.

```bash
python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv --check-live
python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv --apply --admin-handle HANDLE
python3 scripts/apply_duplicate_rulings.py data/corrections/fred_data_179_duplicates.csv --check-live
python3 scripts/apply_duplicate_rulings.py data/corrections/fred_data_179_duplicates.csv --apply --admin-handle HANDLE
```

Each batch commits whole or not at all, and reapplying it changes nothing.
