"""Keep addon_data cache databases compact."""

from __future__ import annotations

import os
import time

import xbmcvfs

from resources.lib.common import tools
from resources.lib.database.cache import Cache
from resources.lib.modules.globals import g

# Tighter defaults — user prefers smaller on-disk caches.
API_CACHE_MAX_ROWS = 2000
API_CACHE_MAX_AGE_HOURS = 18
SYNC_META_PREFETCH_LIMIT = 250
DISPLAY_META_PREFETCH_LIMIT = 750
CATALOG_ITEMS_MAX_ROWS = 12000
CATALOG_ITEMS_MAX_AGE_DAYS = 21
PROVIDER_BLOB_TYPES = ("tmdb", "tvdb", "fanart")

VACUUM_MIN_BYTES = 4 * 1024 * 1024
VACUUM_INTERVAL_SEC = 24 * 60 * 60
STARTUP_GRACE_SEC_ANDROID = 10 * 60
STARTUP_GRACE_SEC_DEFAULT = 5 * 60

_SERVICE_STARTED_KEY = "cache_maintenance.service_started_at"
_SYNC_VACUUM_PENDING_KEY = "cache_maintenance.sync_vacuum.pending"
_SYNC_VACUUM_LAST_RUN_KEY = "cache_maintenance.sync_vacuum.last_run"
_META_VACUUM_PENDING_KEY = "cache_maintenance.meta_vacuum.pending"
_META_VACUUM_LAST_RUN_KEY = "cache_maintenance.meta_vacuum.last_run"
_ENRICH_IN_FLIGHT_KEY = "meta_enrich.in_flight"


def trim_api_cache(max_rows: int = API_CACHE_MAX_ROWS) -> int:
    """Drop oldest API cache rows when over the row cap."""
    if max_rows <= 0:
        return 0
    try:
        cache = Cache()
        removed = cache.trim_disk_rows(max_rows)
        cache.close()
        if removed:
            g.log(f"Trimmed {removed} rows from cache.db", "debug")
        return removed
    except Exception:
        g.log_stacktrace()
        return 0


def purge_stale_api_cache(max_age_hours: int = API_CACHE_MAX_AGE_HOURS) -> int:
    """Delete API cache rows older than max_age_hours even if not yet expired."""
    if max_age_hours <= 0:
        return 0
    try:
        import datetime

        cache = Cache()
        cutoff = Cache._get_timestamp(datetime.timedelta(hours=-max_age_hours))
        removed = cache.purge_disk_older_than(cutoff)
        cache.close()
        if removed:
            g.log(f"Purged {removed} stale rows from cache.db", "debug")
        return removed
    except Exception:
        g.log_stacktrace()
        return 0


def _sqlite_file_size(path: str) -> int:
    if not path or not xbmcvfs.exists(path):
        return 0
    try:
        stat = xbmcvfs.Stat(path)
        return stat.st_size() if hasattr(stat, "st_size") else 0
    except Exception:
        return 0


def _sqlite_needs_vacuum(path: str, min_bytes: int = VACUUM_MIN_BYTES) -> bool:
    """Return True when path exists and exceeds the vacuum size threshold."""
    return _sqlite_file_size(path) >= min_bytes


def _startup_grace_seconds() -> int:
    return STARTUP_GRACE_SEC_ANDROID if g.PLATFORM == "android" else STARTUP_GRACE_SEC_DEFAULT


def _vacuum_interval_elapsed(last_run_key: str) -> bool:
    last_run = g.get_float_runtime_setting(last_run_key, 0)
    if not last_run:
        return True
    return time.time() - last_run >= VACUUM_INTERVAL_SEC


def vacuum_sqlite_if_large(path: str, min_bytes: int = VACUUM_MIN_BYTES) -> bool:
    """Run VACUUM on a SQLite file when it exceeds min_bytes."""
    if not _sqlite_needs_vacuum(path, min_bytes):
        return False
    try:
        import sqlite3

        conn = sqlite3.connect(tools.translate_path(path))
        conn.execute("VACUUM")
        conn.close()
        g.log(f"Vacuumed {os.path.basename(path)}", "debug")
        return True
    except Exception:
        g.log_stacktrace()
        return False


