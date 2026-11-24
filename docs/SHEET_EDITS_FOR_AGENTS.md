# Sheet edits: instructions for coding agents

The file is **Source Records → Sheet edits → Download CSV** (`sheet_edits_YYYY-MM-DD.csv`):
cells of imported entry-sheet rows that the sheet changed after the import, each with
what FLoRA shows now. There is one row per finding and FLoRA record it touches, so a
finding on a row merged into two records has two rows. Background:
[SOURCE_RECORDS.md §6 and §8](SOURCE_RECORDS.md#the-sheet-edit-check).

## Rules

1. **Never edit a sheet, `source_records` or the database.** Every fix is either a FLoRA
   correction or a "Keep FLoRA value" decision, and both are applied by a person with
   `apply_sheet_edits.py`, or by hand in the panel.
2. **Every decision has a note citing evidence**: the page or section of the paper, the
   DOI's landing page, the sheet's own validator note. 3 to 500 characters, the limit
   for FLoRA correction notes (the panel's Keep note allows up to 1,000). A note is
   logged with the change and is what a reviewer reads later.
3. **Change only the three decision columns.** The apply script compares
   `value_in_sheet`, `flora_value` and `flora_version` with the live data and refuses a
   row that moved.
4. **When unsure, `skip`.** A wrong correction is published; a skipped one waits.
5. **Read the file as text, not in Excel.** Data columns hold the values exactly as
   stored, so a cell starting with `=` would run as a formula in a spreadsheet program,
   and a spreadsheet that reformats values on save makes the apply script refuse rows.

## What to do, by state

| `state` | `suggested_action` | Usually |
|---|---|---|
| `pending` (sheet changed, FLoRA not yet) | `use_sheet_value` | Check the sheet's value against the paper, then `use_sheet` |
| `conflict` (changed in both places) | `decide` | Read `flora_value_source` and `state_explained`: a person corrected FLoRA (`correction`), it comes from another row (`other_row`), an old source edit (`source_edit`) or the build (`build`). Choose `use_sheet`, `keep_flora` or `set_value` |
| `not_validated` (no longer validated in the sheet) | `decide_status` | The script cannot remove a record. `keep_flora` with why it stays, or `skip` and flag it for an exclusion or a duplicate ruling |
| `row_removed` (row deleted from the sheet) | `decide_missing` / `review_duplicates` | As above. `review_duplicates`: the paper was re-entered under a new sheet ID; `skip` and flag it |
| `matches`, `metadata`, `kept`, `no_field`, `not_in_flora`, `resolved` | `none` / `check_survivor` | Nothing: `skip`. `check_survivor`: the row was ruled a duplicate; the kept row may need the same fix (flag it) |
| `unknown` | — | FLoRA could not be built: `skip` and download again later |

A blank `sheet_value_for_flora` beside a non-blank `value_in_sheet` means FLoRA would
refuse or drop the sheet's value (an unknown outcome spelling, a URL that repeats the
DOI); `state_explained` says which. Use `set_value` with a valid value, or `skip`.

## The decision columns

- **`decision`**: `use_sheet` (FLoRA takes `sheet_value_for_flora`), `keep_flora`
  (FLoRA's value is right), `set_value` (FLoRA takes `new_flora_value`) or `skip`.
  Blank is `skip`.
- **`new_flora_value`**: only with `set_value`, in the form FLoRA stores: a bare DOI
  (`10.1234/abc`), a canonical outcome (`successful`, `failed`, `mixed`,
  `uninformative`, …), a four-digit year, a URL starting `http`. Blank makes the field
  deliberately blank.
- **`decision_note`**: required for every decision but `skip`.

`use_sheet` and `set_value` are per FLoRA record (`flora_id`); `keep_flora` is per
finding (`finding_id`), so give it once. One finding cannot be both kept and changed,
and one field of one record takes one value.

## How it is applied

```bash
python apply_sheet_edits.py sheet_edits_2026-11-22.csv                              # dry run
python apply_sheet_edits.py sheet_edits_2026-11-22.csv --apply --admin-handle HANDLE # writes
```

The dry run validates the whole file against the live data and prints what would
happen; nothing is written. `--apply` does it in one transaction, all or nothing: one
FLoRA editor save per record (logged under the record's Changes, with your notes, and
published at once), and a "Keep FLoRA value" decision per `keep_flora` finding. The
sheets and the source records are not touched.

## Stale rows

If a row is refused as stale (the sheet, FLoRA's value or the record's version changed
since the download), download the CSV again and decide again on the fresh rows. Do not
edit the data columns to make a row pass.
