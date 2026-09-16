"""Read-only /v1 APIs over the committed FLoRA snapshot.

The DOI responses follow the supplied flora-backend handlers. Permanent pair-row
identity has its own full-MD5 lookup; it is never confused with DOI hash prefixes.
Importing this module neither connects to a database nor starts background jobs.
"""
from contextlib import contextmanager
import json
import logging
import os
import re
from threading import Lock

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
import psycopg2
from psycopg2.extras import RealDictCursor

import flora_api_records
import flora_api_search
import flora_store
from transform_sources import OUTPUT_EXTRAS, PROVENANCE_COLUMNS

logger = logging.getLogger(__name__)
LOOKUP_PATHS = frozenset({"/v1/prefix-lookup", "/v1/original-lookup", "/v1/id-lookup", "/v1/search"})
PUBLIC_PATHS = LOOKUP_PATHS | {"/v1/dois"}
MAX_BODY_BYTES = 65536


def is_public_read_request(request):
    """Only these exact public lookup paths permit cross-origin read-only POST."""
    path = request.url.path
    return (path in PUBLIC_PATHS and request.method in {"GET", "HEAD", "OPTIONS"}
            or path in LOOKUP_PATHS and request.method == "POST")


@contextmanager
def readonly_database():
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        # Revision metadata and dataset rows must describe the same committed
        # snapshot. PostgreSQL also enforces that API requests cannot write.
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
    finally:
        connection.close()


class DatasetNotReady(Exception):
    pass


class InvalidRequest(Exception):
    """Only errors originating from public request validation are safe to echo."""


def _revision(cur):
    cur.execute("SELECT snapshot_sha256, updated_at FROM flora_data_metadata WHERE singleton")
    row = cur.fetchone()
    if row is None:
        raise DatasetNotReady("The prepared FLoRA dataset is not ready. Run the preparation pipeline first.")
    return row["snapshot_sha256"], row["updated_at"]


class DatasetCache:
    """Cache projection only; check the shared DB revision on every request."""
    def __init__(self):
        self._lock = Lock()
        self._key = None
        self._records = None
        self._prefixes = None

    def load(self, cur):
        revision = _revision(cur)
        info = cur.connection.info
        key = (info.host, info.port, info.dbname, *revision)
        with self._lock:
            if key == self._key and self._records is not None:
                return self._records, self._prefixes
            cur.execute("SELECT * FROM flora_data WHERE retired_at IS NULL ORDER BY export_position")
            rows = [flora_store._record(row) for row in cur.fetchall()]
            records = flora_api_records.build_records(rows)
            prefixes = {}
            # Preserve every supplied DOI-prefix association, even if one DOI
            # appears in several rows with different legacy hashes.
            for row in rows:
                for side in ("o", "r"):
                    doi = flora_api_records.normalize_doi(row.get(f"doi_{side}"))
                    if doi not in records:
                        continue
                    prefix = str(row.get(f"doi_{side}_hash") or records[doi].get("doi_hash") or "").strip().lower()[:3]
                    if re.fullmatch(r"[0-9a-f]{3}", prefix):
                        bucket = prefixes.setdefault(prefix, [])
                        if doi not in bucket:
                            bucket.append(doi)
            self._key, self._records, self._prefixes = key, records, prefixes
            return records, prefixes


def _cors(request):
    headers = {"Access-Control-Allow-Methods": "GET,POST,OPTIONS",
               "Access-Control-Allow-Headers": "Content-Type"}
    if request.url.path == "/v1/search":
        allowed = {"https://forrt.org"}
        allowed.update(origin.strip().rstrip("/") for origin in os.getenv("FLORA_SEARCH_ORIGINS", "").split(",") if origin.strip())
        origin = request.headers.get("origin", "")
        if origin in allowed:
            headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    else:
        headers["Access-Control-Allow-Origin"] = "*"
    return headers


def _strings(value, *, name, maximum=200):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    if len(value) > maximum:
        raise ValueError(f"At most {maximum} {name} are allowed")
    if any(len(item) > 2048 for item in value):
        raise ValueError(f"A {name} value is too long")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _lookup_values(body, query, name, aliases=()):
    for key in (name, *aliases):
        if key in body:
            # JSON arrays preserve commas that are part of a DOI itself.
            values = _strings(body[key], name=name)
            if values:
                return values
    values = []
    for key in (name, *aliases):
        values.extend(part for item in query.getlist(key) for part in item.split(","))
    return _strings(values, name=name)


