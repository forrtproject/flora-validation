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
KNOWN_PAPER_TYPES = RESOLVED_STATUSES | frozenset({"false_positive", "needs_review"})

KNOWN_RECORD_TYPES = frozenset({"replication", "reproduction"})
KNOWN_CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
KNOWN_ORIGINAL_MATCH_TYPES = frozenset({
    "single_original", "multiple_original", "multiple_match",
})
KNOWN_DOI_VERIFICATION_VALUES = frozenset({
    "verified", "corrected", "mismatch", "no_doi", "not_found",
    "no_metadata", "api_error", "skipped",
})
KNOWN_SOURCE_VALUES = frozenset({
    "openalex", "openalex_concept", "openalex_snapshot", "semantic_scholar",
    "backfill_old_pipeline", "bob_reed", "i4r",
})


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
    "same_author_year_title_overlap",
    "single_candidate_after_requery",
    "grobid_ref_match",
})

# Superseded upstream, still present in the dated CSVs under data/.
RETIRED_METHODS = frozenset({
    "author_year_match",
    "llm_abstract",
})

RESOLVED_METHODS = CURRENT_METHODS | RETIRED_METHODS

# Known, and deliberately NOT imported: the extractor reached no confident link.
# Listed explicitly so check_csv_vocabulary() can tell "excluded on purpose"
# apart from "we have never seen this value", which is the whole point.
UNRESOLVED_METHODS = frozenset({
    "unidentified_original",
    "keyed_link_disputed",
    "author_year_match_legacy",
    "no_original_found",
    "not_a_replication",
    "prescreen_discard",
    "screen_disagreement",
    "target_pending",
    "api_error",
})

KNOWN_METHODS = RESOLVED_METHODS | UNRESOLVED_METHODS


# ---------------------------------------------------------------------------
# outcome
# ---------------------------------------------------------------------------
# The result held statistically, but the authors flag a methodological problem
# that undermines it. It is a replication category in its own right — not a
# flavour of 'successful' and not 'mixed'.
FLAWED_OUTCOME = "statistically successful but flawed"
DESCRIPTIVE_OUTCOME = "descriptive only"

# The same category reaches us under three spellings from two sources: the
# extractor CSV writes FLAWED_OUTCOME, while the entry sheets
# (snapshots/replications.csv) carry two older forms — 8 rows between them, so
# none of these is hypothetical. All three are accepted and normalised onto the
# extractor's spelling, which is the one the app stores and displays.
FLAWED_OUTCOME_ALIASES = (
    "statistically_successful_but_flawed",
    "statistically_successful_but_fundamentally_flawed",
)

# Retired STORED spelling -> current stored spelling, applied at the import
# boundary so only one spelling ever reaches the database. The map also carries
# one live descriptive-label alias; API-error rows are handled separately below.
#
# Reproduction axis aliases are handled separately by AXIS_VALUE_ALIASES, so all
# old grid spellings derive to the same canonical flat value.
#
# Normalising on the way in — rather than widening the CHECK constraint to accept
# every spelling — is what lets an archived CSV replay without reintroducing the
# old labels the schema migration just cleaned up.
OUTCOME_RENAME = {
    "success": "successful",
    "failure": "failed",
    "descriptive": DESCRIPTIVE_OUTCOME,
    **{alias: FLAWED_OUTCOME for alias in FLAWED_OUTCOME_ALIASES},
}

REPLICATION_OUTCOMES = frozenset({
    "successful",
    "failed",
    "mixed",
    "uninformative",
    DESCRIPTIVE_OUTCOME,
    "cannot_be_determined",
    "not_a_replication",
    FLAWED_OUTCOME,
})

# Older joined spellings accepted for archived CSV replay. Their axis halves are
# normalized before deriving the current flat 4×3 outcome.
LEGACY_JOINED_OUTCOMES = frozenset({
    "computational issues, robustness not checked",
    # Pre-relabelling spellings, still present in the dated CSVs under data/.
    "computationally successful, robust",
    "computationally successful, robustness challenges",
    "computationally successful, robustness not checked",
    "computation not checked, robust",
    "computation not checked, robustness challenges",
    "computation not checked, robustness not checked",
})

