"""Tests for the network checks. Nothing here touches the network: every request
goes to a fake urlopen that answers as the real servers were seen to.

The classification decides what is reported as a dead link, an unregistered DOI
or a retraction, and what is only "could not check", so the tests pin each answer.
"""
import io
import json
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from psycopg2.extras import RealDictCursor

import enrich_works
import validate_flora_network as network
from tests.test_preparation_database import local_database  # noqa: F401
from validate_flora import POSSIBLE_PROBLEM, PROBLEM

OK, FAILED, UNCHECKED = network.OK, network.FAILED, network.UNCHECKED


class Response:
    def __init__(self, status=200, body=b""):
        self.status, self.body, self.read_sizes = status, body, []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body if size is None or size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(code, body=b"", msg="error"):
    return urllib.error.HTTPError("https://example.org", code, msg, {}, io.BytesIO(body))


def url_error(reason):
    return urllib.error.URLError(reason)


def serve(monkeypatch, *answers):
    """Answer the requests in order; record (method, url, headers) for each."""
    pending, calls = list(answers), []

    def urlopen(request, timeout=None):
        calls.append((request.get_method(), request.full_url, dict(request.header_items())))
        if not pending:
            pytest.fail(f"unexpected request: {request.get_method()} {request.full_url}")
        answer = pending.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(network.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)
    return calls


def _row(**over):
    row = {"flora_id": "REPL-000001", "doi_o": "10.1000/o", "doi_r": "10.1000/r", "url_o": None,
           "url_r": None, "oa_url_o": None, "oa_url_r": None, "title_o": "Original",
           "author_o": None, "year_o": None}
    row.update(over)
    return row


def _frame(*rows):
    return pd.DataFrame([_row(**r) for r in rows])


# ── HTTP answers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,outcome", [
    (200, OK), (204, OK), (301, OK), (401, OK), (403, OK), (405, OK),
    (400, FAILED), (404, FAILED), (410, FAILED),
    (429, UNCHECKED), (500, UNCHECKED), (502, UNCHECKED), (503, UNCHECKED), (418, UNCHECKED),
])
def test_each_status_has_one_classification(code, outcome):
    assert network.classify_status(code) == (outcome, f"HTTP {code}")


@pytest.mark.parametrize("error,outcome,reason", [
    (socket.gaierror(socket.EAI_NONAME, "Name or service not known"), FAILED, "DNS: name not found"),
    (socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"), UNCHECKED,
     "DNS: Temporary failure in name resolution"),
    (ConnectionRefusedError(111, "Connection refused"), FAILED, "connection refused"),
    (TimeoutError("timed out"), UNCHECKED, "timed out"),
    # Anything else is no answer: a reset says nothing about the link.
    (ConnectionResetError(104, "Connection reset by peer"), UNCHECKED,
     "ConnectionResetError: [Errno 104] Connection reset by peer"),
])
def test_each_error_has_one_classification(error, outcome, reason):
    assert network.classify_error(url_error(error)) == (outcome, reason)
    assert network.classify_error(error) == (outcome, reason)


def test_a_certificate_python_cannot_verify_is_not_a_dead_link():
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    error.verify_message = "unable to get local issuer certificate"
    outcome, reason = network.classify_error(url_error(error))
    assert outcome == UNCHECKED and reason == "TLS: unable to get local issuer certificate"
    assert network.classify_error(url_error(ssl.SSLError(1, "wrong version")))[0] == UNCHECKED


# ── N3: links ─────────────────────────────────────────────────────────────────

def test_a_reachable_link_takes_one_head(monkeypatch):
    calls = serve(monkeypatch, Response(200))
    assert network.check_link("https://osf.io/abc") == (OK, "HTTP 200")
    assert [c[0] for c in calls] == ["HEAD"]
    assert calls[0][2]["User-agent"] == network.USER_AGENT


@pytest.mark.parametrize("code", [401, 403, 405])
def test_a_link_behind_a_login_or_refusing_head_is_reachable(monkeypatch, code):
    calls = serve(monkeypatch, http_error(code))
    assert network.check_link("https://example.org/a")[0] == OK and len(calls) == 1


def test_a_head_404_that_get_serves_is_reachable(monkeypatch):
    """EconPapers answers HEAD with 404 and GET with the page."""
    get = Response(200, b"x" * 5000)
    calls = serve(monkeypatch, http_error(404), get)
    assert network.check_link("https://EconPapers.repec.org/RePEc:zbw:i4rdps:40") == (OK, "HTTP 200")
    assert [c[0] for c in calls] == ["HEAD", "GET"]
    assert get.read_sizes == [network.GET_BYTES], "a GET reads no more than it needs"


