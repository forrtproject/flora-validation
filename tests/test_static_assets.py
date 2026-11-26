"""Static asset content-fingerprinting and caching."""
import os
import time
from pathlib import Path
from unittest.mock import patch

import static_assets

ROOT = Path(__file__).resolve().parent.parent


def test_index_assets_are_content_fingerprinted_and_revalidated():
    served = static_assets.fingerprinted_index(ROOT / "docs")
    for name in static_assets.FINGERPRINTED_ASSETS:
        assert f'"./{name}"' not in served
        assert f'"./{name}?v=' in served

    # CDN and font links carry their own versions and must be left alone.
    assert "cdn.jsdelivr.net/npm/marked@12/marked.min.js" in served
    assert "?v=" not in served.split("fonts.googleapis.com", 1)[1].split(">", 1)[0]


def test_asset_fingerprint_tracks_content(tmp_path):
    (tmp_path / "app.js").write_text("console.log(1);", encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="./style.css">'
        '<script src="./app.js"></script>',
        encoding="utf-8",
    )

    first = static_assets.asset_fingerprint(tmp_path, "app.js")
    assert first == static_assets.asset_fingerprint(tmp_path, "app.js")
    assert first in static_assets.fingerprinted_index(tmp_path)

    (tmp_path / "app.js").write_text("console.log(2);", encoding="utf-8")
    changed = static_assets.asset_fingerprint(tmp_path, "app.js")
    assert changed != first
    assert changed != static_assets.asset_fingerprint(tmp_path, "style.css")

    (tmp_path / "app.js").unlink()
    assert static_assets.asset_fingerprint(tmp_path, "app.js") == "0"


def test_a_settled_asset_is_hashed_once_and_then_cached(tmp_path):
    asset = tmp_path / "app.js"
    asset.write_bytes(b"console.log(1);")
    old_time = time.time() - 3600
    os.utime(asset, (old_time, old_time))
    static_assets._CACHE.pop(asset, None)

    reads = []
    real_read = Path.read_bytes

    def counting_read(self):
        reads.append(self)
        return real_read(self)

    with patch.object(Path, "read_bytes", counting_read):
        first = static_assets.asset_fingerprint(tmp_path, "app.js")
        second = static_assets.asset_fingerprint(tmp_path, "app.js")
        third = static_assets.asset_fingerprint(tmp_path, "app.js")

    assert first == second == third
    assert len(reads) == 1, "a settled asset must be read exactly once"

    asset.write_bytes(b"console.log(22);")
    os.utime(asset, (old_time + 1, old_time + 1))
    assert static_assets.asset_fingerprint(tmp_path, "app.js") != first


def test_a_just_written_asset_is_not_cached_on_an_ambiguous_stamp(tmp_path):
    asset = tmp_path / "app.js"
    asset.write_bytes(b"console.log(1);")
    static_assets._CACHE.pop(asset, None)

    static_assets.asset_fingerprint(tmp_path, "app.js")
    assert asset not in static_assets._CACHE
