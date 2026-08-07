"""Otaku-style watchlist cache: Simkl API membership + local DB metadata."""
from __future__ import annotations

import time

ACTIVITY_CHECK_SECONDS = 120
CACHE_HOURS_FALLBACK = 24


def ensure_library_cache_tables(db=None) -> None:
    from resources.lib.database.session import get_sync_database

    db = db or get_sync_database()
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS library_status_cache (
            catalog TEXT NOT NULL,
            status TEXT NOT NULL,
            simkl_id INTEGER NOT NULL,
            item_order INTEGER NOT NULL,
            last_updated INTEGER NOT NULL,
            PRIMARY KEY (catalog, status, simkl_id)
        )
        """
    )
    db.execute_sql(
        """
        CREATE INDEX IF NOT EXISTS idx_library_status_cache_list
        ON library_status_cache(catalog, status, item_order)
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS library_status_activity (
            catalog TEXT PRIMARY KEY NOT NULL,
            activity_timestamp TEXT,
            last_checked INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def invalidate_library_cache(catalog: str | None = None) -> None:
    """Drop cached membership lists after a local status change."""
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    if catalog:
        db.execute_sql("DELETE FROM library_status_cache WHERE catalog=?", (catalog,))
    else:
        db.execute_sql("DELETE FROM library_status_cache")


def _cache_is_fresh(last_updated: int | None, hours: float = CACHE_HOURS_FALLBACK) -> bool:
    if not last_updated:
        return False
    return (time.time() - int(last_updated)) < (hours * 3600)


def _get_cached_last_updated(catalog: str, status: str) -> int | None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    row = db.fetchone(
        """
        SELECT MIN(last_updated) AS last_updated
        FROM library_status_cache
        WHERE catalog=? AND status=?
        """,
        (catalog, status),
    )
    if not row or row.get("last_updated") is None:
        return None
    return int(row["last_updated"])


def _get_cached_refs(catalog: str, status: str) -> list[dict]:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    rows = db.fetchall(
        """
        SELECT simkl_id
        FROM library_status_cache
        WHERE catalog=? AND status=?
        ORDER BY item_order ASC
        """,
        (catalog, status),
    )
    return [{"simkl_id": int(row["simkl_id"]), "catalog": catalog} for row in rows]


def _save_cached_refs(catalog: str, status: str, refs: list[dict]) -> None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    now = int(time.time())
    rows = [
        (catalog, status, int(ref["simkl_id"]), order, now)
        for order, ref in enumerate(refs)
        if ref.get("simkl_id") is not None
    ]
    db.execute_sql(
        "DELETE FROM library_status_cache WHERE catalog=? AND status=?",
        (catalog, status),
    )
    if rows:
        db.execute_sql(
            """
            INSERT INTO library_status_cache
                (catalog, status, simkl_id, item_order, last_updated)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def _stored_activity(catalog: str) -> dict | None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    return db.fetchone(
        "SELECT activity_timestamp, last_checked FROM library_status_activity WHERE catalog=?",
        (catalog,),
    )


def _save_activity_check(catalog: str, activity_timestamp: str | None) -> None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_library_cache_tables(db)
    db.execute_sql(
        """
        INSERT INTO library_status_activity (catalog, activity_timestamp, last_checked)
        VALUES (?, ?, ?)
        ON CONFLICT(catalog) DO UPDATE SET
            activity_timestamp=excluded.activity_timestamp,
            last_checked=excluded.last_checked
        """,
        (catalog, activity_timestamp, int(time.time())),
    )


def _remote_library_activity_timestamp(catalog: str) -> str | None:
    from resources.lib.simkl.remote_activities import library_activity_timestamp

    return library_activity_timestamp(catalog)


def _activity_requires_refresh(catalog: str) -> bool:
    stored = _stored_activity(catalog)
    if stored and stored.get("last_checked"):
        if time.time() - int(stored["last_checked"]) < ACTIVITY_CHECK_SECONDS:
            return False

    remote_ts = _remote_library_activity_timestamp(catalog)
    _save_activity_check(catalog, remote_ts)

    if not remote_ts:
        return False

    if not stored or not stored.get("activity_timestamp"):
        return True

    return str(stored["activity_timestamp"]) != str(remote_ts)


def should_refresh_library_cache(catalog: str, status: str) -> bool:
    cached_refs = _get_cached_refs(catalog, status)
    if not cached_refs:
        return True
    if _activity_requires_refresh(catalog):
        return True
    return not _cache_is_fresh(_get_cached_last_updated(catalog, status))


def _schedule_library_refresh(catalog: str, status: str) -> None:
    import xbmc

    from resources.lib.common import tools
    from resources.lib.modules.globals import g

    url = g.create_url(
        g.BASE_URL,
        {
            "action": "refreshLibraryCache",
            "action_args": tools.construct_action_args({"catalog": catalog, "status": status}),
        },
    )
    xbmc.executebuiltin(f'RunPlugin("{url}")')


def refresh_library_cache_background(catalog: str | None, status: str | None) -> None:
    """Rebuild library membership cache from local sync DB (no Simkl API)."""
    from resources.lib.simkl.all_items_sync import rebuild_library_cache_from_db

    rebuild_library_cache_from_db()


def _load_refs_from_sync_db(catalog: str, status: str) -> list[dict]:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    if catalog == "movie":
        refs = db.get_movies_by_simkl_status(status)
        for ref in refs:
            ref["catalog"] = "movie"
        return refs

    refs = db.get_shows_by_simkl_status(status, catalog=catalog)
    for ref in refs:
        ref["catalog"] = catalog
    return refs


def load_library_list_refs(catalog: str, status: str) -> list[dict]:
    """
    Return list-builder refs for a My Library status list.

    Membership comes from local simkl_sync.db (populated by activities all-items sync).
    No per-status Simkl API calls on menu open.
    """
    from resources.lib.modules.globals import g
    from resources.lib.modules.widget_loader import mark_widget_session_loaded
    from resources.lib.simkl.library_sort import sort_library_refs
    from resources.lib.simkl.library_status import queue_library_sync

    activity_stale = _activity_requires_refresh(catalog)
    if activity_stale:
        queue_library_sync(user_initiated=False)

    if g.FROM_WIDGET and mark_widget_session_loaded(f"library.{catalog}.{status}"):
        cached = _get_cached_refs(catalog, status)
        if cached:
            return sort_library_refs(cached, catalog)

    cached = _get_cached_refs(catalog, status)
    cache_fresh = cached and _cache_is_fresh(_get_cached_last_updated(catalog, status))
    if cache_fresh and not activity_stale:
        return sort_library_refs(cached, catalog)

    if cached and not activity_stale and not cache_fresh:
        _schedule_library_refresh(catalog, status)
        return sort_library_refs(cached, catalog)

    refs = sort_library_refs(_load_refs_from_sync_db(catalog, status), catalog)
    if refs:
        _save_cached_refs(catalog, status, refs)
    return refs
