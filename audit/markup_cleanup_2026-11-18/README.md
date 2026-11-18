# Markup cleanup, 18 November 2026

flora-extractor sends some titles and abstracts with HTML or JATS markup, such as
`<i>`, `<sup>1</sup>`, `<h3>`, `<ns4:p>` and `<!DOCTYPE …>`. The validation page
showed those tags as literal text. The problem was reported on 11 November 2026.

## What was changed

**The code.** Since these commits, the nightly import cleans `title_r`, `title_o` and
`abstract_r` with `transform_sources.clean_text` for every pair the extractor still
ships:

- 85341b3 and 7a4d9e7: clean titles and abstracts at import.
- 08b5dc5: drop a footnote marker at the end of a title, so "Decision Makers1"
  reads "Decision Makers".

Titles come out on one line, and abstracts keep their paragraph breaks.

**The data.** The import never revisits three kinds of value, so [cleanup.py](cleanup.py)
cleaned them once:

- `unvalidated` rows of pairs the extractor no longer ships
- `unvalidated.final_title_r`, `final_title_o` and `final_abstract_r`, which are written once
  at consensus
- `validated.title_r`, `title_o` and `abstract_r`, which are copied from those final values

Hamid applied it on 18 November 2026 at about 10:20 UTC. It changed 37 values in
21 records and skipped none. Before and after, the values holding a tag were:

| Table | title_r | title_o | abstract_r | final_* |
|---|---:|---:|---:|---:|
| unvalidated, before the code fix | 272 | 62 | 90 | 9 |
| unvalidated, after the first sync | 12 | 5 | 3 | 9 |
| unvalidated, after cleanup.py | 0 | 0 | 1 | 0 |
| validated, before | 5 | 4 | 0 | – |
| validated, after | 0 | 0 | 0 | – |

The one abstract left ends with `<halshs-01945777>`. That is a HAL archive
identifier, not a tag, so `clean_text` keeps it.

Three titles still read imperfectly, because their source text is written that way:

- "B eckman" and "P hillips" (record `e3d43874…`): the source has a space after
  each small-caps tag.
- "Revisited:A Brief Communication" (`f6b4a2f0…`): the source has no space after
  the colon.
- "Research Note Pork-Barrelling" (`8e3384b5…`): the source has nothing between
  the two parts.

## Where it is recorded

- **[apply_log.jsonl](apply_log.jsonl)** has one line per changed value: the record,
  table, column, old value, new value, time and admin. Undo reads this file.
- **The admin audit log** (`security_events`, shown on the admin page) has one
  `validation.markup_cleaned` entry per record in the admin's name. It is written in
  the same transaction as the record's change. An undo adds a
  `validation.markup_restored` entry.

The FLoRA tab has no entry for this. Its records are rebuilt from these tables and
cleaned on the way, so FLoRA titles read the same before and after. Only the
footnote rule in 08b5dc5 changed what the FLoRA tab shows, and it did so through the
build, not as a correction.

## Undo it or do it again

Before you start:

- Run from the repository root, with `DATABASE_URL` for the production database in
  `.env` or the environment.
- Use the repository's `.venv` Python, which has `psycopg2`. On Windows, use
  `.\.venv\Scripts\python.exe` in place of `python`.
- Pass your own admin handle to `--admin`. The script checks that it exists.
- Do not run it between 02:00 and 05:00 UTC, while the nightly import and FLoRA
  release run. The script refuses to write in that window.

```bash
# See what would change. This only reads, and it writes plan.json beside the script.
python audit/markup_cleanup_2026-11-18/cleanup.py check

# Undo one record
python audit/markup_cleanup_2026-11-18/cleanup.py rollback --admin <handle> --record <record_id>

# Undo everything in the log
python audit/markup_cleanup_2026-11-18/cleanup.py rollback --admin <handle>

# Do it again: check first, then apply
python audit/markup_cleanup_2026-11-18/cleanup.py check
python audit/markup_cleanup_2026-11-18/cleanup.py apply --admin <handle>
```

How the script behaves:

- **One transaction per record.** A record's `unvalidated` row, its `validated`
  row and its audit entry change together, or not at all.
- **Later edits are kept.** Apply skips a record if one of its values changed since
  it was read. Undo leaves a value alone if it no longer holds what apply wrote, and
  skips one that an earlier undo already restored.
- **Duplicates are skipped.** Apply skips a record whose cleaned `validated` row
  would duplicate another validated row: same DOIs, study numbers and titles. An
  admin should merge those.
- **Apply adds to the log.** It appends to `apply_log.jsonl`, and undo works newest
  first, so a value applied twice goes back to what it was before both. Commit the
  updated log after an apply or an undo, so the next person has it.
- **An undo stays in place.** The undone rows belong to pairs the extractor no
  longer ships, so the nightly import does not clean them again. If the extractor
  ships one of those pairs again, the import cleans its raw columns.

To check the result, run this read-only count in the SQL editor:

```sql
WITH t AS (SELECT '</?[A-Za-z!?][^<>]*>'::text AS re)
SELECT 'unvalidated' AS tbl,
       count(*) FILTER (WHERE title_r ~ re)    AS title_r,
       count(*) FILTER (WHERE title_o ~ re)    AS title_o,
       count(*) FILTER (WHERE abstract_r ~ re) AS abstract,
       count(*) FILTER (WHERE final_title_r ~ re OR final_title_o ~ re OR final_abstract_r ~ re) AS final_any
FROM unvalidated, t
UNION ALL
SELECT 'validated', count(*) FILTER (WHERE title_r ~ re), count(*) FILTER (WHERE title_o ~ re),
       count(*) FILTER (WHERE abstract_r ~ re), NULL
FROM validated, t;
```

After the cleanup it gives 0, 0, 1, 0 for `unvalidated` (the one is the HAL
identifier above) and 0, 0, 0 for `validated`.
