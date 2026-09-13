"""apa_references.py — recover a title, authors and year from a hand-typed APA string.

A port of augment_titles_from_apa_text() in R/crossref_cache.R.

WHY IT EXISTS
-------------
Rows entered with a URL but no DOI (the i4replication reports, theses, working
papers) carry their citation only as free text in ref_o / ref_r. No lookup service
can give them a title — there is no identifier to look up — so the only remaining
source is the reference string itself, which has a predictable shape:

    Authors (Year[, Month d]). Title. Journal, 12(3), 45-67. https://doi.org/...

TWO THINGS TO KNOW
------------------
This helper is NOT called by FLoRA_Preparation_Pipeline.r. It sits in the R
codebase unused, so running it puts our output AHEAD of the R pipeline rather than
level with it: the R output carries those blank titles too. It is enabled here
because a missing title is what the notebook's own Step 10 filter drops a row for.

It runs AFTER preprint deduplication, not before. Dedup matches on title
similarity, so feeding it 300 newly-parsed titles would change which rows survive —
a data change disguised as a metadata fix. Filling them afterwards changes only
what each surviving row reports about itself.

Only rows that still lack a title are touched, and only when the text has the shape
of a citation: a bare DOI sitting in a reference column is not one.
"""
import re

from console_encoding import use_utf8_output

use_utf8_output()

_WS = re.compile(r"\s+")

# "Authors (2019, March 3). rest" — the year group also accepts "n.d.".
# Non-greedy author part, so the FIRST parenthesised year wins: a title may itself
# contain a bracketed year.
_CITATION = re.compile(r"^(.*?)\s*\((\d{4}|n\.d\.)[^)]*\)\.?\s+(.*)$")

# A title-first reference: "Title [Working paper]. (n.d.). https://…"
_ENDS_SENTENCE = re.compile(r"[.!?\]]$")
_STARTS_IDENTIFIER = re.compile(r"^(https?://|doi|10\.)")
_TRAILING_BRACKET = re.compile(r"\s*\[[^\]]*\]\.?$")

_ET_AL = re.compile(r"\bet al$")
_QUOTES = ('"', "“", "”")

# A link belongs to the citation's tail, never to the title, and the tail often
# carries no full stop of its own — so without this the scan below runs off the end
# and adopts the whole remainder. Three live rows published titles ending in
# "https://osf.io/8hdu3/", and one published the retrieval statement entire:
# "Retrieved 04:36, September 23, 2017 from http://www.PsychFileDrawer.org/…".
_LINK = re.compile(r"https?://|www\.", re.I)
# Anchored, so only a "title" that IS the retrieval statement is refused. A title
# may well contain the word ("Information Retrieved from Memory"); none begins with it.
_RETRIEVAL = re.compile(r"^Retrieved\b", re.I)


def _squish(text: str) -> str:
    return _WS.sub(" ", str(text)).strip()


def _title_end(rest: str) -> int:
    """Index (exclusive) where the title ends inside the post-year remainder.

    The title runs to the first full stop that is followed by a space, with two
    exceptions that occur in the real data:

      - a full stop inside double quotes does not end it. Replication titles quote
        the original paper's title, which frequently ends in one.
      - "et al." does not end it, for the same reason.

    A closing quote immediately after a full stop DOES end it, and is kept as part
    of the title.
    """
    chars = list(rest)
    n = len(chars)
    in_quote = False
    for i in range(1, n + 1):                 # 1-based, mirroring the R
        char = chars[i - 1]
        if char in _QUOTES:
            in_quote = not in_quote
            if (not in_quote and i > 1 and chars[i - 2] == "."
                    and (i == n or chars[i] == " ")):
                return i                      # keep the closing quote
            continue
        if in_quote or char != "." or (i < n and chars[i] != " "):
            continue
        if _ET_AL.search("".join(chars[:i - 1])):
            continue
        return i - 1                          # drop the full stop itself
    return n


def _authors(author_text: str) -> "str | None":
    """"Last, F. M., Last, G." -> "F. M. Last; G. Last".

    The R emits CrossRef-style JSON here; this emits the "; "-joined display names
    the rest of THIS pipeline uses, because that is what author_o / author_r already
    hold from OpenAlex. A second format in the same column would break every reader.

    An odd number of comma-separated parts means the pairing assumption does not
    hold (one-word surnames, an organisation), so the text is kept whole.
    """
    text = re.sub(r",?\s*&\s*", ", ", author_text)
    parts = [p for p in re.split(r",\s*", text) if p.strip()]
    if not parts or len(parts) % 2 != 0:
        return _squish(author_text) or None
    names = []
    for family, given in zip(parts[0::2], parts[1::2]):
        family, given = family.strip(), given.strip()
        names.append(f"{given} {family}".strip() if given else family)
    return "; ".join(n for n in names if n) or None


def parse(reference) -> "dict | None":
    """Title, year and authors from one APA string, or None if it is not one."""
    if reference is None:
        return None
    ref = _squish(reference)
    if not ref:
        return None

    match = _CITATION.match(ref)
    if not match:
        return None
    author_text, year_text, rest = match.group(1), match.group(2), match.group(3)
    year = year_text if year_text.isdigit() else None

    # Title-first: the part before the year is the title, and what follows the year
    # is only a link. "Title [Working paper]. (n.d.). https://…"
    if _ENDS_SENTENCE.search(author_text) and _STARTS_IDENTIFIER.match(rest):
        title = _squish(_TRAILING_BRACKET.sub("", author_text))
        title = re.sub(r"\.$", "", title).strip()
        return {"title": title, "year": year, "authors": None} if title else None

    if not author_text.strip():
        return None
    link = _LINK.search(rest)
    if link:
        rest = rest[:link.start()]
    title = _squish(rest[:_title_end(rest)])
    if not title or _RETRIEVAL.match(title):
        return None
    return {"title": title, "year": year, "authors": _authors(author_text)}


def augment(frame, side: str, verbose: bool = True) -> int:
    """Fill title/author/year on rows of `frame` that still lack a title.

    Mutates in place and returns how many rows were filled. Never overwrites a
    value that is already there — this is the last resort, not a preference.
    """
    title_col, author_col = f"title_{side}", f"author_{side}"
    year_col, ref_col = f"year_{side}", f"ref_{side}"
    if ref_col not in frame.columns or title_col not in frame.columns:
        return 0

    def blank(value) -> bool:
        return value is None or str(value).strip() in ("", "nan", "None")

    filled = 0
    for index in frame.index:
        if not blank(frame.at[index, title_col]):
            continue
        if blank(frame.at[index, ref_col]):
            continue
        parsed = parse(frame.at[index, ref_col])
        if not parsed:
            continue
        frame.at[index, title_col] = parsed["title"]
        if parsed["authors"] and author_col in frame.columns \
                and blank(frame.at[index, author_col]):
            frame.at[index, author_col] = parsed["authors"]
        if parsed["year"] and year_col in frame.columns \
                and blank(frame.at[index, year_col]):
            frame.at[index, year_col] = parsed["year"]
        filled += 1

    if verbose and filled:
        print(f"      recovered title_{side} from the reference text for "
              f"{filled} row(s)")
    return filled
