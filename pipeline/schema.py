"""Leaf FLoRA schema constants, text/DOI cleaning, dedup keys, and validation findings.

Kept free of orchestrator imports (transform_sources, flora_edits, flora_store,
flora_registry, enrich_works, validate_flora) so the pipeline forms an acyclic graph.
"""
import hashlib
import html
import re
import unicodedata
import urllib.parse
from html.entities import html5

import pandas as pd

import author_overlap
import final_export


# The column set the FLoRA pipeline works with (flora_cols in the R notebook).
FLORA_COLUMNS = [
    "doi_o", "ref_o", "url_o",
    "doi_r", "ref_r", "url_r",
    "abstract_r",
    "outcome", "outcome_quote", "outcome_quote_source",
    "type", "source",
    "alt_identifier_o", "alt_identifier_r",
    # Reproductions only; NULL on replications. The independent coded axes and
    # their evidence are exported alongside the derived flat outcome. New columns
    # go LAST so positional readers of the existing export keep working.
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
]

# The FLoRA *output* contract — `output_cols` in the R notebook, the shape of
# flora.csv. FLORA_COLUMNS above is the notebook's *input* selection (`flora_cols`);
# this is what it emits after enrichment.
#
# 15 of these we produce ourselves. The other 20 are enrichment the R pipeline fetches
# from CrossRef (title/author/journal/year/volume/issue/pages/bibtex), OpenAlex
# (language) and Unpaywall (oa_url). They are emitted as EMPTY columns rather than
# omitted, so the file has the right shape and the pipeline's left_join/coalesce steps
# fill them — an absent column would make those joins fail, a blank one just gets
# filled.
FLORA_OUTPUT_COLUMNS = [
    "doi_o", "alt_identifier_o", "doi_o_hash", "title_o", "author_o", "journal_o",
    "year_o", "volume_o", "issue_o", "pages_o", "apa_ref_o", "bibtex_ref_o",
    "url_o", "language_o",
    "doi_r", "alt_identifier_r", "doi_r_hash", "title_r", "author_r", "journal_r",
    "year_r", "volume_r", "issue_r", "pages_r", "apa_ref_r", "bibtex_ref_r",
    "url_r", "language_r",
    "oa_url_o", "oa_url_r",
    "outcome", "outcome_quote", "outcome_quote_source", "type", "source",
]

# Filled from work_metadata by the enrichment join in build(). Named here so the
# projection inside build() keeps them — reindexing to FLORA_COLUMNS alone silently
# dropped every one of them before to_output_shape could ever see them.
ENRICHMENT_COLUMNS = [
    f"{field}_{side}"
    for side in ("o", "r")
    for field in ("title", "author", "journal", "year", "volume", "issue",
                  "pages", "language", "oa_url", "oa_work_id", "bibtex_ref")
]

# Computed by build() rather than read or fetched. Named here for the same reason
# ENRICHMENT_COLUMNS is: the projection at the end of build() reindexes to an
# explicit list, so a column missing from it is silently dropped before
# to_output_shape can ever see it.
DERIVED_COLUMNS = ["author_overlap", "author_overlap_pct"]

# Kept on the built frame for the FLoRA tab, never published: to_output_shape
# leaves them out. outcome_conflict is a clash of duplicates' outcomes no rule
# settled (preprint_dedup.CONFLICT_COLUMN), listed under Conflicts until a person does.
REVIEW_COLUMNS = ["outcome_conflict"]

# Our columns under the output contract's names.
OUTPUT_RENAMES = {"ref_o": "apa_ref_o", "ref_r": "apa_ref_r"}

# The admin grid retains source registry keys; the published CSV uses the names
# from the supplied preparation notebook and reference snapshot.
OUTPUT_SOURCES = {
    "entry_sheet_replications": "replications",
    "entry_sheet_reproductions": "reproductions",
    "fred_replication_success": "COS",
    "score_2025": "SCORE",
}

# Kept AFTER the 35 rather than dropped. The output contract has no home for them,
# but abstract_r feeds downstream classification and the reproduction axes are the
# authoritative coded fields the flat `outcome` is derived FROM — discarding either
# to match a column list exactly would lose data the database is the only copy of.
# Readers select by name, so trailing columns cost nothing.
#
# oa_work_id_* is OpenAlex's own identifier for the paper ('W2168190474'), which is
# what lets a row be looked up in OpenAlex without a DOI round-trip. enrich_works.py
# has always stored it; until now nothing joined it in, so it sat in the cache while
# the product carried only oa_url_*, the open-access LINK. Those are different things:
# a paper has a work id whether or not anyone has posted a free copy of it.
OUTPUT_EXTRAS = [
    "oa_work_id_o", "oa_work_id_r",
    # How many family names the two author lists share, and that as a percentage of
    # the replication team. A replication by the original authors is a different
    # kind of evidence from one by strangers, and nothing else in the output says so.
    "author_overlap", "author_overlap_pct",
    "abstract_r",
    "outcome_computation", "outcome_computational_quote", "out_quote_computational_source",
    "outcome_robustness", "outcome_robustness_quote", "out_quote_robust_source",
    *final_export.REPLICATION_COLUMNS,
]