@pytest.mark.parametrize("head,get,expected", [
    (http_error(404), http_error(404), (FAILED, "HTTP 404")),
    (http_error(410), http_error(410), (FAILED, "HTTP 410")),            # a deleted Google Doc
    (http_error(400), http_error(400), (FAILED, "HTTP 400")),            # an expired signed link
    (url_error(TimeoutError("timed out")), http_error(404), (FAILED, "HTTP 404")),
    (url_error(socket.gaierror(socket.EAI_NONAME, "x")), url_error(socket.gaierror(socket.EAI_NONAME, "x")),
     (FAILED, "DNS: name not found")),                                   # collabra.org
    (url_error(ConnectionRefusedError(111, "x")), url_error(ConnectionRefusedError(111, "x")),
     (FAILED, "connection refused")),
    (http_error(503), http_error(503), (UNCHECKED, "HTTP 503")),
    (http_error(429), http_error(429), (UNCHECKED, "HTTP 429")),
    (url_error(TimeoutError("timed out")), url_error(TimeoutError("timed out")), (UNCHECKED, "timed out")),
    (http_error(404), url_error(TimeoutError("timed out")), (UNCHECKED, "timed out")),
])
def test_after_a_failed_head_the_get_decides(monkeypatch, head, get, expected):
    serve(monkeypatch, head, get)
    assert network.check_link("https://example.org/paper.pdf") == expected


def test_a_redirect_loop_could_not_be_checked(monkeypatch):
    loop = http_error(302, msg=urllib.request.HTTPRedirectHandler.inf_msg + "Found")
    serve(monkeypatch, loop, http_error(302, msg=urllib.request.HTTPRedirectHandler.inf_msg + "Found"))
    assert network.check_link("https://genome.cshlp.org/lookup/doi/x") == (UNCHECKED, "redirect loop")


def test_a_redirect_without_a_target_could_not_be_checked(monkeypatch):
    serve(monkeypatch, http_error(300), http_error(300))
    assert network.check_link("https://example.org/choices") == (UNCHECKED, "HTTP 300 without a target")


def test_a_certificate_failure_could_not_be_checked(monkeypatch):
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    serve(monkeypatch, url_error(error), url_error(error))
    assert network.check_link("https://hrcak.srce.hr/file/334421")[0] == UNCHECKED


@pytest.mark.parametrize("link", ["https://osf.io]", "http://[::1"])
def test_a_link_urlsplit_refuses_is_not_requested_and_ends_nothing(monkeypatch, link):
    serve(monkeypatch)                                       # no request at all
    outcome, reason = network.check_link(link)
    assert outcome == UNCHECKED and reason.startswith("not a link Python can request")


def test_a_doi_org_link_is_answered_by_the_dois_registration(monkeypatch):
    asked = []
    monkeypatch.setattr(network, "doi_registration",
                        lambda doi: asked.append(doi) or (FAILED, "not registered at doi.org"))
    serve(monkeypatch)                                       # no HEAD or GET at all
    assert network.check_link("https://doi.org/10.17605/OSF.IO/GKNAU") == \
        (FAILED, "the DOI is not registered at doi.org")
    assert asked == ["10.17605/OSF.IO/GKNAU"]


def test_a_link_is_percent_encoded_safely():
    assert network.request_url("https://example.org/Förster & Denzler.pdf?q=ä#part") == \
        "https://example.org/F%C3%B6rster%20&%20Denzler.pdf?q=%C3%A4"
    already = "https://www.proquest.com/docview/221001969?sourcetype=Scholarly%20Journals"
    assert network.request_url(already) == already
    assert network.request_url("https://bücher.example/x") == "https://xn--bcher-kva.example/x"


# ── N2: DOIs ──────────────────────────────────────────────────────────────────

def _handle(code):
    return json.dumps({"responseCode": code, "handle": "10.1/x"}).encode()


def test_a_registered_doi_is_asked_of_the_handle_api(monkeypatch):
    calls = serve(monkeypatch, Response(200, _handle(1)))
    assert network.doi_registration("10.1101/gr.126516.111") == (OK, "registered")
    assert calls[0][1] == network.HANDLE_API + "10.1101/gr.126516.111"


def test_an_unknown_doi_is_not_registered(monkeypatch):
    """The handle API answers an unknown DOI with a 404 that carries responseCode 100."""
    serve(monkeypatch, http_error(404, _handle(100)))
    assert network.doi_registration("10.17705/1atrr.00067") == (FAILED, "not registered at doi.org")


@pytest.mark.parametrize("answer,reason", [
    (Response(200, _handle(2)), "handle API responseCode 2"),
    (Response(200, b"<!doctype html><html>maintenance</html>"), "HTTP 200 without a handle answer"),
    (http_error(404, b"not json"), "HTTP 404 without a handle answer"),
    (http_error(500), "HTTP 500"),
    (http_error(429), "HTTP 429"),
    (url_error(TimeoutError("timed out")), "timed out"),
])
def test_any_other_handle_answer_could_not_be_checked(monkeypatch, answer, reason):
    serve(monkeypatch, answer)
    assert network.doi_registration("10.1/x") == (UNCHECKED, reason)


