# Original-work-type warning

The daily entry check flags `doi_o` when its cached OpenAlex type is neither
`article` nor `preprint`. Dataset, erratum, paratext and similar types receive
higher priority; books, chapters, reviews, letters and conference papers receive
a publication-format warning. A non-article can still be a valid original.

Findings are advisory. Review the original against the replication, then mark
**Not a problem** with a reason or correct it through the logged FLoRA editor.
Decisions are retained across reruns and metadata failures. A different original
DOI gets its own review. Validation status is unchanged; this check never blocks
publication or automatically changes or excludes a record.

`enrich_works.py` preserves OpenAlex types and refreshes missing/stale original
types within a three-minute budget. The existing nightly and website pipelines
run it before the cached network check. Successful types expire after 25–35 days;
unknown results retry after a day. Transient errors do not become cached misses.
Type-only updates preserve bibliography fields and `not_found`.

```sh
python enrich_works.py --type-minutes 3
python validate_flora_network.py --checks work_types --input output/flora.csv
```

These commands use `DATABASE_URL`; `OPENALEX_MAILTO` is optional.
`--type-minutes 0` disables the optional refresh. Reviewing a record makes no
HTTP requests. Historical findings for removed check codes resolve through the
existing obsolete-check path without erasing human review history.

## Completed one-off review

The initial 90-entry review is complete: 68 references kept, 18 original DOI
corrections applied, and 4 extra rows retired. The user approved the final
dispositions on 10 November 2026. Source review/validation status was preserved;
60 then-current warnings were marked **Not a problem**. Logged changes remain
in the database; the published output is tracked in this repository.

The [compact decision CSV](../cache/original_target_review_decisions.csv) records
the before/after DOI, action, retained identifier, surviving record, rationale
and primary evidence link. Empty `doi_o_after` means the row was retired; it does
not instruct the editor to clear its DOI. The CSV is an audit record, not a
scheduled correction queue. The unverified Fostering Healthy Futures conference
identifier was deliberately left out.

[Detailed review evidence — fixed gist revision](https://gist.github.com/LukasWallrich/013855fb7ba97b064ec32add02026ed6/c717aa34f323bee2eb2a81914d403a9b221a4d95)
contains the independent Sol/Opus votes, primary-source investigations, lookup
snapshots and application receipts. The runtime and tests do not read that
bundle or make further model calls. Full PDFs remain in the private paper cache.

## Activation

After merge, the existing GitHub nightly job runs enrichment and the work-type
check automatically. The website's daily checks use the new code after the
normal backend deployment; startup applies the idempotent schema changes. No
one-off correction, backfill or review-import command is required. The approved
corrections and review decisions are already in the database and published data.
