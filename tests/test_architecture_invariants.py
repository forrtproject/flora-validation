"""Architecture and regression-gate invariants.

Guards against:
1. Top-level import cycles across production modules (and tracks full-graph SCC ceiling).
2. Unintended changes to the FastAPI route table, mount ordering, or OpenAPI contract.
3. Broken test paths referenced in .github/workflows/*.yml.
4. Non-stdlib imports creeping into tests/conftest.py (which would break the minimal
   release-flora-zenodo.yml workflow environment).
"""
import ast
import hashlib
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


def _prod_python_files(root: Path = ROOT) -> list[Path]:
    excluded = {".venv", ".git", "__pycache__", "tests", "node_modules", "scripts", "audit", "docs"}
    files = []
    for p in sorted(root.rglob("*.py")):
        if any(part in excluded for part in p.relative_to(root).parts):
            continue
        files.append(p)
    return files


def _import_graphs(root: Path = ROOT):
    files = _prod_python_files(root)
    mod_of = {}
    is_pkg_of = {}
    local_roots = set()
    for p in files:
        rel = p.relative_to(root)
        parts = rel.with_suffix("").parts
        if parts[-1] == "__init__":
            mod = ".".join(parts[:-1])
            is_pkg_of[p] = True
        else:
            mod = ".".join(parts)
            is_pkg_of[p] = False
        mod_of[p] = mod
        local_roots.add(parts[0])

    top_g = defaultdict(set)
    full_g = defaultdict(set)
    for mod in mod_of.values():
        top_g[mod]
        full_g[mod]

    for p, mod in mod_of.items():
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))
        is_pkg = is_pkg_of[p]

        def _add_targets(targets, in_scope: bool):
            expanded = set(targets)
            for target in list(targets):
                # Importing pkg.sub from outside pkg executes pkg/__init__.py first.
                parts = target.split(".")
                for i in range(1, len(parts)):
                    anc = ".".join(parts[:i])
                    if anc in full_g and not (mod == anc or mod.startswith(anc + ".")):
                        expanded.add(anc)
            for target in expanded:
                if target != mod:
                    full_g[mod].add(target)
                    if not in_scope:
                        top_g[mod].add(target)

        def walk(node, in_scope: bool):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, True)
                elif isinstance(child, ast.ClassDef):
                    # Class bodies execute at import time, not deferred.
                    walk(child, in_scope)
                elif isinstance(child, ast.Import):
                    targets = set()
                    for alias in child.names:
                        base = alias.name.split(".")[0]
                        if base in local_roots:
                            targets.add(alias.name if alias.name in full_g else base)
                    _add_targets(targets, in_scope)
                elif isinstance(child, ast.ImportFrom):
                    targets = set()
                    if child.level > 0:
                        # In pkg/__init__.py, level=1 resolves within pkg itself (drop 0).
                        # In pkg/mod.py, level=1 resolves within pkg (drop 1).
                        drop = child.level - 1 if is_pkg else child.level
                        mod_parts = mod.split(".")
                        pkg_parts = mod_parts[:-drop] if drop > 0 else list(mod_parts)
                        base_mod = ".".join(pkg_parts + ([child.module] if child.module else []))
                        any_non_submodule = False
                        for alias in child.names:
                            sub = f"{base_mod}.{alias.name}" if base_mod else alias.name
                            if sub in full_g:
                                targets.add(sub)
                            else:
                                any_non_submodule = True
                        if any_non_submodule and base_mod in full_g:
                            targets.add(base_mod)
                    elif child.module:
                        base = child.module.split(".")[0]
                        if base in local_roots:
                            any_non_submodule = False
                            for alias in child.names:
                                sub = f"{child.module}.{alias.name}"
                                if sub in full_g:
                                    targets.add(sub)
                                else:
                                    any_non_submodule = True
                            if any_non_submodule:
                                if child.module in full_g:
                                    targets.add(child.module)
                                else:
                                    targets.add(base)
                    _add_targets(targets, in_scope)
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


def _flatten_routes(routes, prefix: str = ""):
    out = []
    for r in routes:
        orig_router = getattr(r, "original_router", None)
        if orig_router is not None:
            sub_prefix = prefix + (getattr(r, "prefix", "") or "")
            out.extend(_flatten_routes(orig_router.routes, prefix=sub_prefix))
            continue
        sub = getattr(r, "routes", None)
        if sub and type(r).__name__ != "Mount":
            sub_prefix = prefix + (getattr(r, "path", "") or "")
            out.extend(_flatten_routes(sub, prefix=sub_prefix))
        else:
            raw_path = getattr(r, "path", None)
            full_path = (prefix + raw_path) if (prefix and raw_path is not None) else raw_path
            out.append({
                "type": type(r).__name__,
                "path": full_path,
                "methods": sorted(getattr(r, "methods", []) or []),
                "name": getattr(r, "name", None),
            })
    return out


def _spec_hashes(mapping: dict) -> dict[str, str]:
    """Deterministic SHA-256 digest per OpenAPI path or component schema."""
    return {
        key: hashlib.sha256(
            json.dumps(val, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        for key, val in sorted(mapping.items())
    }


def test_import_graph_analyzer_handles_class_bodies_submodules_and_pkg_init_relatives(tmp_path):
    """Verify the AST import-graph detector catches all import-time cycle forms."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    # 1) Relative import inside __init__.py inside a ClassDef body -> cycle with pkg.child
    (pkg / "__init__.py").write_text(
        "class Boot:\n    from . import child\n", encoding="utf-8"
    )
    (pkg / "child.py").write_text("import pkg\n", encoding="utf-8")
    # 2) `from pkg import leaf` executes pkg/__init__.py first, so if pkg/__init__.py
    #    imports `outer` and `outer` runs `from pkg import leaf`, that is a real cycle.
    (pkg / "leaf.py").write_text(
        "def later():\n    import consumer\n", encoding="utf-8"
    )
    (tmp_path / "consumer.py").write_text(
        "class Holder:\n    from pkg import leaf\n", encoding="utf-8"
    )
    pkg2 = tmp_path / "pkg2"
    pkg2.mkdir()
    (pkg2 / "__init__.py").write_text("import outer\n", encoding="utf-8")
    (pkg2 / "sub.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "outer.py").write_text("from pkg2 import sub\n", encoding="utf-8")

    top_g, full_g = _import_graphs(tmp_path)
    assert "pkg.child" in top_g["pkg"]
    assert "pkg" in top_g["pkg.child"]
    assert ["pkg", "pkg.child"] in _find_sccs(top_g)

    assert "pkg.leaf" in top_g["consumer"]
    assert "pkg" in top_g["consumer"]
    assert "consumer" not in top_g["pkg.leaf"]
    assert "consumer" in full_g["pkg.leaf"]
    assert ["consumer", "pkg.leaf"] not in _find_sccs(top_g)
    assert ["consumer", "pkg.leaf"] in _find_sccs(full_g)
    assert ["outer", "pkg2"] in _find_sccs(top_g)


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
    assert _spec_hashes(schema.get("paths", {})) == expected["openapi_path_hashes"]
    assert _spec_hashes(schema.get("components", {}).get("schemas", {})) == expected["openapi_schema_hashes"]


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
