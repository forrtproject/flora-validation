# Original work types: evaluation of fred-data #109

Snapshot: `output/flora.csv` at e4a98ee (10 November 2026), 2,851 rows.
Recommendation after consultation with **Claude Opus 5.5**: **build a broad,
non-blocking review queue with persistent review decisions**. Start with
higher-priority suspicious targets, retain other non-article formats at lower
priority, and leave preprints to the publication-version work. The baseline
queue is **73 originals across 75 rows** after excluding 15 preprints.
Implement this in a follow-up PR; PR #22 contains the evaluation and design.

## Coverage and counts

2,823 rows have a nonblank original identifier in `doi_o`, representing 2,563
raw strings and 2,562 normalised identifiers. OpenAlex resolved **2,554** of the
normalised identifiers (99.7%), covering 2,815 rows and 2,553 distinct OpenAlex
works. Eight identifiers were unresolved: six non-DOI strings and two malformed
or unmatched DOIs. Unresolved identifiers are unknown, not articles.

The `doi:` filter was queried in batches of 40 with `select=id,doi,title,type`,
without prefiltering by type. OpenAlex can return a journal DOI when queried with
a preprint or older DOI alias. Follow-up singleton lookups resolved every input
not returned under its requested DOI, avoiding mistaking aliases for misses;
they also found one additional Learning & Memory article labelled `preprint`.

**88 original identifiers (3.45% of resolved originals), appearing in 90 FLoRA
rows**, have a type other than `article`:

| OpenAlex type | Unique originals |
|---|---:|
| conference-paper | 24 |
| review | 16 |
| preprint | 15 |
| book-chapter | 13 |
| dataset | 9 |
| book | 5 |
| letter | 2 |
| editorial | 1 |
| book-review | 1 |
| conference-abstract | 1 |
| paratext | 1 |

Crossref was queried **for all 88 flagged DOIs**, not for the entire corpus. It
classifies 27 as `journal-article`, 23 as `proceedings-article`, 14 as
`book-chapter`, nine as `dataset`, nine as `posted-content`, three as
`edited-book`, two as `monograph`, and one as `other`. All 88 lookups ultimately
succeeded (initial rate-limit responses were retried). Thus Crossref removes
27/88 (30.7%) of the OpenAlex alerts, but still leaves 61 alerts in this subset.
This is not a corpus-wide Crossref flag count or a sensitivity estimate.

