# CSV Column Reference — FLoRA Extractor Pipeline

Each stage reads the previous stage's CSV and writes a richer one. Columns are **additive** — every output includes all columns from the input plus the new ones listed below. The authoritative source is [`shared/schema.py`](../shared/schema.py); this file explains what each column means.

---

## Pipeline at a Glance

```
Stage 1  search/        →  data/candidates.csv   (10 cols)
Stage 2  filter/        →  data/filtered.csv     (14 cols = 10 + 4)
Stage 3  extract/       →  data/extracted.csv    (36 cols = 1 pair_id + 14 + 21)
Stage 4  validate/      →  data/validated.csv    (43 cols = 36 + 7)
```

---

## Stage 1 — Search

**Script:** `python -m search.run_search`  
**Input:** OpenAlex API, Semantic Scholar API, Bob Reed list, I4R list  
**Output:** `data/candidates.csv`

Stage 1 casts a wide net. It queries multiple bibliographic sources for papers that might be replications, deduplicates by DOI then by fuzzy title, and writes one row per candidate. Precision is low here by design — Stage 2 filters.

### Output columns

| Column | Type | Description |
|---|---|---|
| `doi_r` | str | DOI of the replication/reproduction paper, cleaned (no `https://doi.org/` prefix). Primary identifier throughout the pipeline. |
| `title_r` | str | Paper title. |
| `abstract_r` | str | Abstract text. Empty if the source API did not return one. |
| `year_r` | int | Publication year. |
| `authors_r` | str | Semicolon-separated author list (`Last, First; Last, First`). |
| `journal_r` | str | Journal or venue name. |
| `url_r` | str | Open-access URL if available (arXiv, OSF, Unpaywall, etc.). Empty otherwise. |
| `openalex_id_r` | str | OpenAlex work ID (e.g. `W2741809807`). Used in Stage 3 to fetch referenced works. |
| `source` | str | Where this candidate came from. Values: `openalex` · `bob_reed` · `i4r` · `semantic_scholar`. |
| `ref_r` | str | FLoRA-style display reference: `"Surname · Year · Journal"`. Built at search time from the first author's surname, publication year, and journal. |

---

## Stage 2 — Filter

**Script:** `python -m filter.run_filter`  
**Input:** `data/candidates.csv`  
**Output:** `data/filtered.csv`

Stage 2 removes false positives. Each paper is first checked by a fast rule-based classifier (keyword patterns, author-year citation check). Papers that are clearly replications or clearly false positives are labelled immediately. Uncertain papers go to a single LLM call. The goal is to pass only genuine replications and reproductions forward.

### New columns added

`paper_type` was called `filter_status` until flora-extractor issue #93. `csv_to_db.py`
reads either name and writes the database column, which keeps the old name
(`record_metadata.filter_status`); CSVs exported before the rename still carry
`filter_status` and import unchanged.

| Column | Type | Values | Description |
|---|---|---|---|
| `paper_type` | str | `replication` · `reproduction` · `false_positive` · `needs_review` | Classification result. `replication` = same methods, different sample. `reproduction` = same data, re-analysis. `false_positive` = not a replication at all. `needs_review` = ambiguous; human review needed. |
| `filter_method` | str | `rule_based` · `llm` · `both` | Which classifier produced the label. `both` means the rule-based and LLM classifiers agreed. |
| `filter_evidence` | str | — | The phrase or quote from the abstract that triggered the classification. Helps reviewers understand why a paper was included or excluded. |
| `filter_confidence` | str | `high` · `medium` · `low` | Categorical confidence in `paper_type`. **Not a float** — a three-level label is more honest than a pseudo-probability from a single LLM call. |

### All columns at this stage

`doi_r`, `title_r`, `abstract_r`, `year_r`, `authors_r`, `journal_r`, `url_r`, `openalex_id_r`, `source`, `ref_r`, `paper_type`, `filter_method`, `filter_evidence`, `filter_confidence`

