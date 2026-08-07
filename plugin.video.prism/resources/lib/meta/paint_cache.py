"""POV-style paint cache: read display rows without provider merge or pickle SQL."""
from __future__ import annotations

import time
from typing import Any

from resources.lib.database.sync_meta_cache import row_has_display_meta, row_has_plot_meta
from resources.lib.modules.air_date_delay import item_has_aired

_PAGE_PAINT_CACHE: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}
_PAGE_PAINT_TTL_SEC = 1800.0
_PAGE_PAINT_MAX_ENTRIES = 32


def _media_type_key(media_type: str) -> str:
    return "movie" if media_type == "movie" else "show"


def _provider_type_for(media_type: str) -> str:
    return "movie" if media_type == "movie" else "tvshow"


def _sync_table_for(media_type: str) -> str:
    return "movies" if media_type == "movie" else "shows"


def _row_has_cast(row: dict[str, Any] | None) -> bool:
    cast = row.get("cast") if isinstance(row, dict) else None
    return isinstance(cast, list) and len(cast) > 0


def sync_row_to_paint_row(sync_item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(sync_item, dict) or sync_item.get("simkl_id") is None:
        return None
    from resources.lib.simkl.field_map import sanitize_list_info

    simkl_obj = sync_item.get("simkl_object")
    if isinstance(simkl_obj, dict):
        info = simkl_obj.get("info") if isinstance(simkl_obj.get("info"), dict) else {}
        art = simkl_obj.get("art") if isinstance(simkl_obj.get("art"), dict) else {}
    else:
        info = sync_item.get("info") if isinstance(sync_item.get("info"), dict) else {}
        art = sync_item.get("art") if isinstance(sync_item.get("art"), dict) else {}
    catalog = sync_item.get("catalog")
    info = sanitize_list_info(info, catalog=catalog)
    row = {
        "simkl_id": int(sync_item["simkl_id"]),
        "info": dict(info),
        "art": dict(art),
        "cast": sync_item.get("cast") if isinstance(sync_item.get("cast"), list) else [],
    }
    for key in ("tmdb_id", "tvdb_id", "imdb_id", "args", "catalog"):
        if sync_item.get(key) is not None:
            row[key] = sync_item[key]
    return row if row_has_display_meta(row) else None


def _merge_cached_cast(rows: list[dict[str, Any]], media_type: str, db) -> list[dict[str, Any]]:
    """Attach art/cast from cached provider blobs when paint rows lack them."""
    if not rows or all(_row_has_cast(row) for row in rows):
        return rows

    from resources.lib.modules.metadataHandler import MetadataHandler

    handler = MetadataHandler()
    provider_type = _provider_type_for(media_type)
    provider_cache = db.load_cached_provider_meta_batch(_sync_table_for(media_type), rows)
    merged: list[dict[str, Any]] = []
    for row in rows:
        if _row_has_cast(row):
            merged.append(row)
            continue
        updated = handler.merge_row_from_cache(
            row,
            provider_type,
            db=db,
            provider_cache=provider_cache,
        )
        merged.append(updated if isinstance(updated, dict) else row)
    return merged


def publish_sync_rows_to_paint_store(catalog: str, sync_items: list[dict[str, Any]]) -> int:
    """Write CDN/sync rows into display_meta for instant list paint."""
    if not sync_items:
        return 0
    from resources.lib.meta.display_store import get_display_meta_store
    from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp

    store = get_display_meta_store()
    written = 0
    paint_rows: list[tuple[str, dict[str, Any]]] = []
    pending_by_media: dict[str, list[int]] = {}
    for item in sync_items:
        if not isinstance(item, dict):
            continue
        item_catalog = item.get("catalog") or catalog
        if item_catalog in ("tv", "anime"):
            paint_media = "tvshow"
        else:
            paint_media = "movie"
        sid = item.get("simkl_id")
        if sid is not None:
            cache_key = "show" if paint_media == "tvshow" else "movie"
            pending_by_media.setdefault(cache_key, []).append(int(sid))
        row = sync_row_to_paint_row(item)
        if row:
            paint_rows.append((paint_media, row))

    trusted_ids: dict[str, set[int]] = {}
    for cache_type, ids in pending_by_media.items():
        media_type = "tvshow" if cache_type == "show" else "movie"
        hits = store.get_batch(cache_type, ids)
        trusted_ids[cache_type] = {
            sid for sid, hit in hits.items() if row_has_trusted_paint_stamp(hit)
        }

    if not paint_rows:
        return 0

    by_media: dict[str, list[dict[str, Any]]] = {}
    for paint_media, row in paint_rows:
        cache_key = "show" if paint_media == "tvshow" else "movie"
        sid = int(row["simkl_id"])
        if sid in trusted_ids.get(cache_key, set()):
            continue
        by_media.setdefault(paint_media, []).append(row)
    for paint_media, batch in by_media.items():
        written += store.set_rows_batch(paint_media, batch)
    return written


def _apply_list_filters(row: dict[str, Any], media_type: str, *, hide_unaired: bool, hide_watched: bool) -> bool:
    """Return True when the row should be hidden."""
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype == "episode":
        if hide_watched and (row.get("play_count") or info.get("playcount")):
            return True
        if hide_unaired:
            air_date = row.get("air_date") or info.get("premiered") or info.get("released") or info.get("aired")
            if air_date and not item_has_aired(air_date):
                return True
        return False
    if hide_unaired:
        air_date = row.get("air_date") or info.get("premiered") or info.get("released")
        if air_date and not item_has_aired(air_date):
            return True
    if hide_watched:
        if media_type == "movie":
            if row.get("play_count") or info.get("playcount"):
                return True
        else:
            episode_count = row.get("episode_count") or info.get("episode_count") or info.get("total_episodes_count")
            watched_episodes = row.get("watched_episodes") or info.get("watched_episodes_count")
            try:
                episode_count = int(episode_count) if episode_count is not None else 0
                watched_episodes = int(watched_episodes) if watched_episodes is not None else 0
            except (TypeError, ValueError):
                episode_count = 0
                watched_episodes = 0
            if episode_count > 0 and watched_episodes >= episode_count:
                return True
    return False


def _overlay_sync_fields(rows: list[dict[str, Any]], media_type: str, db) -> list[dict[str, Any]]:
    simkl_ids = [int(row["simkl_id"]) for row in rows if row.get("simkl_id") is not None]
    if not simkl_ids:
        return rows
    overlays = db.fetch_paint_overlay_fields(media_type, simkl_ids)
    merged: list[dict[str, Any]] = []
    for row in rows:
        updated = dict(row)
        overlay = overlays.get(int(row["simkl_id"]))
        if overlay:
            overlay_cast = overlay.get("cast")
            updated.update({k: v for k, v in overlay.items() if k != "cast"})
            if overlay_cast and not _row_has_cast(updated):
                updated["cast"] = overlay_cast
            info = updated.get("info")
            if isinstance(info, dict):
                for id_key in ("tmdb_id", "tvdb_id", "imdb_id"):
                    if overlay.get(id_key) is not None and info.get(id_key) is None:
                        info[id_key] = overlay[id_key]
        merged.append(updated)
    return merged


def _should_overlay_sync(profile: str | None) -> bool:
    normalized = str(profile or "browse").lower()
    return normalized in ("library", "library_episodes", "airing")


def _prepare_paint_rows(
    rows: list[dict[str, Any]],
    media_type: str,
    db,
    *,
    profile: str = "browse",
) -> tuple[list[dict[str, Any]], list[dict]]:
    if not rows:
        return rows, []
    prepared, enrichment_refs, _stats = db.metadataHandler.prepare_list_rows_for_paint(
        rows,
        media_type,
        db=db,
        profile=profile,
        overlay_sync=_should_overlay_sync(profile),
    )
    return prepared, enrichment_refs


def page_cache_catalog(catalog: str, refs: list[dict] | None = None, *, mixed_list: bool = False) -> str:
    """Session cache bucket for a page — use mixed when refs span multiple catalogs."""
    if mixed_list:
        return "mixed"
    if refs:
        catalogs = {
            str(ref.get("catalog") or catalog)
            for ref in refs
            if isinstance(ref, dict) and ref.get("simkl_id") is not None
        }
        if len(catalogs) > 1:
            return "mixed"
    return catalog


def page_paint_cache_key(
    catalog: str,
    refs: list[dict],
    *,
    hide_unaired: bool,
    hide_watched: bool,
    paint_profile: str,
    prefer_rich_payload: bool,
) -> tuple:
    simkl_ids = tuple(
        sorted(int(ref["simkl_id"]) for ref in refs if isinstance(ref, dict) and ref.get("simkl_id") is not None)
    )
    return (catalog, simkl_ids, hide_unaired, hide_watched, paint_profile, prefer_rich_payload)


def episode_page_paint_cache_key(
    catalog: str,
    episode_simkl_ids: list[int],
    *,
    paint_profile: str,
) -> tuple:
    from resources.lib.meta.menu_paint_profile import page_paint_flags_for_profile

    flags = page_paint_flags_for_profile(paint_profile)
    return (
        "episode_page",
        catalog,
        tuple(sorted(int(i) for i in episode_simkl_ids)),
        flags["hide_unaired"],
        flags["hide_watched"],
        paint_profile,
        flags["prefer_rich_payload"],
    )


def drilldown_list_cache_key(
    show_id: int,
    scope: str,
    *,
    season: int | None = None,
    hide_unaired: bool = False,
    hide_watched: bool = False,
    hide_specials: bool = False,
    catalog_stamp: str = "",
    watch_stamp: str = "",
) -> tuple:
    season_key = ("flat",) if season is None else ("season", int(season))
    return (
        "drilldown",
        scope,
        int(show_id),
        season_key,
        hide_unaired,
        hide_watched,
        hide_specials,
        str(catalog_stamp or ""),
        str(watch_stamp or ""),
    )


def overlay_display_meta_stamps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach trusted paint stamps from display_meta onto in-memory page rows."""
    if not rows:
        return rows
    from resources.lib.meta.display_store import get_display_meta_store

    store = get_display_meta_store()
    movie_ids = [
        int(row["simkl_id"])
        for row in rows
        if isinstance(row, dict) and row.get("simkl_id") is not None and row.get("catalog") == "movie"
    ]
    show_ids = [
        int(row["simkl_id"])
        for row in rows
        if isinstance(row, dict) and row.get("simkl_id") is not None and row.get("catalog") in ("tv", "anime")
    ]
    hits: dict[int, dict[str, Any]] = {}
    if movie_ids:
        hits.update(store.get_batch("movie", movie_ids))
    if show_ids:
        hits.update(store.get_batch("show", show_ids))
    if not hits:
        return rows

    merged: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            merged.append(row)
            continue
        hit = hits.get(int(row["simkl_id"]))
        if hit and hit.get("_paint_stamp") and not row.get("_paint_stamp"):
            updated = dict(row)
            updated["_paint_stamp"] = hit["_paint_stamp"]
            merged.append(updated)
        else:
            merged.append(row)
    return merged


def _db_page_paint_store() -> dict | None:
    try:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        store = getattr(db, "_session_page_paint", None)
        if not isinstance(store, dict):
            db._session_page_paint = {}
            store = db._session_page_paint
        return store
    except Exception:
        return None


def get_session_page_paint(cache_key: tuple) -> list[dict[str, Any]] | None:
    entry = _PAGE_PAINT_CACHE.get(cache_key)
    if not entry:
        db_store = _db_page_paint_store()
        if db_store is not None:
            entry = db_store.get(cache_key)
    if not entry:
        return None
    expires_at, rows = entry
    if time.time() > expires_at:
        _PAGE_PAINT_CACHE.pop(cache_key, None)
        db_store = _db_page_paint_store()
        if db_store is not None:
            db_store.pop(cache_key, None)
        return None
    return overlay_display_meta_stamps([dict(row) for row in rows])


def set_session_page_paint(cache_key: tuple, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    if len(_PAGE_PAINT_CACHE) >= _PAGE_PAINT_MAX_ENTRIES:
        oldest_key = min(_PAGE_PAINT_CACHE, key=lambda key: _PAGE_PAINT_CACHE[key][0])
        _PAGE_PAINT_CACHE.pop(oldest_key, None)
    payload = (time.time() + _PAGE_PAINT_TTL_SEC, [dict(row) for row in rows])
    _PAGE_PAINT_CACHE[cache_key] = payload
    db_store = _db_page_paint_store()
    if db_store is not None:
        if len(db_store) >= _PAGE_PAINT_MAX_ENTRIES:
            oldest_key = min(db_store, key=lambda key: db_store[key][0])
            db_store.pop(oldest_key, None)
        db_store[cache_key] = payload


def rows_paint_all_complete(rows: list[dict[str, Any]], media_type: str, *, profile: str = "browse") -> bool:
    if not rows:
        return True
    from resources.lib.meta.paint_complete import partition_paint_rows

    _complete, incomplete = partition_paint_rows(rows, media_type, profile=profile)
    return not incomplete


def mixed_page_paint_all_complete(rows: list[dict[str, Any]], *, profile: str = "browse") -> bool:
    movies = [row for row in rows if isinstance(row, dict) and row.get("catalog") == "movie"]
    shows = [row for row in rows if isinstance(row, dict) and row.get("catalog") in ("tv", "anime")]
    if movies and not rows_paint_all_complete(movies, "movie", profile=profile):
        return False
    if shows and not rows_paint_all_complete(shows, "tvshow", profile=profile):
        return False
    return True


def paint_rows_fast_or_prepare(
    rows: list[dict[str, Any]],
    media_type: str,
    db,
    *,
    profile: str = "browse",
) -> tuple[list[dict[str, Any]], list[dict], bool]:
    """Partition rows; skip provider prepare when every row is paint-complete."""
    if not rows:
        return rows, [], True

    from resources.lib.meta.paint_complete import partition_paint_rows

    rows = overlay_display_meta_stamps(rows)
    _complete, incomplete = partition_paint_rows(rows, media_type, profile=profile)
    if incomplete:
        prepared_incomplete, enrichment_refs = _prepare_paint_rows(
            incomplete, media_type, db, profile=profile
        )
        by_id = {
            int(row["simkl_id"]): row
            for row in prepared_incomplete
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }
        merged: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict) and row.get("simkl_id") is not None:
                sid = int(row["simkl_id"])
                merged.append(by_id.get(sid, row))
            else:
                merged.append(row)
        _complete, still_incomplete = partition_paint_rows(merged, media_type, profile=profile)
        return merged, enrichment_refs, not still_incomplete

    enrichment_refs = enrichment_refs_for_paint_rows(rows, media_type)
    return rows, enrichment_refs, True


def fetch_simkl_paint_rows_batch(
    media_type: str,
    simkl_ids: list[int],
    db,
) -> dict[int, dict[str, Any]]:
    """Seren-style denormalized rows from simkl_sync RAM/DB (info, art, cast)."""
    if not simkl_ids:
        return {}

    cache_media = _media_type_key(media_type)
    from resources.lib.database.sync_meta_cache import SyncMetaCache

    meta_cache = SyncMetaCache()
    hits = meta_cache.get_many_rows(cache_media, simkl_ids)
    misses = [sid for sid in simkl_ids if sid not in hits]
    if misses:
        db_hits = db.fetch_paint_rows_batch(media_type, misses)
        if db_hits:
            meta_cache.set_many_rows(cache_media, list(db_hits.values()))
            hits.update(db_hits)
    return {
        sid: row
        for sid, row in hits.items()
        if isinstance(row, dict) and row_has_display_meta(row)
    }


def enrichment_refs_for_paint_rows(rows: list[dict[str, Any]], media_type: str) -> list[dict]:
    """Lightweight gap detection without provider blob merge."""
    refs: list[dict] = []
    from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type
    from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp
    from resources.lib.modules.metadataHandler import MetadataHandler

    handler = MetadataHandler()
    for row in rows:
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            continue
        if row_has_trusted_paint_stamp(row):
            continue
        profile = artwork_profile_for_row(row, default_media_type=media_type)
        gaps = handler._row_meta_gaps(row, provider_media_type(profile), profile)
        if gaps:
            refs.append({"simkl_id": int(row["simkl_id"]), "needs_update": True})
    return refs


def _record_paint_cache_layer(layers: set[str], prepare_skipped: bool, *, stamp_trusted: bool = False) -> None:
    try:
        from resources.lib.meta.menu_paint_profile import record_paint_cache_context

        if not prepare_skipped:
            layer = "provider"
        elif "display_meta" in layers and "simkl_sync" in layers:
            layer = "mixed"
        elif "simkl_sync" in layers:
            layer = "simkl_sync"
        elif "display_meta" in layers:
            layer = "display_meta"
        else:
            layer = "provider"
        record_paint_cache_context(layer=layer, prepare_skipped=prepare_skipped, stamp_trusted=stamp_trusted)
    except Exception:
        pass


def try_fast_paint_list(
    media_list: list[dict],
    media_type: str,
    db,
    **params,
) -> list[dict] | None:
    """
    POV-style fast path: display_meta RAM/DB for title/art, plus cached cast merge.
    """
    if not media_list:
        return []

    hide_unaired = bool(params.get("hide_unaired", db.hide_unaired))
    hide_watched = bool(params.get("hide_watched", db.hide_watched))
    profile = str(params.get("paint_profile") or "browse")
    simkl_ids = [int(item["simkl_id"]) for item in media_list if item.get("simkl_id") is not None]
    if not simkl_ids:
        return []

    from resources.lib.meta.display_store import get_display_meta_store
    from resources.lib.modules.metadataHandler import MetadataHandler

    paint_media = _media_type_key(media_type)
    store = get_display_meta_store()
    display_hits = store.get_batch(paint_media, simkl_ids)
    from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp

    missing_ids = [
        sid
        for sid in simkl_ids
        if sid not in display_hits or not row_has_trusted_paint_stamp(display_hits.get(sid))
    ]
    sync_hits: dict[int, dict[str, Any]] = {}
    if missing_ids:
        sync_hits = fetch_simkl_paint_rows_batch(media_type, missing_ids, db)
    hits = dict(sync_hits)
    hits.update(display_hits)
    if len(hits) < len(simkl_ids):
        return None

    rows: list[dict[str, Any]] = []
    used_display = False
    used_sync = False
    for ref in media_list:
        if not isinstance(ref, dict) or ref.get("simkl_id") is None:
            continue
        sid = int(ref["simkl_id"])
        row = hits.get(sid)
        if not row or not row_has_display_meta(row):
            return None
        if sid in display_hits:
            used_display = True
        elif sid in sync_hits:
            used_sync = True
        painted = dict(row)
        if ref.get("catalog"):
            painted["catalog"] = ref["catalog"]
        if _apply_list_filters(painted, media_type, hide_unaired=hide_unaired, hide_watched=hide_watched):
            continue
        rows.append(painted)

    if not rows:
        return rows

    prepared, enrichment_refs, prepare_skipped = paint_rows_fast_or_prepare(
        rows, media_type, db, profile=profile
    )
    db.set_list_enrichment_refs(
        enrichment_refs or enrichment_refs_for_paint_rows(prepared, media_type),
        media_type,
    )
    layers: set[str] = set()
    if used_display:
        layers.add("display_meta")
    if used_sync:
        layers.add("simkl_sync")
    stamp_trusted = prepare_skipped and bool(used_display) and not used_sync
    _record_paint_cache_layer(layers, prepare_skipped, stamp_trusted=stamp_trusted)
    return MetadataHandler.sort_list_items(prepared, media_list)


def paint_sync_list_rows(
    rows: list[dict[str, Any]],
    media_list: list[dict],
    media_type: str,
    db,
    **params,
) -> list[dict[str, Any]]:
    """Paint-first path for sync DB list rows (display overlay + provider merge + cast fill)."""
    if not rows:
        return rows

    from resources.lib.meta.display_store import get_display_meta_store
    from resources.lib.modules.metadataHandler import MetadataHandler
    from resources.lib.simkl.enrich import gapfill_anime_title_rows

    profile = str(params.get("paint_profile") or "browse")
    rows = get_display_meta_store().overlay_rows(rows, media_type)
    rows = gapfill_anime_title_rows(rows)
    prepared, enrichment_refs, prepare_skipped = paint_rows_fast_or_prepare(
        rows, media_type, db, profile=profile
    )
    _record_paint_cache_layer({"display_meta"}, prepare_skipped)
    db.set_list_enrichment_refs(enrichment_refs, media_type)
    return MetadataHandler.sort_list_items(prepared, media_list)


def _ensure_paint_action_args(row: dict[str, Any], catalog: str | None) -> dict[str, Any]:
    if row.get("args"):
        return row
    from resources.lib.simkl.ids import build_action_args

    updated = dict(row)
    item_catalog = catalog or row.get("catalog")
    info = updated.get("info") if isinstance(updated.get("info"), dict) else {}
    if item_catalog and not info.get("catalog"):
        info = dict(info)
        info["catalog"] = item_catalog
        updated["info"] = info
    if not info.get("mediatype"):
        info = dict(info)
        info["mediatype"] = "movie" if item_catalog == "movie" else "tvshow"
        updated["info"] = info
    updated["args"] = build_action_args(
        {
            "simkl_id": updated.get("simkl_id"),
            "catalog": item_catalog,
            "info": updated.get("info"),
            "simkl_object": {"info": updated.get("info"), "art": updated.get("art")},
        }
    )
    return updated


def _refs_for_media_type(page_refs: list[dict], media_type: str) -> list[dict]:
    if media_type == "movie":
        return [ref for ref in page_refs if ref.get("catalog") == "movie"]
    return [ref for ref in page_refs if ref.get("catalog") in ("tv", "anime")]


def _paint_media_group(
    refs: list[dict],
    payload_by_id: dict[int, dict[str, Any]],
    media_type: str,
    *,
    hide_unaired: bool,
    hide_watched: bool,
    db,
    prefer_rich_payload: bool = False,
    paint_profile: str = "browse",
) -> list[dict[str, Any]]:
    """Paint one media bucket: display_meta hit → payload seed → Seren-style prepare."""
    if not refs:
        return []

    from resources.lib.meta.display_store import get_display_meta_store
    from resources.lib.modules.metadataHandler import MetadataHandler

    paint_media = _media_type_key(media_type)
    store_type = "movie" if media_type == "movie" else "tvshow"
    store = get_display_meta_store()
    simkl_ids = [int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None]
    display_hits = store.get_batch(paint_media, simkl_ids) if simkl_ids else {}
    from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp

    missing_ids = [
        sid
        for sid in simkl_ids
        if sid not in display_hits or not row_has_trusted_paint_stamp(display_hits.get(sid))
    ]
    sync_hits = fetch_simkl_paint_rows_batch(media_type, missing_ids, db) if missing_ids else {}

    rows: list[dict[str, Any]] = []
    used_display = False
    used_sync = False
    used_payload = False
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("simkl_id") is None:
            continue
        sid = int(ref["simkl_id"])
        row = display_hits.get(sid) or sync_hits.get(sid)
        payload = payload_by_id.get(sid)
        payload_paint = sync_row_to_paint_row(payload) if payload else None
        use_cached = bool(row and row_has_display_meta(row))
        if use_cached:
            from resources.lib.meta.paint_complete import row_paint_complete

            if row_paint_complete(row, media_type, paint_profile=paint_profile):
                use_cached = True
            elif prefer_rich_payload and payload_paint and row_has_display_meta(payload_paint):
                # Library watchlist: Simkl detail enrichment wins over incomplete cached rows.
                use_cached = False
            elif prefer_rich_payload and payload_paint and row_has_plot_meta(payload_paint) and not row_has_plot_meta(row):
                use_cached = False
        elif prefer_rich_payload and payload_paint and row_has_display_meta(payload_paint):
            use_cached = False
        if use_cached:
            painted = dict(row)
            if sid in display_hits:
                used_display = True
            else:
                used_sync = True
        else:
            if not payload:
                continue
            painted = sync_row_to_paint_row(payload)
            if not painted:
                continue
            used_payload = True
            store.set_row(store_type, painted)
        if ref.get("catalog"):
            painted["catalog"] = ref["catalog"]
        painted = _ensure_paint_action_args(painted, ref.get("catalog"))
        if _apply_list_filters(painted, media_type, hide_unaired=hide_unaired, hide_watched=hide_watched):
            continue
        rows.append(painted)

    if not rows:
        return rows

    rows = overlay_display_meta_stamps(rows)
    if not missing_ids:
        prepared = rows
        enrichment_refs: list[dict] = []
        prepare_skipped = True
        try:
            from resources.lib.meta.menu_paint_profile import record_paint_cache_context, record_prepare_stats

            record_prepare_stats(
                {
                    "complete": len(prepared),
                    "incomplete": 0,
                    "prepare_skipped": 1,
                    "cast_batch": 0,
                    "art_fetch": 0,
                    "art_deduped": 0,
                    "cast_art_parallel_ms": 0.0,
                    "prepare_ms": 0.0,
                }
            )
            record_paint_cache_context(
                layer="display_meta",
                prepare_skipped=True,
                stamp_trusted=True,
            )
        except Exception:
            pass
    else:
        prepared, enrichment_refs, prepare_skipped = paint_rows_fast_or_prepare(
            rows, media_type, db, profile=paint_profile
        )
    prepared = [_ensure_paint_action_args(row, row.get("catalog")) for row in prepared]
    from resources.lib.meta.paint_stamp import attach_stamps_to_complete_rows

    prepared = attach_stamps_to_complete_rows(prepared, media_type)
    db.set_list_enrichment_refs(
        enrichment_refs or enrichment_refs_for_paint_rows(prepared, media_type),
        media_type,
    )
    layers: set[str] = set()
    if used_display:
        layers.add("display_meta")
    if used_sync:
        layers.add("simkl_sync")
    if used_payload:
        layers.add("provider")
    stamp_trusted = prepare_skipped and bool(used_display) and not used_sync and not used_payload
    _record_paint_cache_layer(layers or {"provider"}, prepare_skipped, stamp_trusted=stamp_trusted)
    return MetadataHandler.sort_list_items(prepared, refs)


def paint_discover_page_rows(
    page_refs: list[dict],
    payload_rows: list[dict],
    *,
    hide_unaired: bool = False,
    hide_watched: bool = False,
    prefer_rich_payload: bool = False,
    paint_profile: str = "browse",
) -> list[dict[str, Any]]:
    """
    POV-style discover paint: CDN payload seeds display_meta; no simkl_sync upsert.

    Resolution order per item: prism_meta display_meta → payload row → provider prepare.
    """
    if not page_refs:
        return []

    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    payload_by_id = {
        int(row["simkl_id"]): row
        for row in payload_rows or []
        if isinstance(row, dict) and row.get("simkl_id") is not None
    }

    painted: list[dict[str, Any]] = []
    movie_refs = _refs_for_media_type(page_refs, "movie")
    show_refs = _refs_for_media_type(page_refs, "tvshow")

    def _paint_bucket(refs: list[dict], media_type: str) -> list[dict[str, Any]]:
        if not refs:
            return []
        return _paint_media_group(
            refs,
            payload_by_id,
            media_type,
            hide_unaired=hide_unaired,
            hide_watched=hide_watched,
            db=db,
            prefer_rich_payload=prefer_rich_payload,
            paint_profile=paint_profile,
        )

    if movie_refs and show_refs:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            movie_future = pool.submit(_paint_bucket, movie_refs, "movie")
            show_future = pool.submit(_paint_bucket, show_refs, "tvshow")
            painted.extend(movie_future.result())
            painted.extend(show_future.result())
    else:
        painted.extend(_paint_bucket(movie_refs, "movie"))
        painted.extend(_paint_bucket(show_refs, "tvshow"))

    order = [int(ref["simkl_id"]) for ref in page_refs if ref.get("simkl_id") is not None]
    by_id = {
        int(row["simkl_id"]): row
        for row in painted
        if isinstance(row, dict) and row.get("simkl_id") is not None
    }
    ordered = [by_id[sid] for sid in order if sid in by_id]
    from resources.lib.meta.paint_stamp import attach_stamps_to_complete_rows

    movie_rows = attach_stamps_to_complete_rows(
        [row for row in ordered if row.get("catalog") == "movie"],
        "movie",
    )
    show_rows = attach_stamps_to_complete_rows(
        [row for row in ordered if row.get("catalog") in ("tv", "anime")],
        "tvshow",
    )
    stamped_by_id = {int(row["simkl_id"]): row for row in movie_rows + show_rows if row.get("simkl_id") is not None}
    return [stamped_by_id.get(sid, by_id[sid]) for sid in order if sid in by_id]


def paint_catalog_page_rows(
    page_refs: list[dict],
    payload_rows: list[dict],
    *,
    hide_unaired: bool = False,
    hide_watched: bool = False,
    prefer_rich_payload: bool = False,
    paint_profile: str = "browse",
    page_cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Paint a catalog page (discover or library) from pre-resolved SyncRows."""
    cache_key = None
    if page_cache:
        cache_key = page_paint_cache_key(
            str(page_cache.get("catalog") or ""),
            page_refs,
            hide_unaired=hide_unaired,
            hide_watched=hide_watched,
            paint_profile=paint_profile,
            prefer_rich_payload=prefer_rich_payload,
        )
        cached = get_session_page_paint(cache_key)
        if cached is not None:
            from resources.lib.meta.menu_paint_profile import record_paint_cache_context
            from resources.lib.meta.paint_complete import rows_page_paint_ready

            record_paint_cache_context(
                layer="session_page",
                prepare_skipped=rows_page_paint_ready(cached, profile=paint_profile),
                stamp_trusted=all(
                    row.get("_paint_stamp") for row in cached if isinstance(row, dict) and row.get("simkl_id")
                ),
            )
            return cached

    painted = paint_discover_page_rows(
        page_refs,
        payload_rows,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        prefer_rich_payload=prefer_rich_payload,
        paint_profile=paint_profile,
    )
    if cache_key is not None:
        set_session_page_paint(cache_key, painted)
    return painted
