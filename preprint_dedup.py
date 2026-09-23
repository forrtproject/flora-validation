"""preprint_dedup.py — preprint/publication duplicate detection and resolution.

A Python port of R/preprint_dedup.R (FReD issue #105). Two kinds of duplicate:

1. **Replication-side** — same `doi_o`, but `doi_r` appears as both a preprint and
   the published version of the same paper.
2. **Original-side** — different `doi_o` values that are preprint/published, or
   DOI-format variants, of the same original.

The pipeline touches this twice, as the notebook does:

- `apply_confirmed(...)` runs EARLY, before metadata is fetched, and applies only
  the `keep_1`/`keep_2` rulings — from `cache/confirmed_preprint_duplicates.csv`
  and from the website's FLoRA tab (`preprint_dedup_decisions`). Running
  it first is what makes the enrichment fetch canonical DOIs rather than DOIs it is
  about to discard.
- `resolve(...)` runs AFTER enrichment, because detection needs titles and authors.
  It detects candidates, applies overrides where the confirmed file has them, and
  otherwise falls back to the default rule.

CONFIRMED DECISIONS ANNOTATE; AUTOMATIC ONES DO NOT
---------------------------------------------------
Both drop or replace rows. Only a confirmed one writes the discarded DOI into
`alt_identifier_o` / `alt_identifier_r`. That asymmetry is deliberate in the
original and preserved here: recording an identifier as an alias for a record is a
claim of equivalence, and the automatic rule is a guess until a human has agreed
with it.

DELIBERATE DEPARTURES FROM R
----------------------------
R auto-dropped every candidate. Here a pair is held as `needs_review` (both rows
kept) when its first authors differ, or when both DOIs are preprints without a
matching first author. The generic OSF prefix `10.17605/` is no longer a preprint
prefix. Together these stop independent reproductions of one original, which share
templated titles, from being collapsed into one.

Rulings can also come from the website: admins decide pending pairs in the FLoRA
tab, stored in `preprint_dedup_decisions` and passed in as `rulings`. They use
the confirmed file's vocabulary and win over it for the same pair.

NOT PORTED: the `gh` CLI issue-filing in `maybe_open_dedup_review_issue`. The
candidates log is written exactly as before, and our workflow already uploads
reports as artifacts, so a second nudge mechanism that shells out to `gh` would be
operational glue rather than pipeline behaviour.
"""
import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

from console_encoding import use_utf8_output

# This module prints a warning glyph, and the repo's rule (enforced by
# tests/test_console_encoding.py) is that any module doing so must make its own
# output UTF-8 safe rather than relying on whoever imported it.
use_utf8_output()

ROOT = Path(__file__).parent
CONFIRMED_PATH = ROOT / "cache" / "confirmed_preprint_duplicates.csv"
CANDIDATES_PATH = ROOT / "output" / "preprint_dedup_candidates.csv"

TITLE_THRESHOLD = 0.80

# Preprint servers. Verbatim from the R constant, plus "10.31222/" (MetaArXiv):
# FReD issue #<TBD> — 10.31222/osf.io/sjyp3 (the MetaArXiv preprint of Kohrt et al.
# 2023's published replication, 10.1098/rsos.221306) was missing from this list, so
# is_preprint_doi() returned False for it. The pair still got detected as a
# candidate (same first author, "kohrt"), but only the "preprint loses" rule
# reliably keeps the published version; without it the outcome depended on the
# arbitrary doi_1/doi_2 ordering tie-break instead.
PREPRINT_DOI_PREFIXES = (
    "10.31234/", "10.31219/", "10.31222/", "10.48550/", "10.1101/",
    "10.2139/", "10.20944/", "10.21203/", "10.53841/",
)

# Generic OSF DOIs (projects, registrations, files) — deliberately NOT preprints.
# SCORE-style reproduction reports live here under templated titles such as
# "Reproduction (with author data): Ku & Zaroff (2014, ...)", so two independent
# teams reproducing one original get identical titles. Counting the prefix as a
# preprint let those pairs skip the first-author check and silently dropped one
# attempt. It still loses to a publisher DOI when resolving a confirmed pair.
REPOSITORY_DOI_PREFIXES = ("10.17605/",)

VALID_ACTIONS = {"keep_1", "keep_2", "keep_both"}
NEEDS_REVIEW = "needs_review"
APPLY_ACTIONS = {"keep_1", "keep_2"}
# Pairs no human has ruled on: held with both rows kept, or dropped by the default
# guess. These are what the FLoRA tab lists and the preparation report warns about.
UNRESOLVED_ACTIONS = {NEEDS_REVIEW, "auto_keep_1", "auto_keep_2"}

# A genuine outcome clash survives a merge as "A || B", which validate_flora then
# reports because it is not in the allowed vocabulary. Merging it to something
# valid would hide a source-data mistake.
OUTCOME_CLASH_SEP = " || "
_MIXABLE = {"successful", "mixed", "failed"}

# Records validated on this website (source_records.source = 'validated'; every one
# of them exists because someone validated it). When one shares a group with an
# entry-sheet or FReD row it is the reviewed record, so its judgement wins — and when
# the judgements disagree, its evidence too. It decides VALUES only: which row the
# group becomes (and so its published id) is chosen exactly as before. Everything
# else merges by the ordinary rules, and a field it leaves blank falls back to what
# the others have. See website_overrides().
WEBSITE_SOURCE = "validated"
_JUDGEMENT = ("outcome", "outcome_computation", "outcome_robustness")
_EVIDENCE = ("outcome_quote", "outcome_quote_source", "out_quote_source",
             "outcome_computational_quote", "out_quote_computational_source",
             "outcome_robustness_quote", "out_quote_robust_source")

