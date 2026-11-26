"""Pure stage functions extracted from transform_sources.build().

Each stage receives the working DataFrame and BuildContext and returns the
updated DataFrame. Callbacks for database-backed loaders (load_metadata,
load_identifiers, apply_identifiers) are injected by transform_sources.build()
so existing module-level test patches remain effective.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

import apa_references
import author_overlap
import cos_quote_rewrite
import preprint_dedup
from pipeline.schema import (
    DERIVED_COLUMNS,
    DUMMY_DOI_RE,
    ENRICHMENT_COLUMNS,
    FLORA_COLUMNS,
    JOIN_FALLBACKS,
    META_PAPER_CANDIDATE_URLS,
    META_PAPER_DOIS,
    META_PAPER_NOTE,
    PARAGRAPH_COLUMNS,
    PROVENANCE_COLUMNS,
    REVIEW_COLUMNS,
    WORK_ID_HINT,
    _s,
    clean_doi,
    clean_text,
    dedup_keys,
    derive_quote,
    derive_quote_source,
    has_identifier,
    matching_exclusion,
    norm_url,
    record_keys,
    repair_mojibake,
    strip_redundant_url,
    unescape_entities,
)
from pipeline.transform.context import BuildContext


def stage_outcomes_and_quotes(
    df: pd.DataFrame,
    ctx: BuildContext,
    *,
    canonical_axis: Callable,
    derive_outcome: Callable,
) -> pd.DataFrame:
    """Stages 1 & 1b: normalise/derive outcomes, axes, and COS shorthand quotes."""
    for axis in ("outcome_computation", "outcome_robustness"):
        df[axis] = df.apply(lambda r, col=axis: canonical_axis(r, col, ctx.problems), axis=1)
    outcome_before = list(df["outcome"]) if "outcome" in df.columns else []
    df["outcome"] = df.apply(lambda r: derive_outcome(r, ctx.aliases, ctx.problems), axis=1)
    df["outcome_quote"] = df.apply(derive_quote, axis=1)
    df["outcome_quote_source"] = df.apply(derive_quote_source, axis=1)

    before_quotes = df["outcome_quote"].copy()
    df["outcome_quote"] = cos_quote_rewrite.rewrite(df["outcome_quote"])
    changed_quotes = before_quotes.fillna("") != df["outcome_quote"].fillna("")
    rewritten = int(changed_quotes.sum())
    if rewritten:
        ctx.say(f"  rewrote {rewritten} shorthand outcome quote(s) into readable form")
    noted = changed_quotes & df["type"].ne("reproduction")
    df.loc[noted, "outcome_quote_source"] = (
        df.loc[noted, "outcome_quote_source"].map(cos_quote_rewrite.mark_coding_note)
    )
    n_repro = int((df["type"] == "reproduction").sum())
    ctx.say(f"  outcome normalized/derived; {df['outcome'].notna().sum()} row(s) have one")
    recodes = {}
    for raw, canonical in zip(outcome_before, df["outcome"]):
        raw_text, canonical_text = _s(raw), _s(canonical)
        if raw_text and canonical_text and raw_text != canonical_text:
            recodes[(raw_text, canonical_text)] = recodes.get((raw_text, canonical_text), 0) + 1
    if recodes:
        ctx.say(f"  recoded {sum(recodes.values())} outcome value(s):")
        for (raw_text, canonical_text), n in sorted(recodes.items(), key=lambda kv: -kv[1]):
            ctx.say(f"      {raw_text!r} -> {canonical_text!r} ({n})")
    if n_repro:
        ctx.say(f"  {n_repro} reproduction row(s) carry two authoritative axes")
    return df


def stage_dois_and_confirmed_resolutions(
    df: pd.DataFrame,
    cur,
    ctx: BuildContext,
    *,
    load_identifiers: Callable,
) -> pd.DataFrame:
    """Stages 2 & 2b: clean DOIs and apply confirmed preprint/publication resolutions."""
    before_o, before_r = df["doi_o"].copy(), df["doi_r"].copy()
    df["doi_o"] = df["doi_o"].apply(clean_doi)
    df["doi_r"] = df["doi_r"].apply(clean_doi)
    changed = int(
        ((before_o != df["doi_o"]) & before_o.notna()).sum()
        + ((before_r != df["doi_r"]) & before_r.notna()).sum()
    )
    ctx.say(f"  DOIs cleaned: {changed} value(s) changed")

    for column in ("doi_o", "doi_r"):
        bad = [v for v in df[column] if _s(v) and not _s(v).lower().startswith("10.")]
        if bad:
            shown = bad if len(bad) < 10 else bad[:5]
            label = "" if len(bad) < 10 else " (first 5)"
            ctx.say(
                f"  ⚠ {len(bad)} {column} value(s) are not DOIs{label}: "
                + ", ".join(repr(v) for v in shown)
            )

    publication_guards = {}
    if any(row.get("side", "").endswith("(publication link)") for row in ctx.dedup_decisions):
        ctx.publication_identifiers = load_identifiers(cur)
        for ids, fields in ctx.publication_identifiers.get("records", []):
            for source_id in ids:
                publication_guards.setdefault(source_id, fields)

    return preprint_dedup.apply_confirmed(
        df,
        normalise_outcome=ctx.normalise_outcome,
        verbose=ctx.verbose,
        conflicts_out=ctx.conflicts,
        rulings=ctx.dedup_decisions,
        hand_fills=ctx.hand_fills,
        publication_guards=publication_guards,
    )


def stage_exclusions_and_redundant_urls(df: pd.DataFrame, ctx: BuildContext) -> pd.DataFrame:
    """Stages 3 & 4: apply transform_exclusions and strip redundant url_r values."""
    pairs = {
        (clean_doi(e["doi_o"]), clean_doi(e["doi_r"]))
        for e in ctx.exclusions
        if e.get("doi_o") and e["doi_r"]
    }
    excl_dois = {
        clean_doi(e["doi_r"])
        for e in ctx.exclusions
        if e["doi_r"] and not e.get("doi_o")
    }
    excl_urls = {
        norm_url(e["url_r"])
        for e in ctx.exclusions
        if e["url_r"] and not e.get("doi_o")
    }

    if pairs or excl_dois or excl_urls:
        clean_r = df["doi_r"].apply(clean_doi)
        mask = (
            clean_r.isin(excl_dois - {None})
            | df["url_r"].apply(norm_url).isin(excl_urls - {None})
        )
        if pairs:
            pair_key = list(zip(df["doi_o"].apply(clean_doi), clean_r))
            mask = mask | pd.Series([k in pairs for k in pair_key], index=df.index)
        n_pair = (
            int(pd.Series([k in pairs for k in zip(df["doi_o"].apply(clean_doi), clean_r)]).sum())
            if pairs
            else 0
        )
        ctx.say(
            f"  excluded: {int(mask.sum())} row(s) via transform_exclusions"
            + (f" ({n_pair} by (doi_o, doi_r) pair)" if n_pair else "")
        )
        df = df[~mask]
    else:
        ctx.say("  excluded: 0 (transform_exclusions is empty)")

    before_urls = df["url_r"].notna().sum()
    df["url_r"] = df.apply(strip_redundant_url, axis=1)
    ctx.say(f"  redundant url_r stripped: {before_urls - df['url_r'].notna().sum()}")
    return df


def stage_exact_dedup_and_pair_merge(df: pd.DataFrame, ctx: BuildContext) -> pd.DataFrame:
    """Stages 5 & 5b: exact-key dedup (with distinct exemption, website precedence,
    and hand-fill yielding) followed by compatible (doi_o, doi_r) pair merge."""
    before = len(df)
    key = dedup_keys(df)
    yields = preprint_dedup.yielding(df, key)
    if yields.any():
        order = list(df.index[~yields.to_numpy()]) + list(df.index[yields.to_numpy()])
        df, key, yields = df.loc[order], key.loc[order], yields.loc[order]

    exempt = df["duplicate_status"].eq("distinct")
    collides = key.duplicated() & has_identifier(df)
    survivors = df.index[~key.duplicated()]

    df = df.assign(_key=key)
    dropped = df[collides & ~exempt]
    prior = (
        df["merged_record_ids"]
        if "merged_record_ids" in df.columns
        else pd.Series([[] for _ in range(len(df))], index=df.index)
    )
    merged_by_key = {}
    for idx in dropped.index:
        ids = merged_by_key.setdefault(df.at[idx, "_key"], [])
        ids.append(df.at[idx, "record_id"])
        ids.extend(prior[idx] if isinstance(prior[idx], list) else [])

    survivor_of = {df.at[idx, "_key"]: idx for idx in survivors}
    axis_columns = {column for axis in preprint_dedup.REPRODUCTION_AXES for column in axis}
    for k, group in dropped.groupby("_key"):
        kept = survivor_of[k]
        rows = [df.loc[kept], *(group.loc[i] for i in group.index)]
        hand = [r for r in rows[1:] if yields[r.name]]
        if hand and not yields[kept]:
            ctx.hand_fills.append((df.at[kept, "record_id"], [h.to_dict() for h in hand], False))
            rows = [r for r in rows if not any(r is h for h in hand)]
        added = preprint_dedup.fill_blanks(rows)
        for column, value in added.items():
            df.at[kept, column] = value
        if added.keys() & axis_columns:
            survivor = df.loc[kept]
            df.at[kept, "outcome_quote"] = cos_quote_rewrite.rewrite_one(derive_quote(survivor))
            df.at[kept, "outcome_quote_source"] = derive_quote_source(survivor)
        judged = [r for r in rows if not yields[r.name]] or rows
        overrides = preprint_dedup.website_overrides(judged, ctx.normalise_outcome)
        for column, value in overrides.items():
            if column in df.columns:
                df.at[kept, column] = value
        outcomes = {o for o in (ctx.normalise_outcome(r.get("outcome")) for r in judged) if o}
        undecided_before = next(
            (
                r.get(preprint_dedup.CONFLICT_COLUMN)
                for r in judged
                if not preprint_dedup._blank(r.get(preprint_dedup.CONFLICT_COLUMN))
            ),
            None,
        )
        if len(outcomes) > 1:
            settled = "outcome" in overrides
            ctx.conflicts.append({
                "doi_o": df.at[kept, "doi_o"],
                "doi_r": df.at[kept, "doi_r"],
                "type": df.at[kept, "type"],
                "outcomes": " | ".join(sorted(outcomes)),
                "resolved_to": ctx.normalise_outcome(df.at[kept, "outcome"]),
                "resolved_by": "website record" if settled else preprint_dedup.NEEDS_DECISION,
                "url_r": df.at[kept, "url_r"],
                "sources": " | ".join(dict.fromkeys(str(r.get("source")) for r in rows)),
            })
            df.at[kept, preprint_dedup.CONFLICT_COLUMN] = (
                None if settled else " | ".join(sorted(outcomes))
            )
        elif undecided_before is not None and "outcome" not in overrides:
            df.at[kept, preprint_dedup.CONFLICT_COLUMN] = undecided_before

    keep = ~(collides & ~exempt)
    survivor_set = set(survivors)
    registered = preprint_dedup.REGISTERED_COLUMN
    registered_by_key = (
        dropped.groupby("_key")[registered].any().to_dict()
        if registered in df.columns and not dropped.empty
        else {}
    )
    df = df[keep].copy()
    if registered_by_key:
        df[registered] = [
            bool(flag) or (idx in survivor_set and registered_by_key.get(k, False))
            for idx, flag, k in zip(df.index, df[registered], df["_key"])
        ]
    df["merged_record_ids"] = [
        (list(prior[idx]) if isinstance(prior[idx], list) else [])
        + (merged_by_key.get(k, []) if idx in survivor_set else [])
        for idx, k in zip(df.index, df["_key"])
    ]
    df["dedup_key"] = df["_key"]
    df = df.drop(columns=["_key"])
    rescued = int((collides & exempt).sum())
    ctx.say(
        f"  deduplicated: {before - len(df)} row(s) collapsed"
        + (f"; {rescued} kept as reviewer-confirmed distinct" if rescued else "")
    )

    df, outcome_conflicts = preprint_dedup.merge_doi_pair_dups(
        df, ctx.normalise_outcome, verbose=ctx.verbose, hand_fills=ctx.hand_fills
    )
    if not outcome_conflicts.empty:
        ctx.conflicts.extend(outcome_conflicts.to_dict("records"))
    return df


def stage_enrich_and_preprint_dedup(
    df: pd.DataFrame,
    cur,
    ctx: BuildContext,
    *,
    metadata: dict | None,
    load_metadata: Callable,
    join_metadata: Callable,
    join_metadata_again: Callable,
) -> pd.DataFrame:
    """Stages 6, 7, 6b & hand-fills: project source labels, join metadata, resolve
    preprint/publication duplicates, re-join moved rows, and apply deferred hand-fills."""
    df["source"] = df["source"].replace(
        {"replications": "entry_sheet_replications", "reproductions": "entry_sheet_reproductions"}
    )

    ctx.meta = load_metadata(cur) if metadata is None else metadata
    join_fallbacks = df.set_index("record_id")[
        [c for c in JOIN_FALLBACKS if c in df.columns]
    ].copy()
    ctx.join_fallbacks = join_fallbacks[~join_fallbacks.index.duplicated()]
    join_metadata(df, ctx.meta)

    enriched = int(df["title_o"].notna().sum())
    ctx.say(f"  enriched: {enriched}/{len(df)} row(s) have original-side metadata")

    doi_o_before = dict(zip(df["record_id"], df["doi_o"]))
    df, dedup_log = preprint_dedup.resolve(
        df,
        candidates_out=None,
        normalise_outcome=ctx.normalise_outcome,
        verbose=ctx.verbose,
        conflicts_out=ctx.conflicts,
        open_review_issue=ctx.review_issue,
        rulings=ctx.dedup_decisions,
        hand_fills=ctx.hand_fills,
    )
    ctx.dedup_log = dedup_log

    moved = [
        idx
        for idx, record_id, doi, merged in zip(
            df.index, df["record_id"], df["doi_o"], df["merged_record_ids"]
        )
        if any(
            sid in doi_o_before and _s(doi_o_before[sid]) != _s(doi)
            for sid in [record_id, *(merged if isinstance(merged, list) else [])]
        )
    ]
    if moved:
        join_metadata_again(df, moved, "o", ctx.meta, ctx.join_fallbacks)
        ctx.say(
            f"  joined to the metadata again after an original-side replacement: {len(moved)} row(s)"
        )

    filled = {}
    df = preprint_dedup.apply_hand_fills(df, ctx.hand_fills, filled)
    for record_id, columns in filled.items():
        for column, value in columns.items():
            if column in ctx.join_fallbacks.columns and record_id in ctx.join_fallbacks.index:
                ctx.join_fallbacks[column] = ctx.join_fallbacks[column].astype(object)
                ctx.join_fallbacks.at[record_id, column] = value
    return df


def stage_identifier_corrections_and_quotes(
    df: pd.DataFrame,
    cur,
    ctx: BuildContext,
    *,
    load_identifiers: Callable,
    apply_identifiers: Callable,
    join_metadata_again: Callable,
) -> pd.DataFrame:
    """Stages 6c & 7e: apply product-level identifier corrections, re-join metadata,
    re-check exclusions, warn on collisions, and rebuild reproduction quotes."""
    identifiers = (
        load_identifiers(cur)
        if ctx.publication_identifiers is None
        else ctx.publication_identifiers
    )
    identifier_edits = apply_identifiers(df, identifiers)
    ctx.identifier_edits = identifier_edits

    if identifier_edits:
        ctx.say(f"  identifiers corrected on FLoRA records: {len(identifier_edits)} row(s)")
        for side in ("o", "r"):
            corrected = [
                rid
                for rid, fields in identifier_edits.items()
                if fields.keys() & {f"doi_{side}", f"url_{side}"}
            ]
            rows = df.index[df["record_id"].isin(corrected)]
            hint = WORK_ID_HINT.format(side=side)
            df[hint] = df["record_id"].map(
                lambda rid: (identifier_edits.get(rid) or {}).get(f"url_{side}")
            )
            if len(rows):
                join_metadata_again(
                    df,
                    rows,
                    side,
                    ctx.meta,
                    ctx.join_fallbacks,
                    kept_urls={
                        rid for rid in corrected if f"url_{side}" in identifier_edits[rid]
                    },
                )
        df = df.drop(columns=[WORK_ID_HINT.format(side=side) for side in ("o", "r")], errors="ignore")
        excluded = pd.Series(False, index=df.index)
        for idx in df.index[df["record_id"].isin(identifier_edits)]:
            excluded[idx] = (
                matching_exclusion(
                    ctx.exclusions,
                    df.at[idx, "doi_o"],
                    df.at[idx, "doi_r"],
                    df.at[idx, "url_r"],
                )
                is not None
            )
        if excluded.any():
            ctx.say(
                f"  ⚠ {int(excluded.sum())} row(s) corrected onto an excluded identifier "
                "were left out: " + ", ".join(df.loc[excluded, "display_id"].astype(str))
            )
            df = df[~excluded]
        keys = record_keys(df)
        shared = df["record_id"].isin(identifier_edits) & keys.notna() & keys.duplicated(keep=False)
        if shared.any():
            ctx.say(
                f"  ⚠ {int(shared.sum())} corrected row(s) now match another record's "
                "identifiers: "
                + ", ".join(df.loc[shared, "display_id"].astype(str))
                + " — rule a duplicate in Source Records if it is the same study"
            )

    reproduction = df["type"].eq("reproduction")
    if reproduction.any():
        df.loc[reproduction, "outcome_quote"] = df[reproduction].apply(
            lambda row: cos_quote_rewrite.rewrite_one(derive_quote(row)), axis=1
        )
        df.loc[reproduction, "outcome_quote_source"] = df[reproduction].apply(
            derive_quote_source, axis=1
        )
    return df


def stage_clean_and_finalize(df: pd.DataFrame, ctx: BuildContext) -> pd.DataFrame:
    """Stages 8, 8b, 8c & 9: text/mojibake/URL cleaning, APA title recovery,
    author overlap, meta-paper annotation, column projection, attrs, DUMMY strip,
    and strict outcome/axis validation."""
    for column in (
        "title_o",
        "title_r",
        "journal_o",
        "journal_r",
        "ref_o",
        "ref_r",
        "abstract_r",
        "outcome_quote",
    ):
        if column not in df.columns:
            continue
        before = df[column].copy()
        df[column] = df[column].apply(clean_text, one_line=column not in PARAGRAPH_COLUMNS)
        changed = int((before.fillna("") != df[column].fillna("")).sum())
        if changed:
            ctx.say(f"      cleaned {column}: {changed} cell(s) changed")

    for column in (
        "author_o",
        "author_r",
        "bibtex_ref_o",
        "bibtex_ref_r",
        "outcome_computational_quote",
        "outcome_robustness_quote",
        "outcome_replication_quote",
    ):
        if column in df.columns:
            df[column] = df[column].map(repair_mojibake)

    for column in ("oa_url_o", "oa_url_r"):
        if column in df.columns:
            df[column] = df[column].map(
                lambda url: unescape_entities(url) if isinstance(url, str) else url
            )

    for _side in ("o", "r"):
        apa_references.augment(df, _side, verbose=ctx.verbose)

    author_overlap.augment(df, verbose=ctx.verbose)

    clean_r = df["doi_r"].apply(clean_doi)
    has_url = df["url_r"].notna() & df["url_r"].astype(str).str.strip().ne("")
    annotate = clean_r.isin(META_PAPER_DOIS) & has_url
    if annotate.any():
        def _annotate(row):
            ref = row["ref_r"]
            if not ref or META_PAPER_NOTE in str(ref):
                return ref
            return f"{str(ref).rstrip()} {META_PAPER_NOTE} {row['url_r']}"

        df.loc[annotate, "ref_r"] = df[annotate].apply(_annotate, axis=1)
    ctx.say(f"  meta-paper rows annotated with their report URL: {int(annotate.sum())}")

    linked = df[df["url_r"].notna() & df["doi_r"].notna()].copy()
    if not linked.empty:
        linked["_doi"] = linked["doi_r"].apply(clean_doi)
        url_counts = linked.groupby("_doi")["url_r"].nunique()
        candidates = [
            (d, n)
            for d, n in url_counts.items()
            if n >= META_PAPER_CANDIDATE_URLS and d not in META_PAPER_DOIS
        ]
        if candidates:
            ctx.say(
                f"  ⚠ {len(candidates)} DOI(s) carry >= {META_PAPER_CANDIDATE_URLS} "
                f"distinct url_r but are not listed as meta-papers:"
            )
            for doi, n in sorted(candidates, key=lambda c: -c[1])[:10]:
                ctx.say(f"      {doi} ({n} distinct urls)")
            ctx.say("    → add to META_PAPER_DOIS if they are aggregate reports")

    df["source_record_id"] = df["record_id"]
    df["source_display_id"] = df["display_id"]
    df["flora_id"] = None
    out = df.reindex(
        columns=FLORA_COLUMNS
        + ENRICHMENT_COLUMNS
        + DERIVED_COLUMNS
        + PROVENANCE_COLUMNS
        + REVIEW_COLUMNS
    )

    out.attrs["merged_record_ids"] = dict(zip(df["record_id"], df["merged_record_ids"]))
    out.attrs["dedup_key"] = dict(zip(df["record_id"], df["dedup_key"]))
    out.attrs["outcome_conflicts"] = ctx.conflicts
    out.attrs["preprint_dedup_log"] = [
        {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()}
        for row in ctx.dedup_log.to_dict("records")
    ]
    out.attrs["cross_type_duplicates"] = ctx.cross_type
    out.attrs["identifier_edits"] = ctx.identifier_edits

    for col in ("doi_o", "doi_r"):
        dummy = out[col].astype(str).str.match(DUMMY_DOI_RE, na=False)
        if dummy.any():
            ctx.say(f"  stripped {int(dummy.sum())} DUMMY_* placeholder(s) from {col}")
            out.loc[dummy, col] = None

    ctx.say(f"\n  final: {len(out)} rows × {len(out.columns)} columns")

    unknown_alias = ctx.problems["unknown_alias"]
    bad_alias = ctx.problems["bad_alias"]
    invalid_axis = ctx.problems["invalid_axis"]
    missing = int(out["outcome"].isna().sum())
    if missing:
        invalid_rows = {display_id for display_id, _, _ in invalid_axis}
        blank = missing - len(unknown_alias) - len(bad_alias) - len(invalid_rows)
        if blank > 0:
            ctx.say(
                f"  ⚠ {blank} row(s) have no outcome — blank or incomplete in the source sheet"
            )

    ctx.say("\n  outcome distribution:")
    for value, n in out["outcome"].value_counts(dropna=True).items():
        ctx.say(f"      {str(value):55} {n}")

    if unknown_alias or bad_alias or invalid_axis:
        ctx.say()
        if unknown_alias:
            ctx.say(f"  ✗ {len(unknown_alias)} row(s) carry an outcome spelling absent from outcome_alias:")
            for display_id, raw in unknown_alias[:10]:
                ctx.say(f"      {display_id}: {raw!r}")
            ctx.say("    → add a row to outcome_alias mapping it to a canonical value, and re-run")
        if bad_alias:
            ctx.say(f"  ✗ {len(bad_alias)} row(s) alias onto a value the app does not accept:")
            for display_id, raw, canonical in bad_alias[:10]:
                ctx.say(f"      {display_id}: {raw!r} → {canonical!r}")
            ctx.say(
                "    → fix the outcome_alias row, or add the value to "
                "extractor_vocab.REPLICATION_OUTCOMES and the CHECK constraint"
            )
        if invalid_axis:
            ctx.say(f"  INVALID: {len(invalid_axis)} reproduction axis value(s):")
            for display_id, column, raw in invalid_axis[:10]:
                ctx.say(f"      {display_id}: {column}={raw!r}")
            ctx.say(
                "    fix the value at its source (sheet or validated record) and re-sync, or add "
                "the spelling to AXIS_VALUE_ALIASES in extractor_vocab.py, and re-run"
            )
        problem_count = len(unknown_alias) + len(bad_alias) + len(invalid_axis)
        raise ValueError(
            f"{problem_count} outcome or axis value(s) are not recognised; "
            f"refusing to produce an export. "
            f"Raised under --stats-only too, so a dry run reports the problem "
            f"rather than passing."
        )

    return out