---

## Stage 3 — Extract

**Script:** `python -m extract.run_extract`  
**Input:** `data/filtered.csv`  
**Output:** `data/extracted.csv`

Stage 3 answers two questions for each confirmed replication: which original study does it target, and what was the outcome? It first classifies how many originals the paper targets, then routes through the appropriate pipeline. False positives (`paper_type = false_positive`) are passed through with extraction columns empty — they are included in `extracted.csv` so Stage 4 can see the full picture.

### Leading identifier

| Column | Type | Description |
|---|---|---|
| `pair_id` | str | MD5 of `doi_r + "|" + doi_o` (full 32-char hex). Uniquely identifies a replication–original pair. For false positives or unresolved rows, `doi_o` is empty so `pair_id` is derived from `doi_r` alone. The UI shows only the first 3 characters as a compact visual tag. |

### New columns added

#### Original-match routing (determined first, before any extraction)

| Column | Type | Values | Description |
|---|---|---|---|
| `original_match_type` | str | `single_original` · `multiple_original` | Classification of how many distinct original studies this paper targets. `single_original` = one clear target. `multiple_original` = paper genuinely replicates several independent originals (produces multiple rows in the output, one per original), or 2–5 OpenAlex candidates share an author/year and need disambiguation. Supersedes `multiple_match`, which the dated CSVs under `data/` still carry. |
| `original_match_confidence` | str | `high` · `medium` · `low` | Confidence in the `original_match_type` classification. |

#### Original study identification

| Column | Type | Description |
|---|---|---|
| `doi_o` | str | DOI of the original (target) study, cleaned. The study this replication is testing. |
| `title_o` | str | Title of the original study. |
| `year_o` | int | Publication year of the original study. |
| `authors_o` | str | Authors of the original study (first author or full list). |
| `ref_o` | str | FLoRA-style display reference for the original study: `"Surname · Year · Journal"`. Fetched from OpenAlex after `doi_o` is resolved. Falls back to `"Surname · Year"` if the journal name cannot be retrieved. |

#### Linking — how the original was found

| Column | Type | Values | Description |
|---|---|---|---|
| `link_method` | str | `llm_references` · `llm_title_search` · `llm_author_year_search` · `llm_cited_candidates` · `llm_fulltext` · `title_pattern_match` · `citation_context_match` · `no_original_found` · `target_pending` · `api_error` | How the original was identified. `llm_references` = LLM picked it out of the reference list. `llm_title_search` / `llm_author_year_search` = LLM identified a candidate via a title or author-year lookup. `llm_cited_candidates` = LLM chose among cited works. `llm_fulltext` = LLM needed the full PDF text. `title_pattern_match` / `citation_context_match` = matched without an LLM. `no_original_found` = no identifiable original. `target_pending` = not yet processed. `api_error` = failed after 3 retries. |

The first seven are "resolved" — rows carrying them are imported for validation. The
importer's copy of this list lives in `extractor_vocab.py`, together with the retired
names (`author_year_match`, `llm_abstract`, `single_candidate_after_requery`,
`same_author_year_title_overlap`) that the dated CSVs under `data/` still use. A value
absent from both is rejected outright rather than skipped — see "Vocabulary drift" below.
| `link_evidence` | str | — | The quote or citation pattern used to link the replication to its original (e.g. `"Baumeister et al. (1998)"`). |
| `link_confidence` | str | `high` · `medium` · `low` | Confidence that the identified original is correct. |
| `link_llm_model` | str | — | Exact model identifier used for DOI resolution (e.g. `gemini-2.0-flash`). Empty when linking was rule-based. |

#### Outcome