_TAG_RE = re.compile(r"<[^>]+>")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")


def _blank(value) -> bool:
    return (value is None or (isinstance(value, float) and pd.isna(value))
            or not str(value).strip())


def _s(value) -> str:
    return "" if _blank(value) else str(value).strip()


# ── helpers ───────────────────────────────────────────────────────────────────

def is_preprint_doi(doi) -> bool:
    text = _s(doi).lower()
    return bool(text) and text.startswith(PREPRINT_DOI_PREFIXES)


def is_repository_doi(doi) -> bool:
    text = _s(doi).lower()
    return bool(text) and text.startswith(REPOSITORY_DOI_PREFIXES)


def normalize_doi(doi) -> str:
    """Lowercase, strip resolver prefixes, repair the `10.1037//` typo, percent-decode.

    The double-slash repair and the percent-decode are what make DOI-variant pairs
    detectable at all: `10.1037//0022-3514.46.4.778` and `10.1037/0022-3514.46.4.778`
    are the same paper, and so are the `%3c` and `<` spellings of a Wiley DOI.
    """
    text = _s(doi).lower()
    if not text:
        return ""
    text = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:", "", text)
    text = re.sub(r"^(10\.[0-9]+)//", r"\1/", text)
    try:
        text = unquote(text)
    except Exception:                                          # noqa: BLE001
        pass
    return text.strip()


def normalize_title(title) -> str:
    text = _s(title).lower()
    text = _TAG_RE.sub("", text)
    text = _NON_ALNUM_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def title_similarity(t1, t2) -> float:
    """1 - normalised edit distance, as R's `1 - adist(t1, t2) / max_len`."""
    a, b = normalize_title(t1), normalize_title(t2)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return 1 - _edit_distance(a, b) / max(len(a), len(b))


def extract_first_author(author) -> "str | None":
    """The first author's surname, lowercased.

    R parses CrossRef JSON and takes `family`, falling back to the text before the
    first [,;&]. Our authors come from OpenAlex as "Given Family; Given Family", so
    that fallback would yield a full name where the R version yields a surname. Both
    spellings are handled: "Family, Given" splits on the comma, anything else takes
    the last word.
    """
    text = _s(author)
    if not text:
        return None
    if text.startswith("["):
        try:
            authors = json.loads(text)
            if authors and isinstance(authors[0], dict):
                family = authors[0].get("family") or authors[0].get("name")
                if family:
                    return str(family).strip().lower()
        except (ValueError, TypeError):
            pass
    first = re.split(r"[;&]", text)[0].strip()
    if "," in first:
        return first.split(",")[0].strip().lower() or None
    parts = first.split()
    return parts[-1].lower() if parts else None


def append_alt_identifier(existing, new_id) -> "str | None":
    """Comma-separated, order preserved, deduplicated case-insensitively."""
    def parts(value):
        if _blank(value):
            return []
        return [p.strip() for p in str(value).split(",") if p.strip()]

    combined, seen, out = parts(existing) + parts(new_id), set(), []
    for item in combined:
        if item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return ", ".join(out) or None


def pair_key(doi_1, doi_2) -> str:
    a, b = _s(doi_1).lower(), _s(doi_2).lower()
    low, high = (a, b) if a <= b else (b, a)
    return f"{low}||{high}"


# ── the confirmed file ────────────────────────────────────────────────────────

def load_confirmed(path: Path = CONFIRMED_PATH) -> list:
    """Rows carrying a real action. The INSTRUCTIONS row is dropped by that filter,
    since its `action` cell holds prose rather than one of the three verbs."""
    if not path.exists():
        return []
    # utf-8-sig: the file is Excel-written and carries a BOM, which would otherwise
    # make the first column "﻿side" and every lookup of "side" fail.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.DictReader(fh)
                if (row.get("action") or "").strip() in VALID_ACTIONS]


def confirmed_decisions(path: Path = CONFIRMED_PATH, rulings=None) -> list:
    """The confirmed file's rows overlaid with admin rulings, one row per pair.

    `rulings` are the rulings made in the FLoRA tab (preprint_dedup_decisions).
    For the same pair a ruling there wins over the file: it is the newer decision,
    and the file is edited by hand far less often.
    """
    merged = {pair_key(r["doi_1"], r["doi_2"]): r for r in load_confirmed(path)}
    for row in rulings or []:
        if _s(row.get("action")) in VALID_ACTIONS:
            merged[pair_key(row.get("doi_1"), row.get("doi_2"))] = row
    return list(merged.values())


def unresolved(decisions) -> list:
    """The decisions a human still has to rule on."""
    return [d for d in decisions if _s(d.get("applied_action")) in UNRESOLVED_ACTIONS]


def derive_remove_keep(action, doi_1, doi_2):
    if action == "keep_1":
        return doi_2, doi_1
    if action == "keep_2":
        return doi_1, doi_2
    return None, None


