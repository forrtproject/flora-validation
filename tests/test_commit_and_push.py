""".github/scripts/commit_and_push.sh: how the nightly sync, Publish FLoRA and the
daily export push to one branch without overwriting or mixing each other's files.

Runs the script against a local bare repository with shallow clones, each standing
for one job. Needs bash and git (Git for Windows provides both); skipped without them.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import prepare_flora
import release_helpers

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "commit_and_push.sh"
OWNED = ["output/flora.csv", "output/flora_release_manifest.json",
         "output/flora_validation.md", "output/flora_export_log.csv"]
MANIFEST = "output/flora_release_manifest.json"


def _bash():
    git = shutil.which("git")
    if os.name == "nt":
        # Git's own bash: C:\Windows\System32\bash.exe is WSL, which cannot see
        # these paths the same way.
        if git:                         # Git\cmd\git.exe or Git\mingw64\bin\git.exe
            for root in Path(git).parents[1:3]:
                candidate = root / "bin" / "bash.exe"
                if candidate.exists():
                    return str(candidate)
        return None
    return shutil.which("bash")


BASH = _bash()
needs_bash = pytest.mark.skipif(not (BASH and shutil.which("git")), reason="needs bash and git")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def run(cwd, message, trigger, *paths, manifest=None):
    env = {**os.environ, "GITHUB_REF_NAME": "main", "PYTHON": sys.executable}
    if manifest:
        env["RELEASE_MANIFEST"] = manifest
    return subprocess.run([BASH, str(SCRIPT), message, trigger, *paths], cwd=cwd, env=env,
                          capture_output=True, text=True)


def release(clone, *paths, manifest=True):
    """Commit `clone`'s owned files as a release job does."""
    return run(clone, "release", "output/flora.csv", *(paths or OWNED),
               manifest=MANIFEST if manifest else None)


def write(clone, name, text):
    path = clone / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def version(stored_at, watermark=0):
    return json.dumps({"data_version": {"stored_at": stored_at, "change_watermark": watermark}})


ROWS = "".join(f"row {i}\n" for i in range(1, 31))


@pytest.fixture
def jobs(tmp_path):
    """A remote holding a release, and a factory for jobs' checkouts of it."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "seed@example.org")
    git(seed, "config", "user.name", "seed")
    write(seed, "output/flora.csv", ROWS)
    write(seed, MANIFEST, version("2026-10-01T03:00:00+00:00"))
    write(seed, "output/flora_validation.md", "seed\n")
    write(seed, "output/flora_export_log.csv", "seed log\n")
    write(seed, "output/flora_entry_sheets.csv", "not a release file\n")
    write(seed, "data/validated_export.csv", "seed\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "HEAD:main")

    def checkout(name):
        clone = tmp_path / name
        # A file:// URL, as a shallow clone needs: like actions/checkout's depth 1.
        subprocess.run(["git", "clone", "-q", "--depth=1", remote.as_uri(), str(clone)],
                       check=True, capture_output=True)
        return clone

    return remote, checkout


def on_main(remote, name):
    shown = subprocess.run(["git", "--git-dir", str(remote), "show", f"main:{name}"],
                           capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else None


@needs_bash
def test_an_unchanged_trigger_commits_nothing(jobs):
    remote, checkout = jobs
    nightly = checkout("nightly")
    write(nightly, "output/flora_validation.md", "rewritten\n")
    result = release(nightly)
    assert result.returncode == 0, result.stderr
    assert "output/flora.csv is unchanged" in result.stdout
    assert on_main(remote, "output/flora_validation.md") == "seed\n"


@needs_bash
def test_a_new_untracked_trigger_is_committed(jobs):
    remote, checkout = jobs
    nightly = checkout("nightly")
    write(nightly, "output/new_release.csv", "first\n")
    result = run(nightly, "release", "output/new_release.csv", "output/new_release.csv")
    assert result.returncode == 0, result.stderr
    assert on_main(remote, "output/new_release.csv") == "first\n"


@needs_bash
def test_a_racing_push_never_blends_two_versions_of_a_file(jobs):
    """Each job changes a different row of flora.csv. A line-by-line merge would
    publish both changes: a file neither run produced."""
    remote, checkout = jobs
    nightly, publish = checkout("nightly"), checkout("publish")
    write(nightly, "output/flora.csv", ROWS.replace("row 3\n", "row 3 nightly\n"))
    write(nightly, MANIFEST, version("2026-10-02T03:00:00+00:00"))
    assert release(nightly).returncode == 0

    published = ROWS.replace("row 20\n", "row 20 publish\n")
    write(publish, "output/flora.csv", published)
    write(publish, MANIFEST, version("2026-10-02T09:00:00+00:00"))
    result = release(publish)
    assert result.returncode == 0, result.stderr
    assert "Push rejected" in result.stdout
    assert on_main(remote, "output/flora.csv") == published, "the whole file, not a blend"


@needs_bash
def test_a_file_unchanged_from_the_checkout_is_still_this_jobs(jobs):
    """Publish pushed first and removed the build files. The nightly's log has the
    same bytes as at its checkout; it must not go missing beside its flora.csv."""
    remote, checkout = jobs
    nightly, publish = checkout("nightly"), checkout("publish")
    write(publish, "output/flora.csv", ROWS + "row 31\n")
    write(publish, MANIFEST, version("2026-10-02T02:00:00+00:00"))
    (publish / "output" / "flora_validation.md").unlink()
    (publish / "output" / "flora_export_log.csv").unlink()
    assert release(publish).returncode == 0
    assert on_main(remote, "output/flora_export_log.csv") is None

    write(nightly, "output/flora.csv", ROWS + "row 31\nrow 32\n")
    write(nightly, MANIFEST, version("2026-10-02T03:00:00+00:00"))
    write(nightly, "output/flora_validation.md", "nightly\n")       # its log: unchanged
    result = release(nightly)
    assert result.returncode == 0, result.stderr
    assert on_main(remote, "output/flora.csv") == ROWS + "row 31\nrow 32\n"
    assert on_main(remote, "output/flora_export_log.csv") == "seed log\n"
    assert on_main(remote, "output/flora_validation.md") == "nightly\n"


@needs_bash
def test_another_releases_build_files_do_not_stay_beside_this_one(jobs):
    """The nightly pushed its report files; a publish from the same base, which
    removed them, must not leave the nightly's beside its own flora.csv."""
    remote, checkout = jobs
    nightly, publish = checkout("nightly"), checkout("publish")
    write(nightly, "output/flora.csv", ROWS + "row 31\n")
    write(nightly, MANIFEST, version("2026-10-02T03:00:00+00:00"))
    write(nightly, "output/flora_validation.md", "nightly\n")
    assert release(nightly).returncode == 0

    write(publish, "output/flora.csv", ROWS + "row 31\nrow 32\n")
    write(publish, MANIFEST, version("2026-10-02T09:00:00+00:00"))
    for name in ("flora_validation.md", "flora_export_log.csv"):
        (publish / "output" / name).unlink()
    result = release(publish)
    assert result.returncode == 0, result.stderr
    assert on_main(remote, "output/flora.csv") == ROWS + "row 31\nrow 32\n"
    assert on_main(remote, "output/flora_validation.md") is None
    assert on_main(remote, "output/flora_export_log.csv") is None
    assert on_main(remote, "output/flora_entry_sheets.csv") == "not a release file\n", \
        "files outside the release keep the tip's"


