"""Shared DOI normalisation helpers for the FLoRA validation codebase.

This is a stdlib-only leaf module (zero internal or third-party imports) so every
pipeline, API, sync, and adjudication module can depend on it without introducing
import cycles or breaking minimal environments (e.g. Zenodo release CI).

Different parts of FLoRA intentionally apply four distinct DOI contracts:

1. ``normalize_bare_doi`` — case-preserving bare DOI (strips resolver URL / ``doi:``
   prefix and whitespace, keeps original casing for display/storage).
2. ``normalize_doi_key`` — lowercased bare DOI for exact matching (no percent
   decoding; with ``drop_whitespace=True`` mirrors PostgreSQL ``source_norm_doi()``).
3. ``clean_scraped_doi`` — full pipeline cleaner for scraped/sheet DOIs (idempotent
   percent-decoding of SICI escapes, resolver/scheme stripping, trailing garbage
   removal, whitespace truncation).
4. ``canonical_doi`` — percent-decoded lowercased canonical key for deduplication
   and public API lookup (with optional ``10.1037//`` double-slash repair).
"""
from __future__ import annotations

import math
import re
import urllib.parse

_RESOLVER_HTTP_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/\s*", re.I)
_RESOLVER_BARE_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/\s*", re.I)
_DOI_SCHEME_RE = re.compile(r"^doi:\s*", re.I)
_DOUBLE_SLASH_RE = re.compile(r"^(10\.[0-9]+)//")
_TRAILING_DOI_GARBAGE_RE = re.compile(
    r"\s*digital\s*object\s*identifier.*$|\s*get\s*rights\s*and\s*content.*$",
    re.I,
)
_TRAILING_WS_RE = re.compile(r"\s.*$")


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def decode_doi_escapes(value: str) -> str:
    """Percent-decode a DOI copied from a link once (e.g. ``%3c`` -> ``<``).

    Run before lowercasing so an escaped capital matches a typed letter. Left
    untouched when it contains an escaped percent sign (``%25``) so a second
    pass is always idempotent.
    """
    if "%" not in value or "%25" in value.lower():
        return value
    return urllib.parse.unquote(value)


def strip_doi_prefix(value: str, *, allow_bare_host: bool = False) -> str:
    """Strip leading doi.org / dx.doi.org resolver URLs and ``doi:`` prefixes."""
    text = value.strip()
    resolver = _RESOLVER_BARE_RE if allow_bare_host else _RESOLVER_HTTP_RE
    text = resolver.sub("", text)
    text = _DOI_SCHEME_RE.sub("", text)
    return text.strip()


def normalize_bare_doi(
    value,
    *,
    drop_whitespace: bool = False,
    require_prefix_10: bool = False,
) -> str | None:
    """Case-preserving bare DOI, or ``None`` when blank/missing."""
    if _is_missing(value):
        return None
    text = strip_doi_prefix(str(value))
    if drop_whitespace:
        text = _TRAILING_WS_RE.sub("", text).strip()
    if not text:
        return None
    if require_prefix_10 and not text.lower().startswith("10."):
        return None
    return text


def normalize_doi_key(
    value,
    *,
    drop_whitespace: bool = False,
    allow_bare_host: bool = False,
) -> str:
    """Lowercased bare DOI key for matching (``''`` when blank/missing).

    With ``drop_whitespace=True`` and ``allow_bare_host=False``, this matches
    ``source_norm_doi(TEXT)`` in ``db_schema.sql``.
    """
    if _is_missing(value):
        return ""
    text = strip_doi_prefix(str(value).lower(), allow_bare_host=allow_bare_host)
    if drop_whitespace:
        text = _TRAILING_WS_RE.sub("", text).strip()
    return text


def clean_scraped_doi(value) -> str | None:
    """Normalise a scraped/sheet DOI to a clean, lowercased bare form.

    Handles percent-encoded SICI DOIs (``%3c``/``%3e``), resolver prefixes,
    trailing scraped text (``digital object identifier``, ``get rights and
    content``), and whitespace suffixes.
    """
    if _is_missing(value):
        return None
    text = decode_doi_escapes(str(value).strip()).lower()
    if not text:
        return None
    text = strip_doi_prefix(text, allow_bare_host=True)
    text = _TRAILING_DOI_GARBAGE_RE.sub("", text)
    text = _TRAILING_WS_RE.sub("", text).strip()
    return text or None


def canonical_doi(value, *, repair_double_slash: bool = False) -> str:
    """Percent-decoded, lowercased canonical DOI key for dedup and API lookup."""
    if _is_missing(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = decode_doi_escapes(text).lower()
    text = strip_doi_prefix(text, allow_bare_host=True)
    if repair_double_slash:
        text = _DOUBLE_SLASH_RE.sub(r"\1/", text)
    return text.strip()