def default_resolve_pair(side, doi_1, doi_2, is_preprint_1, is_preprint_2):
    """1. a lone preprint loses. 2. for DOI variants the non-canonical loses.
    3. otherwise doi_2 loses, deterministically. None when either DOI is absent."""
    if _blank(doi_1) or _blank(doi_2):
        return None
    if is_preprint_1 and not is_preprint_2:
        return doi_1, doi_2
    if is_preprint_2 and not is_preprint_1:
        return doi_2, doi_1
    if "DOI variant" in (side or ""):
        canon1 = normalize_doi(doi_1) == _s(doi_1).lower()
        canon2 = normalize_doi(doi_2) == _s(doi_2).lower()
        if canon1 and not canon2:
            return doi_2, doi_1
        if canon2 and not canon1:
            return doi_1, doi_2
    return doi_2, doi_1


def review_reason(candidate) -> "str | None":
    """Why a candidate must not be auto-resolved, or None when the default may run.

    A similar title only says two reports are about the same original; the first
    author is what says they are the same report. So a pair is held for a human when
    the first authors differ, and when both DOIs are preprints the authors must
    positively match — two preprints are otherwise resolved by the arbitrary doi_2
    tie-break. DOI variants are exempt: one normalised DOI already proves identity.
    """
    if "DOI variant" in _s(candidate.get("side")):
        return None
    # _s: a missing author can arrive as NaN once candidates pass through a frame.
    fa1, fa2 = _s(candidate.get("first_author_1")), _s(candidate.get("first_author_2"))
    if fa1 and fa2 and fa1 != fa2:
        return "first authors differ"
    # A generic OSF DOI counts here as it does in default_resolve_pair: otherwise an
    # OSF + preprint pair would fall to the arbitrary doi_2 tie-break this rule exists
    # to prevent.
    unpublished = [candidate.get(f"is_preprint_{n}") or is_repository_doi(candidate.get(f"doi_{n}"))
                   for n in (1, 2)]
    if all(unpublished) and not (fa1 and fa1 == fa2):
        return "two preprints without a matching first author"
    return None


# ── merging rows that share (doi_o, doi_r) ────────────────────────────────────

def _pick_outcome(values, normalise):
    seen = []
    for value in values:
        canonical = normalise(value)
        if canonical and canonical not in seen:
            seen.append(canonical)
    if not seen:
        return None
    if len(seen) == 1:
        return seen[0]
    if all(v in _MIXABLE for v in seen):
        return "mixed"
    return OUTCOME_CLASH_SEP.join(seen)


