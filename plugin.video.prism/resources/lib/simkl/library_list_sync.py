"""Per-status Simkl watchlist verify/reconcile (mirrors per-show episode watch refresh)."""
from __future__ import annotations

import threading
import time

from resources.lib.modules.globals import g
from resources.lib.simkl.library import _unwrap_sync_items, simkl_entry_to_sync_dict, sync_entry_media_blob
from resources.lib.simkl.statuses import MOVIE_STATUS_OPTIONS, SHOW_STATUS_OPTIONS

_VERIFY_COOLDOWN_SECONDS = 120
_MOVIE_STATUSES = tuple(status for status, _ in MOVIE_STATUS_OPTIONS)
_SHOW_STATUSES = tuple(status for status, _ in SHOW_STATUS_OPTIONS)
_verify_lock = threading.Lock()
_verify_scheduled: set[tuple[str, str]] = set()


def _verify_setting_key(catalog: str, status: str) -> str:
    return f"library.verify.{catalog}.{status}"


def _statuses_for_catalog(catalog: str) -> tuple[str, ...]:
    return _MOVIE_STATUSES if catalog == "movie" else _SHOW_STATUSES


def _media_key(catalog: str) -> str:
    if catalog == "movie":
        return "movies"
    if catalog == "anime":
        return "anime"
    return "shows"


def mark_library_catalog_verified(catalog: str) -> None:
    """Trust local library membership briefly after a Simkl Manager edit."""
    for status in _statuses_for_catalog(catalog):
        _mark_verified(catalog, status)


def _verify_cooldown_active(catalog: str, status: str) -> bool:
    raw = g.get_runtime_setting(_verify_setting_key(catalog, status))
    if not raw:
        return False
    try:
        return (time.time() - float(raw)) < _VERIFY_COOLDOWN_SECONDS
    except (TypeError, ValueError):
        return False


def _mark_verified(catalog: str, status: str) -> None:
    g.set_runtime_setting(_verify_setting_key(catalog, status), str(time.time()))


def library_list_needs_verify(catalog: str, status: str, *, force: bool = False) -> bool:
    """
    True when this library bucket should hit Simkl for membership verify.

    Skips API when background sync watermark matches and local cache/DB agree.
    """
    if force:
        return True

    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.library_cache import (
        _cache_is_fresh,
        _cached_refs_match_db,
        _get_cached_last_updated,
        _get_cached_refs,
        _load_refs_from_sync_db,
        get_library_sync_watermark,
        record_library_sync_watermark,
    )

    db = get_sync_database()
    stored_watermark = get_library_sync_watermark(catalog)
    current_watermark = str(db.activities.get("all_activities") or "")

    if stored_watermark and stored_watermark != current_watermark:
        return True

    db_refs = _load_refs_from_sync_db(catalog, status)
    if not db_refs:
        return current_watermark == db.base_date

    cached = _get_cached_refs(catalog, status)
    if not cached:
        if stored_watermark == current_watermark or current_watermark != db.base_date:
            if not stored_watermark:
                record_library_sync_watermark(db, catalog)
            return False
        return True

    if not _cached_refs_match_db(catalog, status, cached):
        return True

    if not _cache_is_fresh(_get_cached_last_updated(catalog, status)):
        if stored_watermark == current_watermark:
            return False
        return True

    if not stored_watermark:
        record_library_sync_watermark(db, catalog)

    from resources.lib.simkl.enrich import simkl_detail_needed
    from resources.lib.simkl.library_cache import library_status_items_from_db

    if any(
        simkl_detail_needed(item)
        for item in library_status_items_from_db(catalog, status)
        if isinstance(item, dict)
    ):
        return True

    return False


def fetch_remote_status_simkl_ids(api, catalog: str, status: str) -> set[int]:
    """Lightweight membership read for one watchlist bucket."""
    media_key = _media_key(catalog)
    payload = api.get_all_items(media_key, status=status, extended="simkl_ids_only")
    ids: set[int] = set()
    for entry in _unwrap_sync_items(payload, media_key):
        if not isinstance(entry, dict):
            continue
        blob = sync_entry_media_blob(entry, media_key)
        simkl_id = (blob.get("ids") or {}).get("simkl")
        if simkl_id is not None:
            ids.add(int(simkl_id))
    return ids


def _local_status_simkl_ids(db, catalog: str, status: str) -> set[int]:
    from resources.lib.simkl.library_cache import _load_refs_from_sync_db

    return {
        int(ref["simkl_id"])
        for ref in _load_refs_from_sync_db(catalog, status)
        if ref.get("simkl_id") is not None
    }


def _clear_local_status_membership(db, catalog: str, status: str, simkl_ids: set[int]) -> None:
    if not simkl_ids:
        return
    table = "movies" if catalog == "movie" else "shows"
    for simkl_id in simkl_ids:
        row = db.fetchone(f"SELECT simkl_status FROM {table} WHERE simkl_id=?", (int(simkl_id),))
        if not row:
            continue
        if str(row.get("simkl_status") or "").lower() != str(status).lower():
            continue
        db.set_simkl_status(int(simkl_id), catalog, None)


