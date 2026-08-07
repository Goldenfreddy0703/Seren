"""Helpers for library-only simkl_sync paint writeback."""
from __future__ import annotations

from typing import Any


def fetch_library_simkl_ids(simkl_ids: list[int], db=None) -> set[int]:
    """Return simkl_ids that belong to the user's library (watched, bookmarked, or synced)."""
    if not simkl_ids:
        return set()
    if db is None:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()

    ids_sql = ",".join(str(int(sid)) for sid in simkl_ids)
    library: set[int] = set()
    for table, watched_col in (("movies", "watched"), ("shows", "watched_episodes")):
        try:
            rows = db.fetchall(
                f"""
                SELECT simkl_id FROM {table}
                WHERE simkl_id IN ({ids_sql})
                  AND (
                    COALESCE({watched_col}, 0) > 0
                    OR simkl_status IS NOT NULL
                    OR simkl_id IN (SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL)
                  )
                """
            )
            library.update(int(row["simkl_id"]) for row in rows or [] if row.get("simkl_id") is not None)
        except Exception:
            from resources.lib.modules.globals import g

            g.log_stacktrace()
    return library


def filter_library_paint_rows(rows: list[dict[str, Any]], db=None) -> list[dict[str, Any]]:
    """Keep only rows that should be mirrored into simkl_sync."""
    if not rows:
        return []
    simkl_ids = [int(row["simkl_id"]) for row in rows if isinstance(row, dict) and row.get("simkl_id") is not None]
    if not simkl_ids:
        return []
    library_ids = fetch_library_simkl_ids(simkl_ids, db=db)
    if not library_ids:
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict) and row.get("simkl_id") is not None and int(row["simkl_id"]) in library_ids
    ]