# Step 9d of the R notebook. Replication papers that aggregate many studies: a
# reference to one of these alone does not tell a reader which of the aggregated
# studies a row is about, so the row's own url_r is appended to the reference.
#
# Kept as a list here, as the notebook keeps it: it is a curated judgement, not
# something a sheet supplies. The candidate warning below is what keeps it current.
META_PAPER_DOIS = {
    "10.1038/s41562-024-02062-9",   # Holzmeister et al. 2024
    "10.3389/fcomm.2022.1048896",   # 10 cases of sensory research
    "10.1073/pnas.2103313118",      # scarcity effects
    "10.1007/s13164-018-0400-9",    # X-PHI
    "10.1038/s41562-018-0399-z",    # Camerer 2018
    "10.1098/rsos.231240",          # Boyce
    "10.1027/1864-9335/a000178",    # ML1
    "10.1177/2515245918810225",     # ML2
    "10.1016/j.jesp.2015.10.012",   # ML3
    "10.1126/science.aac4716",      # RP:P
    "10.1177/08902070221094216",    # SVO development
    "10.1126/science.aaf0918",      # Camerer 2016
}

META_PAPER_NOTE = "Individual report available at"

# A replication DOI carrying this many distinct url_r values is probably a
# meta-paper nobody has classified yet.
META_PAPER_CANDIDATE_URLS = 5

# JATS/HTML markup that reaches us inside titles and abstracts: <i>, <sup>, <scp>,
# and whole <mml:math> blocks from publisher XML. A tag by its shape: a name, "?" or
# "!" straight after "<" or "</", and no "<" inside: "<i>", "<?Pub Caret?>",
# "<!-- -->", but not "< .05" or "<1.0E-6". Those are comparisons, and anything looser
# takes the text up to the next ">" with them, however far off it is. Nor is a name with
# a hyphen before a digit: "<halshs-01945777>" is a HAL archive's identifier.
_TAG_RE = re.compile(r"</?(?![\w:.-]*-\d)[A-Za-z!?][^<>]*>")
# Tags side by side are replaced as one, by what the text either side of them all needs.
_TAG_RUN_RE = re.compile(f"(?:{_TAG_RE.pattern})+")
# A tag's name without its namespace: "p" in <jats:p>, "br" in <br/>.
_TAG_NAME_RE = re.compile(r"</?(?:[\w.-]+:)?([\w.-]*)")
# Tags that end a paragraph, namespaced or not (<jats:title>, <ns3:sec>): the heading in
# "<h3>Introduction</h3> Achilles…" is not the first word of the sentence after it.
_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "sec", "title",
                         "li", "br"})
# Tags around a part of the word before them: CO<sub>2</sub>, 10<sup>-3</sup>.
_ATTACHED_TAGS = frozenset({"sub", "sup"})
# A footnote marker closing a title: "…Decision Makers<sup>1</sup>", "…Comparisons?
# <sup>1</sup>". Only at the very end, after a word of three letters or more or after
# punctuation, so "in km<sup>2</sup>" and "R<sup>2</sup>" keep their exponent.
_FOOTNOTE_MARK = r"(?:\d{1,2}|[*†‡§¶#]{1,3})"
_TRAILING_FOOTNOTE_RE = re.compile(
    r"(?:(?<=[^\W\d_]{3})|(?<=[.?!:)\]\"'”’]))"
    rf"\s*<sup>\s*{_FOOTNOTE_MARK}(?:\s*,\s*{_FOOTNOTE_MARK})*\s*</sup>\s*$")
# A single newline inside a paragraph is a PDF copy artefact — the sentence simply
# continued. Two or more are a real paragraph break and are preserved.
_SOFT_BREAK_RE = re.compile(r"(?<!\n)\n(?!\n)")
# However many newlines, and whatever spaces between them: one paragraph break.
_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")
# The Unicode spaces too: a four-per-em space (U+2005) published "84  TIMP2".
_WS_RE = re.compile(r"[ \t\xa0\u2000-\u200a\u202f\u205f\u3000]+")
# Text whose paragraphs are kept. Every other cleaned column is shown on one line, and
# a title is part of validated's key, so a <jats:title> inside one must not break it.
PARAGRAPH_COLUMNS = ("abstract_r", "outcome_quote", "outcome_computational_quote",
                     "outcome_robustness_quote")