@pytest.mark.parametrize("doi,path", [
    ("10.1002/(sici)1097-0266(199704)18:4<303::aid-smj869>3.0.co;2-g",
     "10.1002/%28sici%291097-0266%28199704%2918%3A4%3C303%3A%3Aaid-smj869%3E3.0.co%3B2-g"),
    # The escaped spelling is the same DOI, not one with a literal "%3c" in it.
    ("10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y",
     "10.1002/%28sici%291099-0771%28199806%2911%3A2%3C107%3A%3Aaid-bdm292%3E3.0.co%3B2-y"),
    ("10.1002/(sici)1520-6297(199601/02)12:1<1::aid-agr1>3.0.co;2-#",
     "10.1002/%28sici%291520-6297%28199601/02%2912%3A1%3C1%3A%3Aaid-agr1%3E3.0.co%3B2-%23"),
])
def test_the_doi_is_percent_encoded(monkeypatch, doi, path):
    calls = serve(monkeypatch, Response(200, _handle(1)))
    network.doi_registration(doi)
    assert calls[0][1] == network.HANDLE_API + path


# ── N2/N3 over a frame ────────────────────────────────────────────────────────

def _resolve(monkeypatch, frame, kind, answers, cached=()):
    remembered = []
    monkeypatch.setattr(network, "fresh_targets", lambda cur, k: set(cached))
    monkeypatch.setattr(network, "remember", lambda cur, k, target: remembered.append((k, target)))
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)
    asked = []

    def answer(target):
        asked.append(target)
        return answers[target]
    monkeypatch.setattr(network, "doi_registration" if kind == "doi" else "check_link", answer)
    stats = {}
    issues, unchecked = network.check_resolution(None, frame, kind, verbose=False, statistics=stats)
    return issues, unchecked, asked, remembered, stats


def test_dois_are_checked_once_and_findings_name_every_row(monkeypatch):
    frame = _frame({"doi_o": "10.1000/ok", "doi_r": "10.17705/1atrr.00067"},
                   {"flora_id": "REPL-000002", "doi_o": "art00005", "doi_r": "10.17705/1atrr.00067"},
                   {"flora_id": "REPL-000003", "doi_o": "10.1000/slow", "doi_r": "10.1000/ok"})
    issues, unchecked, asked, remembered, stats = _resolve(monkeypatch, frame, "doi", {
        "10.1000/ok": (OK, "registered"),
        "10.17705/1atrr.00067": (FAILED, "not registered at doi.org"),
        "10.1000/slow": (UNCHECKED, "timed out")})
    assert asked == ["10.1000/ok", "10.17705/1atrr.00067", "10.1000/slow"], "art00005 is the structural check's"
    assert remembered == [("doi", "10.1000/ok")], "only a success is cached"
    assert [(i["check"], i["flora_ids"], i["column"], i["value"], i["detail"]) for i in issues] == [
        ("doi_not_registered", ["REPL-000001", "REPL-000002"], "doi_r", "10.17705/1atrr.00067",
         "doi_r is not registered at doi.org")]
    assert unchecked == [{"target": "10.1000/slow", "columns": ["doi_o"], "reason": "timed out",
                          "flora_ids": ["REPL-000003"]}]
    assert stats == {"targets": 3, "cached": 0, "checked": 3, "deferred": 0, "unchecked": 1}


def test_cached_targets_are_not_asked_again(monkeypatch):
    _, _, asked, _, stats = _resolve(monkeypatch, _frame({}), "doi",
                                     {"10.1000/r": (OK, "registered")}, cached={"10.1000/o"})
    assert asked == ["10.1000/r"] and stats["cached"] == 1


def test_which_links_are_checked(monkeypatch):
    frame = _frame({"url_o": "https://doi.org/10.1000/o",               # restates doi_o
                    "url_r": "https://osf.io/v6zk8 or http://rpubs.com/x",  # two links
                    "oa_url_r": "https://example.org/gone.pdf"},
                   {"flora_id": "REPL-000002", "doi_o": None, "url_o": "https://www.jstor.org/stable/1911360",
                    "oa_url_o": "http://libres.uncg.edu/ir/asu/f/Spring 2018 Thesis.pdf"},
                   {"flora_id": "REPL-000003", "url_o": "https://doi.org/10.31234/osf.io/x6ehu"})
    issues, _, asked, _, _ = _resolve(monkeypatch, frame, "url", {
        "https://example.org/gone.pdf": (FAILED, "HTTP 404"),
        "https://www.jstor.org/stable/1911360": (OK, "HTTP 403"),
        "https://doi.org/10.31234/osf.io/x6ehu": (OK, "registered")})
    assert asked == ["https://example.org/gone.pdf", "https://www.jstor.org/stable/1911360",
                     "https://doi.org/10.31234/osf.io/x6ehu"]
    assert [(i["check"], i["flora_ids"], i["column"], i["detail"]) for i in issues] == [
        ("dead_link", ["REPL-000001"], "oa_url_r",
         f"oa_url_r is dead (HTTP 404; {network.ENRICHED_NOTE})")]


def test_a_dead_source_link_is_not_called_enrichment(monkeypatch):
    issues, *_ = _resolve(monkeypatch, _frame({"url_r": "http://www.collabra.org/x"}), "url",
                          {"http://www.collabra.org/x": (FAILED, "DNS: name not found")})
    assert issues[0]["detail"] == "url_r is dead (DNS: name not found)"


