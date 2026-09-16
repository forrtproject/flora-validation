"""validate_flora_network.py — the FLoRA checks that need network access.

A Python port of R/validate_flora_network.R:

    N1  Retracted papers, against the Retraction Watch database (both sides)
    N2  DOIs resolve (HEAD to doi.org)
    N3  URLs resolve (HEAD)

Results are cached in `link_checks` for 30 days. The R version caches to an RDS file
in one working directory; a table means CI, the web app and every pod share one
cache instead of each rebuilding its own — which matters here, because a cold run is
thousands of HEAD requests.

ONLY SUCCESSES ARE CACHED, as in the R original. A failure is often transient — a
publisher rate-limiting, a host briefly down — and caching it would turn one bad
afternoon into a month of false reports.

Usage:
    python validate_flora_network.py --checks retractions
    python validate_flora_network.py --output report.md
    python validate_flora_network.py --limit 200      # sample, for a first look

Required environment variables:
    DATABASE_URL — PostgreSQL connection string
"""
import argparse
import csv
import io
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pandas as pd
from dotenv import load_dotenv

from console_encoding import use_utf8_output
from pipeline_logging import start as start_logging
from validate_flora import (DETAILS_THRESHOLD, _blank, _text, item_id,
                            load_dataset, load_suppressions, row_id)

load_dotenv()
use_utf8_output()

RETRACTION_WATCH_URL = "https://api.labs.crossref.org/data/retractionwatch"
CACHE_DAYS = 30
REQUEST_TIMEOUT = 15
# ~5 requests/second, matching the R version's RATE_LIMIT_DELAY.
DELAY = 0.2
USER_AGENT = "flora-validation (network validation)"


def _head_ok(url: str) -> bool:
    """True when the target resolves. Any error is a failure, never an exception:
    one unreachable host must not end a run that has thousands left to check."""
    try:
        request = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.status < 400
    except urllib.error.HTTPError as exc:
        # Some publishers refuse HEAD but serve GET. 405 is "method not allowed",
        # which says the resource exists.
        return exc.code in (403, 405)
    except Exception:
        return False


# ── cache ─────────────────────────────────────────────────────────────────────