| Column | Type | Values | Description |
|---|---|---|---|
| `outcome` | str | `success` · `failure` · `mixed` · `statistically_successful_but_flawed` · `uninformative` · `descriptive` · `cannot_be_determined` · `pending` · `api_error` | Replication outcome. `success` = original finding replicated. `failure` = original finding not replicated. `mixed` = partially replicated. `statistically_successful_but_flawed` = the result held statistically but the authors flag a methodological problem that undermines it — a category in its own right, not a flavour of `success` and not `mixed`, and it applies to reproductions as well as replications. `uninformative` = study ran but could not determine if it replicated. `descriptive` = replicated methods in a different context without testing the original claim (flag for review). `cannot_be_determined` = not enough information. `pending` = not yet processed. `api_error` = extraction failed. |

On **reproduction** rows `outcome` usually carries a two-axis label,
`"<computational>, <robustness>"` — e.g. `computationally reproducible, robust` or
`not checked, robustness challenges`. Two values are exceptions that sit outside the
grid and apply to both record types: `cannot_be_determined` and
`statistically_successful_but_flawed`. The extractor also ships the axes unjoined in
`outcome_computation` / `outcome_robustness`, each with its own quote and source; those
are the coded fields per the FLoRA codebook, and the joined string is legacy. Note the two
disagree when either axis is `cannot_be_determined`: the joined string collapses to the
bare label and loses the other axis. `success`/`failure` are renamed to
`successful`/`failed` on import, as are the retired reproduction spellings
(`computationally successful, X` → `computationally reproducible, X`,
`computation not checked, X` → `not checked, X`).
| `outcome_phrase` | str | — | A verbatim quote from the paper supporting the outcome classification. |
| `outcome_confidence` | str | `high` · `medium` · `low` | Confidence in the `outcome` classification. |
| `out_quote_source` | str | `abstract` · `fulltext` · `title` | Where in the paper the `outcome_phrase` was found. |

#### Record bookkeeping

| Column | Type | Description |
|---|---|---|
| `type` | str | `replication` or `reproduction`. Carried from Stage 2's `paper_type`. |
| `original_rank` | int | `1` for single-original papers. For multi-original papers (`multiple_original`), each original gets its own row with ranks `1`, `2`, `3`, …. |
| `n_originals` | int | Total number of originals for this replication paper. `1` for single-original papers. |

### All columns at this stage

`pair_id`,  
`doi_r`, `title_r`, `abstract_r`, `year_r`, `authors_r`, `journal_r`, `url_r`, `openalex_id_r`, `source`, `ref_r`,  
`paper_type`, `filter_method`, `filter_evidence`, `filter_confidence`,  
`original_match_type`, `original_match_confidence`,  
`doi_o`, `title_o`, `year_o`, `authors_o`, `ref_o`,  
`link_method`, `link_evidence`, `link_confidence`, `link_llm_model`,  
`outcome`, `outcome_phrase`, `outcome_confidence`, `out_quote_source`,  
`type`, `original_rank`, `n_originals`

---

## Stage 4 — Validate

**Script:** `python -m validate.import_csv` then `python -m validate.app`  
**Input:** `data/extracted.csv` (loaded into SQLite via `import_csv.py`)  
**Output:** `data/validated.csv` (exported from the web app)

Stage 4 is a Flask web app where human reviewers vote to confirm or reject each extraction. Two confirm votes (from different reviewers) set `validation_status = confirmed`. Any `needs_review` vote overrides other votes. Reviewers can also correct the extracted original DOI or outcome if Stage 3 got it wrong.

### New columns added

| Column | Type | Values | Description |
|---|---|---|---|
| `validation_status` | str | `confirmed` · `rejected` · `pending` · `needs_review` | Aggregated status from reviewer votes. `pending` = no votes yet. `needs_review` = at least one reviewer flagged it. |
| `vote_count` | int | — | Total number of votes received. |
| `confirm_votes` | int | — | Number of confirm votes. |
| `reject_votes` | int | — | Number of reject votes. |
| `validator_notes` | str | — | Aggregated free-text comments from all reviewers. |
| `validated_doi_o` | str | — | Reviewer-corrected original study DOI. **Blank means the Stage 3 value was accepted unchanged.** Non-blank values allow accuracy measurement by diffing against `doi_o`. |
| `validated_outcome` | str | — | Reviewer-corrected outcome. **Blank means the Stage 3 value was accepted unchanged.** Non-blank values allow accuracy measurement by diffing against `outcome`. |