# ── cache ─────────────────────────────────────────────────────────────────────

def test_cache_lifetimes_are_spread_but_fixed_per_target():
    days = [network.cache_days(f"10.1/{n}") for n in range(500)]
    assert set(days) == set(range(25, 36)), "every day of the window is used"
    assert days == [network.cache_days(f"10.1/{n}") for n in range(500)], "and the same each run"


class Rows:
    """A cursor that answers every query with these rows."""
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


def test_an_entry_is_fresh_for_its_own_lifetime():
    now = datetime.now(timezone.utc)
    target = "10.1/a"
    days = network.cache_days(target)
    rows = [{"target": target, "checked_at": now - timedelta(days=days, hours=-1)},
            {"target": "10.1/old", "checked_at": now - timedelta(days=36)},
            {"target": "10.1/new", "checked_at": now - timedelta(days=24)}]
    assert network.fresh_targets(Rows(rows), "doi") == {target, "10.1/new"}
    rows[0]["checked_at"] = now - timedelta(days=days, hours=1)
    assert network.fresh_targets(Rows(rows), "doi") == {"10.1/new"}


def test_remember_sets_the_kind_on_conflict():
    cursor = Rows([])
    network.remember(cursor, "url", "https://osf.io/a")
    assert "kind = EXCLUDED.kind" in cursor.executed[0][0]


def test_the_caches_on_postgres(local_database):  # noqa: F811
    """link_checks as production has it already gains nothing it would trip on; a
    second ensure_cache is harmless; remember() corrects a stale kind."""
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""CREATE TABLE link_checks (
            target TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('doi', 'url')),
            ok BOOLEAN NOT NULL, checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cur.execute("INSERT INTO link_checks VALUES ('https://osf.io/a', 'doi', FALSE, NOW())")
        network.ensure_cache(cur)
        network.ensure_cache(cur)
        network.remember(cur, "url", "https://osf.io/a")
        cur.execute("SELECT kind, ok FROM link_checks")
        assert cur.fetchall() == [{"kind": "url", "ok": True}]
        assert network.fresh_targets(cur, "url") == {"https://osf.io/a"}
        network.remember_lookup(cur, "a title|smith|2006", {"doi": "10.1/x", "title": "A title",
                                                              "author": "Smith", "year": "2006"})
        network.remember_lookup(cur, "another|jones|1990", None)
        network.remember_lookup(cur, "a title|smith|2006", None)        # the answer changed
        assert network.fresh_lookups(cur) == {"a title|smith|2006": None, "another|jones|1990": None}


# ── N1: retractions ───────────────────────────────────────────────────────────

HEADER = ("Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,"
          "RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,"
          "OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes")


def _notices(*rows):
    lines = [HEADER] + [f"{n},T,S,I,J,P,C,A,U,Article,{date},10.9/n{n},0,1/1/2010 0:00,{doi},0,{nature},R,No,"
                        for n, (doi, nature, date) in enumerate(rows)]
    return "\n".join(lines) + "\n"


@pytest.fixture
def few_rows(monkeypatch):
    monkeypatch.setattr(network, "RETRACTION_WATCH_MIN_DOIS", 1)


def test_retraction_watch_rows_are_read_by_doi(few_rows):
    notices = network.parse_retraction_watch(_notices(
        ("10.1257/aer.20210369", "Retraction", "7/1/2023 0:00"),
        ("https://doi.org/10.1177/0956797611432497", "Expression of concern", "11/9/2021 0:00"),
        ("unavailable", "Retraction", "1/1/2020 0:00")))
    assert notices == {"10.1257/aer.20210369": [("retraction", datetime(2023, 7, 1).date())],
                       "10.1177/0956797611432497": [("expression of concern", datetime(2021, 11, 9).date())]}


@pytest.mark.parametrize("payload,message", [
    ("Record ID,Title,OriginalPaperDOI\n1,T,10.1/x\n", "no RetractionNature, RetractionDate column"),
    ("<!doctype html><html><body>Sign in</body></html>", "no OriginalPaperDOI, RetractionNature"),
    (_notices(("10.1/x", "Retraction", "1/1/2020 0:00")), "names 1 DOI, fewer than 50,000"),
])
def test_a_download_that_is_not_the_database_is_refused(payload, message):
    with pytest.raises(network.RetractionDataError, match=message):
        network.parse_retraction_watch(payload)


def test_a_full_file_whose_doi_column_went_blank_is_refused(monkeypatch):
    """Rows enough, but no paper named: counting rows would pass every paper."""
    monkeypatch.setattr(network, "RETRACTION_WATCH_MIN_DOIS", 3)
    payload = _notices(*[("", "Retraction", "1/1/2020 0:00")] * 5 + [("10.1/x", "Retraction", "1/1/2020 0:00")])
    with pytest.raises(network.RetractionDataError, match="names 1 DOI, fewer than 3"):
        network.parse_retraction_watch(payload)


