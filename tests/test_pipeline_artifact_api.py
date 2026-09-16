"""Retained pipeline downloads stay authenticated and tied to one run.

Compile the actual routes without importing app.py: its module-level init_db()
would execute schema changes, which artifact response tests do not need.
"""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
import pytest


JOB_ID = "2bb9fa81-98bf-4bcc-a204-5a35a2f3a7ae"
URL = f"/api/admin/source-sync/jobs/{JOB_ID}/artifacts/"


@pytest.fixture
def artifact_api():
    app = FastAPI()
    runner = SimpleNamespace(job_artifact=Mock())

    def current_admin():
        raise HTTPException(401, "Admin sign-in required")

    @contextmanager
    def db():
        yield "isolated-cursor"

    source = Path(__file__).resolve().parents[1] / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    endpoint = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "admin_source_sync_artifact")
    namespace = {
        "app": app, "Depends": Depends, "HTTPException": HTTPException,
        "Response": Response, "UUID": UUID, "json": json,
        "current_admin": current_admin, "db": db, "source_sync_runner": runner,
    }
    exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(source), "exec"), namespace)
    return app, TestClient(app), runner, current_admin


def authorize(fixture):
    app, _, _, current_admin = fixture
    app.dependency_overrides[current_admin] = lambda: {"handle": "test-admin"}


@pytest.mark.parametrize("artifact", ["flora.csv", "recovery.csv"])
def test_artifacts_require_admin_session(artifact_api, artifact):
    _, client, runner, _ = artifact_api
    assert client.get(URL + artifact).status_code == 401
    runner.job_artifact.assert_not_called()


@pytest.mark.parametrize("artifact", ["flora.csv", "recovery.csv"])
def test_download_returns_exact_retained_csv(artifact_api, artifact):
    authorize(artifact_api)
    _, client, runner, _ = artifact_api
    original = 'id,id_md5,title\r\n1,abc,"Müller, 2026"\r\n'
    runner.job_artifact.return_value = original
    response = client.get(URL + artifact)
    assert response.status_code == 200
    assert response.content == original.encode("utf-8")
    filename = "flora.csv" if artifact == "flora.csv" else f"flora_{JOB_ID[:8]}_recovery.csv"
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    runner.job_artifact.assert_called_once_with("isolated-cursor", JOB_ID, artifact)


@pytest.mark.parametrize("artifact,content", [
    ("report.json", {"status": "failed", "warnings": ["Metadata unavailable"]}),
    ("report.json", '{"status":"success"}'),
    ("report.md", "# FLoRA report\n\nRows: 3\n"),
])
def test_reports_download_with_correct_content(artifact_api, artifact, content):
    authorize(artifact_api)
    _, client, runner, _ = artifact_api
    runner.job_artifact.return_value = content
    response = client.get(URL + artifact)
    assert response.status_code == 200
    if artifact.endswith("json"):
        assert response.json() == (json.loads(content) if isinstance(content, str) else content)
    else:
        assert response.text == content
    assert artifact in response.headers["content-disposition"]


@pytest.mark.parametrize("path", [
    URL + "secrets.txt",
    "/api/admin/source-sync/jobs/not-a-uuid/artifacts/flora.csv",
])
def test_invalid_artifact_or_job_never_queries_database(artifact_api, path):
    authorize(artifact_api)
    _, client, runner, _ = artifact_api
    assert client.get(path).status_code == 404
    runner.job_artifact.assert_not_called()


def test_missing_artifact_has_actionable_error(artifact_api):
    authorize(artifact_api)
    _, client, runner, _ = artifact_api
    runner.job_artifact.return_value = None
    response = client.get(URL + "flora.csv")
    assert response.status_code == 404
    assert "no retained artifact" in response.json()["detail"]
