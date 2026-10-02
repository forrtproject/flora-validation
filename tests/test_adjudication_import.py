"""The adjudication feature, phase 2: importing fred-data PR #143's disagreements."""
import csv
import io
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import adjudication
from adjudication import importer
from tests.test_preparation_database import local_database  # noqa: F401

HEADER = list(importer.REQUIRED_COLUMNS)


def _csv(*rows, header=HEADER, bom=True) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in header})
    return (("﻿" if bom else "") + out.getvalue()).encode("utf-8")


ROW_SAME = {
    "doi_r": "10.1/REP-A", "title_r": "Replicating A", "kind": "same original, different outcome",
    "our_doi_o": "https://doi.org/10.1/ORIG-A", "our_title_o": "Original A", "our_outcome": "failed",
    "mo_doi_o": "10.1/orig-a", "mo_outcome": "success", "our_link_method": "llm_references",
    "our_link_confidence": "high", "our_outcome_quote": "We could not reproduce it.",
    "out_quote_source": "abstract", "mo_confidence": "high",
}
ROW_NONE = {
    "doi_r": "10.1/rep-b", "title_r": "Replicating B", "kind": "we found no original",
    "our_outcome": "pending", "mo_doi_o": "", "mo_outcome": "inconclusive",
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    importer._CACHE.clear()
    yield
    importer._CACHE.clear()


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

def test_the_source_is_pinned_to_one_commit():
    assert importer.SOURCE_COMMIT == "55d6f044038b57ac5f6057e4011214c192c7d7ac"
    assert f"/{importer.SOURCE_COMMIT}/" in importer.SOURCE_URL
    assert importer.SOURCE_URL.endswith("external/metascience-observatory/disagreements.csv")


def test_rows_are_read_with_a_bom_and_trimmed():
    rows = importer.read_rows(_csv({**ROW_SAME, "title_r": "  Replicating A  "}, ROW_NONE))
    assert [r["doi_r"] for r in rows] == ["10.1/REP-A", "10.1/rep-b"]
    assert rows[0]["title_r"] == "Replicating A"


@pytest.mark.parametrize(("content", "message"), [
    (_csv(ROW_SAME, header=[c for c in HEADER if c != "mo_outcome"]), "lacks column"),
    (_csv({**ROW_SAME, "kind": "something else"}), "unknown kind"),
    (_csv({**ROW_SAME, "doi_r": " "}), "no replication DOI"),
    (_csv(ROW_SAME, {**ROW_SAME, "our_doi_o": "10.1/orig-a"}), "repeats line 2"),
    (_csv(), "no rows"),
    ("doi_r\n\xff".encode("latin-1"), "not UTF-8"),
])
def test_a_file_that_cannot_be_trusted_is_refused(content, message):
    with pytest.raises(importer.ImportDataError, match=message):
        importer.read_rows(content)


@pytest.mark.parametrize(("cell", "doi"), [
    # All three spellings are in the PR's file.
    ("10.1017/S0003055405051658 (case conflict 1)", "10.1017/s0003055405051658"),
    ("10.1371/journal.pone.0147770 ; 10.1371/journal.pone.0147770.s002", "10.1371/journal.pone.0147770"),
    ("https://www.semanticscholar.org/paper/640fcb", "https://www.semanticscholar.org/paper/640fcb"),
    ("http://dx.doi.org/10.1016/0022-1031(76)90055-X", "10.1016/0022-1031(76)90055-x"),
])
def test_a_cell_is_reduced_to_the_doi_it_names(cell, doi):
    assert importer.clean_doi(cell) == doi


def test_rows_that_differ_only_in_a_note_stay_separate_records():
    a = {**ROW_SAME, "doi_r": "10.1017/s0003055405051658 (case conflict 1)"}
    b = {**ROW_SAME, "doi_r": "10.1017/s0003055405051658 (case conflict 2)"}
    rows = importer.read_rows(_csv(a, b))
    records = importer.build_records(rows, {}, {})
    assert len({r["import_key"] for r in records}) == 2
    assert {r["doi_r"] for r in records} == {"10.1017/s0003055405051658"}


def test_only_dois_are_looked_up():
    asked = []
    importer.lookup_works(["10.1/a (note)", "https://www.semanticscholar.org/paper/x", ""],
                          fetch_batch=lambda batch: asked.extend(batch) or [])
    assert asked == ["10.1/a"]


def test_the_import_key_ignores_doi_spelling_but_not_the_kind():
    a = importer.import_key({**ROW_SAME})
    b = importer.import_key({**ROW_SAME, "doi_r": "doi:10.1/rep-a", "our_doi_o": "10.1/orig-a"})
    assert a == b
    assert a != importer.import_key({**ROW_SAME, "kind": "different original"})


# ---------------------------------------------------------------------------
# OpenAlex and Europe PMC
# ---------------------------------------------------------------------------

def test_openalex_abstracts_are_rebuilt_in_word_order():
    assert importer.reconstruct_abstract({"world": [1], "Hello": [0], "again": [2, 3]}) == \
        "Hello world again again"
    assert importer.reconstruct_abstract(None) is None


def test_openalex_is_asked_in_batches_of_fifty_and_a_failed_batch_costs_only_itself():
    dois = [f"10.1/{i:03d}" for i in range(120)]
    seen = []

    def fetch(batch):
        seen.append(len(batch))
        if len(seen) == 2:
            raise RuntimeError("timeout")
        return [{"doi": f"https://doi.org/{d}", "title": f"T {d}", "publication_year": 2019,
                 "abstract_inverted_index": {"word": [0]}} for d in batch]

    importer.OPENALEX_DELAY, delay = 0, importer.OPENALEX_DELAY
    try:
        found, failed = importer.lookup_works(dois, fetch_batch=fetch)
    finally:
        importer.OPENALEX_DELAY = delay
    assert seen == [50, 50, 20]
    assert failed == 1 and len(found) == 70
    assert found["10.1/000"] == {"title": "T 10.1/000", "year": "2019", "abstract": "word"}


def test_lookups_are_reused_for_an_hour_but_failures_are_not_cached():
    calls = []

    def fetch(doi):
        calls.append(doi)
        if doi == "10.1/flaky" and calls.count(doi) == 1:
            raise requests.ConnectionError("reset")
        return f"abstract of {doi}"

    first, failed = importer.lookup_abstracts(["10.1/a", "10.1/flaky"], fetch=fetch)
    assert first == {"10.1/a": "abstract of 10.1/a"} and failed == 1
    second, failed = importer.lookup_abstracts(["10.1/a", "10.1/flaky"], fetch=fetch)
    assert second == {"10.1/a": "abstract of 10.1/a", "10.1/flaky": "abstract of 10.1/flaky"}
    assert failed == 0
    assert calls == ["10.1/a", "10.1/flaky", "10.1/flaky"]


def test_europe_pmc_markup_is_reduced_to_its_words(monkeypatch):
    response = MagicMock()
    response.json.return_value = {"resultList": {"result": [
        {"abstractText": "<h4>Background</h4>Smoking &amp; <i>drug</i>   use."}]}}
    get = MagicMock(return_value=response)
    monkeypatch.setattr(importer.requests, "get", get)
    assert importer._europepmc_abstract("10.1/x") == "Background Smoking & drug use."
    assert get.call_args.kwargs["params"]["query"] == 'DOI:"10.1/x"'


def test_europe_pmc_comparisons_survive_the_markup_clean_up(monkeypatch):
    """Europe PMC writes "<" literally; a bare tag pattern turned
    "(p < .05 and n > 300)" into "(p 300)"."""
    response = MagicMock()
    response.json.return_value = {"resultList": {"result": [{"abstractText":
        "<h4>Results</h4>Effects were small (p\u2009<\u2009.05 and n > 300); x<y, <b>bold</b>, a<br/>b."}]}}
    monkeypatch.setattr(importer.requests, "get", MagicMock(return_value=response))
    assert importer._europepmc_abstract("10.1/x") == \
        "Results Effects were small (p < .05 and n > 300); x<y, bold , a b."


def test_a_lookup_that_finds_nothing_keeps_what_an_earlier_import_found():
    rows = importer.read_rows(_csv(ROW_SAME))
    record = importer.build_records(rows, {}, {})[0]          # every lookup failed
    stored = {**{f: record[f] for f in importer.RECORD_FIELDS},
              "abstract_r": "Stored abstract", "abstract_source": "europepmc",
              "year_r": "2019", "mo_title_o": "Stored title", "import_key": record["import_key"],
              "judged": False}

    class Cur:
        def execute(self, *_):
            pass

        def fetchall(self):
            return [stored]

    actions = importer.plan(Cur(), [record])
    assert actions == {record["import_key"]: "unchanged"}
    assert (record["abstract_r"], record["abstract_source"]) == ("Stored abstract", "europepmc")
    assert (record["year_r"], record["mo_title_o"]) == ("2019", "Stored title")


# ---------------------------------------------------------------------------
# Rows → records
# ---------------------------------------------------------------------------

def test_records_keep_both_answers_and_fill_the_gaps():
    rows = importer.read_rows(_csv(ROW_SAME, ROW_NONE))
    works = {
        "10.1/rep-a": {"title": "OA title", "year": "2019", "abstract": None},
        "10.1/orig-a": {"title": "Original A (OpenAlex)", "year": "2001", "abstract": "x"},
        "10.1/rep-b": {"title": None, "year": None, "abstract": "From OpenAlex"},
    }
    same, none = importer.build_records(rows, works, {"10.1/rep-a": "From Europe PMC"})
    assert same["doi_r"] == "10.1/rep-a" and same["title_r"] == "Replicating A"
    assert (same["abstract_r"], same["abstract_source"]) == ("From Europe PMC", "europepmc")
    assert same["year_r"] == "2019"
    assert (same["flora_doi_o"], same["flora_title_o"], same["flora_outcome"]) == \
        ("10.1/orig-a", "Original A", "failed")
    assert (same["mo_doi_o"], same["mo_title_o"], same["mo_outcome"]) == \
        ("10.1/orig-a", "Original A (OpenAlex)", "success")
    assert same["flora_outcome_quote"] == "We could not reproduce it."
    assert same["raw"]["our_doi_o"] == "https://doi.org/10.1/ORIG-A"     # untouched
    assert (none["abstract_r"], none["abstract_source"]) == ("From OpenAlex", "openalex")
    assert none["flora_doi_o"] is None and none["mo_doi_o"] is None and none["mo_title_o"] is None


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

def _client(ready=True, run=None):
    app = FastAPI()
    app.include_router(adjudication.create_router(
        current_admin=lambda: {"id": 3, "handle": "Hamid"},
        current_validator=lambda: {"coder_id": 7, "validator_tier": 1},
        status=lambda: adjudication.SetupStatus(True, ready, None if ready else "LockNotAvailable"),
        cursor=MagicMock()))
    if run is not None:
        importer.run_import, original = run, importer.run_import
        return TestClient(app), original
    return TestClient(app), None


def test_import_is_refused_until_the_feature_is_set_up():
    client, _ = _client(ready=False)
    response = client.post("/api/admin/disagreements/import", json={"apply": False})
    assert response.status_code == 503
    assert "LockNotAvailable" in response.json()["detail"]


def test_import_previews_by_default_and_records_the_admin():
    calls = []
    client, original = _client(run=lambda cursor, **kw: calls.append(kw) or {"applied": kw["apply"]})
    try:
        assert client.post("/api/admin/disagreements/import", json={}).json() == {"applied": False}
        assert client.post("/api/admin/disagreements/import", json={"apply": True}).json() == {"applied": True}
    finally:
        importer.run_import = original
    assert calls == [{"apply": False, "imported_by": "Hamid"}, {"apply": True, "imported_by": "Hamid"}]


@pytest.mark.parametrize(("error", "status"), [
    (importer.ImportDataError("unknown kind 'x'"), 422),
    (requests.ConnectionError("github down"), 502),
])
def test_import_failures_come_back_as_plain_messages(error, status):
    def run(cursor, **kw):
        raise error

    client, original = _client(run=run)
    try:
        response = client.post("/api/admin/disagreements/import", json={"apply": True})
    finally:
        importer.run_import = original
    assert response.status_code == status
    assert str(error) in response.json()["detail"]


# ---------------------------------------------------------------------------
# Against a real PostgreSQL, when one is offered (FLORA_TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

def test_a_full_import_on_postgres(local_database, monkeypatch):  # noqa: F811
    """On a throwaway database of its own (local_database: localhost only)."""
    import psycopg2
    import psycopg2.extras

    url = os.environ["DATABASE_URL"]
    monkeypatch.setenv("ADJUDICATION_ENABLED", "1")
    admin = psycopg2.connect(url)
    admin.autocommit = True
    assert adjudication.setup(url).ready

    @contextmanager
    def cursor():
        conn = psycopg2.connect(url)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    no_works = lambda dois: ({}, 0)           # noqa: E731
    no_abstracts = lambda dois: ({}, 0)       # noqa: E731
    run = lambda content, apply, by="Hamid": importer.run_import(   # noqa: E731
        cursor, apply=apply, imported_by=by, content=content, lookup=no_works, abstracts=no_abstracts)
    try:
        first = _csv(ROW_SAME, ROW_NONE)
        preview = run(first, apply=False)
        assert preview["actions"] == {"new": 2, "updated": 0, "unchanged": 0, "kept": 0}
        with admin.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM adjudication.records")
            assert cur.fetchone()[0] == 0, "a preview writes nothing"

        assert run(first, apply=True)["actions"]["new"] == 2
        assert run(first, apply=True)["actions"] == {"new": 0, "updated": 0, "unchanged": 2, "kept": 0}

        # One row gets judged; then the file changes both rows.
        with admin.cursor() as cur:
            cur.execute("SELECT record_id FROM adjudication.records WHERE doi_r = '10.1/rep-a'")
            record_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO adjudication.judgements
                           (record_id, validator_id, validator_handle, validator_tier, state,
                            original_choice, outcome, submitted_at)
                           VALUES (%s, 7, 'ana', 1, 'submitted', 'flora', 'failed', NOW())""",
                        (record_id,))
        changed = _csv({**ROW_SAME, "our_outcome": "mixed"}, {**ROW_NONE, "mo_outcome": "failure"})
        assert run(changed, apply=True)["actions"] == {"new": 0, "updated": 1, "unchanged": 0, "kept": 1}
        with admin.cursor() as cur:
            cur.execute("SELECT doi_r, flora_outcome, mo_outcome, imported_by, status "
                        "FROM adjudication.records ORDER BY doi_r")
            assert cur.fetchall() == [
                ("10.1/rep-a", "failed", "success", "Hamid", "open"),     # judged: kept as it was
                ("10.1/rep-b", "pending", "failure", "Hamid", "open"),    # unjudged: refreshed
            ]
            # A row no longer in the file is never deleted.
        assert run(_csv(ROW_NONE), apply=True)["rows"] == 1
        with admin.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM adjudication.records")
            assert cur.fetchone()[0] == 2
    finally:
        admin.close()