def _d(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


@pytest.mark.parametrize("notices,status", [
    ([("retraction", _d("2023-07-01"))], "retracted"),
    ([("retraction", _d("2020-01-01")), ("reinstatement", _d("2021-01-01"))], None),
    ([("reinstatement", _d("2019-01-01")), ("retraction", _d("2020-01-01"))], "retracted"),
    ([("retraction", None), ("reinstatement", None)], None),
    ([("expression of concern", _d("2021-11-09"))], "concern"),
    ([("expression of concern", _d("2021-11-09")), ("retraction", _d("2023-10-29"))], "retracted"),
    ([("correction", _d("2015-01-01"))], None),
    ([], None),
])
def test_the_notice_type_decides(notices, status):
    assert network.notice_status(notices) == status


def test_retractions_and_concerns_are_reported_at_their_levels(monkeypatch, few_rows):
    payload = _notices(("10.1/retracted", "Retraction", "7/1/2023 0:00"),
                       ("10.1/concern", "Expression of concern", "11/9/2021 0:00"),
                       ("10.1/corrected", "Correction", "1/1/2015 0:00"))
    serve(monkeypatch, Response(200, payload.encode()))
    frame = _frame({"doi_o": "10.1/retracted", "doi_r": "10.1/corrected"},
                   {"flora_id": "REPL-000002", "doi_o": "10.1/x", "doi_r": "10.1/CONCERN"})
    issues, problem = network.check_retractions(frame)
    assert problem is None
    assert [(i["check"], i["flora_ids"], i["column"], i["value"], i["detail"]) for i in issues] == [
        ("retracted", ["REPL-000001"], "doi_o", "10.1/retracted", "doi_o is retracted (Retraction Watch)"),
        ("expression_of_concern", ["REPL-000002"], "doi_r", "10.1/CONCERN",
         "doi_r has an expression of concern (Retraction Watch)")]


def test_a_failed_download_is_a_reason_not_a_finding(monkeypatch):
    serve(monkeypatch, http_error(502, msg="Bad Gateway"))
    issues, problem = network.check_retractions(_frame({}))
    assert issues == [] and problem == "HTTPError: HTTP Error 502: Bad Gateway"


def test_the_download_is_crossrefs_current_distribution():
    assert network.RETRACTION_WATCH_URL.startswith("https://gitlab.com/crossref/retraction-watch-data/")


# ── N4: originals that probably have a DOI ────────────────────────────────────

QUERY = ("Is the 1918 Influenza Pandemic Over? Long-Term Effects of In Utero Influenza Exposure",
         "almond", "2006")


def _candidate(**over):
    candidate = {"doi": "10.1086/507154", "title": QUERY[0], "type": "journal-article",
                 "year": "2006", "author": "Almond"}
    candidate.update(over)
    return candidate


@pytest.mark.parametrize("over,matches", [
    ({}, True),
    ({"title": QUERY[0] + " in the post-1940 U.S. population and beyond, revisited"}, False),
    ({"year": "2007"}, True),
    ({"year": "2008"}, False),
    ({"year": ""}, True),
    ({"author": "Almond-Smith"}, True),
    ({"author": "Smith"}, False),
    ({"author": ""}, False),          # a review of the book has no author
    ({"type": "dataset"}, False),
    ({"type": "posted-content"}, False),
    ({"doi": "10.31234/osf.io/abcde"}, False),
    ({"doi": "10.1037/e636952013-245"}, False),
    ({"doi": "10.5555/2627435.2697057"}, False),
])
def test_only_a_strong_match_is_suggested(over, matches):
    assert (network.best_match(QUERY, [_candidate(**over)]) is not None) == matches


def test_without_a_named_author_the_title_and_year_decide():
    assert network.best_match((QUERY[0], "", "2006"), [_candidate(author="")])


def test_the_closest_title_wins():
    close = _candidate(doi="10.1/close", title=QUERY[0][:-3])
    exact = _candidate(doi="10.1/exact")
    assert network.best_match(QUERY, [close, exact])["doi"] == "10.1/exact"


def test_the_lookup_query_comes_from_the_row():
    row = pd.Series(_row(doi_o=None, title_o="Is the <i>1918</i>  Pandemic Over?",
                         author_o='[{"given":"D.","family":"Almond","sequence":"first"}]',
                         year_o="2006.0"))
    assert network.lookup_query(row) == ("Is the 1918 Pandemic Over?", "almond", "2006")
    assert network.lookup_query(pd.Series(_row())) is None, "a row with a doi_o is not looked up"


def test_crossref_is_asked_politely_and_its_answer_matched(monkeypatch):
    body = {"message": {"items": [
        {"DOI": "10.3410/f.1084804.537724", "type": "dataset", "title": [QUERY[0]],
         "issued": {"date-parts": [[2007]]}, "author": [{"family": "Jones"}]},
        {"DOI": "10.1086/507154", "type": "journal-article", "title": [QUERY[0]],
         "issued": {"date-parts": [[2006, 10]]}, "author": [{"given": "Douglas", "family": "Almond"}]}]}}
    calls = serve(monkeypatch, Response(200, json.dumps(body).encode()))
    outcome, match, _ = network.lookup_original_doi(QUERY)
    assert outcome == OK and match["doi"] == "10.1086/507154" and match["year"] == "2006"
    method, url, headers = calls[0]
    assert url.startswith(network.CROSSREF_WORKS + "?query.bibliographic=")
    assert url.endswith("&mailto=" + urllib.parse.quote(enrich_works.MAILTO))
    assert headers["User-agent"] == network.USER_AGENT


def test_a_failed_lookup_could_not_be_checked(monkeypatch):
    serve(monkeypatch, http_error(503))
    assert network.lookup_original_doi(QUERY) == (UNCHECKED, None, "HTTP 503")


@pytest.mark.parametrize("body", [
    {"message": "x"}, {"message": {"items": None}}, [], {"message": {"items": [None]}},
    {"message": {"items": [{"DOI": "10.1086/507154", "title": [QUERY[0]], "author": [None]}]}},
    {"message": {"items": [{"DOI": "10.1086/507154", "title": [QUERY[0]],
                            "issued": {"date-parts": [["x"]]}}]}},
])
def test_an_answer_without_works_is_no_answer(monkeypatch, body):
    """Not a confirmed miss, which would be cached for a month."""
    serve(monkeypatch, Response(200, json.dumps(body).encode()))
    outcome, match, _ = network.lookup_original_doi(QUERY)
    assert outcome == UNCHECKED and match is None


def test_answers_are_cached_found_or_not_and_failures_are_not(monkeypatch):
    frame = _frame({"doi_o": None, "title_o": "Cached found", "year_o": "2006"},
                   {"flora_id": "REPL-000002", "doi_o": None, "title_o": "Cached missing"},
                   {"flora_id": "REPL-000003", "doi_o": None, "title_o": "New found"},
                   {"flora_id": "REPL-000004", "doi_o": None, "title_o": "New missing"},
                   {"flora_id": "REPL-000005", "doi_o": None, "title_o": "Fails"},
                   {"flora_id": "REPL-000006", "title_o": "Has a DOI"})
    found = {"doi": "10.1/found", "title": "Found", "author": "Smith", "year": "2006"}
    monkeypatch.setattr(network, "fresh_lookups", lambda cur: {
        "cached found||2006": found, "cached missing||": None})
    remembered = []
    monkeypatch.setattr(network, "remember_lookup", lambda cur, key, match: remembered.append((key, match)))
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)
    answers = {"New found": (OK, {**found, "doi": "10.1/new"}, "answered"),
               "New missing": (OK, None, "answered"), "Fails": (UNCHECKED, None, "HTTP 503")}
    asked = []
    monkeypatch.setattr(network, "lookup_original_doi",
                        lambda query: asked.append(query[0]) or answers[query[0]])
    stats = {}
    issues, unchecked = network.check_original_dois(None, frame, verbose=False, statistics=stats)
    assert asked == ["New found", "New missing", "Fails"]
    assert remembered == [("new found||", {**found, "doi": "10.1/new"}), ("new missing||", None)]
    assert [(i["check"], i["flora_ids"], i["column"], i["value"]) for i in issues] == [
        ("original_doi_found", ["REPL-000001"], "doi_o", "10.1/found"),
        ("original_doi_found", ["REPL-000003"], "doi_o", "10.1/new")]
    assert issues[0]["detail"] == ("doi_o is blank; Crossref lists this paper with a DOI: "
                                   "'Found' (2006, Smith)")
    assert unchecked == [{"target": "Fails", "columns": ["title_o"], "reason": "HTTP 503",
                          "flora_ids": ["REPL-000005"]}]
    assert stats == {"targets": 5, "cached": 2, "checked": 3, "deferred": 0, "unchecked": 1}


