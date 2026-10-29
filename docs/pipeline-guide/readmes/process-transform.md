# Clean, combine and deduplicate

The transform converts raw source rows into a consistent FLoRA record: clean identifiers, standard outcomes, joined paper metadata, deliberate duplicate choices and traceable provenance.

[Open interactive guide](../index.html#view=overview&node=process-transform)

Code snapshot: 29 October 2026, `2f3a89c`.

## What goes in

- Active source_records plus outcome aliases, exclusions, cached metadata, duplicate decisions and saved corrections.

## What happens

1. Normalize replication outcomes and derive reproduction outcomes from the independent computation and robustness axes.
2. Clean DOI/URL/text values and expand recognized COS quote shorthand.
3. Apply confirmed preprint/publication decisions, pair-aware exclusions, duplicate rules and metadata/reference fallbacks.
4. Retain the source record and merged-source provenance for each resulting record.
5. Attach permanent IDs and saved FLoRA corrections before export; log and omit export rows missing either title.

## What is saved, and where

- build() is read-only and returns a prepared DataFrame.
- run() pins identities through flora_registry, then writes the requested candidate CSV and diagnostic CSVs.

## When it updates

- The FLoRA admin view rebuilds from current inputs when its cache signature changes.
- The final stored CSV/API snapshot changes when preparation stores the dataset, or an eligible existing FLoRA record is corrected directly.

## What comes out

- A prepared dataset with consistent fields, source provenance and stable identities.
- Diagnostics for missing titles, merged outcome conflicts, preprint candidates and cross-type duplicate rulings.

## Keep in mind

- The FLoRA tab is a current derived view; it can include an untitled record that the exported dataset omits.
- Unknown outcome aliases or invalid axes block export rather than silently becoming blank.
- Simply viewing the derived table is not the same as storing a new complete snapshot.

## Find it in the code

- [transform_sources.py:852 — build](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/transform_sources.py#L852) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/transform_sources.py)
- [transform_sources.py:1400 — run](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/transform_sources.py#L1400) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/transform_sources.py)
- [flora_service.py:159 — _current](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L159) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
- [flora_service.py:202 — dataset](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py#L202) · [complete script](https://github.com/forrtproject/flora-validation/blob/2f3a89c56a44bd7b6270eb0fa94548f6d93512f8/flora_service.py)
