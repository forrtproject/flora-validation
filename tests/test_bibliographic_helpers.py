"""Provider parity and cache precedence without HTTP or a live database."""
import csv
import json
from unittest.mock import Mock
import urllib.error
import zipfile

import pytest

import author_overlap
import bibliographic_helpers as bh
import enrich_works as ew


def test_manual_bibtex_recovers_nested_fields_and_preserves_explicit_overrides(tmp_path):
    path = tmp_path / "manual.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "title", "reference_bibtex", "reference_apa"])
        writer.writeheader()
        writer.writerow({"key": "https://doi.org/10.1234/ABC", "title": "Curated title",
                         "reference_apa": "Keep this exact citation.",
                         "reference_bibtex": '@article{x, title={Nested {APA} title}, author={van Hell, Janet and Smith, Jane}, year="2023", journal={A journal}, pages={1--7}}'})
    row = bh.load_manual_references(path)["10.1234/abc"]
    assert row["title"] == "Curated title"
    assert row["apa_ref"] == "Keep this exact citation."
    assert row["year"] == "2023"
    assert row["pages"] == "1--7"
    assert json.loads(row["authors_json"])[0]["family"] == "van Hell"
    assert row["authors"] == "Janet van Hell; Jane Smith"


def test_excel_reader_supports_shared_strings_inline_and_sparse_cells(tmp_path):
    path = tmp_path / "manual.xlsx"
    namespace = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook {namespace} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="References" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet2.xml"/></Relationships>')
        archive.writestr("xl/sharedStrings.xml", f'<sst {namespace}><si><t>key</t></si><si><t>title</t></si><si><t>year</t></si><si><t>DUMMY_BOOK</t></si></sst>')
        archive.writestr("xl/worksheets/sheet2.xml", f'<worksheet {namespace}><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row><row><c r="A2" t="s"><v>3</v></c><c r="B2" t="inlineStr"><is><t>A book title</t></is></c><c r="C2"><v>2024</v></c></row></sheetData></worksheet>')
    row = bh.load_manual_references(path)["dummy_book"]
    assert row["title"] == "A book title"
    assert row["year"] == "2024"
    assert "doi =" not in row["bibtex_ref"]


def test_manual_url_key_and_work_ids_are_normalized():
    assert bh.normalise_key(" HTTPS://WWW.OSF.IO/AbCdE/ ") == "osf.io/abcde"
    assert bh.normalise_key("http://dx.doi.org/10.1234/ABC get rights and content") == "10.1234/abc"
    assert bh.normalise_key("w123456") == "W123456"


def test_crossref_fields_keep_structured_surnames_and_publication_year():
    row = bh.crossref_fields({"title": ["Study"], "container-title": ["Journal"],
        "published-print": {"date-parts": [[2021, 3]]},
        "created": {"date-parts": [[2020]]},
        "author": [{"given": "Janet", "family": "van Hell"}], "page": "12-17"})
    assert row["year"] == "2021"
    assert json.loads(row["authors_json"])[0]["family"] == "van Hell"
    assert row["pages"] == "12-17"


def test_datacite_fallback_and_negotiated_citations(monkeypatch):
    calls = []
    def request(url, accept="application/json"):
        calls.append((url, accept))
        if "api.crossref.org" in url:
            return None
        if "api.datacite.org" in url:
            return {"data": {"attributes": {"titles": [{"title": "Registered replication"}],
                "publicationYear": 2024, "creators": [{"givenName": "Ada", "familyName": "Lovelace"}]}}}
        if accept.startswith("text/x-bibliography"):
            return "Lovelace, A. (2024). Registered replication."
        if accept == "application/x-bibtex":
            return "@misc{key, title={Registered replication}}"
        raise AssertionError("CSL not needed after complete DataCite metadata")
    monkeypatch.setattr(bh, "request", request)
    row = bh.fetch_doi_reference("10.1234/example")
    assert row["metadata_source"] == "datacite"
    assert row["apa_ref"] == "Lovelace, A. (2024). Registered replication."
    assert row["bibtex_ref"].startswith("@misc{key,")
    assert row["title"] == "Registered replication"
    assert len(calls) == 4