def _ingest_remote_status_payload(
    db,
    catalog: str,
    status: str,
    payload,
    *,
    force_meta: bool = False,
) -> None:
    media_key = _media_key(catalog)
    entries = []
    for entry in _unwrap_sync_items(payload, media_key):
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        if not row.get("status"):
            row["status"] = status
        entries.append(row)
    if not entries:
        return
    if catalog == "movie":
        movies = []
        for entry in entries:
            normalized = simkl_entry_to_sync_dict(entry, catalog)
            if normalized:
                normalized["simkl_status"] = status
                info = (normalized.get("simkl_object") or {}).get("info")
                if isinstance(info, dict):
                    info["simkl_status"] = status
                movies.append(normalized)
        if movies:
            db.insert_simkl_movies(movies, force_meta=force_meta)
        return
    shows = []
    for entry in entries:
        normalized = simkl_entry_to_sync_dict(entry, catalog)
        if normalized:
            normalized["simkl_status"] = status
            info = (normalized.get("simkl_object") or {}).get("info")
            if isinstance(info, dict):
                info["simkl_status"] = status
            shows.append(normalized)
    if shows:
        db.insert_simkl_shows(shows, force_meta=force_meta)


def _normalize_remote_status_entries(catalog: str, status: str, payload) -> list[dict]:
    """Normalize a Simkl all-items payload into ordered SyncRows for paint."""
    media_key = _media_key(catalog)
    items: list[dict] = []
    for entry in _unwrap_sync_items(payload, media_key):
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        if not row.get("status"):
            row["status"] = status
        normalized = simkl_entry_to_sync_dict(row, catalog)
        if not normalized:
            continue
        normalized["simkl_status"] = status
        info = (normalized.get("simkl_object") or {}).get("info")
        if isinstance(info, dict):
            info["simkl_status"] = status
        items.append(normalized)
    return items


def prepare_library_browse_page(catalog: str, items: list[dict]) -> list[dict]:
    """Genre-style prep: local CDN hydrate, Simkl detail for thin rows, catalog_items seed."""
    if not items:
        return items

    from resources.lib.meta.list_pipeline import seed_browse_page
    from resources.lib.simkl.enrich import (
        _has_simkl_owned_metadata,
        _row_info_art,
        enrich_sync_items_persisted,
        hydrate_sync_items_local,
        simkl_detail_needed,
    )

    hydrated = hydrate_sync_items_local(items)
    thin: list[dict] = []
    seen: set[int] = set()
    for item in hydrated:
        if not isinstance(item, dict) or item.get("simkl_id") is None:
            continue
        sid = int(item["simkl_id"])
        if sid in seen:
            continue
        info, _ = _row_info_art(item)
        if simkl_detail_needed(item) or not _has_simkl_owned_metadata(info):
            thin.append(item)
            seen.add(sid)

    if thin:
        g.log(
            f"Library browse prep: Simkl detail for {len(thin)} thin {catalog} row(s)",
            "info",
        )
        enriched = enrich_sync_items_persisted(catalog, thin)
        by_id = {
            int(row["simkl_id"]): row
            for row in enriched
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }
        hydrated = [
            by_id.get(int(row["simkl_id"]), row)
            if isinstance(row, dict) and row.get("simkl_id") is not None
            else row
            for row in hydrated
        ]

    return seed_browse_page(catalog, hydrated)


def fetch_library_status_items_from_api(catalog: str, status: str) -> list[dict]:
    """Cold-open fetch: Simkl all-items full payload → sync DB + SyncRows for paint."""
    from resources.lib.indexers.simkl import SimklAPI
    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.library_cache import (
        _load_refs_from_sync_db,
        _save_cached_refs,
        library_status_items_from_db,
        record_library_sync_watermark,
    )
    from resources.lib.simkl.library_sort import sort_library_refs

    if not SimklAPI().is_authenticated():
        return []

    db = get_sync_database()
    media_key = _media_key(catalog)
    try:
        payload = db.simkl_api.get_all_items(
            media_key,
            status=status,
            extended="full",
            next_watch_info="no",
        )
    except Exception:
        g.log_stacktrace()
        return []

    if not payload:
        return []

    _ingest_remote_status_payload(db, catalog, status, payload, force_meta=True)
    try:
        remote_ids = fetch_remote_status_simkl_ids(db.simkl_api, catalog, status)
    except Exception:
        g.log_stacktrace()
        remote_ids = {
            int(ref["simkl_id"])
            for ref in _load_refs_from_sync_db(catalog, status)
            if ref.get("simkl_id") is not None
        }

    for simkl_id in remote_ids:
        db.set_simkl_status(int(simkl_id), catalog, status)

    _mark_verified(catalog, status)
    refs = sort_library_refs(_load_refs_from_sync_db(catalog, status), catalog, status)
    if refs:
        _save_cached_refs(catalog, status, refs)
    record_library_sync_watermark(db, catalog)
    return library_status_items_from_db(catalog, status)