@needs_bash
def test_an_older_dataset_never_replaces_a_newer_one(jobs):
    """Publish read flora_data before the nightly stored a newer snapshot, but
    pushes after it: the tip is left as it is."""
    remote, checkout = jobs
    nightly, publish = checkout("nightly"), checkout("publish")
    write(nightly, "output/flora.csv", ROWS + "nightly\n")
    write(nightly, MANIFEST, version("2026-10-02T03:20:00+00:00", 7))
    assert release(nightly).returncode == 0

    write(publish, "output/flora.csv", ROWS + "publish\n")
    write(publish, MANIFEST, version("2026-10-02T03:10:00+00:00", 7))
    result = release(publish)
    assert result.returncode == 0, result.stderr
    assert "newer data" in result.stdout
    assert on_main(remote, "output/flora.csv") == ROWS + "nightly\n"


@needs_bash
def test_a_job_on_other_paths_keeps_the_others_files(jobs):
    """The daily export racing a release: both land, neither undoes the other."""
    remote, checkout = jobs
    nightly, export = checkout("nightly"), checkout("export")
    write(nightly, "output/flora.csv", ROWS + "row 31\n")
    write(nightly, MANIFEST, version("2026-10-02T03:00:00+00:00"))
    assert release(nightly).returncode == 0
    write(export, "data/validated_export.csv", "export\n")
    result = run(export, "export", "", "data/validated_export.csv")
    assert result.returncode == 0, result.stderr
    assert on_main(remote, "data/validated_export.csv") == "export\n"
    assert on_main(remote, "output/flora.csv") == ROWS + "row 31\n"


# ── the data version ──────────────────────────────────────────────────────────

def test_data_versions_order_by_store_then_correction():
    def at(stored_at, watermark=0):
        return {"data_version": {"stored_at": stored_at, "change_watermark": watermark}}
    assert release_helpers.is_older(at("2026-10-02T03:00:00+00:00"), at("2026-10-02T03:00:01+00:00"))
    assert release_helpers.is_older(at("2026-10-02T03:00:00+00:00", 4), at("2026-10-02T03:00:00+00:00", 5))
    assert not release_helpers.is_older(at("2026-10-02T03:00:00+00:00"), at("2026-10-02T03:00:00+00:00"))
    assert release_helpers.is_older({}, at("2026-10-02T03:00:00+00:00")), "an unversioned release is oldest"
    assert not release_helpers.is_older(at("2026-10-02T05:00:00+02:00"), at("2026-10-02T02:59:00+00:00")), \
        "compared as instants, not as text"


# ── the workflows ─────────────────────────────────────────────────────────────

def test_the_release_file_list_is_the_one_prepare_writes():
    listed = (ROOT / ".github" / "release-files.txt").read_text(encoding="utf-8").split()
    assert listed == [f"output/{name}" for name in prepare_flora.RELEASE_FILES]
    workflow = (ROOT / ".github" / "workflows" / "sync-sources.yml").read_text(encoding="utf-8")
    upload = workflow[workflow.index("- name: Upload FLoRA dataset"):]
    upload = upload[upload.index("path: |"):upload.index("retention-days")]
    assert re.findall(r"output/\S+", upload) == listed, "the artifact holds the release files"


def test_every_job_that_pushes_uses_the_script():
    workflows = ROOT / ".github" / "workflows"
    for name in ("sync-sources.yml", "publish-flora.yml", "daily-export.yml"):
        text = (workflows / name).read_text(encoding="utf-8")
        assert "bash .github/scripts/commit_and_push.sh" in text, name
        assert "git push" not in text and "-X theirs" not in text, name
    for name in ("sync-sources.yml", "publish-flora.yml"):
        text = (workflows / name).read_text(encoding="utf-8")
        assert "output/flora.csv $(cat .github/release-files.txt)" in text, name
        assert "RELEASE_MANIFEST: output/flora_release_manifest.json" in text, name
