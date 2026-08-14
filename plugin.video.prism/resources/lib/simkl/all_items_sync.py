"""Watchlist foundation sync via ``GET /sync/all-items`` (one call per phase).

``extended=full,full_anime_seasons`` loads per-episode watch-state arrays plus anime
TVDB season mapping — not episode catalog metadata (titles/thumbs/plot come from
``GET /tv/episodes/{id}`` via episode warm). Show overview/fanart/genres/ratings come
from Simkl detail endpoints (see ``enrich.enrich_page_for_paint``).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from resources.lib.modules.globals import g
from resources.lib.simkl.episode_catalog_sync import (
    bucket_progress_label,
    episode_warm_enabled,
    run_post_sync_episode_warm,
)
from resources.lib.simkl.library import _unwrap_sync_items, simkl_entry_to_sync_dict
from resources.lib.simkl.statuses import MOVIE_STATUS_OPTIONS, SHOW_STATUS_OPTIONS

if TYPE_CHECKING:
    from resources.lib.database.simkl_sync.activities import SimklSyncDatabase

# Comma-separated extended flags: episode arrays + runtime + anime TVDB season mapping.
ALL_ITEMS_EXTENDED = "full,full_anime_seasons"
EPISODE_PRUNE_AFTER_SYNC = True

_MOVIE_STATUSES = tuple(status for status, _ in MOVIE_STATUS_OPTIONS)
_SHOW_STATUSES = tuple(status for status, _ in SHOW_STATUS_OPTIONS)


def _completed_or_dropped_changed(remote_activities, local_watermark: str) -> bool:
    """True when completed/dropped lists moved since our last all-items watermark."""
    from resources.lib.database.simkl_sync.database import SimklSyncDatabase

    for section in ("movies", "tv_shows", "anime"):
        section_data = remote_activities.get(section) or {}
        for status in ("completed", "dropped"):
            ts = section_data.get(status)
            if ts and SimklSyncDatabase.requires_update(ts, local_watermark):
                return True
    return False


def build_all_items_params(*, date_from: str, include_all_episodes: bool = False) -> dict:
    """Simkl all-items params — always paired with date_from.

    ``include_all_episodes`` synthesizes watched-episode rows for completed/dropped
    shows (watch-state only). Episode display metadata still comes from episode catalog
    milling, not sync.
    """
    params = {
        "date_from": date_from,
        "extended": ALL_ITEMS_EXTENDED,
        "next_watch_info": "yes",
        "episode_watched_at": "yes",
        "episode_tvdb_id": "yes",
    }
    if include_all_episodes:
        params["include_all_episodes"] = "yes"
    return params


def fetch_all_items(api, *, date_from: str, include_all_episodes: bool = False):
    params = build_all_items_params(
        date_from=date_from,
        include_all_episodes=include_all_episodes,
    )
    url = "/sync/all-items/"
    query = dict(params)
    if hasattr(api, "_cdn_query"):
        query.update(api._cdn_query())
    response = api.get(url, **query)
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def ingest_payload(db: "SimklSyncDatabase", payload, *, is_delta: bool = False) -> None:
    """Unified all-items ingest — foundation and delta share one code path."""
    if not payload:
        return

    if not is_delta:
        if _unwrap_sync_items(payload, "movies"):
            db.set_sync_progress(15, g.get_language_string(31001))
        if _unwrap_sync_items(payload, "shows"):
            db.set_sync_progress(35, g.get_language_string(31002))
        if _unwrap_sync_items(payload, "anime"):
            db.set_sync_progress(50, g.get_language_string(31003))

    db._process_all_items_payload(payload)

    if EPISODE_PRUNE_AFTER_SYNC:
        db.prune_library_episodes()
    db.set_sync_progress(60, g.get_language_string(31010))
    _seed_display_meta_from_payload(db, payload)
    _upsert_catalog_items_from_payload(payload)
    rebuild_library_cache_from_db(db)
    phase = "delta" if is_delta else "foundation"
    g.log(f"Simkl all-items ingest complete ({phase})", "info")


def _reapply_episode_watch_state_after_warm(db: "SimklSyncDatabase", payload) -> None:
    """Episode catalog warm upserts rows with watched=0; restore Simkl watch flags afterward."""
    if not payload:
        return
    for catalog, media_key in (("tv", "shows"), ("anime", "anime")):
        entries = _unwrap_sync_items(payload, media_key)
        if not entries:
            continue
        shows = []
        for entry in entries:
            normalized = simkl_entry_to_sync_dict(entry, catalog)
            if normalized:
                shows.append(normalized)
        if shows:
            db.reapply_episode_watch_state_from_entries(entries, shows)


def rebuild_library_cache_from_db(db: "SimklSyncDatabase" | None = None) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.library_cache import _save_cached_refs

    db = db or get_sync_database()
    for status in _MOVIE_STATUSES:
        refs = db.get_movies_by_simkl_status(status)
        for ref in refs:
            ref["catalog"] = "movie"
        _save_cached_refs("movie", status, refs)
    for catalog in ("tv", "anime"):
        for status in _SHOW_STATUSES:
            refs = db.get_shows_by_simkl_status(status, catalog=catalog)
            for ref in refs:
                ref["catalog"] = catalog
            _save_cached_refs(catalog, status, refs)


def _upsert_catalog_items_from_payload(payload) -> None:
    from resources.lib.discover.catalog_store import upsert_sync_items

    items: list[dict] = []
    for entry in _unwrap_sync_items(payload, "movies"):
        normalized = simkl_entry_to_sync_dict(entry, "movie")
        if normalized:
            items.append(normalized)
    for catalog, media_key in (("tv", "shows"), ("anime", "anime")):
        for entry in _unwrap_sync_items(payload, media_key):
            normalized = simkl_entry_to_sync_dict(entry, catalog)
            if normalized:
                items.append(normalized)
    if items:
        upsert_sync_items(items)


def _seed_display_meta_from_payload(db: "SimklSyncDatabase", payload) -> None:
    """Seed title/poster from lean sync rows; plot/fanart filled on menu open."""
    from resources.lib.meta.display_store import get_display_meta_store
    from resources.lib.meta.storage import slim_art_dict, slim_info_dict
    from resources.lib.modules.metadataHandler import MetadataHandler

    store = get_display_meta_store()
    rows_by_type: dict[str, list[dict]] = {"movie": [], "tvshow": []}
    for entry in _unwrap_sync_items(payload, "movies"):
        normalized = simkl_entry_to_sync_dict(entry, "movie")
        if not normalized:
            continue
        simkl_obj = normalized.get("simkl_object") or {}
        info = slim_info_dict(MetadataHandler.simkl_info(normalized) or {}, simkl=True)
        art = slim_art_dict(MetadataHandler.art(simkl_obj) or {}, "movie")
        if info or art:
            rows_by_type["movie"].append({"simkl_id": normalized["simkl_id"], "info": info, "art": art, "cast": []})
    for catalog, media_key in (("tv", "shows"), ("anime", "anime")):
        art_type = "anime" if catalog == "anime" else "tvshow"
        for entry in _unwrap_sync_items(payload, media_key):
            normalized = simkl_entry_to_sync_dict(entry, catalog)
            if not normalized:
                continue
            simkl_obj = normalized.get("simkl_object") or {}
            info = slim_info_dict(MetadataHandler.simkl_info(normalized) or {}, simkl=True)
            art = slim_art_dict(MetadataHandler.art(simkl_obj) or {}, art_type)
            if info or art:
                rows_by_type["tvshow"].append({"simkl_id": normalized["simkl_id"], "info": info, "art": art, "cast": []})
    for media_type, rows in rows_by_type.items():
        if rows:
            store.set_many_rows(media_type, rows)


def _episode_warm_progress(db: "SimklSyncDatabase", current: int, total: int, _title: str, bucket: str) -> None:
    menu = bucket_progress_label(bucket)
    message = g.get_language_string(31011).format(menu, current, total)
    percent = 70 + int(25 * current / max(total, 1))
    db.set_sync_progress(percent, message)


def _finalize_episode_sync_state(
    db: "SimklSyncDatabase",
    payload,
    *,
    force: bool = False,
    blocking_warm: bool = False,
) -> None:
    """Post-ingest episode catalog warm + watch-state restore (single hook)."""
    if episode_warm_enabled():
        db.set_sync_progress(65, g.get_language_string(31012))
        run_post_sync_episode_warm(
            db,
            payload=payload,
            force=force,
            notify_silent=db.silent,
            on_progress=(
                (lambda c, t, title, bucket: _episode_warm_progress(db, c, t, title, bucket))
                if blocking_warm
                else None
            ),
        )
    _reapply_episode_watch_state_after_warm(db, payload)


_LIBRARY_WATCH_STATUSES = frozenset({"watching", "plantowatch", "completed", "hold", "dropped"})


def _find_show_entry(payload, media_key: str, show_id: int) -> dict | None:
    from resources.lib.database.simkl_sync.database import SimklSyncDatabase

    for entry in _unwrap_sync_items(payload, media_key):
        if SimklSyncDatabase._entry_show_simkl_id(entry) == show_id:
            return entry
    return None


def build_repair_watch_params(*, include_all_episodes: bool = False) -> dict:
    """Full watch-state params for one-show repair (no date_from — deltas omit episode arrays)."""
    params = {
        "extended": ALL_ITEMS_EXTENDED,
        "next_watch_info": "yes",
        "episode_watched_at": "yes",
        "episode_tvdb_id": "yes",
    }
    if include_all_episodes:
        params["include_all_episodes"] = "yes"
    return params


def _library_entry_from_sync_watched(item: dict, catalog: str) -> dict | None:
    """Convert POST /sync/watched response row into all-items-style library entry."""
    if not isinstance(item, dict) or not item.get("result") or item.get("result") == "not_found":
        return None
    show_id = item.get("simkl")
    if show_id is None:
        return None
    entry: dict = {
        "status": item.get("list"),
        "watched_episodes_count": item.get("episodes_watched"),
        "total_episodes_count": item.get("episodes_total"),
        "last_watched_at": item.get("last_watched_at"),
        "seasons": [],
    }
    blob = {"ids": {"simkl": int(show_id)}}
    if catalog == "anime":
        entry["anime"] = blob
    else:
        entry["show"] = blob
    for season in item.get("seasons") or []:
        if not isinstance(season, dict) or season.get("number") is None:
            continue
        episodes = []
        for episode in season.get("episodes") or []:
            if not isinstance(episode, dict) or episode.get("number") is None:
                continue
            ep_row = {"number": int(episode["number"])}
            if episode.get("watched") in (True, 1, "true", "True"):
                ep_row["watched"] = True
            if episode.get("last_watched_at"):
                ep_row["watched_at"] = episode["last_watched_at"]
            episodes.append(ep_row)
        entry["seasons"].append({"number": int(season["number"]), "episodes": episodes})
    return entry


def fetch_show_watch_entry_from_sync_watched(api, show_id: int, catalog: str) -> dict | None:
    """Per-show watch repair via POST /sync/watched?extended=episodes,specials."""
    response = api.post_json(
        "/sync/watched",
        [{"ids": {"simkl": int(show_id)}}],
        extended="episodes,specials",
    )
    if not isinstance(response, list) or not response:
        return None
    item = response[0]
    if not isinstance(item, dict):
        return None
    return _library_entry_from_sync_watched(item, catalog)


def _episode_watch_cooldown_active(show_id: int) -> bool:
    cooldown_raw = g.get_runtime_setting(f"episode_watch_refresh_cooldown_{show_id}")
    if not cooldown_raw:
        return False
    try:
        return (time.time() - float(cooldown_raw)) < 120
    except (TypeError, ValueError):
        return False


def _local_watched_episode_keys(db: "SimklSyncDatabase", show_id: int) -> set[tuple[int, int]]:
    rows = db.fetchall(
        """
        SELECT season, number
        FROM episodes
        WHERE simkl_show_id=? AND season != 0 AND COALESCE(watched, 0) > 0
        """,
        (show_id,),
    )
    return {(int(row["season"]), int(row["number"])) for row in rows or []}


def _remote_watched_episode_keys(db: "SimklSyncDatabase", entry: dict) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for season in entry.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        season_num = season.get("number") if season.get("number") is not None else season.get("season")
        if season_num is None:
            continue
        for episode in season.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            ep_num = episode.get("number") if episode.get("number") is not None else episode.get("episode")
            if ep_num is None:
                continue
            if db._episode_marked_watched(episode, entry):
                keys.add((int(season_num), int(ep_num)))
    return keys


def _show_watch_state_matches(
    db: "SimklSyncDatabase",
    *,
    show_id: int,
    entry: dict,
    local_status: str,
    resolved_status: str,
) -> bool:
    """True when local DB watch state already matches Simkl (safe to use cached menus)."""
    remote_watched = int(entry.get("watched_episodes_count") or 0)
    local_watched = len(_local_watched_episode_keys(db, show_id))
    remote_status = str(entry.get("status") or "").strip().lower()

    if resolved_status and remote_status and resolved_status != remote_status:
        return False
    if local_status and remote_status and local_status != remote_status:
        return False
    if local_watched != remote_watched:
        return False

    if db._entry_has_full_episode_watch_detail(entry):
        return _local_watched_episode_keys(db, show_id) == _remote_watched_episode_keys(db, entry)

    row = db.fetchone("SELECT watched_episodes FROM shows WHERE simkl_id=?", (show_id,))
    show_watched = int((row or {}).get("watched_episodes") or 0)
    if show_watched > 0 and remote_watched > 0 and show_watched != remote_watched:
        return False
    return True


def _reconcile_show_watch_entry(
    db: "SimklSyncDatabase",
    show_id: int,
    entry: dict,
    catalog: str,
    status: str,
) -> int:
    """Apply Simkl watch entry to the local DB; return watched episode row count."""
    media_key = "anime" if catalog == "anime" else "shows"
    if not db._entry_has_full_episode_watch_detail(entry) and not db._entry_has_per_episode_watch_rows(entry):
        if status in _LIBRARY_WATCH_STATUSES:
            params = build_repair_watch_params(
                include_all_episodes=status in ("completed", "dropped"),
            )
            payload = db.simkl_api.get_all_items(media_key, status=status, **params)
            richer = _find_show_entry(payload, media_key, show_id)
            if richer and (
                db._entry_has_full_episode_watch_detail(richer)
                or db._entry_has_per_episode_watch_rows(richer)
            ):
                entry = richer

    normalized = simkl_entry_to_sync_dict(entry, catalog)
    if not normalized:
        return 0

    shows = [normalized]
    has_per_episode = db._entry_has_full_episode_watch_detail(entry) or db._entry_has_per_episode_watch_rows(entry)
    simkl_watched_count = int(entry.get("watched_episodes_count") or 0)
    local_episode_rows = int(
        (db.fetchone(
            "SELECT COUNT(*) AS c FROM episodes WHERE simkl_show_id=? AND season != 0",
            (show_id,),
        ) or {}).get("c")
        or 0
    )
    if has_per_episode and local_episode_rows == 0:
        db.apply_sync_episode_stubs_from_entries([entry], shows, catalog, selective=True)

    db._apply_entry_watch_state([entry], shows)
    local_watched_after = len(_local_watched_episode_keys(db, show_id))
    if has_per_episode and simkl_watched_count > 0 and local_watched_after < simkl_watched_count:
        db.apply_sync_episode_stubs_from_entries([entry], shows, catalog, selective=True)
        db._apply_entry_watch_state([entry], shows)
        local_watched_after = len(_local_watched_episode_keys(db, show_id))
    if (
        not has_per_episode
        and simkl_watched_count > 0
        and local_watched_after < simkl_watched_count
        and local_watched_after == 0
    ):
        local_watched_after = max(
            local_watched_after,
            db.apply_watched_progress_from_entry(entry),
        )
    db.apply_show_watch_counters([entry])
    if status == "completed":
        db.apply_completed_show_watch_flags([entry])
    if catalog == "anime":
        db.prune_orphan_anime_seasons(show_id)
    db._refresh_show_and_season_statistics(show_id)
    return local_watched_after


def refresh_show_episode_watch_state(db: "SimklSyncDatabase", simkl_show_id: int, *, force: bool = False) -> bool:
    """Validate per-show watch state against Simkl; reconcile only when local data drifted."""
    show_id = int(simkl_show_id)
    row = db.fetchone(
        "SELECT simkl_status, watched_episodes, episode_count FROM shows WHERE simkl_id=?",
        (show_id,),
    )
    if not row:
        return False

    catalog = db.show_catalog(show_id) or "tv"
    local_status = str(row.get("simkl_status") or "").strip().lower()

    if not force and _episode_watch_cooldown_active(show_id):
        return len(_local_watched_episode_keys(db, show_id)) > 0

    entry = fetch_show_watch_entry_from_sync_watched(db.simkl_api, show_id, catalog)
    if not entry:
        return False

    remote_status = str(entry.get("status") or "").strip().lower()
    remote_watched = int(entry.get("watched_episodes_count") or 0)
    if remote_status not in _LIBRARY_WATCH_STATUSES and remote_watched <= 0:
        return False

    # List bucket membership is owned by library_list_sync (/sync/all-items).
    # /sync/watched is only used here for episode-level watch flags.
    resolved_status = local_status
    if not resolved_status and remote_status in _LIBRARY_WATCH_STATUSES:
        resolved_status = remote_status

    if not force and _show_watch_state_matches(
        db,
        show_id=show_id,
        entry=entry,
        local_status=local_status,
        resolved_status=resolved_status,
    ):
        g.log(
            f"Simkl watch verify: show {show_id} matches remote ({remote_watched} watched) — cache ok",
            "debug",
        )
        return remote_watched > 0 or len(_local_watched_episode_keys(db, show_id)) > 0

    local_watched_after = _reconcile_show_watch_entry(db, show_id, entry, catalog, resolved_status)
    try:
        from resources.lib.meta.paint_cache import clear_session_page_paint_for_show

        clear_session_page_paint_for_show(show_id)
    except Exception:
        pass
    g.log(
        f"Simkl watch reconcile: show {show_id} ({resolved_status or 'watching'}) -> "
        f"{local_watched_after} watched episode(s)",
        "info",
    )
    return local_watched_after > 0


def sync_simkl_library(db: "SimklSyncDatabase", remote_activities, *, force: bool = False) -> None:
    first_sync = str(db.activities["all_activities"]) == db.base_date
    date_from = db.base_date if first_sync else str(db.activities["all_activities"])
    local_watermark = str(db.activities["all_activities"])
    include_all_episodes = first_sync or _completed_or_dropped_changed(remote_activities, local_watermark)
    blocking_warm = not db.silent

    g.log(
        f"Simkl all-items: extended={ALL_ITEMS_EXTENDED}, "
        f"include_all_episodes={'yes' if include_all_episodes else 'no'}, "
        f"phase={'foundation' if first_sync else 'delta'}",
        "debug",
    )

    db.set_sync_progress(5, g.get_language_string(31000))
    payload = fetch_all_items(
        db.simkl_api,
        date_from=date_from,
        include_all_episodes=include_all_episodes,
    )
    if payload:
        ingest_payload(db, payload, is_delta=not first_sync)

    if db._removed_from_list_changed(remote_activities):
        db._reconcile_removed_items()
        rebuild_library_cache_from_db(db)

    _finalize_episode_sync_state(
        db,
        payload,
        force=force,
        blocking_warm=blocking_warm,
    )
    db.set_sync_progress(100, g.get_language_string(31013))