### All columns at this stage

All 36 columns from Stage 3, plus:  
`validation_status`, `vote_count`, `confirm_votes`, `reject_votes`, `validator_notes`, `validated_doi_o`, `validated_outcome`

---

## Column Naming Conventions

| Suffix / prefix | Meaning |
|---|---|
| `_r` | Relates to the **r**eplication study (the paper doing the replicating) |
| `_o` | Relates to the **o**riginal study (the paper being replicated) |
| `validated_` | Reviewer correction; blank = Stage 3 value accepted |
| `link_` | About how the original was identified/linked |
| `filter_` | Added by Stage 2's filter classifier |
| `outcome_` | About the replication result |

## Categorical Value Summary

| Column | Valid values |
|---|---|
| `paper_type` | `replication` · `reproduction` · `false_positive` · `needs_review` |
| `filter_confidence` | `high` · `medium` · `low` |
| `original_match_type` | `single_original` · `multiple_original` (retired: `multiple_match`) |
| `link_method` | `llm_references` · `llm_title_search` · `llm_author_year_search` · `llm_cited_candidates` · `llm_fulltext` · `title_pattern_match` · `citation_context_match` · `no_original_found` · `target_pending` · `api_error` |
| `link_confidence` | `high` · `medium` · `low` |
| `outcome` | `success` · `failure` · `mixed` · `statistically_successful_but_flawed` · `uninformative` · `descriptive` · `cannot_be_determined` · `pending` · `api_error` — or, on reproductions, `"<computational>, <robustness>"` |
| `outcome_confidence` | `high` · `medium` · `low` |
| `out_quote_source` | `abstract` · `fulltext` · `title` · `introduction` · `discussion` · `results` — compound values joined with ` \| ` also occur (e.g. `abstract \| discussion`) |
| `type` | `replication` · `reproduction` |
| `validation_status` | `confirmed` · `rejected` · `pending` · `needs_review` |
| `source` | `openalex` · `bob_reed` · `i4r` · `semantic_scholar` |

---

## Vocabulary drift

These vocabularies are set upstream and have been renamed without notice. In July 2026
every `link_method` value changed; the importer's allowlist did not, and because an
unrecognised value was indistinguishable from "not yet resolved", the nightly import took
31 of 1890 eligible rows for five weeks without raising anything.

Two things prevent a repeat:

- **One definition.** `extractor_vocab.py` owns every vocabulary this repo reads from the
  CSV. `csv_to_db.py`, `find_orphans.py` and `cleanup_orphans.py` import from it; none of
  them declares its own copy. `db_schema.sql`'s `unvalidated_outcome_check` and the
  vocabulary `llm_validator.py` shows the model are covered by a test that fails if they
  drift from it.
- **Refuse, don't skip.** `check_csv_vocabulary()` raises `VocabularyDriftError` on any
  value it does not recognise, naming the value and its row count. The import stops
  instead of quietly shrinking.

**When the extractor renames something:** add the new value to `CURRENT_METHODS` (or the
relevant outcome set) in `extractor_vocab.py` and move the old name to `RETIRED_METHODS`.
Retired values are kept, not deleted — `data/` holds dated CSV snapshots back to May, and
`find_orphans` / `cleanup_orphans` read whichever is passed to `--input`. For a renamed
*outcome*, add the old→new pair to `OUTCOME_RENAME` and to the `_outcome_relabel` table in
`db_schema.sql`, so incoming rows and already-stored rows end up on the same spelling.
