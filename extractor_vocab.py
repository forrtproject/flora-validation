"""
extractor_vocab.py — the flora-extractor CSV's value vocabularies, in one place.

These sets used to be copy-pasted into csv_to_db.py, find_orphans.py and
cleanup_orphans.py. They drifted: the extractor renamed every link_method value,
the three copies were never updated, and because run_import treats an
unrecognised value exactly like "not yet resolved", the nightly import quietly
took 31 of 1890 eligible rows instead of failing. Nothing surfaced for five weeks.

Two rules follow from that:

  1. One definition per vocabulary. Import from here; never re-declare.
  2. check_csv_vocabulary() raises on a value nothing here knows about, so the
     next upstream rename fails the import loudly instead of shrinking it.

Retired values are kept beside current ones rather than deleted. data/ holds
dated CSV snapshots back to May, and find_orphans/cleanup_orphans read whichever
one is passed to --input; a retired value costs nothing (it simply never matches
a current CSV) and keeps those replays honest.

Messages raised from here stay ASCII: they surface through sync_csv.py's
traceback on an unattended nightly run, and a Windows console using cp1252 raises
UnicodeEncodeError on characters outside it (arrows, warning signs) — an error
that cannot print is worse than no error at all.
"""


class VocabularyDriftError(ValueError):
    """A CSV carries a value this module has never heard of.

    Raised instead of silently excluding the rows — that silence is the bug this
    module exists to prevent.
    """


# ---------------------------------------------------------------------------
# Paper type
# ---------------------------------------------------------------------------
# flora-extractor issue #93 renames the pipeline column filter_status ->
# paper_type. Our database column keeps the old name (record_metadata.
# filter_status), so this repo is the conversion point. Newest name first.
PAPER_TYPE_NAMES = ("paper_type", "filter_status")

RESOLVED_STATUSES = frozenset({"replication", "reproduction"})


# ---------------------------------------------------------------------------
# link_method — how the extractor identified the original study
# ---------------------------------------------------------------------------
# Rows carrying one of these are considered confidently linked and are imported
# for validation.
CURRENT_METHODS = frozenset({
    "llm_references",
    "llm_title_search",
    "llm_author_year_search",
    "llm_cited_candidates",
    "llm_fulltext",
    "title_pattern_match",
    "citation_context_match",
})

# Superseded upstream, still present in the dated CSVs under data/.
RETIRED_METHODS = frozenset({
    "author_year_match",
    "llm_abstract",
    "single_candidate_after_requery",
    "same_author_year_title_overlap",
})

RESOLVED_METHODS = CURRENT_METHODS | RETIRED_METHODS

# Known, and deliberately NOT imported: the extractor reached no confident link.
# Listed explicitly so check_csv_vocabulary() can tell "excluded on purpose"
# apart from "we have never seen this value", which is the whole point.
UNRESOLVED_METHODS = frozenset({
    "no_original_found",
    "target_pending",
    "api_error",
})

KNOWN_METHODS = RESOLVED_METHODS | UNRESOLVED_METHODS


# ---------------------------------------------------------------------------
# outcome
# ---------------------------------------------------------------------------
# The result held statistically, but the authors flag a methodological problem
# that undermines it. A category in its own right — not a flavour of 'successful'
# and not 'mixed' — and it applies to reproductions as well as replications, so it
# appears in both vocabularies below.
FLAWED_OUTCOME = "statistically_successful_but_flawed"

# The same category reaches us under three spellings from two sources: the
# extractor CSV writes FLAWED_OUTCOME, while the entry sheets
# (snapshots/replications.csv) carry two older forms — 8 rows between them, so
# none of these is hypothetical. All three are accepted and normalised onto the
# extractor's spelling, which is the one the app stores and displays.
FLAWED_OUTCOME_ALIASES = (
    "statistically successful but flawed",
    "statistically_successful_but_fundamentally_flawed",
)

# Translation applied at the import boundary, so only current spellings are ever
# stored. Three groups:
#   - the extractor's success/failure vs this app's successful/failed
#   - the reproduction relabelling ("computationally successful" ->
#     "computationally reproducible", "computation not checked" -> "not checked")
#   - the entry sheets' two spellings of the flawed category
# Normalising on the way in — rather than widening the CHECK constraint to accept
# every spelling — is what lets an archived CSV replay without reintroducing the
# old labels the schema migration just cleaned up.
OUTCOME_RENAME = {
    "success": "successful",
    "failure": "failed",
    "computationally successful, robust":
        "computationally reproducible, robust",
    "computationally successful, robustness challenges":
        "computationally reproducible, robustness challenges",
    "computation not checked, robust":
        "not checked, robust",
    "computation not checked, robustness challenges":
        "not checked, robustness challenges",
    **{alias: FLAWED_OUTCOME for alias in FLAWED_OUTCOME_ALIASES},
}

