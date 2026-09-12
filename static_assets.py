"""Content-fingerprinted asset URLs for the single-page frontend.

`index.html` links `app.js` and `style.css` by bare filename. That is fine for
the GitHub Pages static build, but for the live app it makes a rolling
deployment unsafe: a browser holding a cached `app.js` keeps talking to the
new API with the previous frontend, and the two disagree about request shapes.

Rewriting those links to `app.js?v=<content hash>` ties the cache entry to the
file's bytes. A changed file gets a new URL and is fetched; an unchanged file
keeps its URL and stays cached. The document itself must be served with
revalidation, or the stale HTML would go on naming the stale asset URLs.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

# Local assets only. The CDN and font links in index.html are already versioned
# by their own URLs and are deliberately left alone.
FINGERPRINTED_ASSETS = ("app.js", "style.css")

# How long after its last write a file is considered settled. (mtime, size) is
# only a safe cache key once it can no longer be ambiguous: a same-length
# rewrite landing inside one filesystem timestamp tick leaves both unchanged,
# and caching on it would serve the previous fingerprint for the new bytes —
# exactly the staleness this module exists to prevent. Two seconds covers the
# coarsest granularity in practical use (FAT's 2s); finer filesystems simply
# settle sooner in wall-clock terms than they need to.
_SETTLE_SECONDS = 2.0

_CACHE: dict[Path, tuple[tuple[int, int], str]] = {}


def asset_fingerprint(docs_dir: Path, name: str) -> str:
    """Return a short content hash for a docs asset.

    Deployed assets are written once and then never change for the life of the
    process, so they are hashed once and served from cache. A file written
    within the last few seconds is re-hashed on every call instead, because its
    (mtime, size) stamp cannot yet be trusted to distinguish two versions.
    """
    path = Path(docs_dir) / name
    try:
        stat = path.stat()
    except OSError:
        # A missing asset is a deployment problem, not a reason to fail the page
        # request; the browser's own 404 for the link is the clearer signal.
        return "0"

    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]

    try:
        content = path.read_bytes()
    except OSError:
        return "0"
    digest = hashlib.sha256(content).hexdigest()[:12]
    if time.time() - stat.st_mtime > _SETTLE_SECONDS:
        _CACHE[path] = (stamp, digest)
    return digest


def fingerprinted_index(docs_dir: Path, assets: tuple[str, ...] = FINGERPRINTED_ASSETS) -> str:
    """Return index.html with its local asset links content-fingerprinted."""
    docs_dir = Path(docs_dir)
    html = (docs_dir / "index.html").read_text(encoding="utf-8")
    for name in assets:
        html = html.replace(
            f'"./{name}"',
            f'"./{name}?v={asset_fingerprint(docs_dir, name)}"',
        )
    return html
