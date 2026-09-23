"""Reference helpers used by the website's preparation pipeline.

Ports the supplied Crossref/DataCite, manual-reference, OSF and Unpaywall
helpers. Network results are persisted by ``enrich_works`` in work_metadata;
these functions themselves do not write files or connect to the database.
"""
from __future__ import annotations

import csv
from functools import lru_cache
import gzip
import html
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


class ReferenceLookupError(RuntimeError):
    """A provider failed; unlike a confirmed miss this must remain retryable."""


def text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value and value.lower() not in {"nan", "na", "none", "null"} else None


def normalise_key(value) -> str:
    """Use the same key for DOI URLs, bare DOIs and scheme-varied source URLs."""
    value = (text(value) or "").lower()
    value = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", "", value)
    if value.startswith("10."):
        value = re.sub(r"\s*digital\s*object\s*identifier.*|\s*get\s*rights\s*and\s*content.*", "", value)
        return value.split()[0] if value else ""
    value = re.sub(r"^https?://(?:www\.)?|^www\.", "", value)
    if re.fullmatch(r"w\d{5,}", value):
        return value.upper()
    return value.rstrip("/")


def is_doi(key: str) -> bool:
    return bool(re.fullmatch(r"10\.\d{4,}/\S+", key))


def request(url: str, accept: str = "application/json", *, retries: int = 3):
    """Return JSON/text or None for a definitive miss; do not cache outages.

    The caller builds requests only to fixed bibliographic API hosts. HTML from
    a DOI redirect is rejected instead of being mistaken for an APA citation.
    """
    host = urllib.parse.urlsplit(url).hostname
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": accept, "User-Agent": "flora-validation/1.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                body = response.read().decode("utf-8-sig")
                if "text/html" in response.headers.get("Content-Type", "").lower():
                    return None
            if "json" in accept:
                return json.loads(body)
            if re.match(r"\s*(?:<!doctype\s+html|<html\b)", body, re.I):
                return None
            return text(body)
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 404, 406, 410, 422}:
                return None
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise ReferenceLookupError(f"{host}: HTTP {exc.code}") from exc
            error = f"HTTP {exc.code}"
        except (OSError, ValueError) as exc:
            error = type(exc).__name__
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    raise ReferenceLookupError(f"{host}: lookup failed after {retries} attempts ({error})")


def _first(value):
    if isinstance(value, list):
        return next((text(item) for item in value if text(item)), None)
    return text(value)


def _year(data):
    for field in ("published-print", "published", "published-online", "issued", "created"):
        parts = (data.get(field) or {}).get("date-parts") or []
        if parts and parts[0]:
            return text(parts[0][0])
    return None


def author_fields(authors):
    """Keep both Crossref-style names and the existing display-name contract."""
    names, structured = [], []
    for item in authors or []:
        given, family = text(item.get("given")), text(item.get("family"))
        literal = text(item.get("literal") or item.get("name"))
        name = " ".join(part for part in (given, family) if part) or literal
        if not name:
            continue
        names.append(name)
        entry = {"given": given, "family": family,
                 "sequence": "first" if not structured else "additional"}
        if not family and literal:
            entry["name"] = literal
        structured.append(entry)
    return {"authors": "; ".join(names) or None,
            "authors_json": json.dumps(structured, ensure_ascii=False) if structured else None}


def crossref_fields(data):
    return {
        "title": _first(data.get("title")),
        "journal": _first(data.get("container-title")),
        "year": _year(data), "volume": text(data.get("volume")),
        "issue": text(data.get("issue")),
        "pages": text(data.get("page") or data.get("article-number")),
        "abstract": text(data.get("abstract")),
        **author_fields(data.get("author")),
    }


def datacite_fields(data):
    creators = []
    for author in data.get("creators") or []:
        creators.append({"given": author.get("givenName"),
                         "family": author.get("familyName"), "name": author.get("name")})
    container = data.get("container") or {}
    publisher = data.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    return {
        "title": next((text(t.get("title")) for t in data.get("titles") or []
                       if text(t.get("title"))), None),
        "journal": text(container.get("title") or publisher),
        "year": text(data.get("publicationYear")),
        "volume": text(container.get("volume")), "issue": text(container.get("issue")),
        "pages": text(container.get("firstPage")),
        "abstract": next((text(d.get("description")) for d in data.get("descriptions") or []
                          if d.get("descriptionType") == "Abstract"), None),
        **author_fields(creators),
    }


