# SCORE report authors

This reviewed batch corrects bibliographic metadata through the existing correction log. It has not been applied to the live database or generated outputs. `score_report_audit.csv` lists each row and unresolved reason; `score_report_evidence.json` contains report bylines and authoritative OSF links. Full methods and ten before/after checks are in [fred-data PR #167](https://github.com/forrtproject/fred-data/pull/167) (`cos_score_integration/report_references/README.md`). No outcomes, effect sizes or identifiers are changed.

From the repository root:

```bash
.venv/bin/python scripts/apply_score_report_authors.py
```

This validates the batch without opening a database. To apply it, the maintainer runs the same command with `--apply --admin-id` followed by their existing administrator ID, using the configured `DATABASE_URL`. All current references are checked before any save; a changed reference aborts the transaction. The script calls `flora_service.update_record`, which uses `flora_edits.save` and publishes through the existing service. An already-applied batch makes no further changes. A rebuild or ordinary export then includes the logged corrections.

References use the report byline; where no date is printed in the header, the OSF file deposit year is labelled as such in the evidence. Bracketed titles are supplied descriptions of the original paper, replacing internal codes. No DOI is invented. Project contributor lists are not substituted for unsigned report bylines when authorship remains ambiguous. Incremental API cost: US$0.
