"""Prepare reviewable local release metadata for an exact FLoRA CSV.

The supplied R helper mixes preparation and OSF upload. This implementation keeps
the website's sync action local: its manifest records the exact bytes available
for download. Publishing a version to OSF is a separate operation.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def extract_current_version(markdown: str):
    match = re.search(r"\*\*Version:\*\*\s+(\d+\.\d+\.\d+)", markdown)
    return match.group(1) if match else None


def increment_version(version: str, version_type: str = "patch") -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must have the form major.minor.patch")
    if version_type not in {"major", "minor", "patch"}:
        raise ValueError("version_type must be major, minor, or patch")
    parts = list(map(int, version.split(".")))
    index = {"major": 0, "minor": 1, "patch": 2}[version_type]
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    return ".".join(map(str, parts))


def add_data_version(output_dir: Path, manifest: dict, stored_at, change_watermark) -> dict:
    """Record which state of flora_data the release's flora.csv holds, and rewrite
    its manifest. `stored_at` is flora_data_metadata.updated_at, which every stored
    snapshot and every saved FLoRA correction moves; `change_watermark` is the newest
    correction included. Two jobs racing to commit a release compare these, so an
    older dataset never replaces a newer one (.github/scripts/commit_and_push.sh)."""
    manifest["data_version"] = {
        "stored_at": stored_at if isinstance(stored_at, str) else stored_at.isoformat(),
        "change_watermark": int(change_watermark or 0),
    }
    (Path(output_dir) / "flora_release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _version_key(manifest: dict):
    """Orderable data version; a manifest from before data versions sorts first."""
    version = manifest.get("data_version") or {}
    if not version.get("stored_at"):
        return (datetime.min.replace(tzinfo=timezone.utc), -1)
    return (datetime.fromisoformat(version["stored_at"]), int(version["change_watermark"]))


def is_older(mine: dict, tip: dict) -> bool:
    """Whether release `mine` holds older data than release `tip`."""
    return _version_key(mine) < _version_key(tip)


def prepare_release(csv_path: Path, output_dir: Path, rows: int, columns: list,
                    version=None, release_notes="") -> dict:
    if version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("version must have the form major.minor.patch")
    payload = csv_path.read_bytes()
    manifest = {
        "schema_version": 1,
        "status": "prepared_locally",
        "published": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filename": "flora.csv",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "md5": hashlib.md5(payload).hexdigest(),
        "rows": rows,
        "columns": columns,
        "version": version,
        "release_notes": release_notes,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "flora_release_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    notes = ["# FLoRA release preparation", "",
             f"Generated: {manifest['generated_at']}", "",
             f"Version: {version or 'not assigned'}", "",
             f"Dataset: flora.csv ({rows:,} rows)", "",
             f"SHA-256: `{manifest['sha256']}`", "",
             "Status: files prepared locally; no external release was published.", "",
             release_notes]
    (output_dir / "flora_release_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    """release_helpers.py older MINE TIP: exit 0 when manifest MINE holds older data
    than manifest TIP, 1 when it does not. Used by .github/scripts/commit_and_push.sh."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] != "older":
        print("usage: release_helpers.py older MINE_MANIFEST TIP_MANIFEST", file=sys.stderr)
        return 2
    mine, tip = (json.loads(Path(path).read_text(encoding="utf-8")) for path in args[1:])
    return 0 if is_older(mine, tip) else 1


if __name__ == "__main__":
    raise SystemExit(main())