def _bibtex_value(bibtex, field):
    match = re.search(r"\b" + re.escape(field) + r"\s*=\s*", bibtex, re.I)
    if not match or match.end() >= len(bibtex):
        return None
    start = match.end()
    first = bibtex[start]
    if first not in "{\"'":
        return text(re.split(r"[,\n}]", bibtex[start:], maxsplit=1)[0])
    depth, escaped = 1, False
    for index in range(start + 1, len(bibtex)):
        char = bibtex[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if first == "{":
            depth += (char == "{") - (char == "}")
            ended = depth == 0
        else:
            ended = char == first
        if ended:
            return text(re.sub(r"\s+", " ", bibtex[start + 1:index]))
    return None


def _parse_authors(value):
    value = text(value)
    if not value:
        return []
    if value.startswith("["):
        try:
            result = json.loads(value)
            if isinstance(result, list) and all(isinstance(a, dict) for a in result):
                return result
        except ValueError:
            pass
    result = []
    for name in re.split(r"\s+and\s+|\s*;\s*", value):
        name = name.strip()
        if name.startswith("{") and name.endswith("}"):
            result.append({"name": name[1:-1]})
        elif "," in name:
            family, given = name.split(",", 1)
            result.append({"family": family.strip(), "given": given.strip()})
        else:
            parts = name.rsplit(" ", 1)
            result.append({"given": parts[0] if len(parts) > 1 else None,
                           "family": parts[-1]})
    return result


def parse_bibtex(bibtex):
    """Recover structured fields, including nested/quoted BibTeX values."""
    if not text(bibtex):
        return {}
    row = {target: _bibtex_value(bibtex, source) for source, target in (
        ("title", "title"), ("journal", "journal"), ("year", "year"),
        ("volume", "volume"), ("number", "issue"), ("pages", "pages"))}
    if not row["journal"]:
        row["journal"] = _bibtex_value(bibtex, "booktitle") or _bibtex_value(bibtex, "publisher")
    row.update(author_fields(_parse_authors(_bibtex_value(bibtex, "author"))))
    return row


def merge_present(base, incoming):
    """Higher-priority nonblank values win, preserving lower-priority coverage."""
    return {**base, **{key: value for key, value in incoming.items() if text(value)}}


def synthesise_references(row, key):
    """Last-resort citation strings; existing supplied citations always win."""
    row = dict(row)
    if not text(row.get("title")):
        return row
    authors = _parse_authors(row.get("authors_json") or row.get("authors"))
    if not row.get("apa_ref"):
        formatted = []
        for author in authors:
            family, given = text(author.get("family")), text(author.get("given"))
            if family:
                initials = " ".join(part[0] + "." for part in re.findall(r"[^\W\d_]+", given or ""))
                formatted.append(f"{family}, {initials}" if initials else family)
            elif author.get("name"):
                formatted.append(author["name"])
        names = (", ".join(formatted[:-1]) + ", & " + formatted[-1]
                 if len(formatted) > 1 else "".join(formatted))
        citation = f"{names} ({row.get('year') or 'n.d.'}). {row['title'].rstrip('.')}.".strip()
        journal = text(row.get("journal"))
        if journal:
            citation += f" {journal}"
            if row.get("volume"):
                citation += f", {row['volume']}"
                if row.get("issue"):
                    citation += f"({row['issue']})"
            if row.get("pages"):
                citation += f", {row['pages']}"
            citation += "."
        if is_doi(key):
            citation += f" https://doi.org/{key}"
        elif "/" in key:
            citation += f" https://{key}"
        row["apa_ref"] = citation
    if not row.get("bibtex_ref"):
        names = []
        for author in authors:
            if author.get("family"):
                names.append(", ".join(str(author[k]) for k in ("family", "given") if author.get(k)))
            elif author.get("name"):
                names.append("{" + author["name"] + "}")
        bib_fields = {"title": row.get("title"), "author": " and ".join(names),
                      "journal": row.get("journal"), "year": row.get("year"),
                      "volume": row.get("volume"), "number": row.get("issue"),
                      "pages": row.get("pages")}
        if is_doi(key):
            bib_fields["doi"] = key
        elif "/" in key:
            bib_fields["url"] = "https://" + key
        elif re.fullmatch(r"W\d+", key):
            bib_fields["url"] = "https://openalex.org/" + key
        entry_key = re.sub(r"[^\w-]", "_", key)
        entry = [f"@{'article' if row.get('journal') else 'misc'}{{{entry_key},"]
        for name, value in bib_fields.items():
            if text(value):
                value = str(value).replace("{", "(").replace("}", ")")
                entry.append(f"  {name} = {{{value}}},")
        row["bibtex_ref"] = "\n".join(entry + ["}"])
    return row


def fetch_doi_reference(doi):
    """Crossref fields, DataCite/CSL fallback, then negotiated APA and BibTeX."""
    key = normalise_key(doi)
    if not is_doi(key):
        return {}
    encoded = urllib.parse.quote(key, safe="")
    result, sources = {}, []
    crossref = request(f"https://api.crossref.org/works/{encoded}")
    if crossref and isinstance(crossref.get("message"), dict):
        result = crossref_fields(crossref["message"])
        sources.append("crossref")
    if not result.get("title") or not result.get("authors"):
        datacite = request(f"https://api.datacite.org/dois/{encoded}")
        if datacite and (datacite.get("data") or {}).get("attributes"):
            fields = datacite_fields(datacite["data"]["attributes"])
            result = merge_present(fields, result)
            sources.append("datacite")
    doi_url = f"https://doi.org/{encoded}"
    if not result.get("title") or not result.get("authors"):
        csl = request(doi_url, "application/vnd.citationstyles.csl+json")
        if csl and isinstance(csl, dict):
            result = merge_present(crossref_fields(csl), result)
            sources.append("doi-csl")
    apa = request(doi_url, "text/x-bibliography; style=apa; locale=en-US")
    bibtex = request(doi_url, "application/x-bibtex")
    if apa:
        result["apa_ref"] = html.unescape(apa.strip())
    if bibtex and re.match(r"\s*@\w+\s*[{(]", bibtex):
        result["bibtex_ref"] = bibtex.strip()
        result = merge_present(parse_bibtex(bibtex), result)
    if sources or apa or bibtex:
        result["metadata_source"] = "+".join(sources or ["doi-negotiation"])
    return synthesise_references(result, key)


def osf_id(url):
    key = normalise_key(url)
    match = re.fullmatch(r"osf\.io/([a-z0-9]{5}|[a-f0-9]{24})(?:/download)?(?:\?[^#]*)?", key)
    return match.group(1) if match else None


def fetch_osf_reference(url):
    """Resolve an OSF node or short file URL to its project's citations."""
    identifier = osf_id(url)
    if not identifier:
        return {}
    prefix = "https://api.osf.io/v2"
    apa = request(f"{prefix}/nodes/{identifier}/citation/apa/")
    if not apa:
        file_data = request(f"{prefix}/files/{identifier}/") or {}
        identifier = (((file_data.get("data") or {}).get("relationships") or {})
                      .get("target") or {}).get("data", {}).get("id")
        if not identifier or not re.fullmatch(r"[a-zA-Z0-9]+", str(identifier)):
            return {}
        apa = request(f"{prefix}/nodes/{identifier}/citation/apa/")
    bibtex = request(f"{prefix}/nodes/{identifier}/citation/bibtex/")
    result = {}
    for name, response in (("apa_ref", apa), ("bibtex_ref", bibtex)):
        citation = ((response or {}).get("data", {}).get("attributes") or {}).get("citation")
        if text(citation):
            result[name] = citation.strip()
    result = merge_present(parse_bibtex(result.get("bibtex_ref")), result)
    if result.get("apa_ref") and not result.get("title"):
        from apa_references import parse
        result = merge_present(parse(result["apa_ref"]) or {}, result)
    if result:
        result["metadata_source"] = "osf"
    return synthesise_references(result, normalise_key(url))


def fetch_unpaywall(doi, email):
    """None means unknown DOI; a known closed work returns oa_url=None."""
    key = normalise_key(doi)
    if not is_doi(key) or not text(email):
        return None
    url = ("https://api.unpaywall.org/v2/" + urllib.parse.quote(key, safe="")
           + "?" + urllib.parse.urlencode({"email": email}))
    result = request(url)
    if result is None:
        return None
    location = result.get("best_oa_location") or {}
    oa_url = (location.get("url_for_pdf") or location.get("url")) if result.get("is_oa") is True else None
    return {"oa_url": text(oa_url), "is_oa": result.get("is_oa") is True}


def _xlsx_rows(path):
    """Read the first worksheet without requiring an Excel runtime/dependency."""
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall("s:si", ns)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = workbook.find("s:sheets/s:sheet", ns)
        if sheet is None:
            return []
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((r.get("Target") for r in rels if r.get("Id") == rid), None)
        if not target:
            raise ValueError("Manual reference workbook has no first worksheet target")
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(archive.read(target))
        rows = []
        for row in root.findall("s:sheetData/s:row", ns):
            values = {}
            for cell in row.findall("s:c", ns):
                letters = re.sub(r"\d", "", cell.get("r", "A1"))
                column = 0
                for letter in letters:
                    column = column * 26 + ord(letter) - ord("A") + 1
                node = cell.find("s:v", ns)
                value = node.text if node is not None else ""
                if cell.get("t") == "s" and value:
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall("s:is//s:t", ns))
                values[column - 1] = value
            if values:
                rows.append([values.get(index, "") for index in range(max(values) + 1)])
    if not rows:
        return []
    headers = rows[0]
    return [{name: row[index] if index < len(row) else "" for index, name in enumerate(headers)}
            for row in rows[1:]]


