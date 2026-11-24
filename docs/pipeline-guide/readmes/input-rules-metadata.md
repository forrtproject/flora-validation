# Exclusions, outcomes and paper metadata

Supporting inputs tell preparation which rows to exclude, how outcome wording maps to the codebook, and which bibliographic details belong to each paper.

[Open interactive guide](../index.html#view=overview&node=input-rules-metadata)

Code snapshot: 24 November 2026, `3cc13da`.

## What goes in

- The exclusions sheet and checked-in notebook/COS exclusion lists.
- outcome_alias, administrator duplicate/preprint rulings and FLoRA corrections.
- OpenAlex metadata for DOIs and work IDs, with reference-based fallbacks.

## What happens

1. sync_exclusions.py replaces only its own source groups in transform_exclusions; manually added exclusions stay intact.
2. enrich_works.py fetches uncached paper metadata and stores found and missing results.
3. The transform reads these inputs to normalize outcomes, choose correct identifiers, enrich references and apply deliberate exclusions.

## What is saved, and where

- transform_exclusions, work_metadata, outcome_alias and preprint_dedup_decisions in PostgreSQL.
- FLoRA corrections are stored separately in flora_record_changes.

## When it updates

- Exclusions and metadata enrichment are stages of the website and nightly FLoRA pipelines.
- Metadata is cached; normal enrichment is mainly for uncached records, with explicit retry options for misses.

## What comes out

- Rules and cached evidence used by the prepared dataset.

## Keep in mind

- A pair-specific exclusion can exclude one original/replication relationship without excluding every use of the replication paper.
- Changing a rule can change the derived FLoRA view before a new full stored snapshot is prepared.

## Find it in the code

- [sync_exclusions.py:158 — sync](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_exclusions.py#L158) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/sync_exclusions.py)
- [enrich_works.py:295 — enrich](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/enrich_works.py#L295) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/enrich_works.py)
- [enrich_works.py:467 — enrich_work_ids](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/enrich_works.py#L467) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/enrich_works.py)
- [transform_sources.py:633 — load_rules](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/transform_sources.py#L633) · [complete script](https://github.com/forrtproject/flora-validation/blob/3cc13dadec9b51c8e25d03a4b30827300f32ac16/transform_sources.py)
