# Permanent FLoRA IDs

The identity registry connects combined records to permanent published IDs so a title or DOI correction does not renumber the dataset.

[Open interactive guide](../index.html#view=overview&node=process-identities)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- The transformed records and their source/merged-source identities.
- Existing flora_records registry and the supplied reference export identity/order.

## What happens

1. Match existing source provenance to existing registry records.
2. Allocate IDs and publication positions for new records.
3. Keep earlier publication positions reserved and append new records.
4. Export id and its full lowercase MD5 as id_md5; the digest is based on the permanent ID, not on a title.

## What is saved, and where

- flora_records keeps permanent registry identities and positions.
- flora_dataset_history keeps dataset count history.
- flora_data carries the resulting id, id_md5 and export_position.

## When it updates

- The FLoRA IDs stage runs after source sync and enrichment.
- Standalone preparation also registers the exact transformed frame before exporting it.

## What comes out

- Stable row identifiers, stable lookup hashes and a predictable output order.

## Keep in mind

- IDs are not regenerated from changing paper titles or DOIs.
- Retiring a stored row preserves its identity; the same ID can be reactivated later.

## Find it in the code

- [flora_registry.py:219 — refresh](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_registry.py#L219) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_registry.py)
- [flora_registry.py:354 — attach_ids](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_registry.py#L354) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_registry.py)
- [prepare_flora.py:71 — validate_identity](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py#L71) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/prepare_flora.py)
- [flora_store.py:89 — materialize](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py#L89) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_store.py)