def _s(value) -> str:
    """Stripped string, with NaN/None as empty. Used by the reporting paths."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


_ENTITY_RE = re.compile(r"&[A-Za-z]{2,8};")


def _lower_unknown_entity(match):
    """`&Amp;` -> `&amp;`, but `&Auml;` left exactly as it is.

    Lowercasing every entity is NOT safe: 375 of the HTML5 named entities differ
    only by case and mean different characters. `&Auml;` is Ä and `&auml;` is ä, so
    a blanket lowercase turns Ängström into ängström and, in a statistics title,
    &Delta; (Δ) into δ and &Omega; (Ω) into ω. The capitalised forms CrossRef
    actually mis-emits — `&Amp;`, `&Rsquo;` — are not entities under any casing,
    which is what separates them from the ones that must be left alone.
    """
    name = match.group(0)[1:]                 # "Amp;" / "Auml;"
    if name in html5 or name.lower() not in html5:
        return match.group(0)
    return match.group(0).lower()


# Enough for the double encoding OpenAlex and the validated set send ("&amp;amp;"),
# with a margin; a bound so text that keeps producing entities cannot loop forever.
_UNESCAPE_PASSES = 3
# An entity written out in full, with its semicolon.
_WHOLE_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")


def _decode_whole_entity(match):
    """`&amp;` -> `&`; `&region;` left alone, which html.unescape makes `®ion;`."""
    entity = match.group(0)
    if entity[1] == "#" or entity[1:] in html5:
        return html.unescape(entity)
    return entity


def unescape_entities(text):
    """Entities decoded until none are left.

    One pass is not enough: titles arrive double-encoded ("Rich &amp;amp; Gureckis",
    "A&amp;nbsp;replication"), and a single pass published "&amp;".

    Only whole entities, never html.unescape's legacy forms without a semicolon:
    those read "Smith &notes" as "Smith ¬es" and a link's "&region=us" as "®ion=us".
    No source cell needs them, and without them decoding text already decoded leaves
    it as it is, so a FLoRA tab correction is not changed by being cleaned again.
    """
    for _ in range(_UNESCAPE_PASSES):
        decoded = _WHOLE_ENTITY_RE.sub(_decode_whole_entity,
                                       _ENTITY_RE.sub(_lower_unknown_entity, text))
        if decoded == text:
            break
        text = decoded
    return text


def _replace_tags(match):
    """What a run of tags leaves: a paragraph break for a block, nothing beside a sub-
    or superscript, and a space only between two words, so "TIMP2</i>AND" reads
    "TIMP2 AND" and "<i>et al</i>." reads "et al."."""
    names = {_TAG_NAME_RE.match(tag).group(1).lower()
             for tag in _TAG_RE.findall(match.group(0))}
    if names & _BLOCK_TAGS:
        return "\n\n"
    if names & _ATTACHED_TAGS:
        return ""
    text, start, end = match.string, match.start(), match.end()
    between_words = (0 < start and end < len(text)
                     and text[start - 1].isalnum() and text[end].isalnum())
    return " " if between_words else ""


def _mojibake_alphabet(codec):
    """Each byte 0x80-0xFF as `codec` shows it. Bytes cp1252 leaves undefined (0x81,
    0x8D, 0x8F, 0x90, 0x9D) arrive as the control character of the same number."""
    return {byte: bytes([byte]).decode(codec, errors="ignore") or chr(byte)
            for byte in range(0x80, 0x100)}


def _mojibake_pattern(alphabet, leads):
    """UTF-8 sequences as `alphabet` mis-decodes them, as a regex alternative.

    `leads` lists each allowed lead byte range with the number of continuation bytes
    it takes, so only a well-formed UTF-8 sequence matches. A fourth item, when
    given, is the lowest first continuation byte the lead may take.
    """
    def chars(lo, hi):
        return "".join(re.escape(alphabet[b]) for b in range(lo, hi + 1))
    parts = []
    for lo, hi, n, *first in leads:
        start = first[0] if first else 0x80
        tail = f"[{chars(start, 0xBF)}]" + (f"[{chars(0x80, 0xBF)}]{{{n - 1}}}" if n > 1 else "")
        parts.append(f"[{chars(lo, hi)}]{tail}")
    return "|".join(parts)


# UTF-8 text decoded as Windows-1252 ("learnerâ€™s", "KÃ¶bach") and as Mac Roman
# ("Kruger‚Äôs"), as provider metadata and pasted sheet cells deliver it. Narrowed to
# the lead bytes those sources produce, because a pattern this short can also be
# real text: Mac Roman shows "“É" as a two-byte sequence, and French "à « " (with a
# no-break space) reads as a three-byte one. So two-byte sequences are repaired only
# for U+0080 to U+017F (Â, Ã, Ä, Å leads: accented Latin letters and Polish/Czech
# ones) and Greek (Î, Ï: "Î±" for α), and three-byte ones only for U+2000 to U+2FFF
# (â€™ for ’, â€“ for –, ‚Äô in Mac Roman): punctuation and symbols.
# One pattern, Mac Roman first: scanning left to right, "‚â•‚ÄÖ" must read as Mac
# Roman's "≥" and a six-per-em space, not as Windows-1252's "â•‚" in its middle.
_CP1252 = _mojibake_alphabet("cp1252")
_MAC_ROMAN = _mojibake_alphabet("mac_roman")
# Mac Roman's "¬" (0xC2) is the lead of Latin-1 symbols ("¬±" is ±, "¬∞" is °),
# common in reported statistics. Greek leads (Î, Ï) need a continuation byte above
# 0xA0, so "Î" before a no-break space stays as written.
_MOJIBAKE_RE = re.compile(
    f"(?P<mac_roman>{_mojibake_pattern(_MAC_ROMAN, [(0xE2, 0xE2, 2), (0xC2, 0xC2, 1)])})"
    f"|(?P<cp1252>{_mojibake_pattern(_CP1252, [(0xC2, 0xC5, 1), (0xCE, 0xCF, 1, 0xA1), (0xE2, 0xE2, 2)])})")
_TO_BYTE = {"mac_roman": {char: byte for byte, char in _MAC_ROMAN.items()},
            "cp1252": {char: byte for byte, char in _CP1252.items()}}
# Enough for text mis-decoded three times ("ÃƒÅ½Ã‚Â±" is α), with a bound.
_MOJIBAKE_PASSES = 4


def _undo_mojibake(match):
    to_byte = _TO_BYTE[match.lastgroup]
    raw = bytes(to_byte[char] for char in match.group(0))
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return match.group(0)
    # A control or format character is no repair: "Â" before a C1 control byte, or
    # "â" and a no-break space before "«" (U+202B), which is real Portuguese text.
    if unicodedata.category(decoded) in ("Cc", "Cf"):
        return match.group(0)
    return decoded


def has_mojibake(value) -> bool:
    """Whether text holds UTF-8 that was decoded with the wrong character set."""
    return isinstance(value, str) and repair_mojibake(value) != value


def repair_mojibake(value):
    """`value` with mis-decoded UTF-8 decoded again; other text left exactly as it is.

    Only well-formed UTF-8 sequences in the alphabets above are touched, so a real
    "Köbach" or "Ä" stays. Non-strings pass through.
    """
    if not isinstance(value, str):
        return value
    for _ in range(_MOJIBAKE_PASSES):
        repaired = _MOJIBAKE_RE.sub(_undo_mojibake, value)
        if repaired == value:
            break
        value = repaired
    return value


def clean_text(value, one_line=False):
    """Strip markup, decode entities, and repair PDF-copied line breaks.

    Step 7c of the R notebook. Runs before anything compares strings, so a title
    differing only by an `<i>` is not mistaken for a different paper.

    Paragraphs come out separated by one blank line, or by a space with `one_line`,
    for a column outside PARAGRAPH_COLUMNS. A one-line value also drops a footnote
    marker at its end, which would otherwise read "Decision Makers1".
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value)
    if not text.strip():
        return None
    if one_line:
        text = _TRAILING_FOOTNOTE_RE.sub("", text)
    text = _TAG_RUN_RE.sub(_replace_tags, text)
    # Unrecognised capitalised entities are lowercased before unescaping: CrossRef
    # returns forms ("&Amp;", "&Rsquo;") that html.unescape does not recognise, and
    # which then reach the published reference verbatim. Only an entity that is not
    # itself a real one is touched — see _lower_unknown_entity.
    text = unescape_entities(text)
    # After the entities: "&acirc;&euro;&trade;" is mojibake once decoded.
    text = repair_mojibake(text)
    # Tags stripped again: publisher XML double-encodes, so &amp;lt;i&amp;gt; only
    # becomes a literal tag once unescaped.
    if "<" in text and ">" in text:
        text = _TAG_RUN_RE.sub(_replace_tags, text)
    # A carriage return, written out or as the &#13; decoded above, is a line break
    # like any other: left in, a page that keeps an abstract's breaks shows each one.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SOFT_BREAK_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    paragraphs = (part.strip() for part in _PARAGRAPH_BREAK_RE.split(text))
    return (" " if one_line else "\n\n").join(part for part in paragraphs if part) or None


