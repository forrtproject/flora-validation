"""Public API transport, prepared snapshot identity, and CSRF integration.

PostgreSQL cases use the explicitly opted-in localhost fixture, never the app's
environment file. The app middleware is compiled from its AST because importing
app.py initializes the database and starts background jobs.
"""
import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

import flora_public_api
import flora_store
from tests.test_preparation_database import local_database  # noqa: F401


ORIGINAL = "10.1234/original"
REPLICATION = "10.1234/repl-one"
SECOND = "10.1234/repl-two"
OTHER = "10.2345/other"
DERIVED = {"outcome_mix", "replication_year_counts", "first_replication_year", "first_replication_outcome"}


def make_row(identifier, **values):
    row = dict.fromkeys(flora_store.CSV_COLUMNS)
    row.update(id=identifier, id_md5=hashlib.md5(identifier.encode()).hexdigest(),
               type="replication", doi_o=ORIGINAL, doi_r=REPLICATION,
               doi_o_hash="abc", doi_r_hash="def", title_o="Alpha memory experiment",
               title_r="Replication of Alpha memory", author_o='[{"given":"Ana","family":"Example"}]',
               author_r='[{"given":"Ben","family":"Example"}]',
               year_o="2010", year_r="2020", outcome="successful", source="replications")
    row.update(values)
    return row


def save_snapshot(connection, rows):
    with connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, pd.DataFrame(rows, columns=flora_store.CSV_COLUMNS))


def make_client(database=flora_public_api.readonly_database, *, middleware=False):
    app = FastAPI()
    app.include_router(flora_public_api.create_router(database=database))
    if middleware:
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8"))
        functions = [node for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name in {"_is_cross_site", "block_cross_site_writes"}]
        assert len(functions) == 2
        namespace = {"app": app, "Request": Request, "JSONResponse": JSONResponse,
                     "_SAFE_METHODS": frozenset({"GET", "HEAD", "OPTIONS"}),
                     "_allowed_origins": lambda: {"https://flora.example"},
                     "is_public_read_request": flora_public_api.is_public_read_request}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), namespace)

        @app.post("/api/admin/test-write")
        def admin_write():
            pytest.fail("A cross-site administrative write must not reach its handler")

    return TestClient(app)


@pytest.fixture
def public_dataset(local_database):  # noqa: F811
    rows = [
        make_row("FLORA-000001", doi_o="HTTPS://DOI.ORG/10.1234/ORIGINAL"),
        make_row("FLORA-000002", doi_r=SECOND, doi_r_hash="456", year_r="2022", outcome="failed"),
        make_row("FLORA-000003", doi_o=OTHER, doi_r=None, doi_r_hash=None,
                 title_o="Beta ecology experiment", title_r="Reproduction report",
                 type="reproduction", year_o="2015", year_r="2023", url_r="https://osf.io/example",
                 outcome="computationally reproducible, robust"),
        # Distinct relationship IDs must survive even when both DOIs repeat.
        make_row("FLORA-000004"),
    ]
    save_snapshot(local_database, rows)
    return local_database, make_client(), rows


@contextmanager
def no_database():
    pytest.fail("Invalid requests and CORS preflights must not open a database")
    yield  # pragma: no cover


def test_doi_lookup_normalizes_and_preserves_every_relationship(public_dataset):
    _, client, rows = public_dataset
    response = client.post("/v1/original-lookup", json={"dois": [" DOI: 10.1234/ORIGINAL ", "10.1234/missing"]})
    assert response.status_code == 200
    results = response.json()["results"]
    assert list(results) == [ORIGINAL, "10.1234/missing"]
    assert results["10.1234/missing"] is None
    record = results[ORIGINAL]
    assert record["doi"] == ORIGINAL
    assert record["title"] == "Alpha memory experiment"
    assert record["types"] == ["original"]
    assert record["record"]["stats"]["n_replications_total"] == 3
    assert [entry["id"] for entry in record["record"]["replications"]] == [rows[i]["id"] for i in (0, 1, 3)]
    assert record["outcome_mix"] == {"successful": 2, "failed": 1}
    assert record["first_replication_year"] == "2020"
    assert not DERIVED.intersection(record["record"])
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"].startswith("public")


