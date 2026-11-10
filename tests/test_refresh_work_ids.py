"""Tests for enrich_works.refresh_work_ids — work ids for keys cached without one.

enrich() asks OpenAlex only about DOIs with no work_metadata row. A DOI first seen
before OpenAlex indexed it was cached as a miss, then enrich_references() stored
its Crossref reference under the same key, and nothing asked OpenAlex again: the
product shipped a title but no oa_work_id. No network: _fetch_batch is replaced.
"""
import io
import json

import enrich_works as ew


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return self.rows


def _work(doi, wid):
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}",
            "title": "T", "publication_year": 2026}


def _run(monkeypatch, cached, product, works):
    asked, stored, updated = [], [], []
    monkeypatch.setattr(ew, "reference_keys_in_product", lambda cur: product)
    monkeypatch.setattr(ew, "_fetch_batch", lambda dois: asked.extend(dois) or
                        [_work(d, works[d]) for d in dois if d in works])
    monkeypatch.setattr(ew, "_store", lambda cur, rows: stored.extend(rows))
    monkeypatch.setattr(ew.psycopg2.extras, "execute_batch",
                        lambda cur, sql, rows: updated.extend(rows))
    monkeypatch.setattr(ew, "DELAY", 0)
    stats = ew.refresh_work_ids(_Cur(cached), verbose=False)
    return stats, asked, stored, updated


def test_a_row_with_metadata_gains_only_its_work_id(monkeypatch):
    cached = [{"doi": "10.1234/new", "not_found": False}]
    stats, asked, stored, updated = _run(
        monkeypatch, cached, ["10.1234/new"], {"10.1234/new": "W7160704995"})
    assert asked == ["10.1234/new"]
    assert updated == [("W7160704995", "10.1234/new")]
    assert stored == [] and stats["filled"] == 1


def test_a_cached_miss_gains_the_whole_openalex_row(monkeypatch):
    cached = [{"doi": "10.1234/late", "not_found": True}]
    _, _, stored, updated = _run(monkeypatch, cached, ["10.1234/late"], {"10.1234/late": "W9"})
    assert updated == []
    assert stored[0]["doi"] == "10.1234/late" and stored[0]["oa_work_id"] == "W9"


def test_an_osf_node_url_is_asked_about_by_its_datacite_doi(monkeypatch):
    """The id lands on the URL key, which the transform's url join reads."""
    cached = [{"doi": "osf.io/4kbna", "not_found": False}]
    _, asked, _, updated = _run(monkeypatch, cached, ["osf.io/4kbna"],
                                {"10.17605/osf.io/4kbna": "W7128643197"})
    assert asked == ["10.17605/osf.io/4kbna"]
    assert updated == [("W7128643197", "osf.io/4kbna")]


def test_keys_outside_the_product_or_not_resolvable_are_not_asked(monkeypatch):
    cached = [{"doi": "10.1234/gone", "not_found": False},
              {"doi": "osf.io/7jy4e/files/u93pc", "not_found": False},
              {"doi": "hdl.handle.net/1/2", "not_found": False}]
    stats, asked, _, _ = _run(monkeypatch, cached,
                              ["osf.io/7jy4e/files/u93pc", "hdl.handle.net/1/2"], {})
    assert asked == [] and stats["pending"] == 0


def test_a_key_openalex_still_lacks_is_left_for_the_next_run(monkeypatch):
    cached = [{"doi": "10.1234/unknown", "not_found": False}]
    stats, asked, stored, updated = _run(monkeypatch, cached, ["10.1234/unknown"], {})
    assert asked == ["10.1234/unknown"]
    assert stored == updated == [] and stats["filled"] == 0


def test_the_update_never_overwrites_an_existing_work_id(monkeypatch):
    sql = []
    monkeypatch.setattr(ew, "reference_keys_in_product", lambda cur: ["10.1234/a"])
    monkeypatch.setattr(ew, "_fetch_batch", lambda dois: [_work("10.1234/a", "W1")])
    monkeypatch.setattr(ew.psycopg2.extras, "execute_batch",
                        lambda cur, s, rows: sql.append(s))
    monkeypatch.setattr(ew, "DELAY", 0)
    ew.refresh_work_ids(_Cur([{"doi": "10.1234/a", "not_found": False}]), verbose=False)
    assert "IS NULL" in sql[0]


def test_a_batch_page_holds_more_works_than_dois(monkeypatch):
    """One DOI can match several works; a page of 50 for 50 DOIs drops the overflow
    and the dropped DOI is cached as a miss."""
    seen = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout):
        seen.append(req.full_url)
        return _Resp(json.dumps({"results": []}).encode())

    monkeypatch.setattr(ew.urllib.request, "urlopen", fake_urlopen)
    ew._fetch_batch(["10.1234/a"])
    assert "per-page=200" in seen[0]
