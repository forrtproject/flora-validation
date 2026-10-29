# Which ID belongs to which record?

The application uses different IDs for the imported paper pair, each human review slot, source-sheet rows and the final public dataset. These IDs are related but are not interchangeable.

[Open interactive guide](../index.html#view=overview&node=review-identities)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- An extractor pair identifier, internal database rows, and permanent combined-dataset identities.

## What happens

1. unvalidated.record_id is the internal candidate UUID. Its pair_id identifies the extractor's pair.
2. validation_queue.queue_id identifies a review slot; its record_id links back to unvalidated and its validator_id links to validators.
3. validated.record_id also refers to the original unvalidated candidate.
4. source_records has its own record_id and display_id. flora_records links the primary and merged sources, pins a flora_id, and reserves publication export_id/export_position.
5. flora_data.id is the permanent prepared/public identity; id_md5 is generated from that ID. Retired positions and identities are not recycled.

## What is saved, and where

- PostgreSQL keys, foreign-key links, registry provenance and generated ID hashes.

## When it updates

- Corrections to DOI/title do not imply a new public record ID.
- Registry/source associations can change during preparation while publication identity stays pinned.

## What comes out

- A stable way to follow a candidate, review evidence, source lineage and public record without treating a DOI correction as a new identity.

## Keep in mind

- The extractor pair_id and public flora_data.id_md5 are different concepts even if both look like hashes.
- Prepared flora_data deliberately has no cascading foreign key to a source row: a published record can outlive its source.

## Find it in the code

- [db_schema.sql:25 — candidate identity](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L25) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:98 — queue identity and links](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L98) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1536 — combined-record identity registry](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1536) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
- [db_schema.sql:1580 — prepared permanent identity](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql#L1580) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/db_schema.sql)