def drop_untitled(frame):
    """Rows with a title on both sides — the notebook's Step 10 filter.

    Shared by the export and by validate_flora's load_dataset so that what ships
    and what gets validated are the same set of rows. Two copies of this predicate
    would drift, and the report would then flag rows nobody can act on because they
    were never published.
    """
    if not {"title_o", "title_r"} <= set(frame.columns):
        return frame
    # astype(bool): mapping an EMPTY str column keeps the str dtype, and two string
    # arrays cannot be combined with &.
    return frame[frame["title_o"].map(lambda value: bool(_s(value))).astype(bool)
                 & frame["title_r"].map(lambda value: bool(_s(value))).astype(bool)]


def missing_title_report(frame):
    """Rows the R notebook's Step 10 drops for want of a title, with the reason.

    Returned rather than written here so the caller decides where it goes — and so
    the report exists even when the filter itself is off.
    """
    def _blank(value):
        return value is None or (isinstance(value, float) and pd.isna(value)) \
            or not str(value).strip()

    rows = []
    for _, row in frame.iterrows():
        no_o, no_r = _blank(row.get("title_o")), _blank(row.get("title_r"))
        if not (no_o or no_r):
            continue
        if no_o and no_r:
            reason = "missing both title_o and title_r"
        elif no_o:
            reason = "missing title_o"
        else:
            reason = "missing title_r"
        rows.append({
            "reason": reason,
            "flora_id": row.get("flora_id"),
            "source": row.get("source"),
            "doi_o": row.get("doi_o"),
            "apa_ref_o": row.get("ref_o"),
            "doi_r": row.get("doi_r"),
            "url_r": row.get("url_r"),
            "apa_ref_r": row.get("ref_r"),
        })
    return pd.DataFrame(rows)