def _queue_vacuum_if_needed(
    path: str,
    pending_key: str,
    last_run_key: str,
    min_bytes: int = VACUUM_MIN_BYTES,
) -> None:
    if not _sqlite_needs_vacuum(path, min_bytes):
        return
    if not _vacuum_interval_elapsed(last_run_key):
        return
    if g.get_bool_runtime_setting(pending_key):
        return
    g.set_runtime_setting(pending_key, True)
    size_mb = _sqlite_file_size(path) / (1024 * 1024)
    g.log(f"Deferred vacuum queued for {os.path.basename(path)} ({size_mb:.1f} MB)", "debug")


def queue_deferred_vacuums() -> None:
    """Mark sync/meta DB vacuums pending for the service idle loop."""
    _queue_vacuum_if_needed(g.SIMKL_SYNC_DB_PATH, _SYNC_VACUUM_PENDING_KEY, _SYNC_VACUUM_LAST_RUN_KEY)
    _queue_vacuum_if_needed(g.PRISM_META_DB_PATH, _META_VACUUM_PENDING_KEY, _META_VACUUM_LAST_RUN_KEY)


def service_background_idle_ready(*, block_when_enrichment_busy: bool = False) -> bool:
    """True when heavy background DB work should not contend with foreground menus."""
    if g.abort_requested():
        return False
    if g.is_addon_visible():
        return False
    if block_when_enrichment_busy:
        if g.get_bool_runtime_setting(_ENRICH_IN_FLIGHT_KEY):
            return False
        try:
            from resources.lib.meta.enrichment import MetaEnrichmentQueue

            if MetaEnrichmentQueue._has_work() and g.get_bool_runtime_setting(_ENRICH_IN_FLIGHT_KEY):
                return False
        except Exception:
            g.log_stacktrace()
            return False
    started_at = g.get_float_runtime_setting(_SERVICE_STARTED_KEY, 0)
    if started_at and time.time() - started_at < _startup_grace_seconds():
        return False
    return True


def _deferred_vacuum_idle_ready() -> bool:
    if not service_background_idle_ready(block_when_enrichment_busy=True):
        return False
    try:
        from resources.lib.meta.enrichment import MetaEnrichmentQueue

        if MetaEnrichmentQueue._has_work() and g.get_bool_runtime_setting(_ENRICH_IN_FLIGHT_KEY):
            return False
    except Exception:
        g.log_stacktrace()
        return False
    return True


def _run_pending_deferred_vacuum(path: str, pending_key: str, last_run_key: str) -> bool:
    if not g.get_bool_runtime_setting(pending_key):
        return False
    if vacuum_sqlite_if_large(path):
        g.set_runtime_setting(last_run_key, time.time())
    g.clear_runtime_setting(pending_key)
    return True


def process_idle_deferred_vacuum() -> bool:
    """Service hook: vacuum sync/meta DBs when idle and uncontended."""
    if not _deferred_vacuum_idle_ready():
        return False
    if _run_pending_deferred_vacuum(g.SIMKL_SYNC_DB_PATH, _SYNC_VACUUM_PENDING_KEY, _SYNC_VACUUM_LAST_RUN_KEY):
        return True
    if _run_pending_deferred_vacuum(g.PRISM_META_DB_PATH, _META_VACUUM_PENDING_KEY, _META_VACUUM_LAST_RUN_KEY):
        return True
    return False