def test_get_repeated_dois_and_prefix_buckets_keep_full_aggregates(public_dataset):
    _, client, _ = public_dataset
    response = client.get("/v1/original-lookup", params=[("dois", ORIGINAL), ("dois", OTHER)])
    assert response.status_code == 200
    assert list(response.json()["results"]) == [ORIGINAL, OTHER]
    reproduction = response.json()["results"][OTHER]["record"]["reproductions"][0]
    assert reproduction["doi"] is None and reproduction["id"] == "FLORA-000003"
    response = client.get("/v1/prefix-lookup", params=[("prefix", "ABC"), ("prefix", "def"), ("prefix", "000")])
    assert response.status_code == 200
    buckets = response.json()["results"]
    assert [entry["doi"] for entry in buckets["abc"]] == [ORIGINAL, OTHER]
    assert [entry["doi"] for entry in buckets["def"]] == [REPLICATION]
    assert buckets["000"] == []
    assert buckets["def"][0]["record"]["originals"][0]["id"] == "FLORA-000001"


def test_api_email_suppression_does_not_poison_cached_public_payload(public_dataset):
    _, client, _ = public_dataset
    for method, path, arguments in [
        ("post", "/v1/original-lookup", {"json": {"dois": [ORIGINAL], "apiEmail": "reader@example.org"}}),
        ("get", "/v1/original-lookup", {"params": {"dois": ORIGINAL, "apiEmail": "reader@example.org"}}),
        ("post", "/v1/prefix-lookup", {"json": {"prefixes": ["abc"], "apiEmail": "reader@example.org"}}),
    ]:
        response = getattr(client, method)(path, **arguments)
        assert response.status_code == 200
        results = response.json()["results"]
        record = results[ORIGINAL] if "original" in path else results["abc"][0]
        assert not DERIVED.intersection(record)
        assert not DERIVED.intersection(record["record"])
    normal = client.post("/v1/original-lookup", json={"dois": [ORIGINAL]}).json()["results"][ORIGINAL]
    assert DERIVED.issubset(normal)


def test_doi_listing_and_search_paginate_after_filtering(public_dataset):
    _, client, _ = public_dataset
    response = client.get("/v1/dois")
    assert response.status_code == 200
    assert response.json() == {"total": 4, "dois": [ORIGINAL, REPLICATION, SECOND, OTHER]}
    response = client.get("/v1/search", params=[("exclude", "absent-token"), ("exclude", "another-absent-token"),
                                                  ("limit", "2"), ("offset", "0")],
                          headers={"Origin": "https://forrt.org"})
    assert response.status_code == 200
    page_one = response.json()
    assert {"query", "total", "offset", "limit", "hasMore", "results"} <= page_one.keys()
    assert (page_one["total"], page_one["offset"], page_one["limit"], page_one["hasMore"]) == (4, 0, 2, True)
    assert len(page_one["results"]) == 2
    assert response.headers["access-control-allow-origin"] == "https://forrt.org"
    second = client.post("/v1/search", json={"exclude": ["absent-token"], "limit": 2, "offset": 2}).json()
    assert (second["total"], second["offset"], second["hasMore"]) == (4, 2, False)
    assert not set(page_one["results"]).intersection(second["results"])
    assert set(page_one["results"]) | set(second["results"]) == {ORIGINAL, REPLICATION, SECOND, OTHER}