def _paste_unique(values, sep):
    seen, out = set(), []
    for value in values:
        text = _s(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return sep.join(out) or None


def _first_non_null(values):
    for value in values:
        if not _blank(value):
            return value
    return None


def _prefer_cos(values):
    present = [v for v in values if not _blank(v)]
    for value in present:
        if str(value).upper() == "COS":
            return value
    return present[0] if present else None


def website_overrides(rows, normalise_outcome=None) -> dict:
    """The values a website-validated record imposes on the row its group becomes.

    `rows` are the group's rows (dicts or Series). Returns {} when none comes from
    the website. The first website row carrying a judgement speaks for them: its
    judgement and its `source` win, and its evidence too when the group's judgements
    disagree. A blank field imposes nothing, so the other rows' value stands.

    Values only — never which row survives. Moving the survivor would move the
    registry's primary record, and with it, in edge cases, the published id.
    """
    normalise_outcome = normalise_outcome or (lambda v: _s(v) or None)
    website = [r for r in rows if _s(r.get("source")) == WEBSITE_SOURCE]
    if not website:
        return {}

    def has_judgement(r):
        return any(not _blank(r.get(c)) for c in _JUDGEMENT)

    preferred = next((r for r in website if has_judgement(r)), website[0])
    fields = list(_JUDGEMENT)
    # `source` credits the record the row publishes — except that the R rule of
    # crediting COS whenever a COS row took part still applies (_prefer_cos).
    if not any(_s(r.get("source")).upper() == "COS" for r in rows):
        fields.append("source")
    if has_judgement(preferred):
        judgements = {tuple(normalise_outcome(r.get(c)) if c == "outcome" else _s(r.get(c))
                            for c in _JUDGEMENT)
                      for r in rows if has_judgement(r)}
        if len(judgements) > 1:
            fields += _EVIDENCE
    return {c: (normalise_outcome(preferred.get(c)) if c == "outcome" else preferred.get(c))
            for c in fields if not _blank(preferred.get(c))}


def merge_doi_pair_dups(frame, normalise_outcome=None, verbose=True):
    """Merge rows sharing (type, doi_o, doi_r) when their url_r values are compatible.

    Two or more distinct non-null url_r means the rows are kept apart — they may be
    different replication reports of the same dataset. One or none means they are
    the same record entered twice, and their columns are combined rather than one
    row being dropped: a discarded row can carry the only outcome_quote there is.

    `type` is part of the key, as in the transform's own dedup: a replication and a
    reproduction of one paper are two records. Merging them published the
    reproduction as a replication, with both outcomes joined into an invalid value.

    A row a reviewer ruled 'distinct' in Source Records is never merged, as the
    transform's own dedup already exempts it: the ruling says it is its own record.
    """
    if frame.empty or not {"doi_o", "doi_r"} <= set(frame.columns):
        return frame, pd.DataFrame()

    normalise_outcome = normalise_outcome or (lambda v: _s(v) or None)
    # Absorbed record_ids must be recorded even before the transform's own dedup
    # has created the column: apply_confirmed() merges at step 2b, and ids dropped
    # there left flora_registry unable to recognise the row once its survivor changed
    # — it minted a new published id and retired the old one.
    if "record_id" in frame.columns and "merged_record_ids" not in frame.columns:
        frame = frame.assign(merged_record_ids=[[] for _ in range(len(frame))])
    has_pair = frame["doi_o"].notna() & frame["doi_r"].notna()
    ruled_distinct = (frame["duplicate_status"].eq("distinct")
                      if "duplicate_status" in frame.columns
                      else pd.Series(False, index=frame.index))
    untouched, pairs = frame[~has_pair | ruled_distinct], frame[has_pair & ~ruled_distinct]
    if pairs.empty:
        return frame, pd.DataFrame()

    keys = ["type", "doi_o", "doi_r"] if "type" in pairs.columns else ["doi_o", "doi_r"]
    merged_rows, kept_index, conflicts = [], [], []
    # dropna=False: a row without a type must still meet its duplicates.
    for key, group in pairs.groupby(keys, sort=False, dropna=False):
        doi_o, doi_r = key[-2], key[-1]
        distinct_urls = group["url_r"].dropna().nunique() if "url_r" in group else 0
        if len(group) <= 1 or distinct_urls > 1:
            kept_index.extend(group.index.tolist())
            continue

        row = {"doi_o": doi_o, "doi_r": doi_r}
        for column in group.columns:
            if column in ("doi_o", "doi_r"):
                continue
            values = list(group[column])
            if column == "study_o":
                row[column] = _paste_unique(values, "; ")
            elif column in ("outcome_quote", "outcome_quote_source", "out_quote_source"):
                row[column] = _paste_unique(values, OUTCOME_CLASH_SEP)
            elif column == "source":
                row[column] = _prefer_cos(values)
            elif column == "outcome":
                row[column] = _pick_outcome(values, normalise_outcome)
            elif column == "merged_record_ids":
                combined = []
                for value in values:
                    if isinstance(value, list):
                        combined.extend(value)
                row[column] = combined
            else:
                row[column] = _first_non_null(values)

        # A website-validated record's values win; the row stays the one it was.
        overrides = {}
        if "source" in group.columns and group["source"].eq(WEBSITE_SOURCE).any():
            overrides = website_overrides(
                [group.iloc[i] for i in range(len(group))], normalise_outcome)
            row.update({c: v for c, v in overrides.items() if c in group.columns})

        # Provenance has to survive the merge: the rows being folded in are the ones
        # flora_registry needs to recognise if a reviewer later promotes one of them,
        # and _first_non_null would simply drop their ids.
        if "record_id" in group.columns and "merged_record_ids" in row:
            survivor = row.get("record_id")
            absorbed = [r for r in group["record_id"] if r and r != survivor]
            row["merged_record_ids"] = list(row["merged_record_ids"] or []) + absorbed

        canonical = {normalise_outcome(v) for v in group["outcome"] if not _blank(v)} \
            if "outcome" in group else set()
        if len(canonical) > 1:
            conflicts.append({
                "doi_o": doi_o, "doi_r": doi_r,
                "type": _s(row.get("type")) or None,
                "outcomes": " | ".join(sorted(str(c) for c in canonical)),
                "resolved_to": row.get("outcome"),
                # Which rule settled it: the website record, or the merge rules.
                "resolved_by": "website record" if "outcome" in overrides else "merge rules",
                "url_r": _paste_unique(group.get("url_r", []), " | "),
                "sources": _paste_unique(group.get("source", []), " | "),
            })
        merged_rows.append(row)

    if not merged_rows:
        return frame, pd.DataFrame()

    out = pd.concat(
        [pairs.loc[kept_index], pd.DataFrame(merged_rows), untouched],
        ignore_index=True, sort=False,
    )
    conflict_frame = pd.DataFrame(conflicts)
    if verbose:
        print(f"  merged {len(merged_rows)} (doi_o, doi_r) group(s); "
              f"removed {len(frame) - len(out)} row(s)")
        if not conflict_frame.empty:
            retained = sum(1 for c in conflicts
                           if OUTCOME_CLASH_SEP in str(c["resolved_to"]))
            print(f"  ⚠ {len(conflicts)} merged group(s) had CONFLICTING outcomes "
                  f"(same paper, same url_r); {len(conflicts) - retained} collapsed "
                  f"to 'mixed', {retained} retained as a clash for validation")
    return out, conflict_frame


# ── detection ─────────────────────────────────────────────────────────────────

def _candidate(side, row_1, row_2, doi_key, title_key, author_key, year_key,
               similarity, group_key, doi_o_group=None):
    return {
        "side": side,
        "doi_1": row_1.get(doi_key), "doi_2": row_2.get(doi_key),
        "title_1": row_1.get(title_key), "title_2": row_2.get(title_key),
        "title_sim": round(similarity, 3),
        "first_author_1": extract_first_author(row_1.get(author_key)),
        "first_author_2": extract_first_author(row_2.get(author_key)),
        "year_1": _s(row_1.get(year_key)) or None,
        "year_2": _s(row_2.get(year_key)) or None,
        "is_preprint_1": is_preprint_doi(row_1.get(doi_key)),
        "is_preprint_2": is_preprint_doi(row_2.get(doi_key)),
        "group_key": group_key,
        "doi_o_group": doi_o_group,
    }


def _replication_context(row_1, row_2):
    """What a reviewer needs to tell two reports apart: the record each came from,
    its type and outcome, and its link. Replication side only — on the original side
    a row's outcome belongs to one of its replications, not to the paper."""
    out = {}
    for n, row in ((1, row_1), (2, row_2)):
        out[f"source_display_id_{n}"] = _s(row.get("display_id")) or None
        out[f"type_{n}"] = _s(row.get("type")) or None
        out[f"outcome_{n}"] = _s(row.get("outcome")) or None
        out[f"url_{n}"] = _s(row.get("url_r")) or None
    return out


def find_duplicates(frame, title_threshold=TITLE_THRESHOLD, verbose=True):
    """Candidate pairs, by the notebook's four routes (A, B1, B2, B3)."""
    candidates = []

    # A) replication-side: same doi_o, different doi_r
    replication_groups = 0
    for doi_o, group in frame[frame["title_r"].notna()].groupby("doi_o", sort=False):
        rows = group.to_dict("records")
        if len(rows) < 2:
            continue
        replication_groups += 1
        for i in range(len(rows) - 1):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if (not _blank(a.get("doi_r")) and not _blank(b.get("doi_r"))
                        and normalize_doi(a["doi_r"]) == normalize_doi(b["doi_r"])):
                    continue
                # A replication and a reproduction of one original are two
                # records, not one record under two DOIs.
                if _s(a.get("type")) != _s(b.get("type")):
                    continue
                similarity = title_similarity(a.get("title_r"), b.get("title_r"))
                if similarity < title_threshold:
                    continue
                fa1, fa2 = (extract_first_author(a.get("author_r")),
                            extract_first_author(b.get("author_r")))
                author_match = bool(fa1) and fa1 == fa2
                any_preprint = (is_preprint_doi(a.get("doi_r"))
                                or is_preprint_doi(b.get("doi_r")))
                # A similar title alone is not enough: replication reports of one
                # original legitimately share wording.
                if not author_match and not any_preprint:
                    continue
                candidate = _candidate(
                    "replication", a, b, "doi_r", "title_r", "author_r", "year_r",
                    similarity, f"doi_o: {doi_o}", doi_o)
                candidate.update(_replication_context(a, b))
                candidates.append(candidate)

    # The original side is judged per distinct doi_o, not per row.
    originals = (frame[frame["doi_o"].notna() & frame["title_o"].notna()]
                 .drop_duplicates(subset=["doi_o"]).copy())
    originals["_title_norm"] = originals["title_o"].apply(normalize_title)
    originals["_doi_norm"] = originals["doi_o"].apply(normalize_doi)
    originals["_author"] = originals["author_o"].apply(extract_first_author) \
        if "author_o" in originals else None

    def _pairs_within(grouped, side, skip):
        for key, group in grouped:
            rows = group.to_dict("records")
            for i in range(len(rows) - 1):
                for j in range(i + 1, len(rows)):
                    a, b = rows[i], rows[j]
                    if skip(a, b):
                        continue
                    similarity = (1.0 if side == "original"
                                  else title_similarity(a.get("title_o"), b.get("title_o")))
                    if side == "original (fuzzy)" and similarity < title_threshold:
                        continue
                    yield _candidate(side, a, b, "doi_o", "title_o", "author_o",
                                     "year_o", similarity, f"{key}")

    # B1) identical normalised title, different DOI
    by_title = [(f"title: {k[:60]}", g) for k, g in originals.groupby("_title_norm")
                if len(g) > 1]
    candidates += list(_pairs_within(
        by_title, "original", lambda a, b: a["_doi_norm"] == b["_doi_norm"]))

    seen = {pair_key(c["doi_1"], c["doi_2"]) for c in candidates}

    # B2) same DOI after normalisation, different as written
    by_doi = [(f"doi_variant: {k}", g) for k, g in originals.groupby("_doi_norm")
              if len(g) > 1]
    for candidate in _pairs_within(by_doi, "original (DOI variant)",
                                   lambda a, b: a["doi_o"] == b["doi_o"]):
        if pair_key(candidate["doi_1"], candidate["doi_2"]) not in seen:
            candidate["title_sim"] = round(
                title_similarity(candidate["title_1"], candidate["title_2"]), 3)
            candidates.append(candidate)
            seen.add(pair_key(candidate["doi_1"], candidate["doi_2"]))

    # B3) same first author, similar title
    if "_author" in originals and originals["_author"] is not None:
        by_author = [(f"author: {k}", g) for k, g in originals.groupby("_author")
                     if len(g) > 1]
        candidates += list(_pairs_within(
            by_author, "original (fuzzy)",
            lambda a, b: (a["_doi_norm"] == b["_doi_norm"]
                          or a["_title_norm"] == b["_title_norm"])))

    # One pair can be found by several routes; the first finding wins.
    deduped, seen = [], set()
    for candidate in candidates:
        key = pair_key(candidate["doi_1"], candidate["doi_2"])
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)

    if verbose:
        replication_n = sum(1 for c in deduped if c["side"] == "replication")
        print(f"  checked {replication_groups} doi_o group(s) and "
              f"{len(originals)} unique doi_o")
        print(f"  found {len(deduped)} candidate pair(s): "
              f"{replication_n} replication-side, "
              f"{len(deduped) - replication_n} original-side")
    return pd.DataFrame(deduped)


