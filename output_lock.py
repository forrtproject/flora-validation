"""Process-safe output directory ownership on Windows and Unix."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


@contextmanager
def output_directory_lock(directory):
    """Serialize writers before they touch candidates, reports, or release files.

    Keep the lock file in place: unlinking it can let a third process acquire a
    different inode while an existing waiter still owns the original file.
    The operating system releases ownership if the process exits unexpectedly.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".flora-preparation.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
