# Extractor CSV: candidate study pairs

The separate flora-extractor project supplies extracted replication/original pairs. This app downloads its CSV and turns eligible pairs into records people can review.

[Open interactive guide](../index.html#view=overview&node=input-extractor)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- flora-extractor/data/extracted.csv from the configured GitHub repository and branch.
- The extractor's data/retired_pairs.csv, pinned to the same source commit, supplies explicit retirement requests.

## What happens

1. Resolve a source commit, download its CSV, save a timestamped archive, and validate the candidate against the previous snapshot.
2. Import resolved, valid pairs into PostgreSQL. A new pair receives a record, extraction metadata, and human_1, human_2 and llm queue slots.
3. Existing pairs refresh extractor-owned values while retaining human decisions; an unambiguous source-slot match can preserve identity when the pair key changes.
4. Promote the imported bytes to extracted_latest.csv and verify their checksum.

## What is saved, and where

- PostgreSQL: unvalidated, record_metadata and validation_queue.
- Server working files: data/extracted_latest.csv and timestamped archives, or EXTRACTOR_DATA_DIR when configured.
- Durable imported snapshots: extractor_snapshots; run status, safety report and logs: extractor_maintenance_runs.

## When it updates

- The app schedules the full maintenance pipeline at 02:00 UTC daily.
- An administrator can queue a run from the Extractor pipeline panel; the server checks that queue every 10 seconds.
- New imported pairs are eligible for the next queue request after the import transaction commits.

## What comes out

- Reviewable candidate pairs and an audited, imported extractor snapshot.

## Keep in mind

- Paper discovery, screening and original-study matching happen upstream; those scripts are outside this checkout.
- An imported candidate is not yet an approved record or a prepared FLoRA output.
- Local CSV archives are working files; the PostgreSQL snapshot copy is the durable application record.

## Find it in the code

- [sync_csv.py:403 — sync_once](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/sync_csv.py#L403) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/sync_csv.py)
- [csv_to_db.py:812 — run_import](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/csv_to_db.py#L812) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/csv_to_db.py)
- [csv_to_db.py:595 — _insert_queue_slots](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/csv_to_db.py#L595) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/csv_to_db.py)
- [extractor_maintenance.py:907 — _keep_snapshots_in_database](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py#L907) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/extractor_maintenance.py)
- [app.py:6687 — _start_scheduler](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py#L6687) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/app.py)