def doi_hash(value):
    """First 3 hex chars of the DOI's md5 — `substr(openssl::md5(doi), 1, 3)` in R.

    A privacy-preserving bucket for API lookup, not an identifier: three characters
    is 4,096 buckets, so it is deliberately lossy.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:3]


def to_output_shape(frame, keep_extras: bool = True):
    """Project a built frame onto the FLoRA output contract.

    Columns we cannot fill are created empty rather than omitted — see
    FLORA_OUTPUT_COLUMNS.
    """
    from bibliographic_helpers import normalise_author_json
    out = frame.rename(columns=OUTPUT_RENAMES).copy()
    # Use the same normalisation for scoring plain bylines and publishing them.
    # Export preserves unresolved entries and uses the scorer's plain-name rules,
    # so recomputing overlap from the published author lists gives the same result.
    author_overlap.augment(out, verbose=False)
    # Every author cell as fred-data's compact JSON list, whatever its source wrote.
    # (Workbook escapes such as "_x000D_" are decoded where a workbook is read, not
    # here: text of our own can contain the pattern, e.g. a BibTeX key "…_x394e_v3".)
    for column in ("author_o", "author_r"):
        if column in out.columns:
            out[column] = out[column].map(lambda value: normalise_author_json(value, preserve_unresolved=True))
    # One separator in the identifier lists, corrections included: ", ".
    for column in ("alt_identifier_o", "alt_identifier_r"):
        if column in out.columns:
            out[column] = out[column].map(lambda v: normalise_identifier_list(v)
                                          if isinstance(v, str) else v)
    out["doi_o_hash"] = out["doi_o"].apply(doi_hash)
    out["doi_r_hash"] = out["doi_r"].apply(doi_hash)
    if "source" in out:
        out["source"] = out["source"].replace(OUTPUT_SOURCES)

    out = final_export.with_replication_columns(final_export.with_identity(out))
    columns = final_export.IDENTITY_COLUMNS + list(FLORA_OUTPUT_COLUMNS)
    if keep_extras:
        columns += [c for c in OUTPUT_EXTRAS if c in out.columns]
        columns += [c for c in PROVENANCE_COLUMNS if c in out.columns]
    return out.reindex(columns=columns)


# Traceability columns appended to the FLoRA set. Appended, never inserted: the
# comment above is explicit that positional readers of the existing export must keep
# working, and flora_id arriving first would break every one of them.
PROVENANCE_COLUMNS = ["flora_id", "source_display_id", "source_record_id"]

# Placeholder DOIs used upstream for papers with no real identifier (book
# chapters and the like). Kept through the join, stripped before output.
DUMMY_DOI_RE = re.compile(r"^dummy[_\-]", re.I)


def clean_doi(value):
    """Normalise a scraped DOI to a bare '10.…' form (delegates to shared.doi)."""
    from shared.doi import clean_scraped_doi

    return clean_scraped_doi(value)


def decode_doi_escapes(value: str) -> str:
    """Percent-decode a DOI copied from a link once (delegates to shared.doi)."""
    from shared.doi import decode_doi_escapes as _decode_doi_escapes

    return _decode_doi_escapes(value)


_SHEET_YEAR_RE = re.compile(r"^(1[5-9]\d{2}|2\d{3})(?:\.0+)?$")


def _sheet_year(value):
    """A hand-entered year_r from the sheet, or None if it is not one.

    244 of the stored values read "2020.0": the column travelled through a float
    somewhere in the sheet import, and publishing that spelling beside OpenAlex's
    "2020" would put two formats in one column. Anything that is not a plausible
    four-digit year is dropped rather than guessed at — this is a fallback for rows
    the DOI lookup could not answer, not a second source of truth.
    """
    text = _s(value)
    match = _SHEET_YEAR_RE.match(text)
    return match.group(1) if match else None


_DOI_URL_RE = re.compile(r"^10\.\d{4,}/")


def _doi_url(value):
    """The DOI's landing page, which R's formatted metadata carries, or None.

    Normalises through _s() rather than truth-testing the cell. pandas 3 hands a
    missing str value to .map() as a float NaN, and NaN is TRUTHY, so a bare
    `if value` guard let it through to re.match and took the whole build down
    with a TypeError on every row that has no original DOI.
    """
    doi = _s(value)
    # "#" and "?" would end the link's path; the rest of a DOI is written as it is.
    return ("https://doi.org/" + doi.replace("#", "%23").replace("?", "%3F")
            if _DOI_URL_RE.match(doi) else None)


def _without_tracking(url):
    """url without its utm_ query parameters, and without the '?' once none is left.

    Campaign tags say how a reader arrived, not which page it is: FRED-000520 and
    FRED-000521 are one thesis, linked once plainly and once with ?utm_source=….
    Every other parameter can name a different page or file, so it stays, in order.
    """
    url, hash_mark, fragment = url.partition("#")
    base, _, query = url.partition("?")
    params = query.split("&")
    if not any(param.lower().startswith("utm_") for param in params):
        return url + hash_mark + fragment
    # Only the tags go: "?a=1&&utm_x=2" keeps its empty parameter, as "?a=1&&" does.
    kept = [param for param in params if not param.lower().startswith("utm_")]
    return base + ("?" + "&".join(kept) if any(kept) else "") + hash_mark + fragment


def norm_url(value):
    """A URL as the exclusions and the duplicate steps compare it: lowercase, without
    the scheme, a leading www., tracking parameters or a trailing slash."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    v = str(value).strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    # Before the slash is stripped: "…/5025/?utm_source=…" is "…/5025" once untagged.
    v = _without_tracking(v)
    v = v.rstrip("/")
    return v or None


