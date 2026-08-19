# Extractor CSV contract

This repository consumes Stage 3 `extracted.csv` files from
`forrtproject/flora-extractor`. The upstream authority is
`flora-extractor/shared/schema.py`; the executable import contract here is
`csv_to_db._CURRENT_EXTRACTED_COLUMNS`, with categorical vocabularies in
`extractor_vocab.py`.

The importer is strict by default. Missing current columns, unknown categorical
values, duplicate `pair_id` values, ambiguous source identities, or unusable
resolved links stop the whole import instead of silently inserting partial rows.
`--allow-legacy-schema` exists only for intentional archived-snapshot replay.

## Current extracted row

The current file contains these 52 columns, in any order:

| Group | Columns |
| --- | --- |
| Pair identity | `pair_id`, `type`, `original_rank`, `n_originals` |
| Replication paper | `doi_r`, `title_r`, `study_r`, `abstract_r`, `year_r`, `authors_r`, `journal_r`, `url_r`, `openalex_id_r`, `oa_work_id_r`, `source`, `ref_r`, `bibtex_ref_r` |
| Stage 2 screening | `paper_type`, `filter_method`, `filter_evidence`, `filter_confidence`, `screen_categories` |
| Original matching | `original_match_type`, `original_match_confidence`, `classify_llm_model` |
| Original paper | `doi_o`, `title_o`, `study_o`, `year_o`, `authors_o`, `oa_work_id_o`, `ref_o`, `bibtex_ref_o` |
| Link decision | `link_method`, `link_evidence`, `link_confidence`, `link_llm_model`, `doi_o_verification` |
| Full-text provenance | `pdf_source`, `parse_method` |
| Flat outcome | `outcome`, `outcome_phrase`, `outcome_confidence`, `out_quote_source`, `outcome_reasoning`, `outcome_llm_model` |
| Reproduction axes | `outcome_computation`, `outcome_computational_quote`, `out_quote_computational_source`, `outcome_robustness`, `outcome_robustness_quote`, `out_quote_robust_source` |

Legacy files may use `filter_status` instead of `paper_type`; that one rename is
accepted without enabling legacy mode.

`title_r` / `title_o` are paper titles. `study_r` / `study_o` are within-paper
study numbers such as `1` or `1, 2`; they are never interchangeable.

## Import eligibility

A row is imported only when:

- `paper_type` is `replication` or `reproduction`;
- `link_method` is one of the resolved methods below;
- `oa_work_id_r` / `openalex_id_r` yields a numeric replication `work_id`, so
  engine lineage cannot silently become `NULL`;
- a DOI-less original has `oa_work_id_o`, giving it stable identity and an
  OpenAlex link; and
- the CSV passes file-wide identity and vocabulary checks.

Current resolved `link_method` values:

- `citation_context_match`
- `same_author_year_title_overlap`
- `single_candidate_after_requery`
- `title_pattern_match`
- `grobid_ref_match`
- `llm_cited_candidates`
- `llm_references`
- `llm_fulltext`
- `llm_title_search`
- `llm_author_year_search`

`author_year_match` and `llm_abstract` are accepted only for archived files.
Known unresolved or quarantined methods are recognized but excluded, including
`unidentified_original`, `keyed_link_disputed`, `not_a_replication`,
`prescreen_discard`, `screen_disagreement`, `no_original_found`,
`target_pending`, and `api_error`.

## Identity and corrections

`pair_id` is the extractor's row identity. For DOI-less originals, upstream uses
`oa_work_id_o` before falling back to normalized title, so different originals do
not all hash as an empty DOI.

`pair_id` can legitimately change when Stage 3 corrects an original. To avoid
inserting a second validator record, this importer also tracks the stable source
slot `(work_id, original_rank)`, where `work_id` is the numeric replication
OpenAlex ID. A unique slot match re-keys the existing record and refreshes its raw
extractor fields. If validators have already touched that record, it is moved to
`need_review` and receives an admin note. Ambiguous slot matches stop the import.

The exact file must still contain one row per `pair_id`. Duplicate IDs are an
upstream data error and are never collapsed automatically.

## Outcomes

Replication outcomes are stored using the current FLoRA labels:

- `successful`
- `failed`
- `mixed`
- `uninformative`
- `descriptive only`
- `statistically successful but flawed`
- `cannot_be_determined`
- `not_a_replication`

Import aliases normalize `success` → `successful`, `failure` → `failed`,
`descriptive` → `descriptive only`, and the old underscore spellings of
`statistically successful but flawed` to the spaced label.

Reproductions are coded on two independent fields, each with its own quote and
source:

| Axis | Settled values | Undetermined value |
| --- | --- | --- |
| `outcome_computation` | `computationally reproducible`, `computational issues`, `technical failure`, `not checked` | `cannot_be_determined` |
| `outcome_robustness` | `robust`, `robustness challenges`, `not checked` | `cannot_be_determined` |

The twelve settled combinations are stored in `outcome` as
`<computation>, <robustness>`. If either axis is missing or undetermined, the
derived flat value is `cannot_be_determined`. The axes remain authoritative and
are validated independently throughout human review, consensus, admin resolution,
and export.

An upstream `api_error` outcome means no verdict was produced; the row remains
available when its link is otherwise eligible, with database `outcome = NULL`.

## DOI-less originals

For `doi_o_verification = no_doi`:

- `doi_o` stays blank;
- `oa_work_id_o` is required;
- `url_o` comes from the CSV or is rendered as an OpenAlex URL; and
- validated uniqueness uses `oa_work_id_o` as `original_key` instead of treating
  every blank DOI as the same original.

Other known verification values are `verified`, `corrected`, `mismatch`,
`not_found`, `no_metadata`, `api_error`, and `skipped`.

## Provenance and lineage

`record_metadata` preserves the extractor fields that are not part of the main
validation form, including screening/link confidence and evidence, OpenAlex IDs,
DOI verification, full-text provenance, model/reasoning fields, BibTeX references,
and `screen_categories`.

The importer requires and derives numeric `work_id` from `oa_work_id_r` /
`openalex_id_r`. It accepts a routing release through `--release-id`, falls back
to a `release_id` column when a forward-compatible/ad-hoc extracted file carries
one, and nightly sync supplies `ROUTING_RELEASE_ID` when configured. Both values
are exported with validated data, so a row can be traced to the filter-engine
release that admitted it.

`screen_categories` is multi-valued and pipe-delimited. Quote-source fields may
also contain several pipe-delimited sections; import normalizes spacing and removes
duplicates.

## Full-text provenance

`pdf_source` records the acquisition tier (for example `row_url`, `arxiv`, `osf`,
`openalex_oa`, `unpaywall_pdf`, `semanticscholar`, `core`, `europepmc`,
`landing_<host>`, `serpapi`, `playwright`, `openalex_xml`, `epmc_xml`,
`osf_registration`, or `html_landing`). `parse_method` records the parser selected
for the LLM input (for example `openalex_xml`, `epmc_xml`, `pdfminer`, `grobid`,
`docpluck`, `opendataloader`, `markitdown`, or `docx`).

A resolved `llm_fulltext` row with blank `pdf_source` is reported as contradictory
and should be treated as unverified.

## Vocabulary drift policy

`extractor_vocab.check_csv_vocabulary()` checks paper type, record type, source,
confidence, original-match type, link method, DOI verification, flat outcome, and
both reproduction axes. Add an upstream rename to this centralized contract and
the matching database constraint/migration together. Unknown values must never be
treated as ordinary unresolved rows.