# ── application ───────────────────────────────────────────────────────────────

def _drop_replication(frame, doi_remove, doi_keep, annotate: bool):
    """Drop rows carrying the losing doi_r, but ONLY where the surviving one has a
    row of the same type under the same original.

    The original restriction stops the same preprint DOI paired with an unrelated
    original being removed as collateral. The type restriction stops a reproduction
    being removed because the published version was recorded only as a replication:
    those are two records about one paper, and only one of them has a survivor."""
    lower_r = frame["doi_r"].apply(lambda v: _s(v).lower())
    lower_o = frame["doi_o"].apply(lambda v: _s(v).lower())
    kind = frame["type"].apply(_s) if "type" in frame.columns else pd.Series("", index=frame.index)
    group = pd.Series(list(zip(lower_o, kind)), index=frame.index)
    keepers = set(group[lower_r == _s(doi_keep).lower()])
    if not keepers:
        return frame

    in_keeper_group = group.apply(lambda g: g in keepers)
    is_target = (lower_r == _s(doi_remove).lower()) & in_keeper_group
    if annotate:
        is_kept = (lower_r == _s(doi_keep).lower()) & in_keeper_group
        if is_kept.any():
            frame.loc[is_kept, "alt_identifier_r"] = [
                append_alt_identifier(v, doi_remove)
                for v in frame.loc[is_kept, "alt_identifier_r"]
            ]
    return frame[~is_target]


