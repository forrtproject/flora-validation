"""HTTP routes of the adjudication feature.

Built by create_router() with the app's own sign-in dependencies and database
helper handed in, so this module never imports app.py. A route that cannot reach
the feature's tables answers with that fact instead of an error page.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends

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


def create_router(*, current_admin: Callable, status: Callable[[], SetupStatus],
                  cursor: Callable) -> APIRouter:
    """The feature's routes. *cursor* is the app's db() context manager."""
    router = APIRouter(tags=["Adjudication"])

    @router.get("/api/admin/disagreements/status")
    def disagreements_status(admin: dict = Depends(current_admin)):
        """Whether the feature is on and set up, and how far the work has got."""
        state = status()
        body = {"enabled": state.enabled, "ready": state.ready,
                "error": state.error, "counts": None}
        if not state.ready:
            return body
        try:
            with cursor() as cur:
                cur.execute(_COUNTS_SQL)
                row = cur.fetchone()
            body["counts"] = {key: int(value or 0) for key, value in dict(row).items()}
        except Exception as exc:
            body["ready"] = False
            body["error"] = f"could not read the adjudication tables: {exc}"[:_ERROR_LIMIT]
        return body

    return router
