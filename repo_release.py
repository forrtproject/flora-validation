"""Ask GitHub to commit a website pipeline run's dataset to output/.

The web server cannot push to the repository. After a successful Run pipeline it
starts the Publish FLoRA workflow (.github/workflows/publish-flora.yml), which
writes the stored dataset to output/ with publish_flora.py and commits it when
flora.csv changed: the place the nightly run commits to as well.

FLORA_RELEASE_TOKEN is a fine-grained GitHub token with "Actions: read and write"
on this repository only: it can start the workflow, it cannot push code. Unset,
nothing is requested and the run's log says so. FLORA_RELEASE_REPO and
FLORA_RELEASE_BRANCH name the repository and branch (default
forrtproject/flora-validation, main).
"""
import os

import requests

WORKFLOW = "publish-flora.yml"


def request_repo_copy(job_id: str) -> str:
    """Start the workflow for this run. Never raises: the run's dataset is already
    stored, and a missing repository copy must not turn the run into a failure."""
    token = os.environ.get("FLORA_RELEASE_TOKEN", "").strip()
    if not token:
        return "repository copy not requested: FLORA_RELEASE_TOKEN is not set"
    repo = os.environ.get("FLORA_RELEASE_REPO", "").strip() or "forrtproject/flora-validation"
    branch = os.environ.get("FLORA_RELEASE_BRANCH", "").strip() or "main"
    try:
        response = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"ref": branch, "inputs": {"job_id": job_id}},
            timeout=(5, 10),            # connect, read: the runner holds its lock meanwhile
        )
    except requests.RequestException as exc:
        return f"repository copy not requested: {type(exc).__name__} reaching GitHub"
    except Exception as exc:
        # A token pasted with an invisible or curly character fails to encode
        # before anything is sent. The message is left out: it can quote the value.
        return f"repository copy not requested: {type(exc).__name__} preparing the request"
    if response.status_code in (200, 204):
        return (f"repository copy requested: Publish FLoRA commits output/flora.csv "
                f"to {repo} ({branch}) if it changed")
    return f"repository copy not requested: GitHub answered HTTP {response.status_code}"