def test_doi_content_negotiation_fallback_and_bibtex_recovery(monkeypatch):
    def request(url, accept="application/json"):
        if accept == "application/vnd.citationstyles.csl+json":
            return {"title": "CSL title", "author": [{"family": "Smith", "given": "J."}],
                    "issued": {"date-parts": [[2020]]}}
        if accept == "application/x-bibtex":
            return '@article{key,title={Wrong title},journal={Recovered journal}}'
        return None
    monkeypatch.setattr(bh, "request", request)
    row = bh.fetch_doi_reference("10.1234/otheragency")
    assert row["title"] == "CSL title"
    assert row["journal"] == "Recovered journal"
    assert row["metadata_source"] == "doi-csl"
    assert "Smith, J." in row["apa_ref"]


def test_osf_file_citation_resolves_the_target_node(monkeypatch):
    urls = []
    def request(url, accept="application/json"):
        urls.append(url)
        if "/nodes/abcde/" in url:
            return None
        if "/files/abcde/" in url:
            return {"data": {"relationships": {"target": {"data": {"id": "vwxyz"}}}}}
        citation = ('@misc{osf,title={Registered study},author={Smith, Jane},year={2020}}'
                    if "/bibtex/" in url else "Smith, J. (2020). Registered study.")
        return {"data": {"attributes": {"citation": citation}}}
    monkeypatch.setattr(bh, "request", request)
    row = bh.fetch_osf_reference("https://osf.io/abcde/")
    assert row["title"] == "Registered study"
    assert row["year"] == "2020"
    assert row["metadata_source"] == "osf"
    assert any("/nodes/vwxyz/citation/bibtex/" in url for url in urls)
    assert bh.fetch_osf_reference("https://example.com/abcde") == {}


@pytest.mark.parametrize("data,expected", [
    ({"is_oa": True, "best_oa_location": {"url_for_pdf": "https://example.org/a.pdf", "url": "https://example.org/a"}}, "https://example.org/a.pdf"),
    ({"is_oa": True, "best_oa_location": {"url": "https://example.org/a"}}, "https://example.org/a"),
    ({"is_oa": False, "best_oa_location": {"url": "https://stale.example.org/"}}, None),
])
def test_unpaywall_uses_pdf_then_landing_and_respects_closed_status(monkeypatch, data, expected):
    monkeypatch.setattr(bh, "request", lambda url: data)
    assert bh.fetch_unpaywall("10.1234/paper", "contact@example.org")["oa_url"] == expected


def test_request_retries_transient_errors_but_does_not_turn_them_into_misses(monkeypatch):
    failure = urllib.error.HTTPError("https://api.crossref.org/works/x", 503, "Unavailable", {}, None)
    call = Mock(side_effect=failure)
    monkeypatch.setattr(bh.urllib.request, "urlopen", call)
    monkeypatch.setattr(bh.time, "sleep", lambda _: None)
    with pytest.raises(bh.ReferenceLookupError, match="HTTP 503"):
        bh.request("https://api.crossref.org/works/x")
    assert call.call_count == 3


def test_request_caches_definitive_misses_without_retry(monkeypatch):
    call = Mock(side_effect=urllib.error.HTTPError("https://doi.org/x", 404, "Missing", {}, None))
    monkeypatch.setattr(bh.urllib.request, "urlopen", call)
    assert bh.request("https://doi.org/x") is None
    assert call.call_count == 1


