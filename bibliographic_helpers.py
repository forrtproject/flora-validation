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


_ORCID_ID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", re.I)


def _orcid(item):
    """An author's ORCID as Crossref writes it (https://orcid.org/…), from a
    Crossref "ORCID", an OpenAlex "orcid" or a DataCite name identifier."""
    value = item.get("ORCID") or item.get("orcid")
    if not isinstance(value, str):
        value = None
        for identifier in item.get("nameIdentifiers") or []:
            if (text(identifier.get("nameIdentifierScheme")) or "").upper() == "ORCID":
                value = identifier.get("nameIdentifier")
                break
    match = _ORCID_ID.search(value) if isinstance(value, str) else None
    # Always the https form with an upper-case check digit, as every ORCID in
    # fred-data's file is: providers still send http:// and a lower-case x.
    return f"https://orcid.org/{match.group(0).upper()}" if match else None


def _name_part(value):
    """A given or family name, kept as it is: unlike text(), "Nan" and "Na" are
    names here ("Nan Jiang", "Elim Na"), not missing values."""
    if value is None or isinstance(value, float):            # None, or a NaN cell
        return None
    return str(value).strip() or None


def author_json(structured):
    """The author list as the FLoRA CSV carries it: FReD's compact JSON, the same
    bytes R's jsonlite::toJSON writes for fred-data's flora.csv."""
    return json.dumps(structured, ensure_ascii=False, separators=(",", ":")) if structured else None


def author_fields(authors):
    """Crossref-style structured authors, plus the "; "-joined display names.

    Each entry keeps given, family (or name, for an organisation), sequence and the
    ORCID when the provider has one. An empty part is left out rather than written
    as null, as fred-data's flora.csv does.
    """
    names, structured = [], []
    for item in authors or []:
        given, family = _name_part(item.get("given")), _name_part(item.get("family"))
        literal = _name_part(item.get("literal") or item.get("name"))
        name = " ".join(part for part in (given, family) if part) or literal
        if not name:
            continue
        names.append(name)
        entry = {}
        if given:
            entry["given"] = given
        if family:
            entry["family"] = family
        elif literal:
            entry["name"] = literal
        entry["sequence"] = "first" if not structured else "additional"
        orcid = _orcid(item)
        if orcid:
            entry["ORCID"] = orcid
        structured.append(entry)
    return {"authors": "; ".join(names) or None, "authors_json": author_json(structured)}


# Words that mark a plain author string as an organisation rather than a person,
# so it is kept whole as {"name": ...} instead of split into given and family.
_ORGANISATION = re.compile(
    r"\b(consortium|group|collaboration|collaborative|team|project|network|initiative|"
    r"committee|society|association|institute|laboratory|lab|center|centre|university|"
    r"foundation|council|organi[sz]ation|investigators|alliance|program|programme|"
    r"partnership|registry|working)\b", re.I)
_INITIALS = re.compile(r"^(?:[A-Z]\.?-?){1,3}$")
# Lower-case name particles that belong to the family name, as Crossref has it:
# "Han L. J. van der Maas" is family "van der Maas".
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "da", "di", "du", "dos",
              "das", "la", "le", "ten", "ter", "zu", "bin", "ibn", "al"}


def _plain_author(name):
    """One display name as a given/family pair, or kept whole as an organisation."""
    name = name.strip()
    if _ORGANISATION.search(name) or name.lower().startswith("the "):
        return {"name": name}
    if "," in name:
        family, given = name.split(",", 1)
        return {"family": family.strip(), "given": given.strip()}
    parts = name.split()
    if len(parts) == 1:
        return {"family": parts[0]}
    # "Busenitz L.": family, then initials. Not "YING LIU": an all-capitals name
    # with a short surname is given then family, as any other.
    last = parts[-1]
    if (len(parts) == 2 and _INITIALS.match(last) and not _INITIALS.match(parts[0])
            and ("." in last or not name.isupper())):
        return {"family": parts[0], "given": last}
    start = len(parts) - 1
    while start > 1 and parts[start - 1] in _PARTICLES:
        start -= 1
    return {"given": " ".join(parts[:start]), "family": " ".join(parts[start:])}


_MISSING = {"na", "n/a", "nan", "none", "null"}


def normalise_author_json(value):
    """An author cell as a structured JSON list, whatever form it arrived in.

    A JSON list is rewritten in the compact form without empty keys. A plain
    "A; B; C" string (a sheet value, or display names) becomes a list too: a search
    index that splits on commas would otherwise read the whole string as one name.
    Anything else (no value, a missing-value marker) is returned unchanged.
    """
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw or raw.lower() in _MISSING:
        return value
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except ValueError:
            return value
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return author_fields(items)["authors_json"] or value
        return value
    items = [_plain_author(part) for part in raw.split(";") if part.strip()]
    return author_fields(items)["authors_json"] or value


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
                         "family": author.get("familyName"), "name": author.get("name"),
                         "nameIdentifiers": author.get("nameIdentifiers")})
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


_XLSX_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def decode_xlsx_escapes(value):
    """Undo the escapes a workbook stores characters with: "_x000D_" is a carriage
    return. Read raw, they reach the output as literal text (a BibTeX entry with
    "_x000D_" at every line end)."""
    if not isinstance(value, str) or "_x" not in value:
        return value

    def character(match):
        code = int(match.group(1), 16)
        # NUL and lone surrogates cannot be stored or encoded: keep those literal.
        return match.group(0) if code == 0 or 0xD800 <= code <= 0xDFFF else chr(code)

    return _XLSX_ESCAPE.sub(character, value)


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
                values[column - 1] = decode_xlsx_escapes(value)
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


def reference_seed_path():
    return Path(os.getenv("FLORA_REFERENCE_SEED", str(
        Path(__file__).resolve().parent / "data" / "reference_metadata_seed.json.gz")))


def reference_files_version() -> tuple:
    """Which seed and manual-reference files load_reference_seed() and
    load_manual_references() would read now, and when each last changed. For a
    caller that keeps their merged result: a swapped file changes this."""
    versions = []
    for path in (reference_seed_path(), manual_reference_path()):
        try:
            versions.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            versions.append((str(path), None))
    return tuple(versions)


def load_reference_seed():
    """Frozen public provider responses supplied with the original R pipeline."""
    path = reference_seed_path()
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