# The one separator of a multi-value identifier column (alt_identifier_o/_r) as
# published: ", ", as preprint_dedup.append_alt_identifier writes it. A semicolon
# cannot be one, because SICI DOIs hold them ("…<107::aid-bdm292>3.0.co;2-y"); it is
# read as a separator only before whitespace or the start of another identifier.
IDENTIFIER_SEPARATOR = ", "
# Where one identifier ends and the next begins. Only before something that starts
# an identifier, or at a comma followed by a space as the published lists are
# written: a URL may hold a bare comma ("…/a,b") and a SICI DOI a semicolon.
_NEXT_IDENTIFIER = r"(?=10\.\d|https?://|doi:|www\.)"
_IDENTIFIER_SPLIT_RE = re.compile(
    r"\s*,\s+|\s*,(?=\s*" + _NEXT_IDENTIFIER[3:]                 # a comma
    + r"|\s*;\s*" + _NEXT_IDENTIFIER + r"|\s*;\s+"            # a semicolon between identifiers
    + r"|\s*\|\s*"                                            # a pipe
    + r"|\s+" + _NEXT_IDENTIFIER                                # whitespace before an identifier
    + r"|\s*\n\s*", re.I)                                     # a line break


def _identifier_key(part):
    """How two listed identifiers compare: a DOI, bare or as a doi.org link,
    ignoring case as DOIs do; any other link exactly (a path's case can name a
    different file)."""
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", part, flags=re.I)
    return doi.lower() if re.match(r"10\.\d", doi) else part


