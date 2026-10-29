# Prepare and check the exact CSV

prepare_flora.py builds one candidate CSV, checks those exact bytes, stores its data, and writes the release files. This is the final stage shared by the website and nightly pipeline.

[Open interactive guide](../index.html#view=overview&node=process-prepare)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The combined transform, current permanent identities and saved FLoRA corrections.
- Structural rules and network evidence/cache.

## What happens

1. Capture the stored dataset revision, build a fresh .flora.candidate.csv, and verify nonblank unique IDs plus correct MD5 hashes.
2. Run structural checks and network checks on that candidate; report findings, skipped targets and deferred targets.
3. Create a release manifest, notes and daily count history for the candidate bytes.
4. Commit the snapshot to flora_data, preserving concurrently corrected rows and rejecting an older build when another complete snapshot won the race.
5. Replace local output files, with flora.csv last; write the final preparation report.

## What is saved, and where

- flora_data and flora_data_metadata for the stored dataset.
- output/flora.csv, flora_release_manifest.json, flora_release_notes.md, flora_history.csv and flora_dataset_summary.md.
- Preparation/structural/network reports and diagnostic CSVs in the selected output directory.

## When it updates

- Runs after successful input stages of a website or nightly pipeline.
- Stored data becomes available when the database commit succeeds; local file replacement is a subsequent step.

## What comes out

- The combined CSV and matching validation/release evidence.

## Keep in mind

- Check findings normally produce needs_attention warnings; identity errors, transform errors and other exceptions fail the run.
- A late file-write failure can occur after the database committed. The report then identifies a recovery CSV; do not assume failed means the stored dataset is unchanged.
- This command does not publish to GitHub or OSF. --input is offline/non-storing by default unless --store-data is explicit.

## Find it in the code

- [prepare_flora.py:273 — prepare](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py#L273) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py)
- [prepare_flora.py:284 — _prepare](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py#L284) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py)
- [prepare_flora.py:405 — store dataset stage](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py#L405) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py)
- [prepare_flora.py:428 — committed recovery CSV](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py#L428) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py)
- [flora_store.py:89 — materialize](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py#L89) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py)
