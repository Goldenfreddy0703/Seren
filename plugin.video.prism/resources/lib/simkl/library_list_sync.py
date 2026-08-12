"""Per-status Simkl watchlist verify/reconcile (mirrors per-show episode watch refresh)."""
from __future__ import annotations

import time

from resources.lib.modules.globals import g
from resources.lib.simkl.library import _unwrap_sync_items, simkl_entry_to_sync_dict, sync_entry_media_blob
from resources.lib.simkl.statuses import MOVIE_STATUS_OPTIONS, SHOW_STATUS_OPTIONS

_VERIFY_COOLDOWN_SECONDS = 120
_MOVIE_STATUSES = tuple(status for status, _ in MOVIE_STATUS_OPTIONS)
_SHOW_STATUSES = tuple(status for status, _ in SHOW_STATUS_OPTIONS)


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
        invalidate_library_cache,
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

    invalidate_library_cache(catalog)
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
    return True