# The original was found, but outcome extraction failed. Keep the row available
# to human validation while storing NULL rather than a value the database cannot
# classify. This is distinct from an unknown vocabulary value, which must still
# stop the sync.
NON_STORED_OUTCOMES = frozenset({"api_error"})

# ---------------------------------------------------------------------------
# Reproduction outcome axes
# ---------------------------------------------------------------------------
# The FLoRA codebook codes reproductions on two INDEPENDENT axes. Independent is
# the operative word: a reproduction can fail computationally and still find the
# conclusion robust, so neither axis can be derived from the other. The flat
# outcome is derived only after both axes have been resolved.
OUTCOME_COMPUTATION_VALUES = frozenset({
    "computationally reproducible",
    "computational issues",
    # New in the codebook, not yet emitted: the re-analysis was defeated by
    # missing or unusable data/code. Declared ahead of its first appearance so
    # that row imports instead of tripping check_csv_vocabulary().
    "technical failure",
    "not checked",
    "cannot_be_determined",
})

OUTCOME_ROBUSTNESS_VALUES = frozenset({
    "robust",
    "robustness challenges",
    "not checked",
    "cannot_be_determined",
})

# Which CSV column holds which axis vocabulary. Drives the drift check below, so
# adding an axis is one entry here rather than another branch.
OUTCOME_AXES = {
    "outcome_computation": OUTCOME_COMPUTATION_VALUES,
    "outcome_robustness": OUTCOME_ROBUSTNESS_VALUES,
}

# The axes remain the independently coded fields. The flat reproduction outcome
# is a derived convenience value retained by the extractor for sorting, dashboard
# summaries, and export. Four settled computation values by three settled
# robustness values produce the authoritative twelve combinations.
CURRENT_REPRODUCTION_OUTCOMES = frozenset(
    f"{computation}, {robustness}"
    for computation in OUTCOME_COMPUTATION_VALUES - {"cannot_be_determined"}
    for robustness in OUTCOME_ROBUSTNESS_VALUES - {"cannot_be_determined"}
)
REPRODUCTION_OUTCOMES = (
    CURRENT_REPRODUCTION_OUTCOMES
    | frozenset({"cannot_be_determined", "not_a_replication"})
)
STORED_OUTCOMES = REPLICATION_OUTCOMES | CURRENT_REPRODUCTION_OUTCOMES

# What a CSV may legitimately contain: the stored vocabulary plus the older
# spellings OUTCOME_RENAME knows how to translate.
KNOWN_CSV_OUTCOMES = (
    STORED_OUTCOMES
    | frozenset(OUTCOME_RENAME)
    | LEGACY_JOINED_OUTCOMES
    | NON_STORED_OUTCOMES
)


def stored_outcome(value, record_type, axes_coded: bool = False,
                   computation=None, robustness=None) -> "str | None":
    """Return the canonical flat outcome stored alongside the coded fields.

    Reproduction axes are authoritative. Their settled 4×3 combinations are
    derived into the extractor's flat label, while an incomplete or undetermined
    axis pair becomes ``cannot_be_determined``. Older joined strings are accepted
    only as a compatibility fallback when axis columns are absent.
    """
    normalized = normalize_outcome(value)
    if normalized in NON_STORED_OUTCOMES:
        return None
    if _clean(record_type) == "reproduction":
        if axes_coded or _clean(computation) or _clean(robustness):
            return derive_reproduction_outcome(computation, robustness)
        old_computation, old_robustness = split_joined_outcome(normalized)
        if old_computation and old_robustness:
            return derive_reproduction_outcome(old_computation, old_robustness)
        return normalized if normalized in REPRODUCTION_OUTCOMES else None
    return normalized or None


