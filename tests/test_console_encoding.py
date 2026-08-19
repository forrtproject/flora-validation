"""Tests for console_encoding — non-ASCII progress output must not abort a run.

The bug: several scripts print glyphs like ⚠ → ✓ ─ ✗. Python picks the locale
encoding for stdout, which on a Windows console is cp1252, and cp1252 can encode
none of them. print() then raises UnicodeEncodeError, usually after the real work
has already been committed, so a successful run reports as a crash.

These tests run the real thing in a subprocess with the encoding forced to
cp1252, because the pytest process itself may well be running under UTF-8 — in
which case an unfixed script would pass by accident.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Every script whose print()/raise statements contain characters cp1252 cannot
# encode, and which therefore has to call use_utf8_output(). Kept explicit rather
# than derived, so adding a script with non-ASCII output is a deliberate choice.
_SCRIPTS_WITH_NON_ASCII_OUTPUT = [
    "backfill_quote_source.py",
    "csv_to_db.py",
    "db_migrate.py",
    "export_validated.py",
    "sync_csv.py",
    "sync_sources.py",
    "transform_sources.py",
    "update_originals.py",
    "update_outcomes.py",
]

_GLYPHS = "⚠ → ✓ ─ ✗ ≤ ≠ ←"


def _cp1252_cannot_encode(text: str) -> set:
    bad = set()
    for char in text:
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            bad.add(char)
    return bad


def test_the_glyphs_really_are_unencodable():
    """Guards the premise. If cp1252 ever gained these, the fix would be moot and
    these tests would be quietly testing nothing."""
    assert _cp1252_cannot_encode(_GLYPHS) == set("⚠→✓─✗≤≠←")


def test_printing_glyphs_under_cp1252_fails_without_the_fix():
    """The bug itself, reproduced — so the test below is known to prove something."""
    result = subprocess.run(
        [sys.executable, "-c", f"print({_GLYPHS!r})"],
        capture_output=True, text=True,
        env={"PYTHONIOENCODING": "cp1252", "PATH": ""},
    )
    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr


def test_use_utf8_output_makes_the_same_print_succeed():
    result = subprocess.run(
        [sys.executable, "-c",
         f"from console_encoding import use_utf8_output\n"
         f"use_utf8_output()\n"
         f"print({_GLYPHS!r})"],
        capture_output=True, cwd=_ROOT,
        env={"PYTHONIOENCODING": "cp1252", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert _GLYPHS.encode("utf-8") in result.stdout


def test_stderr_is_covered_too():
    """Nightly failures surface through traceback.print_exc(), which writes to
    stderr. Fixing only stdout would leave the message explaining what went wrong
    as the thing that crashes."""
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys\n"
         f"from console_encoding import use_utf8_output\n"
         f"use_utf8_output()\n"
         f"print({_GLYPHS!r}, file=sys.stderr)"],
        capture_output=True, cwd=_ROOT,
        env={"PYTHONIOENCODING": "cp1252", "PATH": ""},
    )
    assert result.returncode == 0
    assert _GLYPHS.encode("utf-8") in result.stderr


def test_use_utf8_output_survives_a_stream_without_reconfigure():
    """Best-effort by contract: a replaced stream (pytest capture, a pipe wrapper)
    may not expose reconfigure(), and that must not raise — the point is to remove
    a crash, not add one."""
    import console_encoding

    class Dummy:
        pass

    original = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = Dummy(), Dummy()
    try:
        console_encoding.use_utf8_output()
    finally:
        sys.stdout, sys.stderr = original


@pytest.mark.parametrize("script", _SCRIPTS_WITH_NON_ASCII_OUTPUT)
def test_script_with_non_ascii_output_calls_use_utf8_output(script):
    """Each script that prints these glyphs must wire the fix up. Catches a new
    script copying the print style without the guard."""
    source = (_ROOT / script).read_text(encoding="utf-8")
    assert "use_utf8_output()" in source, (
        f"{script} prints characters cp1252 cannot encode but never calls "
        f"use_utf8_output() — see console_encoding.py"
    )


def test_no_unguarded_script_prints_unencodable_characters():
    """The general rule, enforced across the repo: if a print() or raise carries a
    character cp1252 cannot encode, that module must call use_utf8_output().
    Written as a scan so a new offender fails here rather than at 02:00 in a cron
    job."""
    offenders = []
    for path in sorted(_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "use_utf8_output()" in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            is_print = (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "print")
            if not (is_print or isinstance(node, ast.Raise)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if _cp1252_cannot_encode(sub.value):
                        offenders.append(f"{path.name}:{node.lineno}")
                        break
    assert not offenders, (
        "these emit characters cp1252 cannot encode without calling "
        f"use_utf8_output(): {', '.join(sorted(set(offenders)))}"
    )