Crossref is cleaner about publication format, but its broad `journal-article`
category does not distinguish all corrections, letters, editorials or reviews.
The motivating correction `10.1002/npr2.70058` is absent from this snapshot's
originals. It is a [published erratum](https://pubmed.ncbi.nlm.nih.gov/40954092/),
and OpenAlex classifies it as `erratum` when queried separately. It is not
evidence that the current snapshot contains an erratum target.

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
established. The observed errors nevertheless justify a bounded review queue
whose confirmed decisions survive later runs. These yields do not justify
automatic exclusion, identifier changes or publication failures.

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

## Recommended use after Opus consultation

The two clear wrong-paper targets remain in the current published CSV
(`FLORA-000534` and `FLORA-001286`). Finding those errors has value even when
most format alerts turn out to be legitimate targets. Persistent review decisions
make the initial backlog a one-off task, with future reviews for newly flagged
records or changed originals.

Each affected replication already has a row for its appropriate original:
`FLORA-002357` targets Mazar, Amir and Ariely, and `FLORA-001243` targets the
Davidsson et al. study. These two cleanup reviews concern extraneous original
rows; changing their DOIs to the correct originals would create duplicates.

| Priority | OpenAlex signal | Baseline originals / rows | Reviewed sample |
|---|---|---:|---|
| Higher | Dataset or paratext; also flag future errata and similar nonstudy types | 10 / 10 | 3 identifier problems, including both wrong papers; 3 uncertain |
| Lower | Reviews, proceedings, chapters, books, letters, editorials and other non-article formats | 63 / 65 | 11 legitimate targets |
| Publication-version workflow | Preprint | 15 / 15, excluded from this queue | 3 legitimate targets |

Both priority tiers should use `possible_problem`. Opus suggested `problem`
for the higher tier; this repo's network validator can fail `--fail-on-issues`
on that level. A type label alone should not fail publication or change a record.
Priority can be represented by separate check codes/headings in the existing
review interface, without introducing a new severity system. Preprints are
legitimate originals, and six of the 15 are journal articles OpenAlex mislabels.

OpenAlex should trigger the review. Crossref supplies useful corroborating
evidence and reviewer context, but `journal-article` must not automatically clear
an alert: it also covers corrections and other material that needs inspection.
The index in this audit is a Crossref `book-chapter`. A separate full Crossref
census could assess missed alerts among OpenAlex's articles; the existing
88-DOI comparison cannot establish that sensitivity. It need not delay the
OpenAlex warning queue.

### Retain review decisions

Use the existing `flora_check_findings` and `entry_checks.dismiss` machinery.
A reasoned **Not a problem** decision already records who, when and why, remains
stored with its finding, and can be withdrawn. `_upsert` preserves
these fields, including when the finding resolves and later reopens. General
validation and source-review status must remain unchanged.

Create one finding per record, keyed on **check code + stable FLoRA record ID +
normalised original DOI**. Do not put the title, provider type string, detail
text or all records sharing the DOI into that key. Then:

- Metadata wording, title changes and movement within a priority tier retain the
  decision. Bare and resolver DOI spellings identify the same target.
- A newly added row sharing the original receives its own review; existing
  rows keep theirs. This permits checking the target against each replication.
- A changed `doi_o` resolves the old finding and checks the new target. Restoring
  the old DOI restores its prior decision.
- Moving into the higher-risk tier can create a separate finding while retaining
  the earlier decision in history. New evidence gets attention without erasing
  the previous review.
- Missing or failed metadata lookups keep open findings open. A successful lookup
  that establishes an exempt type can resolve them.

Existing explicit decisions should be carried forward with their attribution and
reason. The audit's `no_problem` entries can prefill proposed review notes; they
should not silently become human-approved dismissals. Three of those 14 entries
are preprints outside the proposed queue, leaving 11 applicable sample judgments.
The audit CSV uses export IDs in `row_ids`; match those to the current record's
`flora_id` before storing any decision.

### Smallest implementation

1. Preserve the OpenAlex `type` already requested by `enrich_works.FIELDS` in
   `work_metadata`, with a type-check timestamp. Fill missing/stale original
   types with polite cached batch requests and singleton alias fallbacks, keyed
   on the requested DOI. Refresh periodically so corrected metadata can resolve
   old alerts. Type-only enrichment must not overwrite bibliographic fields or
   set a good metadata row's `not_found` flag after a failed type lookup.
2. Add a network-check selector in `validate_flora_network.py` that reads those
   cached types, deduplicates lookups by DOI and emits per-record advisory
   findings. No new Google Sheet or review-state table is needed.
3. Extend `entry_checks._network_resolved` with explicit coverage for this check.
   Unknown/deferred types must not resolve earlier findings. Keep this out of
   the row-local consensus checks and structural check's complete-snapshot
   resolution logic.
4. Let reviewers compare original and replication, then confirm the target with
   a reason, correct its identifier through the logged editor, or use the
   existing exclusion flow for an extraneous row. A warning performs none of
   those actions automatically.

Acceptance tests should cover both priority tiers and advisory metadata failures
remaining non-blocking;
`10.1002/npr2.70058` being flagged as an erratum despite broad Crossref typing;
stable decisions through reruns, title/type wording changes, DOI resolver forms
and added records; genuine original changes and reversions; higher-tier
escalation; unknown/failed/deferred lookups retaining open findings; retired
records resolving; alias lookup keys; and preservation of existing metadata,
review status and decision attribution.