def _include_derived(body, query):
    email = body.get("apiEmail")
    if not isinstance(email, str) or not email.strip():
        email = query.get("apiEmail")
    # This is payload selection for legacy callers, never authentication.
    return not (isinstance(email, str) and email.strip())


def _search_params(body, query):
    params = dict(body)
    for name in ("query", "q", "limit", "offset", "yearFrom", "yearTo"):
        if name not in params and name in query:
            params[name] = query[name]
    for name in ("mustHave", "anyOf", "exclude", "outcomes", "paperTypes"):
        if name not in params and name in query:
            params[name] = [part.strip() for item in query.getlist(name) for part in item.split(",") if part.strip()]
        if name in params:
            value = params[name]
            if isinstance(value, str):
                value = [value]
            params[name] = _strings(value, name=name, maximum=50)
    for name in ("query", "q"):
        if name in params and (not isinstance(params[name], str) or len(params[name]) > 2048):
            raise ValueError(f"{name} must be a string of at most 2048 characters")
    return params


def _lookup_hashes(cur, hashes):
    _revision(cur)
    cur.execute("SELECT * FROM flora_data WHERE id_md5 = ANY(%s::text[]) ORDER BY export_position", (hashes,))
    found = {}
    public_columns = [*flora_store.CSV_COLUMNS, *OUTPUT_EXTRAS, *PROVENANCE_COLUMNS, "_meta"]
    for row in cur.fetchall():
        flat = flora_store._record(row)
        # Arbitrary imported extra_fields may carry internal annotations. Only
        # the published pipeline contract belongs in an anonymous API response.
        found[row["id_md5"]] = {key: flat[key] for key in public_columns if key in flat}
    return {"results": {value: found.get(value) for value in hashes}}


def _openapi(kind, method):
    strings = {"type": "array", "items": {"type": "string"}}
    properties = {"apiEmail": {"type": "string", "description": "Omit derived summaries when supplied; not authentication."}}
    example = {}
    if kind == "original-lookup":
        properties["dois"] = {**strings, "maxItems": 200}
        example = {"dois": ["10.1016/0010-0285(72)90003-5"]}
    elif kind == "prefix-lookup":
        properties["prefixes"] = {**strings, "maxItems": 200,
                                  "description": "Three hexadecimal characters from the DOI hash, not the ID hash."}
        example = {"prefixes": ["198"]}
    elif kind == "id-lookup":
        properties["hashes"] = {**strings, "maxItems": 200,
                                "description": "Complete 32-character MD5 hashes of permanent FLoRA row IDs."}
        example = {"hashes": ["2de91c651bc82ac3d5e43031660a22b6"]}
    elif kind == "search":
        properties.update(query={"type": "string"}, q={"type": "string"},
                          limit={"type": "integer", "minimum": 1, "maximum": 1000, "default": 1000},
                          offset={"type": "integer", "minimum": 0, "default": 0},
                          yearFrom={"type": "integer", "minimum": 1},
                          yearTo={"type": "integer", "minimum": 1})
        properties.update({key: {**strings, "maxItems": 50}
                           for key in ("mustHave", "anyOf", "exclude", "outcomes", "paperTypes")})
        example = {"query": "memory", "limit": 20}
    else:
        properties = {}
    if method == "POST":
        return {"requestBody": {"content": {"application/json": {
            "schema": {"type": "object", "properties": properties}, "example": example}}}}
    return {"parameters": [{"name": key, "in": "query", "required": False,
                             "schema": schema, "style": "form", "explode": True}
                            for key, schema in properties.items()]}