# Retired spellings of the individual axis values, per axis. Translating the two
# halves separately, rather than the joined string as a whole, covers every
# combination — including pairings a whole-string map never had an entry for, which
# is exactly where a whole-string lookup silently gave up.
#
# Also used by llm_validator to accept a model answering in the old words: the
# spellings read as natural English either way, and a real judgement should not be
# discarded over vocabulary churn.
AXIS_VALUE_ALIASES = {
    "outcome_computation": {
        "computationally successful": "computationally reproducible",
        "computation not checked": "not checked",
    },
    "outcome_robustness": {
        "robustness not checked": "not checked",
    },
}
_COMPUTATION_ALIASES = AXIS_VALUE_ALIASES["outcome_computation"]
_ROBUSTNESS_ALIASES = AXIS_VALUE_ALIASES["outcome_robustness"]


def derive_reproduction_outcome(computation, robustness) -> str:
    """Derive the extractor's flat reproduction outcome from both axes."""
    computation = _COMPUTATION_ALIASES.get(_clean(computation), _clean(computation))
    robustness = _ROBUSTNESS_ALIASES.get(_clean(robustness), _clean(robustness))
    settled_computation = OUTCOME_COMPUTATION_VALUES - {"cannot_be_determined"}
    settled_robustness = OUTCOME_ROBUSTNESS_VALUES - {"cannot_be_determined"}
    if computation not in settled_computation or robustness not in settled_robustness:
        return "cannot_be_determined"
    return f"{computation}, {robustness}"


def split_joined_outcome(value) -> "tuple[str | None, str | None]":
    """A joined reproduction outcome as (computation, robustness), or (None, None).

    Used by the schema migration to recover already-stored joined values into the
    axis columns and by legacy CSV imports without axis values. Returns (None, None)
    for anything that is not a grid pair.
    """
    cleaned = _clean(value)
    if ", " not in cleaned:
        return None, None
    computation, _, robustness = cleaned.partition(", ")
    computation = _COMPUTATION_ALIASES.get(computation, computation)
    robustness = _ROBUSTNESS_ALIASES.get(robustness, robustness)
    if computation not in OUTCOME_COMPUTATION_VALUES or robustness not in OUTCOME_ROBUSTNESS_VALUES:
        return None, None
    return computation, robustness


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


def normalize_axis_value(column: str, value) -> "str | None":
    """Return one canonical reproduction-axis value.

    Blank input means "not supplied" and returns ``None``. A non-blank value must
    belong to the named axis after applying the archived spelling aliases; raising
    here gives API and migration callers one strict boundary instead of allowing an
    arbitrary string to travel until a PostgreSQL CHECK constraint rejects it.
    """
    cleaned = _clean(value)
    if not cleaned:
        return None
    if column not in OUTCOME_AXES:
        raise ValueError(f"unknown reproduction axis: {column}")
    normalized = AXIS_VALUE_ALIASES.get(column, {}).get(cleaned, cleaned)
    if normalized not in OUTCOME_AXES[column]:
        allowed = ", ".join(sorted(OUTCOME_AXES[column]))
        raise ValueError(
            f"invalid {column} value {cleaned!r}; expected one of: {allowed}"
        )
    return normalized


# Where a quote came from. The extractor names a section (abstract, discussion,
# results, …) and sometimes several, pipe-joined, when the quote spans them.
QUOTE_SOURCE_SEPARATOR = "|"