def normalise_identifier_list(value):
    """An alt_identifier cell as one list joined by ", ": other separators (a
    semicolon between identifiers, a pipe, a line break, a space) become it, and a
    repeated identifier is kept once. Blank stays None."""
    if not isinstance(value, str) or not value.strip():
        return None if not isinstance(value, str) else (value.strip() or None)
    parts, seen = [], set()
    for part in _IDENTIFIER_SPLIT_RE.split(value.strip()):
        part = part.strip()
        if not re.match(r"https?://", part, re.I):   # a link may end in a comma
            part = part.rstrip(";,").strip()
        if part and _identifier_key(part) not in seen:
            seen.add(_identifier_key(part))
            parts.append(part)
    return IDENTIFIER_SEPARATOR.join(parts) or None


def strip_redundant_url(row):
    """Drop url_r when it only restates doi_r.

    In FLoRA, url_r should mean 'a link to something that isn't the DOI' — an OSF
    page, a report. 1,403 of the replication rows carry https://doi.org/<doi_r>,
    which is the DOI written twice.
    """
    doi, url = row.get("doi_r"), row.get("url_r")
    if not doi or not url:
        return url
    return None if clean_doi(doi) == clean_doi(url) else url


def matching_exclusion(exclusions, doi_o, doi_r, url_r):
    """The transform_exclusions row that removes a row with these identifiers, or
    None. The same rules as step 3: an exclusion with doi_o set matches the pair,
    one without it matches doi_r or url_r alone."""
    d_o, d_r, u_r = clean_doi(doi_o), clean_doi(doi_r), norm_url(url_r)
    for exclusion in exclusions:
        if exclusion.get("doi_o"):
            if exclusion.get("doi_r") and (clean_doi(exclusion["doi_o"]), clean_doi(exclusion["doi_r"])) == (d_o, d_r):
                return exclusion
            continue
        if exclusion.get("doi_r") and d_r and clean_doi(exclusion["doi_r"]) == d_r:
            return exclusion
        if exclusion.get("url_r") and u_r and norm_url(exclusion["url_r"]) == u_r:
            return exclusion
    return None


def _identifiers(df):
    """doi_o, doi_r and url_r as step 5 compares them."""
    return pd.DataFrame({"doi_o": df["doi_o"].apply(clean_doi),
                         "doi_r": df["doi_r"].apply(clean_doi),
                         "url_r": df["url_r"].apply(norm_url)}, index=df.index)


def dedup_keys(df):
    """type | doi_o | doi_r | url_r, as step 5 collapses on. A Series.

    Every identifier counts: two reports of one paper pair (individual reports under
    a meta-paper) differ only in url_r, and are two records, as in R, whose key is
    doi_o with url_r, else doi_r. Rows that differ only because one lacks a url_r are
    left to preprint_dedup.merge_doi_pair_dups, which combines them rather than
    dropping one. A key says nothing about a row with no identifiers at all: see
    has_identifier(). Which rows the duplicate steps make one record: record_keys().
    """
    ids = _identifiers(df).fillna("")
    return (df["type"].fillna("") + "|" + ids["doi_o"] + "|" + ids["doi_r"]
            + "|" + ids["url_r"])


def has_identifier(df):
    """Whether each row has a doi_o, doi_r or url_r. A row with none shares its key
    with every other such row of its type, yet is the same record as none of them."""
    return _identifiers(df).notna().any(axis=1)


def record_keys(df):
    """For each row, the key of the record the duplicate steps would make of it, by
    its identifiers: rows with one key would publish as one record. A Series.

    A row's own key (dedup_keys, step 5), except in a paper pair whose rows carry at
    most one url_r between them, which step 5b combines: there each row takes the key
    of the row with the url_r. Missing for a row with no identifier, as it is no other
    row's duplicate. Rulings play no part, so this is what the identifiers say: what
    the warnings about corrected identifiers compare.
    """
    ids = _identifiers(df)
    pair = df["type"].fillna("") + "|" + ids["doi_o"].fillna("") + "|" + ids["doi_r"].fillna("")
    # Grouped by position, so the frame's index, which need not be unique, plays no part.
    urls = ids["url_r"].groupby(pair.to_numpy())
    combined = ids["doi_o"].notna() & ids["doi_r"].notna() & urls.transform("nunique").le(1)
    url_r = ids["url_r"].where(~combined, urls.transform("first"))
    return (pair + "|" + url_r.fillna("")).where(has_identifier(df))



