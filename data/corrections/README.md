# SCORE report authors

This unapplied batch corrects bibliographic metadata through the existing FLoRA
correction log. It prepares 277 named report bylines and explicitly marks 12
unestablished report bylines unknown. Project contributors are never substituted
for report writers. `score_report_audit.csv` retains the reasons (four deleted
files, eight unsigned/unestablished reports); its disposition column separates
named bylines from explicit unknown handling. One unrelated candidate is excluded.

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
python3 scripts/apply_score_report_authors.py --apply --admin-id 3
```

The apply command uses Lukas's verified administrator record (ID 3). Another
operator must use their own administrator ID. Apply outside the sync window: the
289 corrections rebuild the dataset while holding a single transaction.

The default opens no database. --check-live runs with a read-only connection.
Application checks every identity and reference baseline before any save, uses
versioned flora_service.update_record / flora_edits.save, rechecks identity after
the updates, and commits atomically.
Reapplication is a no-op, including null author/date fields. Identifier, report
URL and outcome fields are excluded from the batch. Any baseline drift aborts.

REPRO-000101's baseline was refreshed on 12 November only for the verified
`OSF .` → `OSF.` whitespace change. Its report URL and DOI identity were unchanged;
the strict reference guard is retained. Live preflight: all 289 ready, none already
applied. Local simulation of the entire batch preserved all identifiers,
alternatives, URLs and outcomes, and made all 12 unknown overlaps missing.
No database write or tracked-output edit was performed for this revision.

The source evidence includes OSF report links, report bylines, file hashes and
explicit date provenance. Full original methods and spot checks are in
[fred-data PR #167](https://github.com/forrtproject/fred-data/pull/167).
Companion work: #29 handles author/export correctness; #30 retains the already-
applied identifier corrections; #25 contains only the dependent release pipeline.