# ── the run and its report ────────────────────────────────────────────────────

def _run(monkeypatch, retractions=(), problem=None, dois=None, links=None, originals=None, frame=None):
    """validate() with each check's answers given: lists of findings / unchecked items."""
    monkeypatch.setattr(network, "check_retractions", lambda df: (list(retractions), problem))

    def resolution(cur, df, kind, limit=0, verbose=True, statistics=None, deadline=None):
        issues, unchecked = (dois if kind == "doi" else links) or ([], [])
        statistics.update(targets=10, cached=0, checked=10, deferred=0, unchecked=len(unchecked))
        return list(issues), list(unchecked)

    def original(cur, df, limit=0, verbose=True, statistics=None, deadline=None):
        issues, unchecked = originals or ([], [])
        statistics.update(targets=1, cached=0, checked=1, deferred=0, unchecked=len(unchecked))
        return list(issues), list(unchecked)
    monkeypatch.setattr(network, "check_resolution", resolution)
    monkeypatch.setattr(network, "check_original_dois", original)
    return network.validate(None, _frame({}) if frame is None else frame)


def _item(check, value="10.1/x", flora_id="REPL-000001"):
    return network.finding(check, [pd.Series(_row(flora_id=flora_id))], "doi_o", value, f"{check} detail")


def _unchecked_item(target="https://hrcak.srce.hr/file/334421"):
    return {"target": target, "columns": ["oa_url_o"], "reason": "TLS: unable to get local issuer "
            "certificate", "flora_ids": ["REPL-000001"]}


