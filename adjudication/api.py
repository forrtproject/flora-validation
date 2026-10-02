"""HTTP routes of the adjudication feature.

Built by create_router() with the app's own sign-in dependencies and database
helper handed in, so this module never imports app.py. A route that cannot reach
the feature's tables answers with that fact instead of an error page.
"""

from __future__ import annotations

import traceback
import uuid
from typing import Callable

import psycopg2.errors
import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import export, importer, judging, review
from .bootstrap import SetupStatus

_ERROR_LIMIT = 300

_COUNTS_SQL = """
    SELECT
        (SELECT COUNT(*) FROM adjudication.records)                                      AS records,
        (SELECT COUNT(*) FROM adjudication.records WHERE status = 'open')                AS open,
        (SELECT COUNT(*) FROM adjudication.records WHERE status = 'awaiting_approval')   AS awaiting_approval,
        (SELECT COUNT(*) FROM adjudication.records WHERE status = 'approved')            AS approved,
        (SELECT COUNT(*) FROM adjudication.records WHERE status = 'published')           AS published,
        (SELECT COUNT(*) FROM adjudication.judgements WHERE state = 'submitted')         AS judgements
"""
_BY_KIND_SQL = "SELECT kind, COUNT(*) AS n FROM adjudication.records GROUP BY kind ORDER BY n DESC"


class Answer(BaseModel):
    original_choice: str
    suggested_doi_o: str | None = None
    outcome: str
    note: str | None = None


class Decision(BaseModel):
    doi_o: str | None = None
    title_o: str | None = None
    outcome: str
    outcome_quote: str | None = None
    quote_source: str | None = None
    admin_note: str | None = None


def _record_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "No such record")


def create_router(*, current_admin: Callable, current_validator: Callable,
                  status: Callable[[], SetupStatus], cursor: Callable) -> APIRouter:
    """The feature's routes. *cursor* is the app's db() context manager."""
    router = APIRouter(tags=["Adjudication"])

    def require_ready(*, for_admin: bool = True) -> None:
        state = status()
        if not state.ready:
            # The setup error can name the database host and user: admins only.
            detail = f": {state.error}" if for_admin and state.error else ""
            raise HTTPException(503, "The disagreements feature is not set up" + detail)

    def judge(validator: dict = Depends(current_validator)) -> dict:
        """A signed-in Trusted or Senior validator, while the feature is set up."""
        if not judging.can_judge(validator):
            raise HTTPException(403, "Only Trusted and Senior validators judge the disagreements")
        require_ready(for_admin=False)
        return validator

    def run(work: Callable, *args):
        """One transaction of judging work, its refusals as plain HTTP answers."""
        try:
            with cursor() as cur:
                return work(cur, *args)
        except judging.Refused as refused:
            raise HTTPException(refused.status, str(refused))
        except psycopg2.errors.LockNotAvailable:
            raise HTTPException(503, "Busy for a moment; please try again")

    # -- Validators ----------------------------------------------------------

    @router.get("/api/disagreements/summary")
    def disagreements_summary(validator: dict = Depends(current_validator)):
        """Whether this validator judges disagreements, and how many are left.
        Asked at every sign-in, so it answers rather than fails."""
        if not (judging.can_judge(validator) and status().ready):
            return {"available": False}
        try:
            with cursor() as cur:
                return {"available": True, **judging.progress(cur, validator)}
        except Exception:
            traceback.print_exc()
            return {"available": False}

    @router.post("/api/disagreements/next")
    def disagreements_next(validator: dict = Depends(judge)):
        """The validator's current record, or the next one, claimed for them."""
        return run(judging.next_record, validator)

    @router.post("/api/disagreements/{record_id}/submit")
    def disagreements_submit(record_id: str, answer: Answer, validator: dict = Depends(judge)):
        return run(judging.submit, validator, _record_id(record_id), answer.model_dump())

    @router.post("/api/disagreements/{record_id}/skip")
    def disagreements_skip(record_id: str, validator: dict = Depends(judge)):
        return run(judging.skip, validator, _record_id(record_id))

    # -- Admins --------------------------------------------------------------

    @router.get("/api/admin/disagreements/status")
    def disagreements_status(admin: dict = Depends(current_admin)):
        """Whether the feature is on and set up, and how far the work has got."""
        state = status()
        body = {"enabled": state.enabled, "ready": state.ready,
                "error": state.error, "counts": None, "by_kind": None,
                "source": importer.SOURCE_LABEL, "source_url": importer.SOURCE_URL}
        if not state.ready:
            return body
        try:
            with cursor() as cur:
                cur.execute(_COUNTS_SQL)
                row = cur.fetchone()
                cur.execute(_BY_KIND_SQL)
                kinds = cur.fetchall()
            body["counts"] = {key: int(value or 0) for key, value in dict(row).items()}
            body["by_kind"] = {k["kind"]: int(k["n"]) for k in kinds}
        except Exception as exc:
            body["ready"] = False
            body["error"] = f"could not read the adjudication tables: {exc}"[:_ERROR_LIMIT]
        return body

    def admin_ready(admin: dict = Depends(current_admin)) -> dict:
        require_ready()
        return admin

    @router.get("/api/admin/disagreements/records")
    def disagreements_records(status_filter: str | None = Query(None, alias="status"),
                              admin: dict = Depends(admin_ready)):
        """Every record with both judges' answers; awaiting approval first."""
        return run(review.list_records, status_filter or None)

    @router.get("/api/admin/disagreements/records/{record_id}")
    def disagreements_record(record_id: str, admin: dict = Depends(admin_ready)):
        return run(review.detail, _record_id(record_id))

    @router.post("/api/admin/disagreements/records/{record_id}/approve")
    def disagreements_approve(record_id: str, decision: Decision, admin: dict = Depends(admin_ready)):
        """Approve the final original and outcome (or change an approved one)."""
        return run(review.approve, _record_id(record_id), admin, decision.model_dump())

    @router.post("/api/admin/disagreements/records/{record_id}/undo")
    def disagreements_undo(record_id: str, admin: dict = Depends(admin_ready)):
        return run(review.undo_approval, _record_id(record_id), admin)

    @router.post("/api/admin/disagreements/records/{record_id}/publish")
    def disagreements_publish(record_id: str, admin: dict = Depends(admin_ready)):
        """Add the approved answer to FLoRA, through Source Records."""
        return run(review.publish, _record_id(record_id), admin)

    @router.post("/api/admin/disagreements/records/{record_id}/withdraw")
    def disagreements_withdraw(record_id: str, admin: dict = Depends(admin_ready)):
        """Take a published answer out of FLoRA again."""
        return run(review.withdraw, _record_id(record_id), admin)

    @router.get("/api/admin/disagreements/export/{name}.csv")
    def disagreements_export(name: str, admin: dict = Depends(admin_ready)):
        if name not in export.EXPORTS:
            raise HTTPException(404, "No such export")
        body = run(export.export_csv, name)
        return Response(body, media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="disagreements-{name}.csv"'})

    @router.post("/api/admin/disagreements/import")
    def disagreements_import(apply: bool = Body(False, embed=True),
                             admin: dict = Depends(current_admin)):
        """Preview (apply=false, read-only) or run the import of PR #143's rows."""
        require_ready()
        try:
            return importer.run_import(cursor, apply=apply, imported_by=admin["handle"])
        except importer.ImportDataError as exc:
            raise HTTPException(422, f"The file cannot be imported: {exc}"[:_ERROR_LIMIT])
        except requests.RequestException as exc:
            raise HTTPException(502, f"Could not download the file from GitHub: {exc}"[:_ERROR_LIMIT])

    return router
