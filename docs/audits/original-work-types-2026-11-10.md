# Original work types: evaluation of fred-data #109

Snapshot: `output/flora.csv` at e4a98ee (10 November 2026), 2,851 rows.
Recommendation: **do not build a blanket non-article check**. A future narrow,
non-blocking warning for corrections, indexes and other paratext could be useful;
confirm identity from title/publisher correction metadata rather than work type
alone. No type validation rule is added in this PR.

## Coverage and counts

2,823 rows have an original DOI, representing 2,563 distinct DOI strings. OpenAlex
returned 2,553 works (99.6%); ten unmatched DOIs are unknown, not articles. The
`doi:` filter was queried in batches of 40 with `select=id,doi,title,type`. No
OpenAlex type filter was applied before querying.

87 unique originals (3.41% of matched works), appearing in **89 FLoRA rows**, have
a type other than `article`:

| OpenAlex type | Unique originals |
|---|---:|
| conference-paper | 24 |
| review | 16 |
| preprint | 14 |
| book-chapter | 13 |
| dataset | 9 |
| book | 5 |
| letter | 2 |
| editorial | 1 |
| book-review | 1 |
| conference-abstract | 1 |
| paratext | 1 |

Crossref was queried **for all 87 flagged DOIs**, not for the entire corpus. It
classifies 26 as `journal-article`, 23 as `proceedings-article`, 14 as
`book-chapter`, nine as `dataset`, nine as `posted-content`, three as
`edited-book`, two as `monograph`, and one as `other`. All 87 lookups ultimately
succeeded (initial rate-limit responses were retried). Thus Crossref removes
26/87 (29.9%) of the OpenAlex alerts, but still leaves 61 alerts in this subset.
This is not a corpus-wide Crossref flag count or a sensitivity estimate.

Crossref is cleaner about publication format, but its broad `journal-article`
category does not distinguish all corrections, letters, editorials or reviews.
The motivating correction `10.1002/npr2.70058` is absent from this snapshot's
originals. It is a [published erratum](https://pubmed.ncbi.nlm.nih.gov/40954092/),
not evidence that the current snapshot contains an erratum target.

## Hand review

The accompanying CSV records every alert, its FLoRA row IDs and replication,
and judgments for a **purposive 20-original sample** spanning suspicious identifiers
and likely false alarms. Review compared API bibliographic records and the FLoRA
original/replication pair, with publisher, PubMed and replication sources for
ambiguous cases. This is a screening judgment, not a full-text systematic audit.

- **3/20 (15%) clear identifier problems**: a book index; an unrelated cheating
  paper attributed to the Mazar, Amir & Ariely replication; and a PsycEXTRA record
  where a journal version is verified. Only the first two are clearly wrong-study
  targets (2/20, 10%); the third is an identifier-preference problem.
- **3/20 (15%) need further adjudication**: PsycEXTRA identifiers for relevant
  studies; a publication-format flag alone cannot establish an error.
- **14/20 (70%) show no problem with the target**: empirical journal papers
  misclassified as preprints/reviews, valid meta-analysis replications, letters,
  conference studies and an empirical book chapter.

Observed precision is **15% for actionable identifier cleanup**, or at most 30%
if all three uncertain cases become problems. It is **10% for clearly wrong
study targets**. These are sample yields, not unbiased population estimates;
selection deliberately included suspicious cases. High precision is not
established, so even a warning should be more selective than `type != article`.

Specific checks:

- `10.4337/9781848442764.00018`: Crossref title is **Index**. The replication
  already points to the actual Davidsson et al. study in another FLoRA row;
  this index is extraneous. [Publisher record](https://www.elgaronline.com/view/9781847202192.00018.xml).
- `10.1037/e722852011-095`: title is *Temporal Influences on Cheating Behavior:
  The “What the Hell” Effect*, whereas the paired
  [registered replication](https://www.psychologicalscience.org/publications/mazar-amir-ariely-rrr)
  targets Mazar, **Amir**, and Ariely (2008).
- `10.1037/e513702014-054`: the journal version of Bryan, Adams and Monin's study
  is [10.1037/a0030655](https://pubmed.ncbi.nlm.nih.gov/23127418/). This is a
  relevant study with a poor preferred identifier, not a nonstudy replication.
- For the Fostering Healthy Futures PsycEXTRA record, the
  [replication text](https://pmc.ncbi.nlm.nih.gov/articles/PMC6917986/) names the
  2010 pilot trial; the exact identifier should be adjudicated rather than
  rejected from its `dataset` label.
- `10.1101/gr.126516.111`, `10.1101/lm.035493.114` and
  `10.1101/lm.042978.116` are journal articles according to Crossref. A server
  prefix or OpenAlex preprint label alone is insufficient.

No FLoRA records were changed for this evaluation. See
[all flagged originals and sample judgments](original-work-types-2026-11-10.csv).