def test_a_clean_complete_run_passes(monkeypatch):
    report = _run(monkeypatch)
    assert report["status"] == "passed" and report["issue_count"] == 0
    assert [r["code"] for r in report["results"]] == [c[0] for c in network.CHECKS]


def test_findings_carry_their_levels(monkeypatch):
    report = _run(monkeypatch, retractions=[_item("retracted"), _item("expression_of_concern")],
                  dois=([_item("doi_not_registered")], []), links=([_item("dead_link")], []),
                  originals=([_item("original_doi_found")], []))
    assert (report["problems"], report["possible_problems"]) == (3, 2)
    levels = {r["code"]: {i["level"] for i in r["items"]} for r in report["results"]}
    assert levels == {"retracted": {PROBLEM}, "doi_not_registered": {PROBLEM}, "dead_link": {PROBLEM},
                      "expression_of_concern": {POSSIBLE_PROBLEM},
                      "original_doi_found": {POSSIBLE_PROBLEM}}
    assert report["status"] == "needs_attention"


def test_possible_problems_alone_need_review(monkeypatch):
    report = _run(monkeypatch, originals=([_item("original_doi_found")], []))
    assert report["status"] == "needs_attention" and report["problems"] == 0


def test_could_not_check_is_listed_and_never_counted(monkeypatch):
    report = _run(monkeypatch, links=([], [_unchecked_item()]))
    assert report["status"] == "passed" and report["issue_count"] == 0
    text = network.render_validation(report)
    assert "<details><summary><b>Could not check</b> (1 link)</summary>" in text
    assert ("- REPL-000001, oa_url_o: TLS: unable to get local issuer certificate — "
            "`https://hrcak.srce.hr/file/334421`") in text
    assert "(1 could not be checked)" in text


def test_a_could_not_check_line_cannot_become_markdown():
    """The reason can be a server's own words, the target any link."""
    line = network._unchecked_line({"target": "https://example.org/a`b", "columns": ["url_r"],
                                    "reason": "BadStatusLine: <html> *x*", "flora_ids": ["REPL-000001"]})
    assert line == r"REPL-000001, url_r: BadStatusLine: \<html\> \*x\* — `` https://example.org/a`b ``"


def test_a_check_where_most_targets_went_unanswered_is_incomplete(monkeypatch):
    report = _run(monkeypatch, dois=([], [_unchecked_item(f"10.1/{n}") for n in range(6)]))
    assert report["status"] == "incomplete"
    assert report["notices"] == ["Most DOIs could not be checked (6 of 10)"]


def test_a_suppressed_network_finding_disappears(monkeypatch):
    item = _item("dead_link")
    monkeypatch.setattr(network, "check_retractions", lambda df: ([], None))
    monkeypatch.setattr(network, "check_resolution",
                        lambda cur, df, kind, limit=0, verbose=True, statistics=None, deadline=None: (
                            statistics.update(targets=1, cached=0, checked=1, deferred=0, unchecked=0)
                            or ([item] if kind == "url" else [], [])))
    monkeypatch.setattr(network, "check_original_dois",
                        lambda cur, df, limit=0, verbose=True, statistics=None, deadline=None: (
                            statistics.update(targets=0, cached=0, checked=0, deferred=0, unchecked=0)
                            or ([], [])))
    report = network.validate(None, _frame({}), suppressions={("dead_link", item["key"])})
    assert report["suppressed"] == 1 and report["issue_count"] == 0


def test_the_report_names_rows_and_lists_problems_first(monkeypatch):
    report = _run(monkeypatch, retractions=[_item("expression_of_concern")],
                  dois=([_item("doi_not_registered", "10.17705/1atrr.00067")], []))
    text = network.render_validation(report)
    assert network.ITEM_FORMAT in text
    assert text.index("### Problems (1)") < text.index("### Possible problems (1)")
    assert "- REPL-000001 (`10.1000/o | 10.1000/r`): doi_not_registered detail — `10.17705/1atrr.00067`" in text
    assert "**Dead links:** none" in text


def test_the_csv_has_one_row_per_finding(monkeypatch):
    report = _run(monkeypatch, dois=([_item("doi_not_registered")], [_unchecked_item()]))
    assert network.finding_rows(report) == [
        {"check": "doi_not_registered", "level": PROBLEM, "flora_ids": "REPL-000001",
         "column": "doi_o", "value": "10.1/x", "detail": "doi_not_registered detail",
         "key": "doi_not_registered|REPL-000001|doi_o|10.1/x"}]


def test_a_report_stored_before_findings_were_records_still_renders():
    """publish_flora writes a stored run's report out, which may predate this shape."""
    stored = {"status": "incomplete", "checks": "all", "rows": 3097, "suppressed": 0,
              "issue_count": 1,
              "sections": {"Retracted papers": ["SKIPPED: could not download (HTTP Error 502)"],
                           "DOIs that failed to resolve": []},
              "coverage": {"retractions": {"status": "skipped"},
                           "dois": {"targets": 5, "cached": 4, "checked": 1, "deferred": 0}}}
    text = network.render_validation(stored)
    assert "- SKIPPED: could not download (HTTP Error 502)" in text
    assert "**DOIs that failed to resolve:** none" in text and "- DOIs: 5 distinct" in text
    assert network.finding_rows(stored) == [{"check": "Retracted papers", "level": PROBLEM,
                                             "detail": "SKIPPED: could not download (HTTP Error 502)"}]


