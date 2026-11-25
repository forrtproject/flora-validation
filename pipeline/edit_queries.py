"""Read-only queries on flora_record_changes shared by flora_edits and flora_store."""
from __future__ import annotations


def _table_exists(cur) -> bool:
    """The nightly job runs from the repository and can reach the database before
    the deployed site's init_db() has created the table."""
    cur.execute("SELECT to_regclass('flora_record_changes') IS NOT NULL AS present")
    return bool(cur.fetchone()["present"])


def change_watermark(cur) -> int:
    """The newest change id, which a pipeline run notes as it starts."""
    if not _table_exists(cur):
        return 0
    cur.execute("SELECT COALESCE(MAX(change_id), 0) AS v FROM flora_record_changes")
    return int(cur.fetchone()["v"])


def changed_since(cur, watermark: int) -> set:
    """The records changed after a pipeline run noted the watermark. Each change
    rewrote its own row in flora_data, so the run keeps those rows as they are."""
    if not _table_exists(cur):
        return set()
    cur.execute(
        "SELECT DISTINCT export_id FROM flora_record_changes WHERE change_id > %s",
        (int(watermark or 0),),
    )
    return {row["export_id"] for row in cur.fetchall()}
