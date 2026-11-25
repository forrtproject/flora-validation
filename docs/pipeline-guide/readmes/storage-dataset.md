# Stored dataset: flora_data

flora_data is the durable prepared FLoRA snapshot used by Download full CSV and the public API. It is different from raw source records and human-review records.

[Open interactive guide](../index.html#view=overview&node=storage-dataset)

Code snapshot: 25 November 2026, `9cc088d`.

## What goes in

- A complete prepared snapshot, or a direct correction to an already-stored active FLoRA record.

## What happens

1. Insert new IDs and update changed data without changing identities or positions.
2. Mark IDs absent from a new snapshot retired instead of deleting them.
3. Reactivate returning IDs with their original positions.
4. Update shared revision metadata so readers know the dataset changed.

## What is saved, and where

- PostgreSQL flora_data: prepared values, permanent identity, extra fields, position, version and retirement timestamp.
- PostgreSQL flora_data_metadata: output columns, snapshot fingerprint and update time.

## When it updates

- After a successful prepare/store transaction.
- After Save on an eligible active record already present in flora_data; records not yet stored require a pipeline run.

## What comes out

- Current complete CSV export and active records for public lookup endpoints.

## Keep in mind

- PostgreSQL is the durable store. A server's output/flora.csv is a separate filesystem copy.
- Concurrent corrections are preserved during a long build, so an immutable build artifact can differ from the latest stored export.
- No empty full snapshot is accepted by default.

## Find it in the code

- [flora_store.py:89 — materialize](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_store.py#L89) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_store.py)
- [flora_store.py:251 — export_csv](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_store.py#L251) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_store.py)
- [flora_edits.py:902 — publish](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_edits.py#L902) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/flora_edits.py)
- [db_schema.sql:1584 — flora_data](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql#L1584) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql)
- [db_schema.sql:1640 — flora_data_metadata](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql#L1640) · [complete script](https://github.com/forrtproject/flora-validation/blob/9cc088db7f6736bc30bd37be748a15e995fbd230/db_schema.sql)