def _join_parts(*values):
    """Join the non-empty values with ' || ', dropping blanks and repeats.

    `if p` is NOT a sufficient emptiness test here: df.apply(axis=1) hands back
    the column's NA sentinel, and float('nan') is truthy while str(nan) == 'nan'
    — which is how the literal string "nan" ends up in a shipped dataset. Use
    pd.isna, and de-duplicate so an identical quote on both dimensions is not
    emitted twice.
    """
    parts = []
    for v in values:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            continue
        text = str(v).strip()
        if text and text.lower() != "nan" and text not in parts:
            parts.append(text)
    return " || ".join(parts) if parts else None


def _join_axes(computational, robustness):
    """A reproduction's two values as one, each labelled with its axis.

    fred-data's combine_reproduction_quotes, which readers of the old flora.csv
    rely on: "Computational: A || Robustness: B", one labelled value when only one
    axis has text, and an identical pair collapsed to one unlabelled value.
    """
    comp, rob = _join_parts(computational), _join_parts(robustness)
    if comp and rob:
        return comp if comp == rob else f"Computational: {comp} || Robustness: {rob}"
    if comp:
        return f"Computational: {comp}"
    if rob:
        return f"Robustness: {rob}"
    return None


def derive_quote(row):
    """Reproductions have two quotes; join them rather than picking one, so no
    coder's text is silently dropped."""
    if row["type"] != "reproduction":
        return row.get("outcome_quote")
    return _join_axes(row.get("outcome_computational_quote"),
                      row.get("outcome_robustness_quote"))


def derive_quote_source(row):
    if row["type"] != "reproduction":
        return row.get("out_quote_source")
    return _join_axes(row.get("out_quote_computational_source"),
                      row.get("out_quote_robust_source"))


# The columns the metadata join keeps from the source where no provider resolves the
# work: the sheet's reference, abstract and year, and the original's landing page.
# year_o has no source value: the sheets carry only the replication's year.
JOIN_FALLBACKS = ("ref_o", "url_o", "ref_r", "abstract_r", "year_r")
# Everything the join writes for one side.
JOINED_COLUMNS = {
    side: [f"{field}_{side}" for field in ("title", "author", "journal", "volume", "issue",
                                           "pages", "language", "oa_url", "oa_work_id",
                                           "bibtex_ref", "year", "ref", "url")]
          + (["abstract_r"] if side == "r" else [])
    for side in ("o", "r")
}


# A working column for step 6c: the url a correction replaced, which can be the
# OpenAlex record of a work with no DOI.
WORK_ID_HINT = "_replaced_work_id_{side}"
_OPENALEX_WORK_RE = re.compile(r"openalex\.org/(?:works/)?(W\d{5,})", re.I)


def openalex_work_ids(value) -> list:
    """The W-ids of the OpenAlex links in `value` (a URL or an identifier list)."""
    if not isinstance(value, str):
        return []
    return [match.group(1).upper() for match in _OPENALEX_WORK_RE.finditer(value)]


def openalex_work_id(value) -> "str | None":
    """The W-id of the first OpenAlex link in `value`."""
    ids = openalex_work_ids(value)
    return ids[0] if ids else None



# ── validation findings ──────────────────────────────────────────────────────

def _blank(value) -> bool:
    return (value is None or (isinstance(value, float) and pd.isna(value))
            or not str(value).strip())


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def row_id(row) -> str:
    """`doi_o | doi_r-or-url_r`, the pair as the R report names a row. Shown after
    the FLoRA ID, so a reader recognises the papers without looking the ID up."""
    right = _text(row.get("doi_r")) or _text(row.get("url_r"))
    return f"{_text(row.get('doi_o'))} | {right}"


# ── findings ──────────────────────────────────────────────────────────────────

def finding_key(check, flora_ids, column, value) -> str:
    """(check, sorted FLoRA IDs, column, normalised value) as one string.

    The same finding gets the same key on every run, whatever else in the row
    changes, and a new key once the value it flagged changes. Only the value can
    hold a "|", and it comes last, so the key splits back unambiguously.
    """
    normalised = " ".join(_text(value).split()).casefold()
    return "|".join([check, ",".join(sorted(flora_ids)), column or "", normalised])


def finding(check, rows, column, value, detail, label=None) -> dict:
    """One finding about one or more rows.

    `detail` is plain text saying what is wrong; the value it is about is kept
    apart, for the key and for whoever shows it. `label` is the rows' `doi_o |
    doi_r`, by default the one they all share.
    """
    rows = list(rows)
    flora_ids = sorted({_text(row.get("flora_id")) for row in rows} - {""})
    if label is None:
        labels = {row_id(row) for row in rows}
        label = labels.pop() if len(labels) == 1 else ""
    value = None if _blank(value) else str(value).strip()
    return {"check": check, "flora_ids": flora_ids, "column": column, "value": value,
            "detail": detail, "label": label,
            "key": finding_key(check, flora_ids, column, value)}