def prune_non_library_provider_blobs() -> int:
    """Drop TMDB/TVDB/Fanart blobs for browse-only titles (no library engagement)."""
    removed = 0
    try:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        provider_types = ",".join(f"'{name}'" for name in PROVIDER_BLOB_TYPES)
        prune_specs = (
            (
                "movies_meta",
                """
                id IN (
                    SELECT m.tmdb_id FROM movies AS m
                    WHERE m.tmdb_id IS NOT NULL
                      AND COALESCE(m.watched, 0) = 0
                      AND m.simkl_id NOT IN (SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL)
                )
                OR id IN (
                    SELECT m.tvdb_id FROM movies AS m
                    WHERE m.tvdb_id IS NOT NULL
                      AND COALESCE(m.watched, 0) = 0
                      AND m.simkl_id NOT IN (SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL)
                )
                """,
            ),
            (
                "shows_meta",
                """
                id IN (
                    SELECT s.tmdb_id FROM shows AS s
                    WHERE s.tmdb_id IS NOT NULL
                      AND COALESCE(s.watched_episodes, 0) = 0
                      AND s.simkl_id NOT IN (SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL)
                )
                OR id IN (
                    SELECT s.tvdb_id FROM shows AS s
                    WHERE s.tvdb_id IS NOT NULL
                      AND COALESCE(s.watched_episodes, 0) = 0
                      AND s.simkl_id NOT IN (SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL)
                )
                """,
            ),
        )
        for meta_table, where in prune_specs:
            before = db.fetchone(f"SELECT COUNT(*) AS count FROM {meta_table} WHERE type IN ({provider_types})")
            db.execute_sql(f"DELETE FROM {meta_table} WHERE type IN ({provider_types}) AND ({where})")
            after = db.fetchone(f"SELECT COUNT(*) AS count FROM {meta_table} WHERE type IN ({provider_types})")
            if before and after:
                removed += max(0, int(before.get("count") or 0) - int(after.get("count") or 0))
        if removed:
            g.log(f"Pruned {removed} non-library provider blob rows", "debug")
    except Exception:
        g.log_stacktrace()
    return removed


def invalidate_paint_stamps() -> None:
    """Drop paint trust stamps so rows re-validate on next open."""
    from resources.lib.meta.paint_stamp import invalidate_paint_stamp_cache

    invalidate_paint_stamp_cache()
    try:
        from resources.lib.meta.display_store import get_display_meta_store

        get_display_meta_store().clear_paint_stamps()
    except Exception:
        g.log_stacktrace()
    try:
        from resources.lib.meta.paint_cache import clear_session_page_paint

        clear_session_page_paint()
    except Exception:
        g.log_stacktrace()
    try:
        from resources.lib.meta.list_paint import clear_show_menu_context_cache

        clear_show_menu_context_cache()
    except Exception:
        g.log_stacktrace()


def invalidate_all_menu_caches(
    *,
    include_api_cache: bool = False,
    include_library_cache: bool = True,
) -> None:
    """Clear paint, discover RAM, sync session, and library list caches."""
    invalidate_paint_stamps()
    try:
        from resources.lib.meta.display_store import get_display_meta_store

        get_display_meta_store().clear_all()
    except Exception:
        g.log_stacktrace()

    g.set_runtime_setting("sync_meta.prefetch.done", False)
    g.set_runtime_setting("simkl.activities.cached_payload", None)
    g.set_runtime_setting("simkl.activities.last_fetch", None)

    try:
        from resources.lib.database.sync_meta_cache import SyncMetaCache

        SyncMetaCache().clear_session()
    except Exception:
        g.log_stacktrace()

    if include_library_cache:
        try:
            from resources.lib.simkl.library_cache import invalidate_library_cache

            invalidate_library_cache()
        except Exception:
            g.log_stacktrace()

    try:
        from resources.lib.meta.list_pipeline import clear_ram_cache

        clear_ram_cache()
    except Exception:
        g.log_stacktrace()

    if include_api_cache:
        try:
            from resources.lib.database.simkl_sync.milling import clear_raw_episodes_cache

            clear_raw_episodes_cache()
            g.CACHE.clear_all()
            g._init_cache()
        except Exception:
            g.log_stacktrace()


def run_cache_maintenance() -> None:
    """Entry point for periodic cache size management."""
    purge_stale_api_cache()
    trim_api_cache()
    prune_non_library_provider_blobs()
    try:
        from resources.lib.discover.catalog_store import trim_stale_catalog_items

        trim_stale_catalog_items()
    except Exception:
        g.log_stacktrace()
    vacuum_sqlite_if_large(g.CACHE_DB_PATH)
    queue_deferred_vacuums()