def test_id_hash_lookup_is_flat_complete_and_distinct_from_doi_prefix(public_dataset):
    _, client, rows = public_dataset
    digest = rows[0]["id_md5"]
    unknown = "0" * 32
    response = client.post("/v1/id-lookup", json={"hashes": [digest.upper(), unknown]})
    assert response.status_code == 200
    record = response.json()["results"][digest]
    assert set(flora_store.CSV_COLUMNS).issubset(record)
    assert record["id"] == "FLORA-000001" and record["id_md5"] == digest
    assert record["_meta"]["active"] is True
    assert response.json()["results"][unknown] is None
    assert client.get("/v1/id-lookup", params={"id_md5": digest}).json()["results"][digest]["id"] == record["id"]
    assert client.post("/v1/prefix-lookup", json={"prefixes": [digest]}).status_code == 400
    assert client.post("/v1/id-lookup", json={"hashes": ["abc"]}).status_code == 400


def test_committed_snapshot_invalidates_doi_search_cache_and_retains_retired_ids(public_dataset):
    connection, client, rows = public_dataset
    before = client.get("/v1/original-lookup", params={"dois": ORIGINAL}).json()["results"][ORIGINAL]
    assert before["title"] == "Alpha memory experiment"
    # Replace the prepared snapshot after the router has populated its cache.
    corrected = dict(rows[0], title_o="Corrected gamma experiment", doi_r="10.1234/corrected-replication", doi_r_hash="789")
    save_snapshot(connection, [corrected])
    after = client.get("/v1/original-lookup", params={"dois": ORIGINAL}).json()["results"][ORIGINAL]
    assert after["title"] == "Corrected gamma experiment"
    assert after["record"]["stats"]["n_replications_total"] == 1
    listing = client.get("/v1/dois").json()
    assert listing == {"total": 2, "dois": [ORIGINAL, "10.1234/corrected-replication"]}
    assert client.get("/v1/prefix-lookup", params={"prefix": "def"}).json()["results"]["def"] == []
    search = client.post("/v1/search", json={"exclude": ["absent-token"]}).json()
    assert search["total"] == 2
    retired_hash = rows[2]["id_md5"]
    retired = client.get("/v1/id-lookup", params={"hashes": retired_hash}).json()["results"][retired_hash]
    assert retired["id"] == rows[2]["id"]
    assert retired["_meta"]["active"] is False and retired["_meta"]["retired_at"]


@pytest.mark.parametrize("path,body", [
    ("original-lookup", {}), ("original-lookup", {"dois": "10.1234/wrong-shape"}),
    ("original-lookup", {"dois": [1]}), ("original-lookup", {"dois": ["x" * 2049]}),
    ("original-lookup", {"dois": [f"10.1234/{index}" for index in range(201)]}),
    ("prefix-lookup", {"prefixes": ["ab"]}), ("prefix-lookup", {"prefixes": ["xyz"]}),
    ("prefix-lookup", {"prefixes": ["abc"] * 201}),
    ("id-lookup", {"hashes": ["x" * 32]}), ("id-lookup", {"hashes": ["a" * 32] * 201}),
    ("search", {"query": ["wrong"]}), ("search", {"query": "valid", "offset": -1}),
    ("search", {"query": "valid", "limit": 0}),
])
def test_invalid_values_are_rejected_before_opening_database(path, body):
    response = make_client(no_database).post("/v1/" + path, json=body)
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [b"{", b"[1, 2]", b"null", b"42"])
def test_post_requires_a_json_object(body):
    response = make_client(no_database).post("/v1/original-lookup", content=body)
    assert response.status_code == 400


def test_oversized_body_is_rejected_without_database_access():
    body = json.dumps({"dois": ["a" * flora_public_api.MAX_BODY_BYTES]})
    response = make_client(no_database).post("/v1/original-lookup", content=body)
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", sorted(flora_public_api.PUBLIC_PATHS))
def test_options_are_database_free_and_advertise_public_methods(path):
    response = make_client(no_database).options(path, headers={"Origin": "https://forrt.org",
                                                             "Access-Control-Request-Method": "POST"})
    assert response.status_code == 200
    assert {"GET", "POST", "OPTIONS"} <= set(response.headers["access-control-allow-methods"].split(","))
    assert response.headers["access-control-allow-headers"] == "Content-Type"
    assert response.headers["access-control-allow-origin"] == ("https://forrt.org" if path.endswith("search") else "*")


