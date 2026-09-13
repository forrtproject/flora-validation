"""Tests for pipeline_logging — the shared logs/flora_pipeline.log.

Two things this must never do: hijack the web app's stdout (transform_sources is
imported by flora_service, so a module-level start() would redirect the whole web
process), and fail a run because it could not write a log file.

The fixture restores sys.stdout/sys.stderr around every test — a leaked tee would
redirect the rest of the suite's output into a temp file.
"""
import io
import sys

import pytest

import pipeline_logging as pl


@pytest.fixture(autouse=True)
def _restore_streams(monkeypatch, tmp_path):
    saved_out, saved_err, saved_started = sys.stdout, sys.stderr, pl._started
    monkeypatch.setattr(pl, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(pl, "LOG_PATH", tmp_path / "logs" / "flora_pipeline.log")
    pl._started = False
    yield
    sys.stdout, sys.stderr = saved_out, saved_err
    pl._started = saved_started


# ── the tee itself ────────────────────────────────────────────────────────────

def _tee():
    console, log = io.StringIO(), io.StringIO()
    return console, log, pl._Tee(console, log, "test")


def test_output_still_reaches_the_console():
    """The console is read by humans watching a sync and by the runner that
    captures it; teeing must not change what they see."""
    console, _, tee = _tee()
    tee.write("hello\n")
    assert console.getvalue() == "hello\n"


def test_the_log_copy_is_timestamped_and_labelled():
    _, log, tee = _tee()
    tee.write("hello\n")
    written = log.getvalue()
    assert "[test] hello" in written
    assert written.startswith("[20")


def test_a_prefix_is_not_inserted_mid_line():
    """Progress output arrives in fragments ('  50/4901' then a newline); a prefix
    per fragment would put a timestamp in the middle of a line."""
    _, log, tee = _tee()
    tee.write("  50")
    tee.write("/4901\n")
    assert "  50/4901\n" in log.getvalue()
    assert log.getvalue().count("[test]") == 1


def test_each_new_line_gets_its_own_prefix():
    _, log, tee = _tee()
    tee.write("one\ntwo\n")
    assert log.getvalue().count("[test]") == 2


def test_blank_lines_are_kept_but_not_prefixed():
    _, log, tee = _tee()
    tee.write("\n")
    assert log.getvalue() == "\n"


def test_a_broken_log_handle_never_breaks_the_console():
    """Losing the log is acceptable; losing the run is not."""
    class Exploding:
        def write(self, _):
            raise OSError("disk full")

        def flush(self):
            raise OSError("disk full")

    console = io.StringIO()
    tee = pl._Tee(console, Exploding(), "test")
    tee.write("still printed\n")
    tee.flush()
    assert console.getvalue() == "still printed\n"


def test_unknown_attributes_come_from_the_real_stream():
    """isatty/encoding/fileno are asked for by libraries; they belong to the
    stream being wrapped, not the tee."""
    console, _, tee = _tee()
    assert tee.getvalue() == console.getvalue()


# ── start() ───────────────────────────────────────────────────────────────────

def test_start_creates_the_log_and_redirects():
    path = pl.start("unit")
    print("a line")
    assert path.exists()
    assert "[unit] a line" in path.read_text(encoding="utf-8")


def test_start_writes_a_header_with_the_component():
    path = pl.start("unit")
    assert "[unit] START" in path.read_text(encoding="utf-8")


def test_start_is_idempotent():
    """Two scripts importing each other must not stack tees."""
    pl.start("unit")
    first = sys.stdout
    pl.start("unit")
    assert sys.stdout is first


def test_start_appends_rather_than_truncating():
    pl.LOG_DIR.mkdir(parents=True, exist_ok=True)
    pl.LOG_PATH.write_text("earlier run\n", encoding="utf-8")
    pl.start("unit")
    assert "earlier run" in pl.LOG_PATH.read_text(encoding="utf-8")


def test_an_unwritable_log_does_not_stop_the_run(monkeypatch):
    """A pipeline that refuses to run because it cannot write a log is worse than
    one that runs unlogged."""
    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(pl.Path, "mkdir", boom)
    saved = sys.stdout
    assert pl.start("unit") is None
    assert sys.stdout is saved          # nothing was redirected


# ── rotation ──────────────────────────────────────────────────────────────────

def test_a_large_log_is_rotated():
    """An unattended loop that goes wrong must not fill a disk."""
    pl.LOG_DIR.mkdir(parents=True, exist_ok=True)
    pl.LOG_PATH.write_text("x" * (pl.MAX_BYTES + 1), encoding="utf-8")
    pl.start("unit")
    backup = pl.LOG_PATH.with_suffix(pl.LOG_PATH.suffix + pl.BACKUP_SUFFIX)
    assert backup.exists()
    assert pl.LOG_PATH.stat().st_size < pl.MAX_BYTES


def test_only_one_backup_is_kept():
    pl.LOG_DIR.mkdir(parents=True, exist_ok=True)
    backup = pl.LOG_PATH.with_suffix(pl.LOG_PATH.suffix + pl.BACKUP_SUFFIX)
    backup.write_text("old backup", encoding="utf-8")
    pl.LOG_PATH.write_text("x" * (pl.MAX_BYTES + 1), encoding="utf-8")
    pl.start("unit")
    assert "old backup" not in backup.read_text(encoding="utf-8")


def test_a_small_log_is_left_alone():
    pl.LOG_DIR.mkdir(parents=True, exist_ok=True)
    pl.LOG_PATH.write_text("small\n", encoding="utf-8")
    pl.start("unit")
    assert not pl.LOG_PATH.with_suffix(pl.LOG_PATH.suffix + pl.BACKUP_SUFFIX).exists()


# ── tail ──────────────────────────────────────────────────────────────────────

def test_tail_returns_the_last_lines():
    pl.LOG_DIR.mkdir(parents=True, exist_ok=True)
    pl.LOG_PATH.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    assert pl.tail(3) == "line 97\nline 98\nline 99\n"


def test_tail_of_a_missing_log_is_empty():
    assert pl.tail() == ""


# ── the import-time rule ──────────────────────────────────────────────────────

def test_no_pipeline_script_starts_logging_at_import():
    """transform_sources is imported by flora_service to build the FLoRA tab. A
    module-level start() would tee the whole web process into a pipeline log the
    moment somebody opened a page."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:                       # module level only
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id in ("start_logging", "start")
                        and isinstance(node, ast.Expr)):
                    offenders.append(path.name)
    assert not offenders, f"start() called at import time in: {offenders}"