def ensure_cache(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS link_checks (
            target     TEXT PRIMARY KEY,
            kind       TEXT NOT NULL CHECK (kind IN ('doi', 'url')),
            ok         BOOLEAN NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def fresh_targets(cur, kind: str) -> set:
    cur.execute(
        "SELECT target FROM link_checks "
        "WHERE kind = %s AND ok AND checked_at > NOW() - make_interval(days => %s)",
        (kind, CACHE_DAYS),
    )
    return {r["target"] for r in cur.fetchall()}


def remember(cur, kind: str, target: str) -> None:
    cur.execute(
        """
        INSERT INTO link_checks (target, kind, ok, checked_at)
        VALUES (%s, %s, TRUE, NOW())
        ON CONFLICT (target) DO UPDATE SET ok = TRUE, checked_at = NOW()
        """,
        (target, kind),
    )


# ── N1: retractions ───────────────────────────────────────────────────────────

def retraction_watch_dois() -> set:
    request = urllib.request.Request(RETRACTION_WATCH_URL,
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(payload))
    out = set()
    for row in reader:
        doi = (row.get("OriginalPaperDOI") or "").strip().lower()
        if doi:
            out.add(doi)
    return out


def check_retractions(df) -> list:
    try:
        retracted = retraction_watch_dois()
    except Exception as exc:                                   # noqa: BLE001
        return [f"SKIPPED: could not download the Retraction Watch database ({exc})"]

    issues = []
    for _, row in df.iterrows():
        for column in ("doi_o", "doi_r"):
            doi = _text(row.get(column)).lower()
            if doi and doi in retracted:
                issues.append(f"`{doi}`: retracted ({column}) — {row_id(row)}")
    return issues


# ── N2/N3: resolution ─────────────────────────────────────────────────────────

def _collect(df, columns) -> list:
    seen, out = set(), []
    for _, row in df.iterrows():
        for column in columns:
            value = _text(row.get(column))
            if value and value not in seen:
                seen.add(value)
                out.append(value)
    return out


def check_resolution(cur, df, kind: str, limit: int = 0, verbose: bool = True,
                     statistics: dict = None) -> list:
    if limit < 0:
        raise ValueError("network limit must be zero (all) or positive")
    columns = ("doi_o", "doi_r") if kind == "doi" else ("url_r", "oa_url_o", "oa_url_r")
    targets = _collect(df, columns)
    if kind == "url":
        # url_o is deliberately excluded: it is known to hold titles rather than
        # links on a large number of rows, which the structural validator already
        # reports. Checking them here would be thousands of guaranteed failures
        # drowning the real ones.
        targets = [t for t in targets if t.lower().startswith("http")]

    cached = fresh_targets(cur, kind)
    outstanding = [t for t in targets if t not in cached]
    # Counted before the limit is applied: with --limit, the rest are deferred, not
    # cached, and reporting them as cached would say the run was complete.
    n_cached = len(targets) - len(outstanding)
    todo = outstanding[:limit] if limit else outstanding
    if statistics is not None:
        statistics.update(targets=len(targets), cached=n_cached, checked=len(todo),
                          deferred=len(outstanding) - len(todo))

    if verbose:
        deferred = len(outstanding) - len(todo)
        note = f", {deferred} deferred by --limit" if deferred else ""
        print(f"  {kind}s: {len(targets)} distinct, {n_cached} cached, "
              f"{len(todo)} to check{note}", file=sys.stderr)

    failed = []
    for n, target in enumerate(todo, 1):
        url = f"https://doi.org/{target}" if kind == "doi" else target
        if _head_ok(url):
            remember(cur, kind, target)
        else:
            failed.append(f"`{target}`")
        if verbose and n % 100 == 0:
            print(f"    {n}/{len(todo)}", file=sys.stderr)
        time.sleep(DELAY)
    return failed


def validate(cur, df, checks="all", limit=0, suppressions=None) -> dict:
    """Check one exported frame and preserve coverage as well as findings.

    A limited run and an unavailable Retraction Watch download are incomplete,
    even when none of the links checked failed. They must never look like a pass.
    """
    if checks not in {"all", "retractions", "dois", "urls", "none"}:
        raise ValueError(f"Unknown network checks: {checks}")
    if limit < 0:
        raise ValueError("network limit must be zero (all) or positive")
    suppressions = suppressions or set()
    sections, coverage = {}, {}
    suppressed = 0
    incomplete = checks != "all"

    def keep(heading, items):
        nonlocal suppressed
        retained = []
        for label in items:
            if (heading, item_id(label)) in suppressions:
                suppressed += 1
            else:
                retained.append(label)
        return retained

    if checks in ("all", "retractions"):
        items = check_retractions(df)
        skipped = any(item.startswith("SKIPPED:") for item in items)
        incomplete = incomplete or skipped
        coverage["retractions"] = {"status": "skipped" if skipped else "checked"}
        # An unavailable source is not a suppressible data-quality finding.
        sections["Retracted papers"] = items if skipped else keep("Retracted papers", items)
    for kind, selector, heading in (
        ("doi", "dois", "DOIs that failed to resolve"),
        ("url", "urls", "URLs that failed to resolve"),
    ):
        if checks not in ("all", selector):
            continue
        stats = {}
        sections[heading] = keep(heading, check_resolution(
            cur, df, kind, limit, statistics=stats))
        coverage[selector] = stats
        incomplete = incomplete or bool(stats["deferred"])
    issues = sum(len(items) for items in sections.values())
    return {"status": "skipped" if checks == "none" else
            "incomplete" if incomplete else "needs_attention" if issues else "passed",
            "checks": checks, "sections": sections, "coverage": coverage,
            "suppressed": suppressed, "issue_count": issues, "rows": len(df)}


def render_validation(report: dict) -> str:
    text = render(report["sections"], report["rows"], report["suppressed"])
    text += f"\n\n**Network validation status: {report['status']}**\n"
    for kind, coverage in report["coverage"].items():
        if "targets" in coverage:
            text += (f"\n- {kind}: {coverage['targets']} targets; {coverage['cached']} cached; "
                     f"{coverage['checked']} checked; {coverage['deferred']} deferred.")
    if report["checks"] != "all":
        text += f"\n\nOnly the selected checks ran: {report['checks']}."
    return text + "\n"


# ── report ────────────────────────────────────────────────────────────────────

def render(sections: dict, rows: int, suppressed: int = 0) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = ["## FLoRA Network Validation Report", "",
           f"_Generated: {stamp} UTC | Dataset: {rows} rows_", ""]

    for heading, items in sections.items():
        if not items:
            out.append(f"- [x] {heading}: nothing found")
            continue
        n = len(items)
        lines = [f"- [ ] {item}" for item in items]
        if n <= DETAILS_THRESHOLD:
            out += [f"**{heading}** ({n} issue{'s' if n != 1 else ''}):", ""] + lines + [""]
        else:
            out += [f"<details><summary><b>{heading}</b> ({n} issues)</summary>", ""] \
                   + lines + ["", "</details>", ""]

    if suppressed:
        out += ["", f"_({suppressed} suppressed false positive"
                    f"{'s' if suppressed != 1 else ''} not shown)_"]
    return "\n".join(out)


def main() -> int:
    start_logging("validate-network")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checks", default="all",
                        choices=["all", "retractions", "dois", "urls"],
                        help="which checks to run (default: all)")
    parser.add_argument("--limit", type=int, default=0,
                        help="check at most this many new targets per kind")
    parser.add_argument("--output", type=Path, help="write the report here")
    parser.add_argument("--input", type=Path,
                        help="validate this exact CSV; database is used only for link cache")
    parser.add_argument("--fail-on-issues", action="store_true")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        ensure_cache(cur)
        df = (pd.read_csv(args.input, dtype=str, keep_default_na=False,
                          na_values=["NA", ""], encoding="utf-8-sig")
              if args.input else load_dataset(cur))
        suppressions = load_suppressions()
        report = validate(cur, df, args.checks, args.limit, suppressions)
        text = render_validation(report)
    finally:
        conn.close()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"report written to {args.output}")
    else:
        print(text)

    has_issues = report["status"] != "passed"
    return 1 if (args.fail_on_issues and has_issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