def test_request_rejects_html_as_citation(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    response.headers = {"Content-Type": "text/html"}
    response.read.return_value = b"<html>Publisher landing page</html>"
    monkeypatch.setattr(bh.urllib.request, "urlopen", lambda *args, **kwargs: response)
    assert bh.request("https://doi.org/10.1234/paper", "text/x-bibliography; style=apa") is None


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
    def execute(self, sql, params=None):
        self.sql = sql
    def fetchall(self):
        return self.rows


def test_metadata_seed_beats_openalex_references_and_manual_beats_both(monkeypatch):
    seed = {"records": {"10.1234/paper": {"title": "Crossref title", "apa_ref": "APA from cache", "authors_json": '[{"family":"Smith"}]'}}, "url_to_doi": {}}
    monkeypatch.setattr(bh, "load_reference_seed", lambda: seed)
    monkeypatch.setattr(bh, "load_manual_references", lambda: {"10.1234/paper": {"title": "Curated title", "metadata_source": "manual"}})
    cur = Cursor([{"doi": "10.1234/paper", "title": "OpenAlex title", "metadata_source": "openalex", "oa_work_id": "W12345", "language": "en"}])
    row = ew.load_metadata(cur)["10.1234/paper"]
    assert row["title"] == "Curated title"
    assert row["apa_ref"] == "APA from cache"
    assert row["oa_work_id"] == "W12345"
    assert row["language"] == "en"


def test_existing_direct_unpaywall_closed_status_clears_seed_url(monkeypatch):
    monkeypatch.setattr(bh, "load_reference_seed", lambda: {"records": {"10.1234/a": {"oa_url": "https://old.example.org"}}})
    monkeypatch.setattr(bh, "load_manual_references", lambda: {})
    row = ew.load_metadata(Cursor([{"doi": "10.1234/a", "reference_checked_at": "2026-09-15", "oa_url": None}]))["10.1234/a"]
    assert row["oa_url"] is None


def test_reference_enrichment_uses_supplied_caches_without_network(monkeypatch):
    seed_row = {"title": "Cached study", "apa_ref": "Cached APA", "bibtex_ref": "@misc{cached}",
                "_reference_cached": True, "_unpaywall_cached": True}
    monkeypatch.setattr(ew, "reference_keys_in_product", lambda cur: ["10.1234/a"])
    monkeypatch.setattr(ew, "load_metadata", lambda cur, include_seed: {"10.1234/a": seed_row})
    monkeypatch.setattr(bh, "load_manual_references", lambda: {})
    monkeypatch.setattr(bh, "request", lambda *args, **kwargs: pytest.fail("Cached references must not use HTTP"))
    stored = []
    monkeypatch.setattr(ew, "_store_reference_rows", lambda cur, rows: stored.extend(rows))
    stats = ew.enrich_references(Cursor(), verbose=False)
    assert stats["cached"] == 1
    assert stats["fetched"] == 0
    assert stored[0]["apa_ref"] == "Cached APA"


def test_dry_run_does_not_request_or_write_references(monkeypatch):
    monkeypatch.setattr(ew, "reference_keys_in_product", lambda cur: ["10.1234/new"])
    monkeypatch.setattr(ew, "load_metadata", lambda cur, include_seed: {})
    monkeypatch.setattr(bh, "load_manual_references", lambda: {})
    monkeypatch.setattr(bh, "request", lambda *args, **kwargs: pytest.fail("Dry run requested network"))
    monkeypatch.setattr(ew, "_store_reference_rows", lambda *args: pytest.fail("Dry run wrote metadata"))
    assert ew.enrich_references(Cursor(), dry_run=True, verbose=False)["todo"] == 1


def test_transient_reference_failure_remains_retryable(monkeypatch):
    monkeypatch.setattr(ew, "reference_keys_in_product", lambda cur: ["10.1234/new"])
    monkeypatch.setattr(ew, "load_metadata", lambda cur, include_seed: {})
    monkeypatch.setattr(bh, "load_manual_references", lambda: {})
    monkeypatch.setattr(bh, "fetch_doi_reference", Mock(side_effect=bh.ReferenceLookupError("provider down")))
    store = Mock()
    monkeypatch.setattr(ew, "_store_reference_rows", store)
    with pytest.raises(bh.ReferenceLookupError):
        ew.enrich_references(Cursor(), verbose=False)
    store.assert_not_called()


def test_openalex_abstract_reconstruction_preserves_position_order():
    assert ew.reconstruct_abstract({"study": [1, 4], "This": [0], "is": [2], "a": [3]}) == "This study is a study"
    assert ew.reconstruct_abstract(None) is None


def test_work_id_is_a_bibtex_url_never_a_fake_doi():
    row = ew.shape_by_work_id({"title": "Thesis", "id": "https://openalex.org/W123456"}, "W123456")
    assert "doi =" not in row["bibtex_ref"]
    assert "url = {https://openalex.org/W123456}" in row["bibtex_ref"]


def test_structured_author_overlap_preserves_surnames_and_team_size():
    original = json.dumps([{"family": "van Hell", "given": "Janet"}, {"family": "Smith", "given": "A"}])
    replication = json.dumps([{"family": "van Hell", "given": "Janet"}, {"family": "Smith", "given": "A"}, {"family": "Smith", "given": "B"}])
    assert author_overlap.families(original) == {"van hell", "smith"}
    assert author_overlap.count(original, replication) == (2, 66.7)
    assert author_overlap.families("[invalid JSON]") == set()


def test_supplied_seed_and_manual_workbook_are_readable():
    seed = bh.load_reference_seed()
    manual = bh.load_manual_references()
    assert len(seed["records"]) >= 4000
    assert len(seed["url_to_doi"]) >= 200
    assert len(manual) >= 200
    assert all(key == bh.normalise_key(key) for key in seed["records"])
    assert any(row.get("authors_json") and row.get("apa_ref") for row in seed["records"].values())
