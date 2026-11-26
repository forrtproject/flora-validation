"""
transform_sources.py — turn reviewed source_records into the FLoRA dataset.

Stage 11 of the entry-sheet pipeline. source_records is a *record*: what the
sheet said, stored dirty on purpose and never edited by hand. This produces
the *product*: cleaned, deduplicated, narrowed to the columns FLoRA consumes.
Corrections are made to the product, not the record: flora_edits.py stores them
per FLoRA record and applies them on top of every build.

The build() function derives the product without changing source data. Improving
a cleaning rule and re-running applies the new behaviour to every row. Normal
CLI exports also register permanent publication IDs before writing the CSV;
--stats-only keeps the whole operation read-only.

Seven operations:
  1. normalise/derive the outcome (reproductions derive it from their two axes)
  2. clean DOIs                 (prefixes, whitespace, trailing garbage)
  3. strip redundant url_r      (~86% are just doi.org/<doi_r>)
  4. apply exclusions           (transform_exclusions table)
  5. dedup                      (reviewer decisions + identifier collapse)
  6. join cached bibliographic metadata (enrich_works.py)
  7. project to the FLoRA output contract

Usage:
    python transform_sources.py
    python transform_sources.py --output output/flora_entry_sheets.csv
    python transform_sources.py --stats-only

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import hashlib
import html
import os
import re
import sys
import unicodedata
import urllib.parse
from html.entities import html5
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
import apa_references
import author_overlap
import cos_quote_rewrite
import preprint_dedup
import final_export
from extractor_vocab import (
    REPLICATION_OUTCOMES,
    derive_reproduction_outcome,
    normalize_axis_value,
)

load_dotenv()

# Progress output below uses non-ASCII glyphs; a cp1252 console cannot encode
# them and print() would abort the run. See console_encoding.py.
use_utf8_output()

ROOT = Path(__file__).parent
DEFAULT_OUTPUT = ROOT / "output" / "flora_entry_sheets.csv"

from pipeline.schema import (  # noqa: F401
    DERIVED_COLUMNS,
    DUMMY_DOI_RE,
    ENRICHMENT_COLUMNS,
    FLORA_COLUMNS,
    FLORA_OUTPUT_COLUMNS,
    IDENTIFIER_SEPARATOR,
    JOINED_COLUMNS,
    JOIN_FALLBACKS,
    META_PAPER_CANDIDATE_URLS,
    META_PAPER_DOIS,
    META_PAPER_NOTE,
    OUTPUT_EXTRAS,
    OUTPUT_RENAMES,
    OUTPUT_SOURCES,
    PARAGRAPH_COLUMNS,
    PROVENANCE_COLUMNS,
    REVIEW_COLUMNS,
    WORK_ID_HINT,
    _CP1252,
    _DOI_URL_RE,
    _ENTITY_RE,
    _IDENTIFIER_SPLIT_RE,
    _MAC_ROMAN,
    _MOJIBAKE_PASSES,
    _MOJIBAKE_RE,
    _OPENALEX_WORK_RE,
    _PARAGRAPH_BREAK_RE,
    _SHEET_YEAR_RE,
    _SOFT_BREAK_RE,
    _TAG_NAME_RE,
    _TAG_RE,
    _TAG_RUN_RE,
    _TO_BYTE,
    _TRAILING_FOOTNOTE_RE,
    _UNESCAPE_PASSES,
    _WHOLE_ENTITY_RE,
    _WS_RE,
    _decode_whole_entity,
    _doi_url,
    _identifier_key,
    _identifiers,
    _join_axes,
    _join_parts,
    _lower_unknown_entity,
    _mojibake_alphabet,
    _mojibake_pattern,
    _replace_tags,
    _s,
    _sheet_year,
    _undo_mojibake,
    _without_tracking,
    clean_doi,
    clean_text,
    decode_doi_escapes,
    dedup_keys,
    derive_quote,
    derive_quote_source,
    doi_hash,
    drop_untitled,
    has_identifier,
    has_mojibake,
    matching_exclusion,
    missing_title_report,
    norm_url,
    normalise_identifier_list,
    openalex_work_id,
    openalex_work_ids,
    record_keys,
    repair_mojibake,
    strip_redundant_url,
    to_output_shape,
    unescape_entities,
)
from pipeline.transform.context import BuildContext
from pipeline.transform.stages import (
    stage_clean_and_finalize,
    stage_dois_and_confirmed_resolutions,
    stage_enrich_and_preprint_dedup,
    stage_exact_dedup_and_pair_merge,
    stage_exclusions_and_redundant_urls,
    stage_identifier_corrections_and_quotes,
    stage_outcomes_and_quotes,
)


def load(cur):
    """The source rows. Anything ruled a duplicate is left behind here, and so is a
    row marked deleted (a record no longer validated).

    In display_id order, except that rows added by hand (manual_records.py) come
    last: the duplicate steps keep each group's first row, so one never takes over
    a record another source holds, whatever its prefix sorts as. It is absorbed
    instead, filling only blanks, unless it is ruled distinct. (A hand record that
    is already a FLoRA record keeps it: build() moves it ahead within its group.)"""
    cur.execute(
        """
        SELECT record_id::text AS record_id, display_id, source, type,
               doi_o, ref_o, url_o, doi_r, ref_r, url_r, abstract_r,
               outcome, outcome_quote, out_quote_source,
               outcome_computation, outcome_computational_quote, out_quote_computational_source,
               outcome_robustness, outcome_robustness_quote, out_quote_robust_source,
               year_r,
               study_o, alt_identifier_o, alt_identifier_r,
               validation_status, reviewed_by, duplicate_status
        FROM source_records
        WHERE duplicate_status IS DISTINCT FROM 'duplicate'
          AND deleted_at IS NULL
        ORDER BY (source = %s), display_id
        """,
        (preprint_dedup.MANUAL_SOURCE,),
    )
    return pd.DataFrame([dict(r) for r in cur.fetchall()])


def load_rules(cur):
    cur.execute("SELECT raw_value, canonical_value FROM outcome_alias")
    aliases = {r["raw_value"]: r["canonical_value"] for r in cur.fetchall()}

    cur.execute("SELECT doi_o, doi_r, url_r, reason FROM transform_exclusions")
    exclusions = [dict(r) for r in cur.fetchall()]
    return aliases, exclusions


def load_live_primaries(cur) -> set:
    """The record_ids that are the source row of a live FLoRA record (flora_records).

    Guarded like the rulings: the nightly job can reach a database before the site's
    init_db() has created the table."""
    cur.execute("SELECT to_regclass('flora_records') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        return set()
    cur.execute("SELECT primary_source_record_id::text AS sid FROM flora_records "
                "WHERE retired_at IS NULL")
    return {row["sid"] for row in cur.fetchall()}


def load_dedup_decisions(cur):
    """Admin rulings on preprint duplicate pairs, made in the FLoRA tab.

    Guarded: the nightly job runs from the repository and can reach the database
    before the deployed site's init_db() has created the table.
    """
    cur.execute("SELECT to_regclass('preprint_dedup_decisions') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        return []
    cur.execute("SELECT side, doi_1, doi_2, action, decided_at, decided_by FROM preprint_dedup_decisions")
    return [dict(r) for r in cur.fetchall()]


def load_cross_type_duplicates(cur):
    """Rows ruled a duplicate of a row of the OTHER type, which load() leaves out.

    The Source Records duplicate detector keys on the paper, not the type, so a
    replication and a reproduction of one paper arrive there as a duplicate group.
    Usually both are valid records; a 'duplicate' ruling between them removes one
    from FLoRA. Listed so every run surfaces them for a second look.
    """
    cur.execute(
        """
        SELECT d.display_id, d.type, d.doi_o, d.doi_r, d.url_r,
               s.display_id AS duplicate_of, s.type AS duplicate_of_type,
               d.duplicate_reviewed_by
        FROM source_records d
        JOIN source_records s ON s.record_id = d.duplicate_of
        WHERE d.duplicate_status = 'duplicate' AND d.type IS DISTINCT FROM s.type
          AND d.deleted_at IS NULL AND s.deleted_at IS NULL
        ORDER BY d.display_id
        """
    )
    return [dict(r) for r in cur.fetchall()]


def _canonical_axis(row, column, problems):
    """Canonical axis value for export, recording rather than hiding bad data."""
    if row.get("type") != "reproduction":
        return None
    try:
        return normalize_axis_value(column, row.get(column))
    except ValueError:
        problems.setdefault("invalid_axis", []).append(
            (row.get("display_id"), column, row.get(column))
        )
        return None


def derive_outcome(row, aliases, problems):
    """The `outcome` string for a row, or None when the row has none.

    Replications carry `outcome` and only need spelling normalised.

    Reproduction axes remain the authoritative coded fields. The flat outcome is
    deterministically derived from their settled 4×3 grid for compatibility with
    the extractor schema; an incomplete or undetermined pair is represented as
    ``cannot_be_determined``.

    Nothing unrecognised is passed through. An outcome spelling absent from
    outcome_alias used to fall through to the export verbatim, which is how a
    vocabulary change reaches published data without anyone noticing — the same
    silent-drift failure that cost five weeks on the extractor side (see
    extractor_vocab.py). Unknown values are collected and reported instead, and
    run() refuses to write once any turned up.
    """
    if row["type"] == "reproduction":
        computation = _canonical_axis(row, "outcome_computation", problems)
        robustness = _canonical_axis(row, "outcome_robustness", problems)
        return derive_reproduction_outcome(
            computation, robustness
        )
    raw = row.get("outcome")
    # RealDictCursor returns SQL NULL as None, but once the records enter a
    # DataFrame pandas promotes missing values in this column to float NaN.
    # NaN is truthy, so `if not raw` misclassified an ordinary blank as an
    # unknown literal outcome and stopped the nightly export.
    if raw is None or (not isinstance(raw, str) and pd.isna(raw)):
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    if raw not in aliases:
        problems["unknown_alias"].append((row["display_id"], raw))
        return None
    canonical = aliases[raw]
    # A bad alias row is as damaging as a missing one: it silently republishes a
    # real outcome as the wrong category. Catch a canonical value the rest of the
    # app would reject.
    if canonical not in REPLICATION_OUTCOMES:
        problems["bad_alias"].append((row["display_id"], raw, canonical))
        return None
    return canonical




def join_metadata(df, meta, sides=("o", "r")):
    """Step 7: join cached bibliographic metadata onto df, in place, per side.

    Keyed on each side's DOI, then on the OpenAlex work id in its url column for
    works with no DOI. Every metadata column is replaced; the JOIN_FALLBACKS columns
    must hold the source's values, which are kept where no provider answers.
    """
    from enrich_works import extract_work_id        # local: avoids an import cycle
    from bibliographic_helpers import add_reference_authors, normalise_author_json, normalise_key

    def authors_present(value):
        return isinstance(value, (str, list)) and bool(value)
    for side in sides:
        doi_col = f"doi_{side}"
        keys = df[doi_col].map(lambda d: meta.get(normalise_key(d)) if d else None)
        for field, column in (("title", f"title_{side}"), ("authors", f"author_{side}"),
                              ("journal", f"journal_{side}"), ("volume", f"volume_{side}"),
                              ("issue", f"issue_{side}"), ("pages", f"pages_{side}"),
                              ("language", f"language_{side}"), ("oa_url", f"oa_url_{side}"),
                              ("oa_work_id", f"oa_work_id_{side}"),
                              ("bibtex_ref", f"bibtex_ref_{side}")):
            df[column] = keys.map(lambda m, f=field: (m or {}).get(f))

        # Prefer provider-formatted citations and structured Crossref authors;
        # retain source-sheet references when no provider can resolve the work.
        df[f"author_{side}"] = keys.map(lambda m: (m or {}).get("authors_json")).fillna(df[f"author_{side}"])
        df[f"ref_{side}"] = keys.map(lambda m: (m or {}).get("apa_ref")).fillna(df[f"ref_{side}"])
        if side == "r":
            df["abstract_r"] = df["abstract_r"].fillna(keys.map(lambda m: (m or {}).get("abstract")))

        # year is the one field we may already hold: the replications sheet carries
        # one. Metadata wins (it is the published year, the sheet's is hand-entered),
        # but the sheet's is kept where the DOI resolved to nothing — the R pipeline
        # drops the sheet value entirely and ends up with a blank.
        # Held back until after 7b rather than applied here: "metadata wins" means
        # the work-id lookup below wins too, and filling the sheet value first would
        # make its fillna a no-op on the 17 rows that have both.
        sheet_year = (df[f"year_{side}"].map(_sheet_year)
                      if f"year_{side}" in df.columns else None)
        df[f"year_{side}"] = keys.map(lambda m: (m or {}).get("year"))

        # 7b ── works with no DOI, only an OpenAlex link (notebook Step 7b)
        # Theses and working papers are recorded as https://openalex.org/W… in the
        # url column. Keyed on the W-id, cached by enrich_works alongside the DOI
        # rows. Only fills what the DOI join left empty, so a real DOI always wins.
        url_column = f"url_{side}"
        if url_column in df.columns:
            id_keys = df[url_column].map(
                lambda u: (meta.get(normalise_key(u)) or meta.get(extract_work_id(u))) if u else None)
            # The OpenAlex record can also be named elsewhere: in the alternative
            # identifiers, or, for a url corrected from the OpenAlex link to the
            # work's landing page, as the record the correction replaced (step 6c).
            # It remains the key to the metadata of a work with no DOI.
            # Only for a side with no DOI: the DOI join describes the work, and an
            # alias's metadata would fill its gaps with another version's authors.
            no_doi = df[doi_col].map(lambda d: not _s(d))
            for column in (WORK_ID_HINT.format(side=side), f"alt_identifier_{side}"):
                if column in df.columns:
                    named = df[column].map(lambda v: next(
                        (meta[w] for w in openalex_work_ids(v) if w in meta), None))
                    id_keys = id_keys.where(id_keys.notna() | ~no_doi, named)
            for field, column in (("title", f"title_{side}"),
                                  ("authors", f"author_{side}"),
                                  ("journal", f"journal_{side}"),
                                  ("volume", f"volume_{side}"),
                                  ("issue", f"issue_{side}"),
                                  ("pages", f"pages_{side}"),
                                  ("language", f"language_{side}"),
                                  ("oa_url", f"oa_url_{side}"),
                                  ("oa_work_id", f"oa_work_id_{side}"),
                                  ("bibtex_ref", f"bibtex_ref_{side}"),
                                  ("year", f"year_{side}")):
                from_id = id_keys.map(lambda m, f=field: (m or {}).get(f))
                df[column] = df[column].fillna(from_id)
            # URL-only manual entries and OSF citations apply on both sides.
            no_doi_citation = keys.map(lambda m: not bool((m or {}).get("apa_ref")))
            from_citation = id_keys.map(lambda m: (m or {}).get("apa_ref"))
            df.loc[no_doi_citation, f"ref_{side}"] = from_citation[no_doi_citation].combine_first(df.loc[no_doi_citation, f"ref_{side}"])
            from_authors = id_keys.map(lambda m: (m or {}).get("authors_json"))
            no_doi_authors = keys.map(lambda m: not bool((m or {}).get("authors_json") or (m or {}).get("authors")))
            df.loc[no_doi_authors, f"author_{side}"] = from_authors[no_doi_authors].fillna(df.loc[no_doi_authors, f"author_{side}"])

        # …and only now the sheet's own year, for the rows neither lookup answered.
        if sheet_year is not None:
            df[f"year_{side}"] = df[f"year_{side}"].fillna(sheet_year)

        # A cached reference that opens with "(Ed.). (2010)." lacks its author: DOI
        # content negotiation drops group authors and works deposited without any.
        # The author list joined above supplies it where one is known.
        # Authors read as the export will publish them: OpenAlex supplies display
        # names, which normalise_author_json structures, keeping a group whole.
        df[f"ref_{side}"] = [
            add_reference_authors(ref, normalise_author_json(authors, preserve_unresolved=True)
                                  if authors_present(authors) else None)
            for ref, authors in zip(df[f"ref_{side}"], df[f"author_{side}"])]

        if side == "o":
            # R's formatted metadata includes the original DOI landing page.
            df["url_o"] = df["url_o"].fillna(df["doi_o"].map(_doi_url))


def join_metadata_again(df, rows, side, meta, fallbacks, kept_urls=()):
    """Join `rows` of df to the metadata again for one side, in place.

    For rows whose identifier changed after the join (steps 6b and 6c): every column
    the join writes then describes the new identifier, exactly as for a row that had
    it from the start. `fallbacks` is what the source supplied for JOIN_FALLBACKS, by
    record_id. `kept_urls` are the record_ids whose url column is itself the new
    identifier, which stays as it is.
    """
    part = df.loc[rows].copy()
    for column in JOIN_FALLBACKS:
        if column.endswith(f"_{side}"):
            fallback = part["record_id"].map(fallbacks[column]) \
                if column in fallbacks else None
            if column == f"url_{side}":
                # A corrected URL is the identifier itself; only the derived
                # landing page goes back to what the source supplied.
                fallback = part[column].where(part["record_id"].isin(kept_urls), fallback)
            part[column] = fallback
    if side == "o":
        part["year_o"] = None               # the sheets carry no original year
    join_metadata(part, meta, sides=(side,))
    for column in JOINED_COLUMNS[side]:
        if column not in part.columns:
            continue
        if column not in df.columns:
            df[column] = None
        if df[column].dtype.kind in "biufc" and part[column].dtype.kind not in "biufc":
            df[column] = df[column].astype(object)
        df.loc[rows, column] = part[column]




def build(cur, verbose: bool = True,
          review_issue: bool = False, *,
          rows: "pd.DataFrame | None" = None,
          metadata: "dict | None" = None) -> pd.DataFrame:
    """The prepared FLoRA dataset, as a DataFrame. Reads only; writes nothing.

    Orchestrates the build stages in pipeline.transform.stages while keeping
    all database loaders injected through module lookups so existing monkeypatches
    on enrich_works and flora_edits remain effective.
    """
    def say(*args):
        if verbose:
            print(*args)

    df = load(cur) if rows is None else rows.copy()
    aliases, exclusions = load_rules(cur)
    dedup_decisions = load_dedup_decisions(cur)
    cross_type = load_cross_type_duplicates(cur)

    say("=== FLoRA entry-sheet transform ===")
    if cross_type:
        say(f"  ⚠ {len(cross_type)} row(s) ruled a duplicate of a record of the other "
            "type are left out: " + ", ".join(r["display_id"] for r in cross_type[:10]))
    if df.empty:
        say("No rows to transform.")
        return pd.DataFrame(columns=FLORA_COLUMNS + PROVENANCE_COLUMNS)
    say(f"  loaded: {len(df)} rows "
        f"({(df['type'] == 'replication').sum()} replication, "
        f"{(df['type'] == 'reproduction').sum()} reproduction)")

    df[preprint_dedup.CONFLICT_COLUMN] = None
    if df["source"].eq(preprint_dedup.MANUAL_SOURCE).any():
        df[preprint_dedup.REGISTERED_COLUMN] = df["record_id"].isin(load_live_primaries(cur))

    import enrich_works
    import flora_edits

    ctx = BuildContext(
        verbose=verbose,
        review_issue=review_issue,
        say=say,
        aliases=aliases,
        exclusions=exclusions,
        dedup_decisions=dedup_decisions,
        cross_type=cross_type,
    )

    df = stage_outcomes_and_quotes(
        df, ctx, canonical_axis=_canonical_axis, derive_outcome=derive_outcome
    )
    df = stage_dois_and_confirmed_resolutions(
        df, cur, ctx, load_identifiers=flora_edits.load_identifiers
    )
    df = stage_exclusions_and_redundant_urls(df, ctx)
    df = stage_exact_dedup_and_pair_merge(df, ctx)
    df = stage_enrich_and_preprint_dedup(
        df,
        cur,
        ctx,
        metadata=metadata,
        load_metadata=enrich_works.load_metadata,
        join_metadata=join_metadata,
        join_metadata_again=join_metadata_again,
    )
    df = stage_identifier_corrections_and_quotes(
        df,
        cur,
        ctx,
        load_identifiers=flora_edits.load_identifiers,
        apply_identifiers=flora_edits.apply_identifiers,
        join_metadata_again=join_metadata_again,
    )
    return stage_clean_and_finalize(df, ctx)


def _has_text(series):
    return series.apply(lambda v: bool(_s(v)))


def print_summary(shaped, output: Path) -> None:
    """The notebook's closing statistics. Printed rather than returned: this is
    the thing an operator reads to decide whether a run looks sane."""
    print("\n### Summary statistics")
    print(f"  total paper pairs: {len(shaped)}")

    if "type" in shaped.columns:
        print("\n  rows by type:")
        for value, n in shaped["type"].value_counts(dropna=False).items():
            print(f"      {str(value):<14} {n}")

    print()
    pairs = [
        ("rows with an outcome code", "outcome"),
        ("original papers with an APA reference", "apa_ref_o"),
        ("replication papers with an APA reference", "apa_ref_r"),
        ("original papers with BibTeX", "bibtex_ref_o"),
        ("replication papers with BibTeX", "bibtex_ref_r"),
        ("original papers with a title", "title_o"),
        ("replication papers with a title", "title_r"),
        ("original papers with language metadata", "language_o"),
        ("replication papers with language metadata", "language_r"),
        ("original papers with an OA url", "oa_url_o"),
        ("replication papers with an OA url", "oa_url_r"),
        ("rows with an original DOI", "doi_o"),
        ("rows with a replication DOI", "doi_r"),
    ]
    for label, column in pairs:
        if column in shaped.columns:
            n = int(_has_text(shaped[column]).sum())
            pct = (100 * n / len(shaped)) if len(shaped) else 0
            print(f"  {label:<44} {n:>5}  ({pct:>3.0f}%)")


def run(output: Path, stats_only: bool = False,
        require_titles: bool = True, review_issue: bool = False) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("ERROR: DATABASE_URL must be set in environment or .env")

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Standalone preparation must pin IDs too. Otherwise a new row
                # can ship under a provisional UUID and change identity when a
                # later scheduled registry stage assigns its permanent ID.
                # Lock before building; register this exact frame rather than
                # running the transform again against potentially changed data.
                if not stats_only:
                    cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
                out = build(cur, review_issue=review_issue)
                # Imported here because flora_registry imports this module.
                from flora_registry import attach_ids, refresh
                if not stats_only:
                    refresh(cur, verbose=False, frame=out)
                out = attach_ids(cur, out)
                # Corrections made on FLoRA records: keyed on flora_id, so only
                # once the ids are attached.
                import flora_edits
                out = flora_edits.apply(cur, out)
                if not stats_only and not out.empty:
                    if (out["export_id"].map(final_export.text).eq("").any()
                            or out["export_position"].isna().any()):
                        raise ValueError("Cannot export a record without a pinned publication identity")
    finally:
        conn.close()

    if out.empty:
        return
    if stats_only:
        print("\n[stats-only] nothing written")
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    # Step 10 of the notebook: rows with no title on one side are logged, and
    # dropped from the export. The notebook does this unconditionally.
    #
    # It was OFF here while our title coverage was worse than R's — we had only the
    # OpenAlex DOI lookup against R's CrossRef plus three fallbacks, so 331 rows
    # lacked a title for reasons of OUR coverage rather than the data's, and
    # dropping them would have quietly published a smaller dataset than R does.
    #
    # The fallbacks now exist: OpenAlex work-id lookups for DOI-less rows (Step 7b)
    # and title recovery from the reference text (Step 8b). That leaves 24 rows,
    # which are genuinely untitled — Google Docs links, DOIs OpenAlex does not hold
    # — so the filter is ON, matching the notebook. --keep-untitled turns it off.
    #
    # This applies to the EXPORT only. The FLoRA tab still shows these rows: it is
    # the surface someone fixes them on, and a row hidden there is a row nobody
    # fixes. flora_service.counts() reports how many will not reach the export.
    # Outcome clashes resolved during a merge — the same paper, the same url_r,
    # disagreeing outcomes. Source-data mistakes worth someone's attention, so
    # they get their own file rather than scrolling past in the log.
    conflicts = out.attrs.get("outcome_conflicts") or []
    if conflicts:
        conflict_path = output.parent / "dup_outcome_conflicts.csv"
        pd.DataFrame(conflicts).to_csv(conflict_path, index=False,
                                       encoding="utf-8", lineterminator="\n")
        print(f"\n  ⚠ {len(conflicts)} merged group(s) had conflicting outcomes "
              f"-> {conflict_path}")
        for conflict in conflicts[:5]:
            print(f"      {conflict.get('doi_o')} + {conflict.get('doi_r')}  "
                  f"[{conflict.get('outcomes')} -> {conflict.get('resolved_to')!r}]")

    # Every detected preprint pair and what happened to it. Beside the output so
    # the nightly artifact and the preparation report carry it.
    dedup_log = out.attrs.get("preprint_dedup_log") or []
    if dedup_log:
        dedup_path = output.parent / "preprint_dedup_candidates.csv"
        preprint_dedup.write_candidates(dedup_log, dedup_path)
        pending = len(preprint_dedup.unresolved(dedup_log))
        print(f"\n  {len(dedup_log)} preprint duplicate pair(s), {pending} awaiting "
              f"a decision in the FLoRA tab -> {dedup_path}")

    cross_type = out.attrs.get("cross_type_duplicates") or []
    if cross_type:
        cross_path = output.parent / "cross_type_duplicate_rulings.csv"
        pd.DataFrame(cross_type).to_csv(cross_path, index=False, encoding="utf-8",
                                        lineterminator="\n")
        print(f"\n  ⚠ {len(cross_type)} row(s) ruled a duplicate of a record of the other "
              f"type -> {cross_path}")

    report = missing_title_report(out)
    if not report.empty:
        log_path = output.parent / "flora_export_log.csv"
        report.to_csv(log_path, index=False, encoding="utf-8", lineterminator="\n")
        print(f"\n  {len(report)} row(s) missing a title -> {log_path}")
        for reason, n in report["reason"].value_counts().items():
            print(f"      {reason}: {n}")
        if require_titles:
            out = drop_untitled(out)
            print(f"  dropped from the export; {len(out)} row(s) remain "
                  f"(--keep-untitled to keep them)")
        else:
            print("  --keep-untitled: kept in the export")

    shaped = to_output_shape(out)
    # Explicit newline so the file does not differ between a Windows dev box and
    # the Linux runner that produces the nightly artifact.
    output.write_text(final_export.csv_text(shaped), encoding="utf-8", newline="")
    empty = [c for c in FLORA_OUTPUT_COLUMNS if shaped[c].isna().all()]
    print(f"\n  saved: {output}")
    print(f"  columns: {len(shaped.columns)} "
          f"({len(FLORA_OUTPUT_COLUMNS) - len(empty)}/{len(FLORA_OUTPUT_COLUMNS)} "
          f"of the output contract carry data)")
    if empty:
        # A contract column with nothing in it at all means an enrichment source
        # stopped answering — worth saying rather than shipping quietly.
        print(f"  ⚠ no data at all in: {', '.join(empty)}")
        print("    → run enrich_works.py, or check whether OpenAlex is reachable")

    print_summary(shaped, output)


if __name__ == "__main__":
    start_logging("transform")
    parser = argparse.ArgumentParser(description="Transform source_records into the FLoRA column set")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--review-issue", action="store_true",
                        help="file or comment on a GitHub issue when pairs are "
                             "being auto-resolved and cache/confirmed_preprint_"
                             "duplicates.csv has gone stale. Needs the gh CLI. "
                             "OFF by default so opening the FLoRA tab cannot file "
                             "issues.")
    parser.add_argument("--keep-untitled", action="store_true",
                        help="keep rows with no title on one side. They are dropped "
                             "by default, as the R notebook's Step 10 does.")
    parser.add_argument("--stats-only", action="store_true",
                        help="Report what would be produced without writing.")
    args = parser.parse_args()

    run(args.output, stats_only=args.stats_only,
        require_titles=not args.keep_untitled, review_issue=args.review_issue)
