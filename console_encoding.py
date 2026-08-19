"""
console_encoding.py — make stdout/stderr able to carry the progress output these
scripts already print.

Several scripts use non-ASCII glyphs for progress and warnings (⚠ → ✓ ─ ✗).
Python picks the locale encoding for stdout, which on a Windows console is
cp1252, and cp1252 can encode none of them. print() then raises
UnicodeEncodeError — typically *after* the real work has been committed, so a
run that succeeded reports as a crash. Worse, it hits the reporting path: a
nightly failure in sync_csv.py is reported via traceback.print_exc(), which is
itself a write to stderr, so an unrelated non-ASCII character can swallow the
message explaining what actually went wrong.

export_validated.py fixed this for itself in ad05792. This is the same fix,
factored out so every entry point gets it — and so the next script doesn't have
to rediscover it.

Call use_utf8_output() once, at import time, in any script that prints.

Note this changes the encoding, not the font: a legacy console still renders the
glyphs as mojibake. That is the intended trade — unreadable output beats an
aborted job, and when output is redirected to a log (the nightly case) UTF-8 is
simply correct.
"""
import sys


def use_utf8_output() -> None:
    """Switch stdout and stderr to UTF-8 wherever the streams allow it.

    Best-effort by design: a stream that has been replaced — pytest's capture, a
    pipe wrapper, a closed handle under pythonw — may not expose reconfigure().
    Failing to adjust it is not worth aborting over, since the whole point is to
    remove a crash rather than introduce one.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
