# FORRT Library of Reproduction and Replication Attempts (FLoRA), version $version

FLoRA links published original studies in the social and behavioural sciences to
attempts to replicate them (with new data) or reproduce them (with the original data).
Each row pairs one original study with one replication or reproduction report and
records the outcome as the report's authors describe it. A report that tests several
original studies contributes several rows.

This version has $records records: $replications replications and $reproductions reproductions.

## Files

- `flora.csv`: the data. UTF-8 with BOM, every field quoted; `NA` marks missing or
  inapplicable values. References and quotes can contain line breaks, so read the
  file with a CSV parser (for example, R or Python).
- `CODEBOOK.md`: definitions of all columns and the categories used in this version.
- `CHANGELOG.md`: changes in this and earlier versions, newest first.
- `CITATION.cff`: how to cite this version, with its DOI.

## Using the data

- Rows are pairs of publications, not effect sizes. Effect-level statistics for many
  of these replications are in the companion database FReD: https://doi.org/$fred_doi
- Outcomes are the report authors' conclusions as coded by the FLoRA team, not an
  independent assessment. Replication rows use `outcome`; reproduction rows use
  `outcome_computation` and `outcome_robustness`. The coding rules are in the FLoRA
  coding guidance: $faq_url
- `NA` does not mean failure. Outcome fields that do not apply to a row's `type` are `NA`.
- `author_overlap_pct` compares family names only, so it can over- or understate
  how much the original and replication teams overlap.
- `source` names the project a record comes from (for example, RP:P or SCORE) or,
  otherwise, how it entered FLoRA.
- `id` is permanent. FLoRA is updated regularly: report the version you used, and use
  `id` to match records across versions.

## Licence and citation

The data are licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).
The licence covers this dataset, not the publications it references. Cite this version
with `CITATION.cff` or this record's DOI; to cite FLoRA regardless of version, use
https://doi.org/$concept_doi.

FLoRA is maintained by FORRT (https://forrt.org/replication-hub/) in
https://github.com/forrtproject/flora-validation. Please report errors or missing
replications there as issues.