def test_search_cors_respects_configured_origins(monkeypatch):
    monkeypatch.setenv("FLORA_SEARCH_ORIGINS", "https://one.example/, https://two.example")
    client = make_client(no_database)
    assert client.options("/v1/search", headers={"Origin": "https://one.example"}).headers["access-control-allow-origin"] == "https://one.example"
    denied = client.options("/v1/search", headers={"Origin": "https://other.example"})
    assert "access-control-allow-origin" not in denied.headers
    assert denied.headers["vary"] == "Origin"


@pytest.mark.parametrize("error_type", [RuntimeError, psycopg2.OperationalError, ValueError])
def test_internal_failures_never_expose_database_details(error_type):
    @contextmanager
    def broken_database():
        raise error_type("private database server and credentials must stay hidden")
        yield  # pragma: no cover
    response = make_client(broken_database).get("/v1/original-lookup", params={"dois": ORIGINAL})
    assert response.status_code == 500
    assert response.json() == {"error": "Internal Server Error"}
    assert response.headers["cache-control"] == "no-store"


def test_missing_prepared_snapshot_reports_unavailable(local_database):  # noqa: F811
    response = make_client().get("/v1/dois")
    assert response.status_code == 503
    assert "preparation pipeline" in response.json()["error"]


def test_hash_lookup_exposes_published_extras_without_internal_annotations(local_database):
    row = make_row("FLORA-000001", abstract_r="Published abstract", author_overlap="1",
                   author_overlap_pct="25", reviewer_email="private@example.org")
    with local_database:
        with local_database.cursor(cursor_factory=RealDictCursor) as cur:
            flora_store.materialize(cur, pd.DataFrame([row]))
    client = make_client()
    response = client.get("/v1/id-lookup", params={"hashes": row["id_md5"]})
    assert response.status_code == 200
    record = response.json()["results"][row["id_md5"]]
    assert record["abstract_r"] == "Published abstract"
    assert record["author_overlap"] == "1"
    assert "reviewer_email" not in record
    aggregate = client.get("/v1/original-lookup", params={"dois": ORIGINAL}).json()["results"][ORIGINAL]
    assert aggregate["record"]["replications"][0]["author_overlap_pct"] == "25"
    assert "private@example.org" not in json.dumps(aggregate)


def test_public_database_context_is_repeatable_read_and_rejects_writes(local_database):
    with flora_public_api.readonly_database() as cur:
        cur.execute("SHOW transaction_read_only")
        assert cur.fetchone()["transaction_read_only"] == "on"
        cur.execute("SHOW transaction_isolation")
        assert cur.fetchone()["transaction_isolation"] == "repeatable read"
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        with flora_public_api.readonly_database() as cur:
            cur.execute("UPDATE flora_data SET title_o='API requests cannot write'")


def test_exact_public_post_routes_are_exempt_but_admin_writes_remain_blocked(public_dataset):
    _, _, rows = public_dataset
    client = make_client(middleware=True)
    headers = {"Origin": "https://unrelated.example", "Sec-Fetch-Site": "cross-site"}
    for path, body in [
        ("/v1/original-lookup", {"dois": [ORIGINAL]}),
        ("/v1/prefix-lookup", {"prefixes": ["abc"]}),
        ("/v1/id-lookup", {"hashes": [rows[0]["id_md5"]]}),
        ("/v1/search", {"exclude": ["absent-token"]}),
    ]:
        assert client.post(path, json=body, headers=headers).status_code == 200
    for path in ("/api/admin/test-write", "/v1/original-lookup/extra", "/v1/original-lookup/", "/v1/dois"):
        assert client.post(path, json={}, headers=headers).status_code == 403
    assert client.delete("/v1/original-lookup", headers=headers).status_code == 403
    assert client.options("/v1/original-lookup", headers=headers).status_code == 200