def normalize_quote_source(value) -> str:
    """Tidy a quote-source value: one separator, no repeats, no stray spacing.

    Two upstream quirks this absorbs, both agreed as ours to handle:

      - 'abstract | abstract' — a self-join carrying no more information than
        'abstract'. The joining step upstream does not deduplicate.
      - two separator conventions in one file: out_quote_source uses ' | ' (34
        rows) while screen_categories uses '|' (1238). Consumers would otherwise
        need two parsers, and one of them would eventually be written wrong. The
        higher-volume convention wins.

    Order is preserved rather than sorted: 'title | abstract' may well encode
    where the quote starts, and that is not ours to reorder.
    """
    cleaned = _clean(value)
    if not cleaned:
        return ""
    seen, parts = set(), []
    for part in cleaned.split(QUOTE_SOURCE_SEPARATOR):
        part = part.strip().lower()
        if part and part not in seen:
            seen.add(part)
            parts.append(part)
    return QUOTE_SOURCE_SEPARATOR.join(parts)


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

    paper_types = paper_type_column(df)
    unknown_types = {
        t for t in (_clean(v) for v in paper_types.unique())
        if t and t not in KNOWN_PAPER_TYPES
    }
    if unknown_types:
        counts = paper_types.value_counts()
        listed = ", ".join(
            f"{t!r} ({counts.get(t, 0)} rows)" for t in sorted(unknown_types)
        )
        problems.append(
            f"unrecognised paper type value(s): {listed}.\n"
            "    -> Add the new value to KNOWN_PAPER_TYPES in extractor_vocab.py "
            "or map the upstream spelling before importing."
        )

    categorical_contracts = {
        "type": KNOWN_RECORD_TYPES,
        "original_match_type": KNOWN_ORIGINAL_MATCH_TYPES,
        "doi_o_verification": KNOWN_DOI_VERIFICATION_VALUES,
        "source": KNOWN_SOURCE_VALUES,
        "filter_confidence": KNOWN_CONFIDENCE_VALUES,
        "original_match_confidence": KNOWN_CONFIDENCE_VALUES,
        "link_confidence": KNOWN_CONFIDENCE_VALUES,
        "outcome_confidence": KNOWN_CONFIDENCE_VALUES,
    }
    for column, allowed in categorical_contracts.items():
        if column not in df.columns:
            continue
        unknown = {
            value for value in (_clean(raw) for raw in df[column].unique())
            if value and value not in allowed
        }
        if unknown:
            counts = df[column].value_counts()
            listed = ", ".join(
                f"{value!r} ({counts.get(value, 0)} rows)"
                for value in sorted(unknown)
            )
            problems.append(f"unrecognised {column} value(s): {listed}.")

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
        # Checked against KNOWN_CSV_OUTCOMES, not STORED_OUTCOMES, so archived
        # reproduction spellings can be normalized before storage.
        unknown_outcomes = {
            o for o in (normalize_outcome(v) for v in importable["outcome"].unique())
            if o and o not in KNOWN_CSV_OUTCOMES
        }
        if unknown_outcomes:
            counts = importable["outcome"].value_counts()
            listed = ", ".join(
                f"{o!r} ({counts.get(o, 0)} rows)" for o in sorted(unknown_outcomes)
            )
            problems.append(
                f"unrecognised outcome value(s) on importable rows: {listed}.\n"
                "    -> Add each to REPLICATION_OUTCOMES in extractor_vocab.py AND to the\n"
                "      unvalidated_outcome_check constraint in db_schema.sql, or map an old\n"
                "      spelling via OUTCOME_RENAME. The two must agree or the import\n"
                "      transaction will roll back. Reproduction axis values belong in\n"
                "      OUTCOME_AXES; their flat 4×3 labels are derived."
            )

    # The two reproduction axes. Checked only over importable rows for the same
    # reason as outcome, and only when the column exists — a CSV predating the
    # codebook change carries neither.
    for column, allowed in OUTCOME_AXES.items():
        if column not in importable.columns:
            continue
        unknown_axis = {
            v for v in (_clean(x) for x in importable[column].unique())
            if v and v not in allowed
        }
        if unknown_axis:
            counts = importable[column].value_counts()
            listed = ", ".join(
                f"{v!r} ({counts.get(v, 0)} rows)" for v in sorted(unknown_axis)
            )
            problems.append(
                f"unrecognised {column} value(s): {listed}.\n"
                f"    -> Add each to extractor_vocab.OUTCOME_AXES['{column}'] AND to the\n"
                f"       matching CHECK constraint in db_schema.sql. The two must agree or\n"
                f"       the import transaction will roll back."
            )

    if problems:
        raise VocabularyDriftError(
            "The extractor CSV uses values this importer does not know:\n\n  "
            + "\n\n  ".join(problems)
            + "\n\nRefusing to import rather than silently skipping the affected rows."
        )
