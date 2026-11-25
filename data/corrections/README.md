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

# Identifier and link corrections from fred-data #179

`fred_data_179_fields.csv` holds record-level corrections that the build cannot
derive from a source: a missing report DOI, a wrong original link, and OpenAlex
records that stood in for a report link. Each row names the field, the value it
replaces, the new value, a note for the correction log, and the API record that
verifies it. Published versions of preprints for the same issue are in
`cache/approved_publication_corrections.csv`; apply those first, so a corrected
`doi_r` and its preserved preprint alias are in place.

```bash
python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv               # offline check
python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv --check-live  # plan, then roll back
python3 scripts/apply_field_corrections.py data/corrections/fred_data_179_fields.csv --apply --admin-handle HANDLE
```

`set` refuses a record whose field no longer holds `before`; `append` adds an
alternative identifier once. Each record is one editor save in the correction log,
and the batch commits whole or not at all. Reapplying it changes nothing.