REPLICATION_OUTCOMES = frozenset({
    "successful",
    "failed",
    "mixed",
    "uninformative",
    "descriptive",
    "cannot_be_determined",
    FLAWED_OUTCOME,
})

# Reproductions are coded on two axes (computational x robustness) that the
# extractor still also ships pre-joined into this single string. The FLoRA
# codebook has since settled on the two axes as the coded fields, each with its
# own quote and source — until that lands (see docs/PROJECT.md) the joined string
# is what gets stored, so it needs a vocabulary.
REPRODUCTION_OUTCOMES = frozenset({
    "computationally reproducible, robust",
    "computationally reproducible, robustness challenges",
    "computationally reproducible, not checked",
    "computational issues, robust",
    "computational issues, robustness challenges",
    "not checked, robust",
    "not checked, robustness challenges",
    # Two labels that are not (computational, robustness) pairs. The extractor
    # writes them bare rather than as a pair, so they sit alongside the grid —
    # both are shared with the replication vocabulary above.
    "cannot_be_determined",
    FLAWED_OUTCOME,
})

# Everything the `unvalidated_outcome_check` CHECK constraint in db_schema.sql
# accepts. Keep the two in step: llm_validator.py shows the model this same
# vocabulary, and a value the schema rejects would fail the whole import
# transaction, not just its own row.
STORED_OUTCOMES = REPLICATION_OUTCOMES | REPRODUCTION_OUTCOMES

# What a CSV may legitimately contain: the stored vocabulary plus the older
# spellings OUTCOME_RENAME knows how to translate.
KNOWN_CSV_OUTCOMES = STORED_OUTCOMES | frozenset(OUTCOME_RENAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(value) -> str:
    """Coerce to a stripped string; NaN/None become ''."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()


def paper_type_column(df):
    """The paper-type column of *df*, under whichever name the CSV carries."""
    for name in PAPER_TYPE_NAMES:
        if name in df.columns:
            return df[name]
    raise KeyError(
        "the CSV has no paper-type column "
        f"(looked for {', '.join(PAPER_TYPE_NAMES)})"
    )


def paper_type(row) -> str:
    """One row's paper type. Selects the column the same way paper_type_column
    does — by presence, newest name first — so a blank newer column doesn't
    silently fall through to the older one and disagree with the frame-level
    filter."""
    for name in PAPER_TYPE_NAMES:
        if name in row:
            return _clean(row[name])
    return ""


def normalize_outcome(value) -> str:
    """The stored spelling of a CSV outcome. Exact-match only."""
    cleaned = _clean(value)
    return OUTCOME_RENAME.get(cleaned, cleaned)


def resolved_mask(df):
    """Boolean mask over *df*: the rows ready to import."""
    return paper_type_column(df).isin(RESOLVED_STATUSES) & df["link_method"].isin(RESOLVED_METHODS)


def check_csv_vocabulary(df) -> None:
    """Raise VocabularyDriftError if *df* carries a link_method or outcome this
    module doesn't recognise.

    link_method is checked over every row, because an unknown value there is
    exactly the drift that shrinks the import. outcome is checked only over the
    rows that would actually be imported — a false_positive row's outcome never
    reaches the database, so it isn't ours to police.
    """
    problems = []

    unknown_methods = {
        m for m in (_clean(v) for v in df["link_method"].unique())
        if m and m not in KNOWN_METHODS
    }
    if unknown_methods:
        counts = df["link_method"].value_counts()
        listed = ", ".join(
            f"{m!r} ({counts.get(m, 0)} rows)" for m in sorted(unknown_methods)
        )
        problems.append(
            f"unrecognised link_method value(s): {listed}.\n"
            "    -> If the extractor renamed one, add it to CURRENT_METHODS and move the\n"
            "      old name to RETIRED_METHODS in extractor_vocab.py. If these rows are\n"
            "      not meant to be validated, add it to UNRESOLVED_METHODS instead."
        )

    importable = df[resolved_mask(df)]
    if "outcome" in importable.columns:
        unknown_outcomes = {
            o for o in (normalize_outcome(v) for v in importable["outcome"].unique())
            if o and o not in STORED_OUTCOMES
        }
        if unknown_outcomes:
            counts = importable["outcome"].value_counts()
            listed = ", ".join(
                f"{o!r} ({counts.get(o, 0)} rows)" for o in sorted(unknown_outcomes)
            )
            problems.append(
                f"unrecognised outcome value(s) on importable rows: {listed}.\n"
                "    -> Add each to REPLICATION_OUTCOMES or REPRODUCTION_OUTCOMES in\n"
                "      extractor_vocab.py AND to the unvalidated_outcome_check constraint\n"
                "      in db_schema.sql, or map an old spelling via OUTCOME_RENAME.\n"
                "      The two must agree or the import transaction will roll back."
            )

    if problems:
        raise VocabularyDriftError(
            "The extractor CSV uses values this importer does not know:\n\n  "
            + "\n\n  ".join(problems)
            + "\n\nRefusing to import rather than silently skipping the affected rows."
        )