def create_router(database=readonly_database):
    router = APIRouter(tags=["Public FLoRA dataset"])
    cache = DatasetCache()

    def execute(kind, body, query):
        include_derived = _include_derived(body, query)
        # Validate requests before opening a connection.
        values, params = None, None
        try:
            if kind == "prefix-lookup":
                values = [value.lower() for value in _lookup_values(body, query, "prefixes", ("prefix",))]
                if not values:
                    raise ValueError("No prefixes provided")
                if any(not re.fullmatch(r"[0-9a-f]{3}", value) for value in values):
                    raise ValueError("Each DOI hash prefix must contain exactly three hexadecimal characters")
            elif kind == "original-lookup":
                values = [flora_api_records.normalize_doi(value) for value in _lookup_values(body, query, "dois")]
                values = list(dict.fromkeys(value for value in values if value))
                if not values:
                    raise ValueError("No DOIs provided")
            elif kind == "id-lookup":
                values = [value.lower() for value in _lookup_values(body, query, "hashes", ("id_md5",))]
                if not values:
                    raise ValueError("No ID hashes provided")
                if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in values):
                    raise ValueError("Each ID hash must contain the complete 32-character MD5")
            elif kind == "search":
                params = _search_params(body, query)
                # The pure search validates remaining scalar/filter values as well.
                flora_api_search.search({}, params, include_derived=include_derived)
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc
        with database() as cur:
            if kind == "id-lookup":
                return _lookup_hashes(cur, list(dict.fromkeys(values)))
            records, prefixes = cache.load(cur)
        if kind == "dois":
            return {"total": len(records), "dois": list(records)}
        if kind == "original-lookup":
            return {"results": {value: flora_api_records.serialize_record(records[value], include_derived)
                                if value in records else None for value in values}}
        if kind == "prefix-lookup":
            selected = set(doi for value in values for doi in prefixes.get(value, []))
            if len(selected) > 5000:
                raise InvalidRequest("Request matches more than 5000 papers; request fewer prefixes")
            return {"results": {value: [flora_api_records.serialize_record(records[doi], include_derived)
                                        for doi in prefixes.get(value, [])] for value in values}}
        return flora_api_search.search(records, params, include_derived=include_derived)

    def endpoint_for(kind):
        async def endpoint(request: Request):
            headers = _cors(request)
            if request.method == "OPTIONS":
                return Response(status_code=200, headers=headers)
            try:
                body = {}
                if request.method == "POST":
                    raw = bytearray()
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw) > MAX_BODY_BYTES:
                            return JSONResponse({"error": "Request body exceeds 64 KiB"}, status_code=413,
                                                headers={**headers, "Cache-Control": "no-store"})
                    if raw:
                        try:
                            body = json.loads(raw)
                        except (ValueError, UnicodeError) as exc:
                            raise InvalidRequest("Request body must contain valid JSON") from exc
                        if not isinstance(body, dict):
                            raise InvalidRequest("Request body must be a JSON object")
                payload = await run_in_threadpool(execute, kind, body, request.query_params)
                ttl = 300 if kind == "search" else 3600
                return JSONResponse(jsonable_encoder(payload), headers={**headers, "Cache-Control": f"public, max-age={ttl}"})
            except InvalidRequest as exc:
                return JSONResponse({"error": str(exc)}, status_code=400, headers={**headers, "Cache-Control": "no-store"})
            except DatasetNotReady as exc:
                return JSONResponse({"error": str(exc)}, status_code=503, headers={**headers, "Cache-Control": "no-store"})
            except Exception:
                logger.exception("Public FLoRA %s request failed", kind)
                return JSONResponse({"error": "Internal Server Error"}, status_code=500,
                                    headers={**headers, "Cache-Control": "no-store"})
        return endpoint

    for kind in ("prefix-lookup", "original-lookup", "dois", "search", "id-lookup"):
        endpoint = endpoint_for(kind)
        methods = ("GET", "OPTIONS") if kind == "dois" else ("GET", "POST", "OPTIONS")
        for method in methods:
            router.add_api_route(f"/v1/{kind}", endpoint, methods=[method],
                                 name=f"flora_{kind.replace('-', '_')}_{method.lower()}",
                                 include_in_schema=method != "OPTIONS",
                                 summary=f"FLoRA {kind} ({method})",
                                 openapi_extra=_openapi(kind, method),
                                 responses={400: {"description": "Invalid or missing query parameters"},
                                            503: {"description": "Prepared dataset has not been imported"}})
    return router
