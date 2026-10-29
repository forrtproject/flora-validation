# Stored dataset: flora_data

flora_data is the durable prepared FLoRA snapshot used by Download full CSV and the public API. It is different from raw source records and human-review records.

[Open interactive guide](../index.html#view=overview&node=storage-dataset)

Code snapshot: 29 October 2026, `2f3a89c`.

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

- [flora_store.py:89 — materialize](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py#L89) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py)
- [flora_store.py:252 — export_csv](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py#L252) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py)
- [flora_edits.py:759 — publish](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py#L759) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_edits.py)
- [db_schema.sql:1580 — flora_data](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1580) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1636 — flora_data_metadata](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1636) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
