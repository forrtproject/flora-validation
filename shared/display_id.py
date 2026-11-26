"""Sequential human-readable display IDs backed by source_display_counters."""
from __future__ import annotations


def next_display_id(cur, source_key: str, prefix: str) -> str:
    """Allocate the next monotonic ``PREFIX-000001`` ID for *source_key*.

    Accepts both RealDictCursor (mapping) and tuple cursors so every caller can
    share one implementation without changing its cursor factory.
    """
    cur.execute(
        """
        INSERT INTO source_display_counters (source, last_value) VALUES (%s, 1)
        ON CONFLICT (source) DO UPDATE SET last_value = source_display_counters.last_value + 1
        RETURNING last_value
        """,
        (source_key,),
    )
    row = cur.fetchone()
    value = row["last_value"] if isinstance(row, dict) else row[0]
    return f"{prefix}-{value:06d}"