@pytest.mark.parametrize("checks", ["retractions", "dois", "urls", "originals"])
def test_a_partial_run_is_incomplete_and_says_so(monkeypatch, checks):
    monkeypatch.setattr(network, "check_retractions", lambda df: ([], None))
    for name in ("check_resolution", "check_original_dois"):
        monkeypatch.setattr(network, name, lambda *a, statistics=None, **kw: (
            statistics.update(targets=0, cached=0, checked=0, deferred=0, unchecked=0) or ([], [])))
    report = network.validate(None, _frame({}), checks=checks)
    assert report["status"] == "incomplete"
    assert f"Only these checks ran: {checks}." in network.render_validation(report)


# ── the time budget ───────────────────────────────────────────────────────────

def test_a_deadline_defers_the_targets_it_leaves(monkeypatch):
    """Each answer takes a minute of a fake clock; the deadline falls after two."""
    clock = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: clock[0])
    remembered = []

    def answer(target):
        clock[0] += 60
        return OK, "registered"
    monkeypatch.setattr(network, "fresh_targets", lambda cur, kind: set())
    monkeypatch.setattr(network, "remember", lambda cur, kind, target: remembered.append(target))
    monkeypatch.setattr(network, "doi_registration", answer)
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)
    frame = _frame({"doi_o": "10.1000/a", "doi_r": "10.1000/b"},
                   {"flora_id": "REPL-000002", "doi_o": "10.1000/c", "doi_r": "10.1000/d"})
    stats = {}
    network.check_resolution(None, frame, "doi", verbose=False, statistics=stats, deadline=120)
    assert remembered == ["10.1000/a", "10.1000/b"], "the asked ones are cached: the next run goes on"
    assert stats == {"targets": 4, "cached": 0, "checked": 2, "deferred": 2, "unchecked": 0}


def test_a_passed_deadline_defers_every_lookup(monkeypatch):
    monkeypatch.setattr(network, "fresh_lookups", lambda cur: {})
    monkeypatch.setattr(network, "lookup_original_doi", lambda query: pytest.fail("asked after the deadline"))
    stats = {}
    network.check_original_dois(None, _frame({"doi_o": None}), verbose=False, statistics=stats,
                                deadline=network.time.monotonic() - 1)
    assert (stats["checked"], stats["deferred"]) == (0, 1)


def test_a_run_out_of_time_is_incomplete_and_says_why(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(network.time, "monotonic", lambda: clock[0])

    def slow_download(df):
        clock[0] += 16 * 60
        return [], None
    monkeypatch.setattr(network, "check_retractions", slow_download)
    monkeypatch.setattr(network, "fresh_targets", lambda cur, kind: set())
    monkeypatch.setattr(network, "fresh_lookups", lambda cur: {})
    for name in ("doi_registration", "check_link", "lookup_original_doi"):
        monkeypatch.setattr(network, name, lambda *a: pytest.fail("asked after the budget ran out"))
    # Two DOIs, no link, one original without a DOI.
    report = network.validate(None, _frame({"doi_o": None}, {"flora_id": "REPL-000002"}), minutes=15)
    assert report["status"] == "incomplete" and report["issue_count"] == 0
    assert report["notices"] == ["The 15-minute network budget ran out: 3 targets deferred to the next run"]
    text = network.render_validation(report)
    assert "**The 15-minute network budget ran out: 3 targets deferred to the next run.**" in text
    assert "- DOIs: 2 distinct; 0 cached, 0 checked, 2 deferred." in text


def test_without_a_budget_nothing_is_deferred(monkeypatch):
    monkeypatch.setattr(network, "check_retractions", lambda df: ([], None))
    monkeypatch.setattr(network, "fresh_targets", lambda cur, kind: set())
    monkeypatch.setattr(network, "remember", lambda *a: None)
    monkeypatch.setattr(network, "fresh_lookups", lambda cur: {})
    monkeypatch.setattr(network, "remember_lookup", lambda *a: None)
    monkeypatch.setattr(network, "doi_registration", lambda doi: (OK, "registered"))
    monkeypatch.setattr(network, "lookup_original_doi", lambda query: (OK, None, "answered"))
    monkeypatch.setattr(network.time, "sleep", lambda delay: None)
    report = network.validate(None, _frame({"doi_o": None}, {"flora_id": "REPL-000002"}))
    assert report["status"] == "passed" and report["notices"] == []
    assert all(c.get("deferred", 0) == 0 for c in report["coverage"].values())


def test_a_negative_budget_is_refused():
    with pytest.raises(ValueError, match="network minutes"):
        network.validate(None, _frame({}), minutes=-1)
