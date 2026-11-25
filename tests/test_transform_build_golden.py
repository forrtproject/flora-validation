"""Offline golden build test for transform_sources.build().

Exercises every stage of transform_sources.build() without a database or network:
  1  outcome derivation & alias recoding + 1b COS quote rewrite
  2  DOI cleaning (resolver prefix, percent-encoded SICI, trailing garbage, DUMMY_)
  2b confirmed preprint/publication resolution
  3  exclusions (pair-keyed and url_r-keyed)
  4  redundant url_r stripping
  5  exact-key dedup (distinct exemption, website precedence, blank fill, hand-fill yield)
  5b DOI-pair merge (compatible url_r vs separate reports)
  6  source projection (entry_sheet_replications / entry_sheet_reproductions)
  7  bibliographic metadata join
  6b preprint/publication dedup + original-side re-join
  6c FLoRA identifier corrections + re-join
  7e reproduction quote & quote-source rebuild from final axes
  8  text cleaning (JATS/HTML, mojibake, URL &amp; unescape) + 8b APA title + 8c author overlap
  9  meta-paper annotation & DUMMY_ stripping + to_output_shape() + final_export.csv_text()

Freezes the exact CSV text, dedup_keys, and all six out.attrs contracts so any refactor
of transform_sources.py or shared helpers proves byte-for-byte invariance.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

import final_export
import flora_edits
import preprint_dedup
import transform_sources
from tests.test_duplicate_steps import BLANK, Tables

GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "transform_build_golden.json"


def _row(display_id: str, **values) -> dict:
    return {
        **BLANK,
        "record_id": f"rec-{display_id}",
        "display_id": display_id,
        "source": "replications",
        "type": "replication",
        **values,
    }


def _fixture_inputs():
    rows = [
        # 1) Standard replication + percent-encoded SICI DOI + mojibake + COS shorthand quote
        _row(
            "REPL-000001",
            doi_o="https://doi.org/10.1002/(sici)1099-0771(199806)11:2%3c107::aid-bdm292%3e3.0.co;2-y",
            doi_r="10.1016/j.learninstruc.2018.04.010 get rights and content",
            outcome="successful",
            outcome_quote="p < .001",
            out_quote_source="abstract",
            url_r="https://doi.org/10.1016/j.learninstruc.2018.04.010",  # redundant url_r -> stripped in stage 4
        ),
        # 2) Exact duplicate of REPL-000001 from website-validated source -> website overrides outcome
        _row(
            "VAL-000002",
            source="validated",
            doi_o="10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y",
            doi_r="10.1016/j.learninstruc.2018.04.010",
            outcome="mixed",
            outcome_quote="Mixed replication findings across two samples.",
            out_quote_source="results",
        ),
        # 3) Distinct-ruled record sharing identifiers -> must NOT collapse in stage 5
        _row(
            "REPL-000003",
            doi_o="10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y",
            doi_r="10.1016/j.learninstruc.2018.04.010",
            outcome="failed",
            outcome_quote="Study 2 failed to replicate.",
            out_quote_source="abstract",
            duplicate_status="distinct",
        ),
        # 4 & 5) Pair merge (stage 5b): one lacks url_r, second adds url_r & quote; plus manual blank-fill
        _row(
            "REPL-000004",
            doi_o="10.5555/orig.merge",
            doi_r="10.5555/rep.merge",
            outcome="successful",
            outcome_quote="",
            study_o="1",
        ),
        _row(
            "REPL-000005",
            doi_o="10.5555/orig.merge",
            doi_r="10.5555/rep.merge",
            url_r="https://osf.io/report-merge/?utm_source=test&keep=1",
            outcome="successful",
            outcome_quote="The primary effect replicated cleanly.",
            out_quote_source="abstract",
            study_o="2",
            abstract_r="Abstract with &amp; entity.",
        ),
        _row(
            "MAN-000006",
            source="manual",
            doi_o="10.5555/orig.merge",
            doi_r="10.5555/rep.merge",
            url_r="https://osf.io/report-merge/?keep=1",
            outcome="failed",  # manual yields: must not create conflict or overwrite outcome
            ref_r="Manual fallback ref",
        ),
        # 6) Reproduction with two axes (stage 1 & 7e)
        _row(
            "REPR-000007",
            source="reproductions",
            type="reproduction",
            doi_o="10.5555/orig.repro",
            doi_r="10.5555/rep.repro",
            outcome_computation="computationally reproducible",
            outcome_computational_quote="All main tables reproduced exactly.",
            out_quote_computational_source="results",
            outcome_robustness="robustness challenges",
            outcome_robustness_quote="Alternative specification attenuated the effect.",
            out_quote_robust_source="discussion",
        ),
        # 7) Excluded by url_r (stage 3)
        _row(
            "REPL-000008",
            doi_o="10.5555/orig.excl",
            doi_r="10.5555/rep.excl",
            url_r="https://example.org/excluded-report",
            outcome="failed",
            outcome_quote="Excluded row.",
        ),
        # 8) Record with DUMMY_ DOI + APA reference title recovery (stage 8b) + DUMMY stripping (stage 9)
        _row(
            "REPL-000009",
            doi_o="DUMMY_ORIG_001",
            doi_r="10.5555/rep.apa",
            ref_o="Smith, J., & Doe, A. (2015). Recovered Original Title From APA. Journal of Tests, 10(2), 1-12.",
            outcome="failed",
            outcome_quote="Failed to find the reported effect.",
            out_quote_source="abstract",
        ),
        # 9) Record with FLoRA identifier correction (stage 6c) that re-joins metadata
        _row(
            "REPL-000010",
            doi_o="10.5555/orig.wrong",
            doi_r="10.5555/rep.corrected",
            outcome="successful",
            outcome_quote="Effect replicated after correction.",
            out_quote_source="abstract",
        ),
    ]

    def _meta(**vals):
        base = {
            "title": None, "author": None, "journal": None, "year": None,
            "volume": None, "issue": None, "pages": None, "apa_ref": None,
            "bibtex_ref": None, "url": None, "language": None, "oa_url": None,
            "oa_work_id": None,
        }
        return {**base, **vals}

    metadata = {
        "10.1002/(sici)1099-0771(199806)11:2<107::aid-bdm292>3.0.co;2-y": _meta(
            title="<jats:title>Decision Making Under <i>Risk</i></jats:title>",
            author='[{"family":"K\u00c3\u00b6bach","given":"A."}]',
            journal="Journal of Behavioral Decision Making",
            year=1998,
            oa_url="https://example.org/oa?article=1063&amp;amp;context=trr",
            oa_work_id="W1001",
        ),
        "10.1016/j.learninstruc.2018.04.010": _meta(
            title="Learning and Instruction Replication",
            author='[{"family":"K\u00f6bach","given":"A."},{"family":"Lee","given":"B."}]',
            journal="Learning and Instruction",
            year=2018,
            oa_work_id="W1002",
        ),
        "10.5555/orig.merge": _meta(
            title="Original Merge Study",
            author='[{"family":"Alpha","given":"A."}]',
            journal="Original J",
            year=2010,
            oa_work_id="W2001",
        ),
        "10.5555/rep.merge": _meta(
            title="Replication Merge Study",
            author='[{"family":"Beta","given":"B."}]',
            journal="Replication J",
            year=2022,
            oa_work_id="W2002",
        ),
        "10.5555/orig.repro": _meta(
            title="Original Repro Study",
            author='[{"family":"Gamma","given":"G."}]',
            journal="Repro Orig J",
            year=2019,
            oa_work_id="W3001",
        ),
        "10.5555/rep.repro": _meta(
            title="Computational Reproduction Report",
            author='[{"family":"Delta","given":"D."}]',
            journal="Repro J",
            year=2024,
            oa_work_id="W3002",
        ),
        "10.5555/rep.apa": _meta(
            title="APA Replication Report",
            author='[{"family":"Smith","given":"J."}]',
            journal="Test J",
            year=2023,
            oa_work_id="W4002",
        ),
        "10.5555/orig.wrong": _meta(
            title="Wrong Original Title",
            author='[{"family":"Wrong","given":"W."}]',
            journal="Wrong J",
            year=2001,
            oa_url="https://example.org/wrong-oa",
            oa_work_id="W5000",
        ),
        "10.5555/orig.right": _meta(
            title="Corrected Original Title",
            author='[{"family":"Right","given":"R."}]',
            journal="Right J",
            year=2016,
            volume="12",
            issue="3",
            pages="10-20",
            apa_ref="Right, R. (2016). Corrected Original Title. Right J, 12(3), 10-20.",
            bibtex_ref="@article{right2016, title={Corrected Original Title}}",
            url="https://doi.org/10.5555/orig.right",
            language="en",
            oa_url="https://example.org/right-oa",
            oa_work_id="W5001",
        ),
        "10.5555/rep.corrected": _meta(
            title="Corrected Replication Title",
            author='[{"family":"Other","given":"O."}]',
            journal="Other J",
            year=2025,
            oa_work_id="W5002",
        ),
    }

    exclusions = [
        {"doi_o": None, "doi_r": None, "url_r": "https://example.org/excluded-report"},
    ]

    publication_identifiers = {
        "primaries": {"rec-REPL-000010"},
        "records": [(["rec-REPL-000010"], {"doi_o": "10.5555/orig.right"})],
    }

    return pd.DataFrame(rows), metadata, exclusions, publication_identifiers


def _run_golden_build(monkeypatch):
    monkeypatch.setattr(preprint_dedup, "load_confirmed", lambda path=None: [])
    rows_df, metadata, exclusions, pub_ids = _fixture_inputs()
    monkeypatch.setattr(flora_edits, "load_identifiers", lambda cur: pub_ids)
    cur = Tables(exclusions=exclusions)
    built = transform_sources.build(
        cur,
        verbose=False,
        rows=rows_df,
        metadata=metadata,
    )
    # Assign deterministic synthetic IDs as registry would, then project to output shape
    built = built.copy()
    built["id"] = [f"FLORA-TEST-{i:04d}" for i in range(1, len(built) + 1)]
    built["id_md5"] = [hashlib.md5(v.encode("utf-8")).hexdigest() for v in built["id"]]
    shaped = transform_sources.to_output_shape(built)
    csv_text = final_export.csv_text(shaped)

    def _clean_json(obj):
        if isinstance(obj, pd.DataFrame):
            return _clean_json(obj.to_dict("records"))
        if isinstance(obj, dict):
            return {k: _clean_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean_json(v) for v in obj]
        if isinstance(obj, float) and pd.isna(obj):
            return None
        if obj is pd.NA:
            return None
        return obj

    attrs_snapshot = {}
    for key in (
        "merged_record_ids",
        "dedup_key",
        "preprint_dedup_log",
        "identifier_edits",
        "outcome_conflicts",
        "flora_edits",
    ):
        attrs_snapshot[key] = _clean_json(built.attrs.get(key))

    return {
        "row_count": len(shaped),
        "columns": list(shaped.columns),
        "csv_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
        "csv_text": csv_text,
        "attrs": attrs_snapshot,
    }


def test_transform_build_matches_frozen_golden_snapshot(monkeypatch):
    actual = _run_golden_build(monkeypatch)
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert actual["columns"] == expected["columns"]
    assert actual["row_count"] == expected["row_count"]
    assert actual["attrs"] == expected["attrs"]
    assert actual["csv_text"] == expected["csv_text"]
    assert actual["csv_sha256"] == expected["csv_sha256"]