def _replace_original(frame, mapping, annotate_removals):
    lower_o = frame["doi_o"].apply(lambda v: _s(v).lower())
    matched = lower_o.isin(mapping)
    annotate = lower_o.isin(annotate_removals)
    if annotate.any():
        frame.loc[annotate, "alt_identifier_o"] = [
            append_alt_identifier(alt, doi)
            for alt, doi in zip(frame.loc[annotate, "alt_identifier_o"],
                                frame.loc[annotate, "doi_o"])
        ]
    if matched.any():
        frame.loc[matched, "doi_o"] = [mapping[v] for v in lower_o[matched]]
    return frame


def _ensure_alt_columns(frame):
    for column in ("alt_identifier_o", "alt_identifier_r"):
        if column not in frame.columns:
            frame[column] = None
    return frame


def apply_confirmed(frame, confirmed_path: Path = CONFIRMED_PATH,
                    normalise_outcome=None, verbose=True, conflicts_out=None,
                    rulings=None):
    """Step 6a — apply only the confirmed keep_1/keep_2 rows, before enrichment.

    `rulings` are admin rulings from the FLoRA tab, overlaid on the file.
    """
    frame = _ensure_alt_columns(frame.copy())
    confirmed = [r for r in confirmed_decisions(confirmed_path, rulings)
                 if r["action"] in APPLY_ACTIONS]
    if not confirmed:
        if verbose:
            print("  no confirmed preprint duplicates to apply")
        return frame

    before = len(frame)
    for row in confirmed:
        remove, keep = derive_remove_keep(row["action"], row["doi_1"], row["doi_2"])
        if _blank(remove) or _blank(keep):
            continue
        if (row.get("side") or "").startswith("replication"):
            frame = _drop_replication(frame, remove, keep, annotate=True)

    originals = [r for r in confirmed if (r.get("side") or "").startswith("original")]
    if originals:
        mapping, removals = {}, set()
        for row in originals:
            remove, keep = derive_remove_keep(row["action"], row["doi_1"], row["doi_2"])
            if not _blank(remove) and not _blank(keep):
                mapping[_s(remove).lower()] = _s(keep).lower()
                removals.add(_s(remove).lower())
        frame = _replace_original(frame, mapping, removals)
        frame, conflicts = merge_doi_pair_dups(frame, normalise_outcome, verbose=False)
        # Collected rather than dropped: a merge here can be the only place an
        # outcome clash is ever detected, and the R side logs every one of them.
        if conflicts_out is not None and not conflicts.empty:
            conflicts_out.extend(conflicts.to_dict("records"))

    if verbose:
        print(f"  applied {len(confirmed)} confirmed resolution(s); "
              f"net rows removed: {before - len(frame)}")
    return frame