def schedule_library_status_verify(catalog: str, status: str) -> None:
    """Background membership verify (stale-while-revalidate — never blocks menu paint)."""
    key = (str(catalog), str(status))
    with _verify_lock:
        if key in _verify_scheduled:
            return
        _verify_scheduled.add(key)

    def _run() -> None:
        try:
            if not library_list_needs_verify(catalog, status):
                return
            refresh_library_status_list(catalog, status)
        except Exception:
            g.log_stacktrace()
        finally:
            with _verify_lock:
                _verify_scheduled.discard(key)

    from resources.lib.common.thread_pool import defer_background

    defer_background(_run, name=f"prism-library-verify-{catalog}-{status}")


def _enrich_thin_library_status_items(catalog: str, status: str) -> int:
    """Gap-fill Simkl detail for membership rows that only have simkl_id + status locally."""
    from resources.lib.simkl.enrich import enrich_sync_items_persisted, simkl_detail_needed
    from resources.lib.simkl.library_cache import library_status_items_from_db

    items = library_status_items_from_db(catalog, status)
    thin = [item for item in items if isinstance(item, dict) and simkl_detail_needed(item)]
    if not thin:
        return 0
    enrich_sync_items_persisted(catalog, thin)
    return len(thin)


def refresh_library_status_list(catalog: str, status: str, *, force: bool = False) -> bool:
    """
    Verify one My Library bucket against Simkl.

    When local membership matches remote, keep cached list rows. On drift, reconcile
    this bucket from Simkl and rebuild the local membership cache.
    """
    from resources.lib.indexers.simkl import SimklAPI

    if not SimklAPI().is_authenticated():
        return False

    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.library_cache import (
        _load_refs_from_sync_db,
        _save_cached_refs,
        record_library_sync_watermark,
    )

    db = get_sync_database()
    local_ids = _local_status_simkl_ids(db, catalog, status)
    try:
        remote_ids = fetch_remote_status_simkl_ids(db.simkl_api, catalog, status)
    except Exception:
        g.log_stacktrace()
        return False

    if not force and _verify_cooldown_active(catalog, status):
        if local_ids == remote_ids:
            return True
        g.log(
            f"Simkl library verify: {catalog}/{status} drift during cooldown "
            f"(local={len(local_ids)} remote={len(remote_ids)}) — reconciling",
            "debug",
        )

    if local_ids == remote_ids:
        g.log(
            f"Simkl library verify: {catalog}/{status} matches remote ({len(remote_ids)} items) — cache ok",
            "debug",
        )
        _mark_verified(catalog, status)
        refs = _load_refs_from_sync_db(catalog, status)
        if refs:
            _save_cached_refs(catalog, status, refs)
        record_library_sync_watermark(db, catalog)
        enriched = _enrich_thin_library_status_items(catalog, status)
        if enriched:
            g.log(
                f"Simkl library metadata: gap-filled {enriched} thin {catalog}/{status} row(s)",
                "info",
            )
        _refresh_library_list_store(catalog, status)
        return True

    g.log(
        f"Simkl library reconcile: {catalog}/{status} "
        f"local={len(local_ids)} remote={len(remote_ids)}",
        "info",
    )

    media_key = _media_key(catalog)
    removed_ids = local_ids - remote_ids
    added_ids = remote_ids - local_ids
    try:
        payload = db.simkl_api.get_all_items(
            media_key,
            status=status,
            extended="full",
            next_watch_info="no",
        )
    except Exception:
        g.log_stacktrace()
        return False

    if payload:
        _ingest_remote_status_payload(db, catalog, status, payload, force_meta=True)

    for simkl_id in remote_ids:
        db.set_simkl_status(int(simkl_id), catalog, status)

    if removed_ids:
        _clear_local_status_membership(db, catalog, status, removed_ids)

    refs = _load_refs_from_sync_db(catalog, status)
    final_ids = {int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None}
    if final_ids != remote_ids:
        g.log(
            f"Simkl library reconcile incomplete: {catalog}/{status} "
            f"db={len(final_ids)} remote={len(remote_ids)}",
            "warning",
        )
    else:
        _mark_verified(catalog, status)
    if refs:
        _save_cached_refs(catalog, status, refs)
    record_library_sync_watermark(db, catalog)
    _refresh_library_list_store(catalog, status)
    return True


def _refresh_library_list_store(catalog: str, status: str) -> None:
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.simkl.library_cache import library_status_items_from_db

    items = library_status_items_from_db(catalog, status)
    if items:
        get_list_store("library").remember_items(catalog, make_list_id(status), items)
