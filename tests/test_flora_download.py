"""The FLoRA tab's downloads.

Download full CSV is the stored dataset (flora_data): the flora.csv the last
pipeline run published and committed to output/, with corrections saved since, so
it is the same file the public API and other repositories read. Export filtered CSV
is the tab's own view of the current build. The route is compiled from app.py on
its own, with both services replaced, as in test_flora_edits.
"""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {"_flora_filters", "admin_flora_export"}
URL = "/api/admin/flora/export.csv"


@pytest.fixture
def download():
    app = FastAPI()
    service = Mock()
    service.export_csv.return_value = "id\nlive\n"
    store = Mock()
    store.has_dataset.return_value = True
    store.export_csv.return_value = "id\nstored\n"

    def current_admin():
        return {"id": 7, "handle": "test-admin"}

    @contextmanager
    def db():
        yield "isolated-cursor"

    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in ROUTES]
    assert {node.name for node in nodes} == ROUTES
    namespace = {"app": app, "Depends": Depends, "HTTPException": HTTPException,
                 "Response": Response, "datetime": datetime, "timezone": timezone,
                 "current_admin": current_admin, "db": db,
                 "flora_service": service, "flora_store": store}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return SimpleNamespace(client=TestClient(app), service=service, store=store)


def test_the_full_download_is_the_stored_dataset(download):
    response = download.client.get(URL)
    assert response.status_code == 200
    assert response.text == "id\nstored\n"
    download.store.export_csv.assert_called_once_with("isolated-cursor")
    download.service.export_csv.assert_not_called()


@pytest.mark.parametrize("query", ["search=memory", "type=replication", "edited=true",
                                   "page=1&per_page=50&view=tab"])
def test_a_filtered_download_is_the_tabs_view(download, query):
    """The last case is Export filtered CSV in the default All view: no filter."""
    response = download.client.get(URL + "?" + query)
    assert response.text == "id\nlive\n"
    download.store.export_csv.assert_not_called()


@pytest.mark.parametrize("query", ["page=1&per_page=50", "search=", "edited=false"])
def test_paging_or_empty_filters_still_give_the_stored_dataset(download, query):
    assert download.client.get(URL + "?" + query).text == "id\nstored\n"


def test_the_filtered_button_asks_for_the_tabs_view():
    app_js = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
    handler = app_js[app_js.index('$("#flora-export-btn")'):]
    assert 'view: "tab"' in handler[:handler.index("});")]


def test_no_stored_dataset_says_to_run_the_pipeline(download):
    download.store.has_dataset.return_value = False
    response = download.client.get(URL)
    assert response.status_code == 409
    assert "run the pipeline" in response.json()["detail"]
    download.store.export_csv.assert_not_called()
