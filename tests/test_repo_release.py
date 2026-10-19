"""repo_release.py: after a successful website run, ask GitHub for the repository copy.

No network: requests.post is replaced. The request must never raise and never put
the token in the run's log.
"""
from types import SimpleNamespace

import pytest
import requests

import repo_release

TOKEN = "github_pat_example_secret"


@pytest.fixture
def github(monkeypatch):
    """The calls made, and the status GitHub answers with."""
    state = SimpleNamespace(calls=[], status=204)

    def post(url, **kwargs):
        state.calls.append(SimpleNamespace(url=url, **kwargs))
        return SimpleNamespace(status_code=state.status)

    monkeypatch.setattr(repo_release.requests, "post", post)
    for name in ("FLORA_RELEASE_TOKEN", "FLORA_RELEASE_REPO", "FLORA_RELEASE_BRANCH"):
        monkeypatch.delenv(name, raising=False)
    return state


def test_nothing_is_requested_without_a_token(github):
    assert "FLORA_RELEASE_TOKEN is not set" in repo_release.request_repo_copy("job-1")
    assert github.calls == []


def test_the_publish_workflow_is_started_for_the_run(github, monkeypatch):
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", TOKEN)
    message = repo_release.request_repo_copy("job-1")
    (call,) = github.calls
    assert call.url == ("https://api.github.com/repos/forrtproject/flora-validation"
                        "/actions/workflows/publish-flora.yml/dispatches")
    assert call.json == {"ref": "main", "inputs": {"job_id": "job-1"}}
    assert call.headers["Authorization"] == "Bearer " + TOKEN
    assert call.timeout
    assert message.startswith("repository copy requested")


def test_the_repository_and_branch_can_be_named(github, monkeypatch):
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", TOKEN)
    monkeypatch.setenv("FLORA_RELEASE_REPO", "someone/fork")
    monkeypatch.setenv("FLORA_RELEASE_BRANCH", "staging")
    repo_release.request_repo_copy("job-1")
    assert "/repos/someone/fork/" in github.calls[0].url
    assert github.calls[0].json["ref"] == "staging"


def test_a_refusal_is_reported_without_the_token(github, monkeypatch):
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", TOKEN)
    github.status = 403
    message = repo_release.request_repo_copy("job-1")
    assert message == "repository copy not requested: GitHub answered HTTP 403"
    assert TOKEN not in message


def test_a_refusal_carries_githubs_reason(monkeypatch):
    """403 alone does not say whether the token lacks Actions access, waits for the
    organisation's approval, or belongs to the wrong owner. GitHub's message does."""
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", TOKEN)
    body = {"message": "Resource not accessible by personal access token"}
    monkeypatch.setattr(repo_release.requests, "post",
                        lambda url, **kwargs: SimpleNamespace(status_code=403, json=lambda: body))
    assert repo_release.request_repo_copy("job-1") == (
        "repository copy not requested: GitHub answered HTTP 403 "
        "(Resource not accessible by personal access token)")


def test_a_token_that_cannot_be_sent_never_raises(monkeypatch):
    """A zero-width space pasted with the token: http.client fails to encode the
    header as latin-1, a UnicodeEncodeError, which is not a RequestException."""
    token = TOKEN + "​"
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", token)

    def post(url, headers, **kwargs):
        headers["Authorization"].encode("latin-1")          # what http.client does
        pytest.fail("the header encoded")

    monkeypatch.setattr(repo_release.requests, "post", post)
    message = repo_release.request_repo_copy("job-1")
    assert message == ("repository copy not requested: UnicodeEncodeError "
                       "preparing the request")
    assert TOKEN not in message


def test_an_unreachable_github_never_raises(monkeypatch):
    monkeypatch.setenv("FLORA_RELEASE_TOKEN", TOKEN)

    def post(*args, **kwargs):
        raise requests.ConnectionError("no route to host " + TOKEN)

    monkeypatch.setattr(repo_release.requests, "post", post)
    message = repo_release.request_repo_copy("job-1")
    assert message == "repository copy not requested: ConnectionError reaching GitHub"
