"""Architecture and regression-gate invariants.

Guards against:
1. Top-level import cycles across production modules (and tracks full-graph SCC ceiling).
2. Unintended changes to the FastAPI route table, mount ordering, or OpenAPI contract.
3. Broken test paths referenced in .github/workflows/*.yml.
4. Non-stdlib imports creeping into tests/conftest.py (which would break the minimal
   release-flora-zenodo.yml workflow environment).
"""
import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "app_routes_snapshot.json"

# Only the 2-module facade pair {flora_registry, transform_sources} remains in the
# full graph (kept so flora_registry.transform_sources.build patches stay compatible).
ALLOWED_FULL_GRAPH_SCCS = [["flora_registry", "transform_sources"]]


def _prod_python_files() -> list[Path]:
    excluded = {".venv", ".git", "__pycache__", "tests", "node_modules", "scripts", "audit", "docs"}
    files = []
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in excluded for part in p.parts):
            continue
        files.append(p)
    return files


def _import_graphs():
    files = _prod_python_files()
    mod_of = {}
    local_roots = set()
    for p in files:
        rel = p.relative_to(ROOT)
        parts = rel.with_suffix("").parts
        if parts[-1] == "__init__":
            mod = ".".join(parts[:-1])
        else:
            mod = ".".join(parts)
        mod_of[p] = mod
        local_roots.add(parts[0])

    top_g = defaultdict(set)
    full_g = defaultdict(set)
    for mod in mod_of.values():
        top_g[mod]
        full_g[mod]

    for p, mod in mod_of.items():
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))

        def walk(node, in_scope: bool):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, True)
                elif isinstance(child, ast.ClassDef):
                    # Class bodies execute at import time, not deferred.
                    walk(child, in_scope)
                elif isinstance(child, ast.Import):
                    for alias in child.names:
                        base = alias.name.split(".")[0]
                        if base in local_roots:
                            target = alias.name if alias.name in full_g else base
                            if target != mod:
                                full_g[mod].add(target)
                                if not in_scope:
                                    top_g[mod].add(target)
                elif isinstance(child, ast.ImportFrom):
                    targets = set()
                    if child.level > 0:
                        pkg_parts = mod.split(".")[:-child.level]
                        base_mod = ".".join(pkg_parts + ([child.module] if child.module else []))
                        if base_mod in full_g:
                            targets.add(base_mod)
                        for alias in child.names:
                            sub = f"{base_mod}.{alias.name}" if base_mod else alias.name
                            if sub in full_g:
                                targets.add(sub)
                    elif child.module:
                        base = child.module.split(".")[0]
                        if base in local_roots:
                            if child.module in full_g:
                                targets.add(child.module)
                            else:
                                targets.add(base)
                            for alias in child.names:
                                sub = f"{child.module}.{alias.name}"
                                if sub in full_g:
                                    targets.add(sub)
                    for target in targets:
                        if target != mod:
                            full_g[mod].add(target)
                            if not in_scope:
                                top_g[mod].add(target)
                else:
                    walk(child, in_scope)

        walk(tree, False)
    return top_g, full_g


def _find_sccs(graph):
    idx_counter = [0]
    stack = []
    lowlink = {}
    index = {}
    on_stack = set()
    sccs = []

    def strongconnect(v):
        index[v] = lowlink[v] = idx_counter[0]
        idx_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in graph:
                continue
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(sorted(scc))

    for v in sorted(graph):
        if v not in index:
            strongconnect(v)
    return sccs


def _load_app_stubbed():
    os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
    os.environ.setdefault("ADMIN_PASSWORD", "bootstrap-password-for-import")
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 1}
    cursor.fetchall.return_value = []
    connection = MagicMock()
    connection.cursor.return_value = cursor
    with patch("psycopg2.connect", return_value=connection), patch(
        "apscheduler.schedulers.background.BackgroundScheduler.start"
    ):
        import app as app_mod
    return app_mod.app


def _flatten_routes(routes):
    out = []
    for r in routes:
        sub = getattr(r, "routes", None)
        if sub and type(r).__name__ != "Mount":
            out.extend(_flatten_routes(sub))
        else:
            out.append({
                "type": type(r).__name__,
                "path": getattr(r, "path", None),
                "methods": sorted(getattr(r, "methods", []) or []),
                "name": getattr(r, "name", None),
            })
    return out


def test_no_top_level_import_cycles():
    top_g, full_g = _import_graphs()
    assert _find_sccs(top_g) == [], f"Top-level import cycles found: {_find_sccs(top_g)}"
    full_sccs = _find_sccs(full_g)
    assert full_sccs == ALLOWED_FULL_GRAPH_SCCS, (
        f"Unexpected full-graph import cycles: {full_sccs}"
    )


def test_fastapi_routes_and_openapi_match_snapshot():
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    fastapi_app = _load_app_stubbed()
    routes = _flatten_routes(fastapi_app.routes)
    schema = fastapi_app.openapi()

    assert routes[-1]["type"] == "Mount" and routes[-1]["name"] == "docs", (
        "StaticFiles mount must remain last so API routes are never shadowed"
    )
    assert len(routes) == expected["route_count"]
    assert routes == expected["routes"]
    assert schema.get("paths", {}) == expected["openapi_paths_spec"]
    assert schema.get("components", {}).get("schemas", {}) == expected["openapi_schemas_spec"]


def test_all_workflow_referenced_test_files_exist():
    missing = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for ref in sorted(set(re.findall(r"tests/[A-Za-z0-9_/]+\.py", text))):
            if not (ROOT / ref).is_file():
                missing.append((wf.name, ref))
    assert missing == [], f"Workflow references missing test file(s): {missing}"


def test_conftest_uses_only_stdlib_and_pytest():
    """release-flora-zenodo.yml runs pytest with only requests/pyyaml/pandas/pytest."""
    tree = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    allowed = set(sys.stdlib_module_names) | {"pytest"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in allowed:
                    bad.append(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root not in allowed:
                bad.append(root)
    assert bad == [], f"tests/conftest.py must stay stdlib+pytest only, found: {bad}"
