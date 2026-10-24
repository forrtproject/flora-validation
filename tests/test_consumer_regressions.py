"""Regressions a consumer of flora.csv found (the search API, the Atlas).

- Website runs built a different dataset from the nightly job: the Railway image
  lacked cache/confirmed_preprint_duplicates.csv (.dockerignore left out cache/).
- An outcome clash was published as "failed || uninformative".
- A SICI DOI published as "%3c…%3e" was not found by its real "<…>" form.
- An APA reference held a CSL JSON record.
"""
import fnmatch
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import bibliographic_helpers as references
import enrich_works
import flora_api_records
import flora_edits
import flora_service
import preprint_dedup
import validate_flora

ROOT = Path(__file__).resolve().parents[1]
SICI = "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y"
SICI_ENCODED = "10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y"


# ── the Railway image ─────────────────────────────────────────────────────────

def _docker_includes(path, patterns):
    """Docker's .dockerignore rule: the last pattern matching the path or one of
    its parent directories decides; "!" re-includes."""
    included = True
    parts = path.split("/")
    for pattern in patterns:
        negate = pattern.startswith("!")
        pattern = pattern[1:] if negate else pattern
        candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        if any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates):
            included = negate
    return included


def test_the_image_carries_the_confirmed_preprint_duplicates():
    """Without it a website run merges, keeps and canonicalises differently from
    the nightly job, and records are retired and restored by turns."""
    patterns = [line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    confirmed = preprint_dedup.CONFIRMED_PATH.relative_to(ROOT).as_posix()
    assert _docker_includes(confirmed, patterns)
    assert not _docker_includes("cache/openalex_works.json", patterns), "other caches stay out"
    assert not _docker_includes("output/flora.csv", patterns)


# ── outcome clashes ───────────────────────────────────────────────────────────

def _frame(**over):
    base = {"type": ["replication", "replication"], "doi_o": ["10.1234/a", "10.1234/b"],
            "doi_r": ["10.1234/r", "10.1234/s"], "outcome": ["failed", "successful"],
            "edited_fields": [None, None], "flora_id": ["X-1", "X-2"], "source": ["COS", "COS"],
            "outcome_conflict": ["failed | uninformative", None]}
    base.update(over)
    return pd.DataFrame(base)


def test_an_undecided_clash_stays_marked_on_its_record():
    out = flora_service._attach_outcome_conflicts(_frame())
    marked = [value if pd.notna(value) else None for value in out["outcome_conflict"]]
    assert marked == ["failed | uninformative", None]


def test_correcting_the_outcome_settles_the_clash():
    out = flora_service._attach_outcome_conflicts(_frame(edited_fields=["outcome", None]))
    assert out["outcome_conflict"].isna().all()


def test_the_conflicts_filter_and_count():
    out = flora_service._attach_outcome_conflicts(_frame())
    filtered = flora_service._apply_filters(out, {"conflict": True})
    assert filtered["flora_id"].tolist() == ["X-1"]
    assert flora_service.counts(out.assign(title_o="T", title_r="T"))["conflicts"] == 1


def _built(**over):
    """One record as flora_service holds it before corrections: ids attached."""
    row = {"type": "replication", "export_id": "FLORA-000001", "flora_id": "REPL-000001",
           "source_record_id": "row-1", "doi_o": "10.1234/a", "doi_r": "10.1234/r",
           "outcome": "failed", "outcome_quote": None, "outcome_quote_source": None,
           "outcome_computation": None, "outcome_computational_quote": None,
           "out_quote_computational_source": None, "outcome_robustness": None,
           "outcome_robustness_quote": None, "out_quote_robust_source": None,
           "outcome_conflict": "failed | uninformative"}
    row.update(over)
    return pd.DataFrame([row])


REPRODUCTION = {"type": "reproduction", "outcome": "computationally reproducible, not checked",
                "outcome_computation": "computationally reproducible",
                "outcome_robustness": "not checked",
                "outcome_conflict": "computationally reproducible, not checked | "
                                    "computationally reproducible, robust"}


@pytest.mark.parametrize("field, value", [("outcome_robustness", "robust"),
                                          ("outcome_computation", "computational issues")])
def test_correcting_a_reproductions_axis_settles_its_clash(field, value):
    """A reproduction's editor corrects the two axes, never `outcome` itself (the
    build derives it from them), so an axis correction is what settles its clash."""
    frame = _built(**REPRODUCTION)
    marked = flora_service._attach_outcome_conflicts(flora_edits.apply(None, frame, corrections={}))
    assert marked["outcome_conflict"].notna().all()

    corrected = flora_edits.apply(None, frame, corrections={"FLORA-000001": {field: value}})
    assert corrected.at[0, "edited_fields"] == field
    assert flora_service._attach_outcome_conflicts(corrected)["outcome_conflict"].isna().all()


def test_correcting_a_quote_does_not_settle_a_reproductions_clash():
    corrected = flora_edits.apply(None, _built(**REPRODUCTION), corrections={
        "FLORA-000001": {"outcome_robustness_quote": "A clearer quote."}})
    assert flora_service._attach_outcome_conflicts(corrected)["outcome_conflict"].notna().all()


@pytest.mark.parametrize("field", ["doi_o", "doi_r"])
def test_correcting_a_doi_does_not_settle_the_clash(field):
    """The mark is on the record, not keyed on its DOIs, so a DOI the tab corrects,
    or a later build step rewrites, cannot lose it."""
    corrected = flora_edits.apply(None, _built(**{field: "10.1234/corrected"}), corrections={
        "FLORA-000001": {field: "10.1234/corrected"}})
    assert flora_service._attach_outcome_conflicts(corrected)["outcome_conflict"].tolist() == [
        "failed | uninformative"]


def test_choosing_an_outcome_stores_what_derives_it():
    assert flora_edits._chosen_values("replication", "uninformative") == {"outcome": "uninformative"}
    assert flora_edits._chosen_values("reproduction", "computational issues, robust") == {
        "outcome_computation": "computational issues", "outcome_robustness": "robust"}
    assert flora_edits._chosen_values("reproduction", "cannot_be_determined") == {
        "outcome_computation": "cannot_be_determined",
        "outcome_robustness": "cannot_be_determined"}
    assert flora_edits.outcome_options({"outcome_conflict": "failed | uninformative"}) == [
        "failed", "uninformative"]


def _pair(outcomes, **columns):
    """Rows of one (type, doi_o, doi_r) pair, as merge_doi_pair_dups receives them."""
    n = len(outcomes)
    base = {"type": ["replication"] * n, "doi_o": ["10.1234/o"] * n, "doi_r": ["10.1234/r"] * n,
            "url_r": [None] * n, "source": ["COS"] * n, "outcome": list(outcomes),
            "record_id": [f"r{i}" for i in range(n)]}
    base.update(columns)
    return pd.DataFrame(base)


def test_the_merge_marks_the_record_only_when_a_person_must_decide():
    undecided, _ = preprint_dedup.merge_doi_pair_dups(_pair(["failed", "uninformative"]), verbose=False)
    settled, _ = preprint_dedup.merge_doi_pair_dups(_pair(["failed", "successful"]), verbose=False)
    assert undecided.iloc[0][preprint_dedup.CONFLICT_COLUMN] == "failed | uninformative"
    assert settled.iloc[0]["outcome"] == "mixed"
    assert pd.isna(settled.iloc[0][preprint_dedup.CONFLICT_COLUMN])


def test_a_merged_reproductions_outcome_matches_its_axes():
    """Each axis whole from the first row that judged it, the outcome derived from
    them: never the first row's "cannot_be_determined" beside another row's axis."""
    frame = _pair(
        ["cannot_be_determined", "computational issues, robust"], type=["reproduction"] * 2,
        outcome_computation=["computationally reproducible", "computational issues"],
        outcome_computational_quote=["first comp quote", "second comp quote"],
        out_quote_computational_source=["abstract", "full text"],
        outcome_robustness=[None, "robust"],
        outcome_robustness_quote=[None, "second robust quote"],
        out_quote_robust_source=[None, "full text"])
    out, _ = preprint_dedup.merge_doi_pair_dups(frame, verbose=False)
    row = out.iloc[0]
    assert (row["outcome_computation"], row["outcome_computational_quote"],
            row["out_quote_computational_source"]) == ("computationally reproducible",
                                                       "first comp quote", "abstract")
    assert (row["outcome_robustness"], row["outcome_robustness_quote"],
            row["out_quote_robust_source"]) == ("robust", "second robust quote", "full text")
    assert row["outcome"] == "computationally reproducible, robust"

# ── DOI forms ─────────────────────────────────────────────────────────────────

def test_a_percent_encoded_doi_is_the_same_doi():
    assert flora_api_records.normalize_doi(SICI_ENCODED.upper()) == SICI
    assert flora_api_records.normalize_doi("https://doi.org/" + SICI) == SICI


# ── JSON where a reference belongs ────────────────────────────────────────────

CSL = '{"volume":"45","issue":"1","author":[{"literal":"陳振宇"}],"title":"Masked Priming"}'


def test_escapes_are_decoded_before_lower_casing():
    assert flora_api_records.normalize_doi("10.1234/AB%41") == "10.1234/aba"
    assert flora_api_records.published_doi("HTTPS://DOI.ORG/10.1234/AB%41") == "10.1234/ab%41"


def test_text_nested_past_the_parser_is_not_mistaken_for_a_crash():
    assert references.is_structured_text("[" * 100_000) is False


def test_json_is_recognised_and_a_bracketed_title_is_not():
    assert references.is_structured_text(CSL)
    assert references.is_structured_text("[1, 2]")
    assert not references.is_structured_text("[Re] Replication of a study")
    assert not references.is_structured_text("{Smith} (2003). A title.")


def test_a_json_answer_to_the_apa_request_is_not_kept(monkeypatch):
    def request(url, accept="application/json"):
        if "api.crossref.org" in url:
            return {"message": {"title": ["Masked Priming"], "author": [{"literal": "陳振宇"}],
                                "container-title": ["中華心理學刊"], "issued": {"date-parts": [[2003]]}}}
        if accept.startswith("text/x-bibliography"):
            return CSL                       # what the registration agency sends
        return None

    monkeypatch.setattr(references, "request", request)
    row = references.fetch_doi_reference("10.6129/cjp.2003.4501.07")
    assert not references.is_structured_text(row["apa_ref"])
    assert row["apa_ref"].startswith("陳振宇 (2003). Masked Priming.")


def test_a_cached_json_reference_is_rebuilt_from_the_works_fields(monkeypatch):
    cached = {"doi": "10.6129/cjp.2003.4501.07", "title": "Masked Priming", "authors": "陳振宇",
              "journal": "中華心理學刊", "year": "2003", "apa_ref": CSL, "metadata_source": "doi-csl"}

    class Cur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [dict(cached)]

    monkeypatch.setattr(enrich_works.references, "load_reference_seed",
                        lambda: {"records": {}, "url_to_doi": {}})
    monkeypatch.setattr(enrich_works.references, "load_manual_references", lambda: {})
    apa = enrich_works.load_metadata(Cur())["10.6129/cjp.2003.4501.07"]["apa_ref"]
    assert apa.startswith("陳振宇 (2003). Masked Priming. 中華心理學刊")


def test_the_validation_report_flags_json_in_a_text_field():
    frame = pd.DataFrame([{"doi_o": "10.1/o", "doi_r": "10.1/r", "apa_ref_o": CSL,
                           "title_o": "[Re] A title", "title_r": "T"}])
    issues = validate_flora._check_structured_text(frame)
    assert len(issues) == 1 and "apa_ref_o holds JSON" in issues[0]


# ── against PostgreSQL: both DOI forms find the record ───────────────────────

from tests.test_preparation_database import local_database  # noqa: E402,F401


def test_both_forms_of_a_sici_doi_find_the_record(local_database):  # noqa: F811
    from tests.test_flora_public_api import make_client, make_row, save_snapshot
    save_snapshot(local_database, [make_row(
        "FLORA-000001", doi_o=SICI_ENCODED,
        doi_o_hash=hashlib.md5(SICI_ENCODED.encode()).hexdigest()[:3])])
    client = make_client()
    for form in (SICI, SICI_ENCODED):
        result = client.post("/v1/original-lookup", json={"dois": [form]}).json()["results"]
        # Answered under the DOI as the client sent it; the record reads as published.
        assert list(result) == [form]
        assert result[form]["doi"] == SICI_ENCODED, form
    for doi in (SICI, SICI_ENCODED):
        prefix = hashlib.md5(doi.encode()).hexdigest()[:3]
        bucket = client.get("/v1/prefix-lookup", params={"prefix": prefix}).json()["results"][prefix]
        assert [entry["doi"] for entry in bucket] == [SICI_ENCODED], doi
    listed = client.get("/v1/dois").json()["dois"]
    assert SICI_ENCODED in listed and SICI not in listed, "the DOI list reads as published"


def test_the_conflicts_filter_follows_the_build_and_its_corrections(local_database,  # noqa: F811
                                                                    monkeypatch):
    """End to end: a preprint and its publication merge into one record whose
    outcomes the rules cannot settle. Correcting its DOI leaves it a conflict;
    choosing the other duplicate's outcome settles it, as a logged change."""
    import flora_registry
    import transform_sources
    from psycopg2.extras import RealDictCursor
    from tests.test_preparation_database import add_source

    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    journal, preprint = "10.9999/journal.1", "10.31234/osf.io/abcd1"
    report, fixed = "10.9999/report.1", "10.9999/report.fixed"
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": journal, "doi_r": report, "outcome": "failed"}, "REPL-000001")
        add_source(cur, {"doi_o": preprint, "doi_r": report, "outcome": "uninformative"},
                   "REPL-000002")
        for doi, title in ((journal, "The original study"), (preprint, "The original study"),
                           (report, "The replication report"), (fixed, "The corrected report")):
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
        cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
        flora_registry.refresh(cur, verbose=False, frame=transform_sources.build(cur, verbose=False))
    flora_service.invalidate()

    def conflicts():
        with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
            listed = flora_service.list_records(cur, {"conflict": True})
        return ([(r["doi_o"], r["doi_r"], r["outcome"], r["outcome_conflict"])
                 for r in listed["records"]], listed["counts"]["conflicts"])

    def save(fields):
        with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
            [record] = flora_service.list_records(cur, {"search": journal})["records"]
            version = flora_service.editing(cur, flora_service.get_record(
                cur, record["flora_id"]))["version"]
            return flora_service.update_record(cur, record["flora_id"], fields, [], version,
                                               {"id": None, "handle": "luke"},
                                               {field: "checked" for field in fields})

    # The kept record (the first row) publishes its outcome; the clash is listed.
    assert conflicts() == ([(journal, report, "failed", "failed | uninformative")], 1)

    assert save({"doi_r": fixed})["changed_fields"] == ["doi_r"]
    assert conflicts() == ([(journal, fixed, "failed", "failed | uninformative")], 1)

    def choose(outcome):
        with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
            [record] = flora_service.list_records(cur, {"search": journal})["records"]
            editing = flora_service.editing(cur, flora_service.get_record(cur, record["flora_id"]))
            return record["flora_id"], flora_service.confirm_outcome(
                cur, record["flora_id"], editing["version"], {"id": None, "handle": "luke"},
                "checked the report", outcome)

    # Only an outcome the duplicates gave can be chosen; anything else is a correction.
    with pytest.raises(ValueError, match="Choose one of the outcomes its duplicates gave"):
        choose("successful")
    assert conflicts()[1] == 1, "a refused choice settles nothing"

    # The other duplicate's outcome was right: chosen, published, logged, settled.
    flora_id, result = choose("uninformative")
    assert result["changed_fields"] == ["outcome"]
    assert conflicts() == ([], 0)
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        record = flora_service.get_record(cur, flora_id)
        latest = flora_service.editing(cur, record)["history"][0]
    assert record["outcome"] == "uninformative"
    assert (latest["action"], latest["note"], latest["changes"]) == (
        "confirm", "checked the report", {"outcome": ["failed", "uninformative"]})


def test_a_dropped_duplicates_clash_is_listed_too(local_database, monkeypatch):  # noqa: F811
    """The transform's own duplicate step (5) drops the second of two rows entered
    twice, every identifier the same. Their outcomes disagreed ("failed" and "mixed"):
    the kept row's stands in and the clash is listed, where it used to be settled
    silently ("first record")."""
    import flora_registry
    import transform_sources
    from psycopg2.extras import RealDictCursor
    from tests.test_preparation_database import add_source

    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        add_source(cur, {"doi_o": "10.9999/o.1", "doi_r": "10.9999/r.1", "outcome": "failed",
                         "url_r": "https://osf.io/aaaa1"}, "REPL-000001")
        add_source(cur, {"doi_o": "10.9999/o.1", "doi_r": "10.9999/r.1", "outcome": "mixed",
                         "url_r": "https://osf.io/aaaa1/"}, "REPL-000002")
        for doi, title in (("10.9999/o.1", "The original"), ("10.9999/r.1", "The replication")):
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
        frame = transform_sources.build(cur, verbose=False)
        cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
        flora_registry.refresh(cur, verbose=False, frame=frame)
    flora_service.invalidate()
    clash = [c for c in frame.attrs["outcome_conflicts"] if c["outcomes"] == "failed | mixed"]
    assert [c["resolved_by"] for c in clash] == [preprint_dedup.NEEDS_DECISION]
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        listed = flora_service.list_records(cur, {"conflict": True})
    assert [(r["outcome"], r["outcome_conflict"]) for r in listed["records"]] == [
        ("failed", "failed | mixed")]

    # Confirming the published outcome: nothing changes but the decision is recorded.
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        [record] = listed["records"]
        editing = flora_service.editing(cur, flora_service.get_record(cur, record["flora_id"]))
        result = flora_service.confirm_outcome(cur, record["flora_id"], editing["version"],
                                               {"id": None, "handle": "luke"}, "", "failed")
        assert result["changed_fields"] == ["outcome"]
        assert flora_service.get_record(cur, record["flora_id"])["outcome"] == "failed"
        assert flora_service.list_records(cur, {"conflict": True})["records"] == []


def test_a_merged_reproductions_published_outcome_can_settle_its_clash(local_database,  # noqa: F811
                                                                      monkeypatch):
    """Each axis comes from the first row that judged it, so the outcome published
    can be neither row's. It can still be confirmed (the editor offers it beside
    theirs); a restore brings the clash back; choosing a row's outcome sets both axes."""
    import uuid

    import flora_registry
    import transform_sources
    from psycopg2.extras import RealDictCursor

    monkeypatch.setattr("bibliographic_helpers.request",
                        lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    luke = {"id": None, "handle": "luke"}
    original, report = "10.9999/o.1", "10.9999/r.1"
    with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
        for display_id, computation, robustness in (
                ("REPR-000001", "computationally reproducible", None),
                ("REPR-000002", "computational issues", "robust")):
            cur.execute(
                """INSERT INTO source_records (source, sheet_row_id, display_id, type, doi_o,
                       doi_r, outcome_computation, outcome_robustness)
                   VALUES ('reproductions', %s, %s, 'reproduction', %s, %s, %s, %s)""",
                (str(uuid.uuid4()), display_id, original, report, computation, robustness))
        for doi, title in ((original, "The original"), (report, "The reproduction")):
            cur.execute("INSERT INTO work_metadata (doi, title, authors, year) "
                        "VALUES (%s, %s, 'Ana Fox', '2020')", (doi, title))
        cur.execute("LOCK TABLE flora_records IN SHARE ROW EXCLUSIVE MODE")
        flora_registry.refresh(cur, verbose=False, frame=transform_sources.build(cur, verbose=False))
    flora_service.invalidate()

    def record():
        with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
            [listed] = flora_service.list_records(cur, {})["records"]
            found = flora_service.get_record(cur, listed["flora_id"])
            return found, flora_service.editing(cur, found)["version"]

    def change(call):
        found, version = record()
        with local_database, local_database.cursor(cursor_factory=RealDictCursor) as cur:
            return call(cur, found["flora_id"], version)

    published, _ = record()
    assert published["outcome"] == "computationally reproducible, robust"
    assert flora_edits.outcome_options(published) == [
        "cannot_be_determined", "computational issues, robust"]

    result = change(lambda cur, flora_id, version: flora_service.confirm_outcome(
        cur, flora_id, version, luke, "", published["outcome"]))
    assert result["changed_fields"] == ["outcome_computation", "outcome_robustness"]
    assert (record()[0]["outcome"], record()[0]["outcome_conflict"]) == (published["outcome"], None)

    change(lambda cur, flora_id, version: flora_service.restore_record(
        cur, flora_id, 0, version, luke))
    assert record()[0]["outcome_conflict"] == "cannot_be_determined | computational issues, robust"

    change(lambda cur, flora_id, version: flora_service.confirm_outcome(
        cur, flora_id, version, luke, "", "computational issues, robust"))
    chosen, _ = record()
    assert (chosen["outcome_computation"], chosen["outcome_robustness"], chosen["outcome"],
            chosen["outcome_conflict"]) == ("computational issues", "robust",
                                            "computational issues, robust", None)


def test_an_older_change_log_accepts_a_confirmation(local_database):  # noqa: F811
    """Databases created before 'confirm' have the old CHECK; the schema replaces it."""
    from tests.test_preparation_database import final_export
    schema = (final_export.ROOT / "db_schema.sql").read_text(encoding="utf-8")
    with local_database, local_database.cursor() as cur:
        cur.execute("ALTER TABLE flora_record_changes DROP CONSTRAINT flora_record_changes_action_check")
        cur.execute("ALTER TABLE flora_record_changes ADD CONSTRAINT flora_record_changes_action_check "
                    "CHECK (action IN ('edit', 'restore'))")
        cur.execute(schema)
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'flora_record_changes_action_check'")
        assert "confirm" in cur.fetchone()[0]
