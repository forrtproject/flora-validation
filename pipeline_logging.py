"""pipeline_logging.py — one log file for every FLoRA pipeline run.

The pipeline scripts report progress with `print()`, which is the right thing for
a terminal and is what the sync button captures into `source_sync_jobs.log_text`.
But a run started any other way — a cron job, a shell on the server, a developer
checking something — left nothing behind. This adds a file that every run appends
to, regardless of how it was started.

HOW IT WORKS
------------
`start()` replaces sys.stdout and sys.stderr with a tee: everything still goes to
the console exactly as before, and a timestamped copy goes to the log. Teeing
rather than converting 300 `print()` calls to `logger.info()` keeps the console
output unchanged — it is read by humans watching a sync, and by the runner that
captures it — while making the file a complete record.

NEVER CALLED AT IMPORT
----------------------
`start()` is called from each script's main(), never at module level. This matters:
`transform_sources` is imported by the web app (flora_service builds the FLoRA tab
from it), and a module-level call would redirect the whole web process's stdout
into a pipeline log the moment somebody opened a page.

Usage:
    from pipeline_logging import start
    def main():
        start("transform")
        ...
"""
import atexit
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "flora_pipeline.log"

# Rotated at this size, keeping one previous file. A nightly run writes a few KB,
# so this holds months — and an unattended loop that goes wrong cannot fill a disk.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_SUFFIX = ".1"

_started = False


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _rotate_if_large(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except OSError:
        # A locked or unwritable log must not stop a data pipeline.
        pass


class _Tee:
    """Writes to the real stream and to the log, prefixing each complete line.

    Line-buffered on purpose: progress output arrives in fragments (`  50/4901`
    then a newline), and prefixing every fragment would produce a timestamp
    mid-line. The prefix is attached when a line actually ends.
    """

    def __init__(self, stream, handle, component: str):
        self._stream = stream
        self._handle = handle
        self._component = component
        self._at_line_start = True

    def write(self, text):
        self._stream.write(text)
        try:
            self._write_log(text)
        except Exception:                                      # noqa: BLE001
            # Losing the log is acceptable; losing the run is not.
            pass
        return len(text)

    def _write_log(self, text):
        if not text:
            return
        prefix = f"[{_timestamp()}] [{self._component}] "
        for piece in text.splitlines(keepends=True):
            if self._at_line_start and piece.strip():
                self._handle.write(prefix)
            self._handle.write(piece)
            self._at_line_start = piece.endswith(("\n", "\r"))
        self._handle.flush()

    def flush(self):
        self._stream.flush()
        try:
            self._handle.flush()
        except Exception:                                      # noqa: BLE001
            pass

    # Anything else (isatty, encoding, fileno, …) belongs to the real stream.
    def __getattr__(self, name):
        return getattr(self._stream, name)


def start(component: str) -> "Path | None":
    """Begin teeing this process's output into the pipeline log.

    Safe to call twice (the second call is a no-op) and safe to call when the log
    cannot be opened — it reports and carries on, because a pipeline that refuses
    to run because it cannot write a log file is worse than one that runs unlogged.
    """
    global _started
    if _started:
        return LOG_PATH

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_large(LOG_PATH)
        handle = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[logging] could not open {LOG_PATH}: {exc}", file=sys.stderr)
        return None

    _started = True
    started_at = time.monotonic()
    argv = " ".join(sys.argv[1:])
    handle.write(f"\n{'=' * 78}\n")
    handle.write(f"[{_timestamp()}] [{component}] START"
                 f"{(' ' + argv) if argv else ''} (pid {os.getpid()})\n")
    handle.flush()

    sys.stdout = _Tee(sys.stdout, handle, component)
    sys.stderr = _Tee(sys.stderr, handle, component)

    def _finish():
        try:
            elapsed = time.monotonic() - started_at
            handle.write(f"[{_timestamp()}] [{component}] END ({elapsed:.1f}s)\n")
            handle.flush()
            handle.close()
        except Exception:                                      # noqa: BLE001
            pass

    atexit.register(_finish)
    return LOG_PATH


def tail(lines: int = 50) -> str:
    """The last N lines of the log. For an operator asking what just happened."""
    if not LOG_PATH.exists():
        return ""
    with LOG_PATH.open(encoding="utf-8", errors="replace") as handle:
        return "".join(handle.readlines()[-lines:])