def write_candidates(decisions, path: Path) -> None:
    """The candidates log, headed by a row telling a reader what to do with it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    instructions = {
        "side": "INSTRUCTIONS -->", "doi_1": "DOI #1", "doi_2": "DOI #2",
        "applied_action": "auto-default = drop one (preprint loses; else doi_2); "
                          "needs_review = kept both, differing first authors "
                          "or two preprints without a matching author",
        "resolution": "Decide in the website's FLoRA tab (Preprint duplicates), or "
                      "copy the row into cache/confirmed_preprint_duplicates.csv "
                      "with action keep_both / keep_1 / keep_2",
    }
    pd.concat([pd.DataFrame([instructions]), pd.DataFrame(list(decisions))],
              ignore_index=True) \
      .to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def resolve(frame, confirmed_path: Path = CONFIRMED_PATH,
            candidates_out: Path = CANDIDATES_PATH,
            title_threshold=TITLE_THRESHOLD, normalise_outcome=None, verbose=True,
            conflicts_out=None, open_review_issue=False, rulings=None):
    """Step 7d — detect candidates and apply overrides or the default rule.

    `rulings` are admin rulings from the FLoRA tab, overlaid on the file.
    """
    frame = _ensure_alt_columns(frame.copy())
    candidates = find_duplicates(frame, title_threshold, verbose)
    if candidates.empty:
        if verbose:
            print("  no preprint-publication duplicates detected")
        return frame, candidates

    overrides = {pair_key(r["doi_1"], r["doi_2"]): r
                 for r in confirmed_decisions(confirmed_path, rulings)}

    decisions = []
    for candidate in candidates.to_dict("records"):
        key = pair_key(candidate["doi_1"], candidate["doi_2"])
        override = overrides.get(key)
        decision = dict(candidate)
        if override:
            action = override["action"]
            if action == "keep_both":
                decision.update(resolution="override: keep_both",
                                applied_action="keep_both",
                                doi_remove=None, doi_keep=None)
            else:
                remove, keep = derive_remove_keep(action, override["doi_1"],
                                                  override["doi_2"])
                decision.update(resolution=f"override: {action}",
                                applied_action=action,
                                doi_remove=remove, doi_keep=keep)
        elif _blank(candidate["doi_1"]) or _blank(candidate["doi_2"]):
            # Before review: a ruling names both DOIs, so a pair without one could
            # never be cleared from the queue and would warn on every run.
            decision.update(resolution="skipped: NA DOI in pair",
                            applied_action="skipped",
                            doi_remove=None, doi_keep=None)
        elif (reason := review_reason(candidate)) is not None:
            decision.update(resolution=f"review: {reason}",
                            applied_action=NEEDS_REVIEW,
                            doi_remove=None, doi_keep=None)
        else:
            # A repository DOI is not a preprint for detection, but against a
            # publisher DOI it is still the less canonical record.
            result = default_resolve_pair(
                candidate["side"], candidate["doi_1"], candidate["doi_2"],
                candidate["is_preprint_1"] or is_repository_doi(candidate["doi_1"]),
                candidate["is_preprint_2"] or is_repository_doi(candidate["doi_2"]))
            if result is None:
                decision.update(resolution="skipped: NA DOI in pair",
                                applied_action="skipped",
                                doi_remove=None, doi_keep=None)
            else:
                remove, keep = result
                dropped = "doi_1" if remove == candidate["doi_1"] else "doi_2"
                decision.update(
                    resolution=f"auto: drop {dropped}",
                    applied_action="auto_keep_2" if dropped == "doi_1" else "auto_keep_1",
                    doi_remove=remove, doi_keep=keep)
        decisions.append(decision)

    before = len(frame)
    confirmed_actions = VALID_ACTIONS

    for decision in decisions:
        if _blank(decision["doi_remove"]) or not decision["side"].startswith("replication"):
            continue
        frame = _drop_replication(
            frame, decision["doi_remove"], decision["doi_keep"],
            annotate=decision["applied_action"] in confirmed_actions)

    originals = [d for d in decisions
                 if d["side"].startswith("original") and not _blank(d["doi_remove"])]
    if originals:
        mapping = {_s(d["doi_remove"]).lower(): _s(d["doi_keep"]).lower()
                   for d in originals}
        removals = {_s(d["doi_remove"]).lower() for d in originals
                    if d["applied_action"] in confirmed_actions}
        frame = _replace_original(frame, mapping, removals)
        frame, conflicts = merge_doi_pair_dups(frame, normalise_outcome, verbose=False)
        if conflicts_out is not None and not conflicts.empty:
            conflicts_out.extend(conflicts.to_dict("records"))

    log = pd.DataFrame(decisions)
    if candidates_out:
        write_candidates(decisions, candidates_out)

    if verbose:
        auto = sum(1 for d in decisions if d["resolution"].startswith("auto"))
        over = sum(1 for d in decisions if d["resolution"].startswith("override"))
        skip = sum(1 for d in decisions if d["resolution"].startswith("skipped"))
        review = sum(1 for d in decisions if d["resolution"].startswith("review"))
        print(f"  auto-dropped: {auto} | override: {over} | "
              f"held for review (kept both): {review} | skipped (NA DOI): {skip}")
        print(f"  net rows removed: {before - len(frame)}")
        if candidates_out:
            print(f"  candidates log: {candidates_out}")

    # OFF by default, and that default matters: build() runs on every FLoRA tab
    # load, so a default of True would file GitHub issues when somebody opens a
    # page. Only the CLI and the nightly job turn it on.
    if open_review_issue:
        # build() writes no log itself (run() does, under the same name), but the
        # issue text still has to point at one.
        # A ruling made in the tab is review activity just as an edit of the file is.
        ruled_at = [r.get("decided_at") for r in rulings or [] if r.get("decided_at")]
        maybe_open_review_issue(decisions, confirmed_path, candidates_out or CANDIDATES_PATH,
                                verbose=verbose, last_ruling_at=max(ruled_at, default=None))
    return frame, log


# ── review issue for unconfirmed auto-resolutions ─────────────────────────────

# Carried in the issue title so a later run can find its own issue rather than
# filing a new one every night.
DEDUP_ISSUE_MARKER = "[preprint-dedup-review]"
DEDUP_STALE_DAYS = 7


def _gh(*args, timeout=60):
    """Run `gh`, returning stdout or None. Never raises.

    Every failure here — gh missing, not a repo, no token, network down — is an
    environment problem, and none of them is a reason to fail a dataset build
    that has already succeeded.
    """
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(("gh",) + args, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except Exception:                                          # noqa: BLE001
        return None
    return result.stdout if result.returncode == 0 else None


def _days_since(moment) -> float:
    if moment is None:
        return float("inf")
    return (datetime.now(timezone.utc) - moment).total_seconds() / 86400


def _issue_body(auto_rows, confirmed_path: Path, candidates_out: Path,
                stale_days: int, age_days: float) -> str:
    preview_n = min(20, len(auto_rows))
    preview = "\n".join(
        "- `{}` <-> `{}` ({}, sim={}) — {}".format(
            row.get("doi_1"), row.get("doi_2"), row.get("side"),
            row.get("title_sim", "?"), row.get("applied_action"))
        for row in auto_rows[:preview_n]
    )
    age = "never written" if age_days == float("inf") else f"{age_days:.1f} days ago"
    return (
        f"The FLoRA preparation pipeline auto-resolved or held for review "
        f"**{len(auto_rows)}** preprint/publication duplicate pair(s) "
        f"(`needs_review` pairs keep both rows), but `{confirmed_path.name}` has "
        f"not been touched in over {stale_days} days (last edit {age}).\n\n"
        "Please decide them in the website's FLoRA tab (Preprint duplicates), or add "
        f"rows to `cache/{confirmed_path.name}` with `action=keep_1` / `keep_2` to "
        "confirm the auto-default, or `keep_both` to override it.\n\n"
        "Until each pair is confirmed, the surviving row's `alt_identifier_*` will "
        "**not** be annotated with the dropped DOI.\n\n"
        f"**First {preview_n} of {len(auto_rows)} unconfirmed pair(s):**\n\n"
        f"{preview}\n\n"
        f"Full log: `{candidates_out.name}` (uploaded as a workflow artifact)\n\n"
        "_Filed automatically by `preprint_dedup.py`._"
    )


def maybe_open_review_issue(decisions, confirmed_path: Path = CONFIRMED_PATH,
                            candidates_out: Path = CANDIDATES_PATH,
                            stale_days: int = DEDUP_STALE_DAYS,
                            verbose: bool = True, last_ruling_at=None) -> str:
    """Nudge a human when pairs are being auto-resolved and nobody is reviewing.

    Three outcomes, matching the R original:
      - no open issue carrying the marker   -> file one
      - an open one, active within the window -> leave it alone
      - an open one, itself stale             -> add a comment

    Staleness is measured on the confirmed file's mtime: pairs being resolved by
    the default rule are fine as long as somebody is still confirming them.
    """
    # Held pairs need a human as much as guessed ones: until confirmed, a real
    # preprint duplicate stays in the dataset twice.
    auto_rows = unresolved(decisions)
    if not auto_rows:
        if verbose:
            print("  no auto-resolved pairs — no review issue needed")
        return "skipped"

    age_days = (_days_since(
        datetime.fromtimestamp(confirmed_path.stat().st_mtime, timezone.utc))
        if confirmed_path.exists() else float("inf"))
    # Rulings made in the FLoRA tab never touch the file, so they count as well.
    if last_ruling_at is not None:
        age_days = min(age_days, _days_since(last_ruling_at))
    if age_days <= stale_days:
        if verbose:
            print(f"  {len(auto_rows)} auto-resolved pair(s); confirmed file touched "
                  f"{age_days:.1f} day(s) ago — no issue needed")
        return "skipped"

    if shutil.which("gh") is None:
        if verbose:
            print("  ⚠ gh CLI not found; skipping the review issue")
        return "skipped"

    body = _issue_body(auto_rows, confirmed_path, candidates_out, stale_days, age_days)

    listing = _gh("issue", "list", "--state", "open", "--search", DEDUP_ISSUE_MARKER,
                  "--json", "number,title,updatedAt", "--limit", "20")
    try:
        existing = [i for i in json.loads(listing or "[]")
                    if DEDUP_ISSUE_MARKER in (i.get("title") or "")]
    except Exception:                                          # noqa: BLE001
        existing = []

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as handle:
        body_file = handle.name
        handle.write(body)

    try:
        if not existing:
            title = (f"{DEDUP_ISSUE_MARKER} {len(auto_rows)} unconfirmed "
                     f"preprint-duplicate exclusions need review")
            out = _gh("issue", "create", "--title", title, "--body-file", body_file)
            if out:
                if verbose:
                    print(f"  ✓ filed review issue: {out.strip()}")
                return "filed"
            if verbose:
                print("  ⚠ gh issue create produced no output")
            return "skipped"

        # The most recently touched one; an older duplicate is left alone.
        def _updated(issue):
            try:
                return datetime.strptime(issue["updatedAt"], "%Y-%m-%dT%H:%M:%SZ") \
                    .replace(tzinfo=timezone.utc)
            except Exception:                                  # noqa: BLE001
                return datetime.fromtimestamp(0, timezone.utc)

        issue = max(existing, key=_updated)
        issue_age = _days_since(_updated(issue))
        if issue_age <= stale_days:
            if verbose:
                print(f"  open review issue #{issue['number']} last active "
                      f"{issue_age:.1f} day(s) ago — leaving it alone")
            return "skipped"

        with io.open(body_file, "w", encoding="utf-8") as handle:
            handle.write(
                f"Still **{len(auto_rows)}** unconfirmed preprint-duplicate "
                f"exclusion(s) outstanding (this issue was last touched "
                f"{issue_age:.1f} days ago).\n\n" + body)

        out = _gh("issue", "comment", str(issue["number"]), "--body-file", body_file)
        if out:
            if verbose:
                print(f"  ✓ commented on review issue #{issue['number']}")
            return "commented"
        if verbose:
            print("  ⚠ gh issue comment produced no output")
        return "skipped"
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass
