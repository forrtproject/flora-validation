"""The author lists and text in flora.csv, against fred-data's file it replaces.

Four regressions found comparing the two: ORCIDs dropped, Excel's "_x000D_" escapes
left in, author cells as plain "A; B; C" strings, and "given": null written where
fred-data leaves the key out. The JSON must also be fred-data's compact form.
"""
import json
import zipfile

import pandas as pd

import bibliographic_helpers as references
import enrich_works
import transform_sources


def test_crossref_orcids_are_kept_in_freds_form():
    authors = [{"given": "Lukas", "family": "Wallrich", "sequence": "first",
                "ORCID": "https://orcid.org/0000-0003-2121-5177", "authenticated-orcid": False,
                "affiliation": []},
               {"given": "Miki", "family": "Wesołowska", "sequence": "additional", "affiliation": []}]
    assert references.author_fields(authors)["authors_json"] == (
        '[{"given":"Lukas","family":"Wallrich","sequence":"first",'
        '"ORCID":"https://orcid.org/0000-0003-2121-5177"},'
        '{"given":"Miki","family":"Wesołowska","sequence":"additional"}]')


def test_an_empty_name_part_is_left_out_not_null():
    out = json.loads(references.author_fields([{"family": "Plato"}])["authors_json"])
    assert out == [{"family": "Plato", "sequence": "first"}]
    org = json.loads(references.author_fields([{"name": "Open Science Collaboration"}])["authors_json"])
    assert org == [{"name": "Open Science Collaboration", "sequence": "first"}]


def test_datacite_orcids_come_from_name_identifiers():
    fields = references.datacite_fields({"creators": [
        {"givenName": "Ada", "familyName": "Lovelace", "nameIdentifiers": [
            {"nameIdentifier": "0000-0002-1825-0097", "nameIdentifierScheme": "ORCID"}]}]})
    assert json.loads(fields["authors_json"])[0]["ORCID"] == "https://orcid.org/0000-0002-1825-0097"


def test_openalex_orcids_are_kept():
    work = {"authorships": [
        {"author": {"display_name": "Roderick C. Slieker", "orcid": "https://orcid.org/0000-0003-0961-9152"}},
        {"author": {"display_name": "Louise A. Donnelly", "orcid": None}}]}
    assert json.loads(enrich_works._authors_json(work)) == [
        {"given": "Roderick C.", "family": "Slieker", "sequence": "first",
         "ORCID": "https://orcid.org/0000-0003-0961-9152"},
        {"given": "Louise A.", "family": "Donnelly", "sequence": "additional"}]


def test_a_plain_author_string_becomes_a_list():
    """A search index splitting on commas reads "A; B; C" as one name."""
    out = json.loads(references.normalise_author_json(
        "Ning Yang; Tripti Singh; Allen C. Johnston;"))
    assert [(a.get("given"), a["family"]) for a in out] == [
        ("Ning", "Yang"), ("Tripti", "Singh"), ("Allen C.", "Johnston")]
    assert [a["sequence"] for a in out] == ["first", "additional", "additional"]


def test_plain_names_keep_organisations_whole_and_read_family_initials():
    out = json.loads(references.normalise_author_json(
        "The International Multiple Sclerosis Genetics Consortium; Busenitz L.; K. Hyndman"))
    assert out[0] == {"name": "The International Multiple Sclerosis Genetics Consortium",
                      "sequence": "first"}
    assert (out[1]["family"], out[1]["given"]) == ("Busenitz", "L.")
    assert (out[2]["family"], out[2]["given"]) == ("Hyndman", "K.")


def test_a_cached_list_is_rewritten_compactly_without_nulls():
    cached = '[{"given": null, "family": "Rachev", "sequence": "first"}]'
    assert references.normalise_author_json(cached) == '[{"family":"Rachev","sequence":"first"}]'
    assert references.normalise_author_json(None) is None
    assert references.normalise_author_json('[{"broken"') == '[{"broken"', "unparseable JSON is kept"


def test_workbook_escapes_are_decoded(tmp_path):
    """data/manual_references.xlsx stores each carriage return as "_x000D_"."""
    path = tmp_path / "refs.xlsx"
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>bibtex_ref</t></is></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>@misc{x,_x000D_\n title={T}}</t></is></c>'
             '</row></sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml",
                         '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                         '<sheets><sheet name="s" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    assert references._xlsx_rows(path) == [{"bibtex_ref": "@misc{x,\r\n title={T}}"}]


def test_the_csv_output_carries_no_plain_authors_and_leaves_other_text_alone():
    """Escapes are decoded where a workbook is read, never in the output: a BibTeX
    key of our own, from 10.31219/osf.io/x394e_v3, holds the pattern "_x394e_"."""
    key = "@misc{10_31219_osf_io_x394e_v3,\n}"
    frame = pd.DataFrame([{
        "doi_o": "10.1/o", "doi_r": "10.1/r", "title_o": "O", "title_r": "R",
        "author_o": '[{"given": null, "family": "A", "sequence": "first"}]',
        "author_r": "Ning Yang; Tripti Singh",
        "bibtex_ref_r": key, "type": "replication"}])
    out = transform_sources.to_output_shape(frame)
    assert out.loc[0, "author_o"] == '[{"family":"A","sequence":"first"}]'
    assert json.loads(out.loc[0, "author_r"])[1] == {"given": "Tripti", "family": "Singh",
                                                    "sequence": "additional"}
    assert out.loc[0, "bibtex_ref_r"] == key


def test_escapes_that_cannot_be_stored_stay_literal():
    """NUL and lone surrogates would fail the PostgreSQL write or the UTF-8 encode."""
    assert references.decode_xlsx_escapes("a_x000D_b _x0000_ _xD800_") == "a\rb _x0000_ _xD800_"


def test_nan_and_na_are_names_not_missing_values():
    """"Nan Jiang" and "Elim Na" are in fred-data's file with both parts."""
    out = json.loads(references.author_fields(
        [{"given": "Nan", "family": "Jiang"}, {"given": "Elim", "family": "Na"}])["authors_json"])
    assert [(a["given"], a["family"]) for a in out] == [("Nan", "Jiang"), ("Elim", "Na")]


def test_plain_names_with_particles_and_capitals_split_as_crossref_does():
    out = json.loads(references.normalise_author_json(
        "Han L. J. van der Maas; Marcel A. L. M. van Assen; YING LIU; NATHAN LEE"))
    assert [(a["given"], a["family"]) for a in out] == [
        ("Han L. J.", "van der Maas"), ("Marcel A. L. M.", "van Assen"),
        ("YING", "LIU"), ("NATHAN", "LEE")]


def test_orcids_are_always_the_https_form_with_an_upper_case_check_digit():
    assert references._orcid({"ORCID": "http://orcid.org/0000-0002-1694-233x"}) == \
        "https://orcid.org/0000-0002-1694-233X"
    assert references._orcid({"ORCID": True}) is None
    assert references._orcid({"orcid": "not an orcid"}) is None


def test_missing_author_values_are_left_as_they_are():
    for value in (None, pd.NA, "", "NA", "N/A"):
        assert references.normalise_author_json(value) is value


def test_the_refresh_falls_back_to_datacite_then_the_doi(monkeypatch):
    """A row cached as "crossref+datacite" took its authors from DataCite."""
    import backfill_author_orcids as backfill
    asked = []

    def request(url, accept="application/json"):
        asked.append(url.split("/")[2])
        if "datacite" in url:
            return {"data": {"attributes": {"creators": [{"givenName": "Ada", "familyName": "Lovelace"}]}}}
        return {"message": {"author": []}} if "crossref" in url else None

    monkeypatch.setattr(backfill.references, "request", request)
    authors, authors_json = backfill.fetch_authors({"doi": "10.1/x", "metadata_source": "crossref+datacite"})
    assert asked == ["api.crossref.org", "api.datacite.org"]
    assert authors == "Ada Lovelace"


def test_the_refresh_selects_every_doi_row_but_hand_entered_ones():
    import backfill_author_orcids as backfill

    class Cur:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchall(self):
            return [{"doi": "10.1234/x", "metadata_source": "doi-csl", "authors": "A", "authors_json": "[]"},
                    {"doi": "W123", "metadata_source": "openalex-workid", "authors": "A", "authors_json": "[]"}]

    cur = Cur()
    assert [row["doi"] for row in backfill.cached_rows(cur)] == ["10.1234/x"], "DOIs only"
    assert cur.params == (["manual", "osf", "crossref_not_found"],)


def test_an_openalex_rows_orcids_win_over_a_seed_list_without_them(monkeypatch):
    """For OpenAlex rows the seed's Crossref fields win, but not its ORCID-less authors."""
    seed = {"records": {"10.1/x": {"title": "Seed title",
                                   "authors_json": '[{"family":"Lovelace","sequence":"first"}]',
                                   "authors": "Lovelace"}}, "url_to_doi": {}}
    cached = {"doi": "10.1/x", "title": "OpenAlex title", "metadata_source": "openalex",
              "authors": "Ada Lovelace",
              "authors_json": '[{"given":"Ada","family":"Lovelace","sequence":"first",'
                              '"ORCID":"https://orcid.org/0000-0002-1825-0097"}]'}

    class Cur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [dict(cached)]

    monkeypatch.setattr(enrich_works.references, "load_reference_seed", lambda: seed)
    monkeypatch.setattr(enrich_works.references, "load_manual_references", lambda: {})
    merged = enrich_works.load_metadata(Cur())["10.1/x"]
    assert "ORCID" in merged["authors_json"]
    assert merged["title"] == "Seed title", "other fields keep the seed's precedence"
