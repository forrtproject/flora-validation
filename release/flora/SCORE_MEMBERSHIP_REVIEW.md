# Review of the 65 unassigned SCORE mentions

All 65 belong to SCORE: 64 replications and one reproduction. No false positives
or unresolved project memberships remain in this review. Checked 11 November 2026
against the 2,884-record snapshot identified in the machine-readable review.

The [CSV review](score-project-membership-review.csv) lists every record and its
report/project links. The [JSON evidence](score-project-membership-review.json)
includes the evidence method, live OSF titles, source identifiers and limitations.
The release registry now explicitly includes these permanent record IDs. This
changes the release's source projection; it does not edit intake routes or outputs.

## Evidence

Every record ID and exact report URL matched the independent report-byline audit
in [PR #26](https://github.com/forrtproject/flora-validation/pull/26), pinned at
commit `17b84324ae024c6e840361173c19b335c05190da`. Its file-to-component mappings
were reused; each report component was then checked against live OSF metadata.

- **64 records:** the public report component's parent title starts `SCORE - `.
  For example, [the Wurst report component](https://osf.io/2be7n/) belongs to
  [SCORE – Wurst](https://osf.io/pn9mb/).
- **FLORA-002692:** [the report component](https://osf.io/cr6zm/) is public, but
  parent `w9ke5` requires authentication (HTTP 401). Its public
  [preregistration](https://osf.io/e2fsq/), listed in the same component, explicitly
  states `SCORE report Ma-Kellams_PsychologSci_2011_lkBL_618` on page 2.
  This establishes membership without guessing the private parent's title.

The intake routes were 63 `replications`, one `COS` (FLORA-002670), and one
`reproductions` (REPRO-000101). All receive the project label, consistent with
project precedence over intake. SCORE has 153 records in this release preview:
the original 88 intake records plus these 65; no further records were added by
shared-link expansion. There are no remaining unmatched project-name mentions.

## Separate outstanding corrections

Project membership does not establish inclusion in a particular published SCORE
paper. No Nature DOI has been assigned by this review. Per-record publication and
identifier linkage remains subject to the correction-log review in
[SCORE_LINKAGE.md](SCORE_LINKAGE.md).

PR #26 proposes report-byline corrections for 62 of these 65 records. The other
three have unresolved report authorship: FLORA-002648, FLORA-002655 and
FLORA-002692. Project contributors and preregistration team names are not
substituted for a report byline. Those corrections have not been applied here;
author-overlap percentages remain dependent on the authoritative metadata rebuild.
