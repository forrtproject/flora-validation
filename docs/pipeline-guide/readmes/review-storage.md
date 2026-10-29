# Where the review data lives

PostgreSQL is the shared durable store. Raw candidates, individual judgements, approved validation records and prepared FLoRA records are separate tables with different jobs.

[Open interactive guide](../index.html#view=overview&node=review-storage)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Extractor imports, human and admin actions, source-sheet synchronization, and preparation runs.

## What happens

1. unvalidated holds each imported candidate, workflow status, raw extraction, review summaries and final_* corrections. Its name does not mean every row is still awaiting review.
2. validation_queue holds the two human review slots plus the LLM slot; record_metadata holds additional extraction/provenance fields linked to the candidate.
3. validated holds approved validation records. It is an input to later combined-dataset preparation, not the entire combined FLoRA database.
4. source_records combines sheet rows and approved inputs. flora_records is the identity/provenance registry used for combined records.
5. flora_data holds the complete prepared FLoRA rows for public lookups. It has its own persistent IDs, order and retirement state.
6. Separate history tables preserve skips, admin decisions, merges, source edits, FLoRA changes, checks and run records.

## What is saved, and where

- Shared server PostgreSQL selected by DATABASE_URL; the guide does not expose or require its credentials.
- Local browser drafts/pending submissions are distinct from these server tables.
- CSV/report files are exported artifacts, not a replacement for the underlying review tables.

## When it updates

- Review tables change when their API transactions commit.
- Complete stored snapshots change during preparation; eligible FLoRA field corrections also update an existing stored row immediately. Published files have their own release step.

## What comes out

- A traceable chain: unvalidated → validation_queue decisions → validated → source_records → prepared flora_data, with source-sheet rows joining at source_records.

## Keep in mind

- Do not confuse validated (approved extractor reviews), flora_records (registry), and flora_data (prepared public rows). They answer different questions.
- Database state can be newer than a CSV download or published release.

## Find it in the code

- [db_schema.sql:25 — unvalidated](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L25) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:98 — validation_queue](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L98) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:243 — validated](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L243) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:296 — record_metadata](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L296) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1223 — source_records](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1223) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1536 — flora_records](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1536) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1580 — flora_data](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1580) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