def manual_reference_path():
    configured = os.getenv("FLORA_MANUAL_REFERENCES")
    if configured:
        return Path(configured)
    supplied = Path(__file__).resolve().parent / "data" / "manual_references.xlsx"
    if supplied.exists():
        return supplied
    cache = Path(os.getenv("FLORA_CACHE_DIR", str(Path(__file__).resolve().parent / "cache")))
    xlsx = cache / "manual_references.xlsx"
    return xlsx if xlsx.exists() else cache / "manual_references.csv"


@lru_cache(maxsize=4)
def _read_seed(path, modified_ns):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("records"), dict):
        raise ValueError("Reference metadata seed has an unsupported schema")
    return data


def load_reference_seed():
    """Frozen public provider responses supplied with the original R pipeline."""
    path = Path(os.getenv("FLORA_REFERENCE_SEED", str(
        Path(__file__).resolve().parent / "data" / "reference_metadata_seed.json.gz")))
    if not path.exists():
        if os.getenv("FLORA_REFERENCE_SEED"):
            raise FileNotFoundError(f"Configured reference seed is missing: {path}")
        return {"records": {}, "url_to_doi": {}}
    return _read_seed(str(path), path.stat().st_mtime_ns)


def load_manual_references(path=None):
    """Read DOI/URL/DUMMY overrides; first duplicate wins, as in the R helper."""
    use_mapping = path is None
    path = Path(path) if path is not None else manual_reference_path()
    if not path.exists():
        if os.getenv("FLORA_MANUAL_REFERENCES"):
            raise FileNotFoundError(f"Configured manual reference file is missing: {path}")
        return {}
    if path.suffix.lower() == ".xlsx":
        records = _xlsx_rows(path)
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
    else:
        raise ValueError("Manual references must be a .csv or .xlsx file")
    result = {}
    mapping = load_reference_seed().get("url_to_doi", {}) if use_mapping else {}
    for record in records:
        key = normalise_key(record.get("key") or record.get("doi") or record.get("url"))
        if not key or key in result:
            continue
        row = {field: text(record.get(field)) for field in (
            "title", "journal", "year", "volume", "issue", "pages", "abstract", "language", "oa_url")}
        row["apa_ref"] = text(record.get("reference_apa") or record.get("apa_ref"))
        row["bibtex_ref"] = text(record.get("reference_bibtex") or record.get("bibtex_ref"))
        row = merge_present(parse_bibtex(row["bibtex_ref"]), row)
        if text(record.get("author") or record.get("authors") or record.get("authors_json")):
            row.update(author_fields(_parse_authors(record.get("authors_json") or record.get("author") or record.get("authors"))))
        if row.get("apa_ref") and not row.get("title"):
            from apa_references import parse
            row = merge_present(parse(row["apa_ref"]) or {}, row)
        row["metadata_source"] = "manual"
        result[key] = synthesise_references(row, key)
        canonical = mapping.get(key)
        if canonical and canonical not in result:
            result[canonical] = result[key]
    return result
