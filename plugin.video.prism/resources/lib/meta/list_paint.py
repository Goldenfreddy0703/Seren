"""Paint-first list pipeline: cache → ListItem → background enrich."""
from __future__ import annotations

import threading
from typing import Any

from resources.lib.simkl.menu_helpers import list_filter_kwargs

# Browse lists paint from Simkl/local cache first; cast/art enrich in background (POV-style).
BROWSE_LIST_KWARGS = {**list_filter_kwargs(), "skip_mill": True, "skip_update": True}


def browse_list_kwargs(**overrides) -> dict:
    """Shared list-builder kwargs for discover, search, library, genres, actor, etc."""
    kwargs = dict(BROWSE_LIST_KWARGS)
    kwargs.setdefault("menu_cache", True)
    kwargs.setdefault("paint_only", True)
    kwargs.update(overrides)
    return kwargs


def _resolve_catalog_paint_flags(
    list_kwargs: dict | None,
    *,
    library_paint: bool = False,
    simkl_detail_paint: bool = False,
    prefer_catalog_payload: bool = False,
) -> tuple[bool, bool, bool]:
    """Read paint-mode flags from list kwargs when callers only pass profile_list_kwargs."""
    merged = list_kwargs or {}
    return (
        library_paint or bool(merged.get("library_paint")),
        simkl_detail_paint or bool(merged.get("simkl_detail_paint")),
        merged.get("prefer_catalog_payload", prefer_catalog_payload),
    )


def attach_preloaded_catalog_paint(
    catalog: str,
    refs: list[dict],
    list_kwargs: dict | None = None,
    *,
    hide_unaired: bool | None = None,
    hide_watched: bool | None = None,
    library_paint: bool = False,
    simkl_detail_paint: bool = False,
    prefer_catalog_payload: bool = False,
    payload_rows: list[dict] | None = None,
) -> dict:
    """Resolve catalog_items rows and paint the current page before ListBuilder runs."""
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.meta.paint_cache import paint_catalog_page_rows

    merged = dict(list_kwargs or {})
    library_paint, simkl_detail_paint, prefer_catalog_payload = _resolve_catalog_paint_flags(
        merged,
        library_paint=library_paint,
        simkl_detail_paint=simkl_detail_paint,
        prefer_catalog_payload=prefer_catalog_payload,
    )
    if hide_unaired is None:
        hide_unaired = bool(merged.get("hide_unaired", False))
    if hide_watched is None:
        hide_watched = bool(merged.get("hide_watched", False))

    from resources.lib.meta.menu_paint_profile import MenuPaintProfile

    if library_paint:
        paint_profile = MenuPaintProfile.LIBRARY.value
    elif simkl_detail_paint:
        paint_profile = MenuPaintProfile.SEARCH.value
    else:
        paint_profile = MenuPaintProfile.BROWSE.value

    prefer_rich = prefer_catalog_payload or library_paint or simkl_detail_paint
    from resources.lib.meta.paint_cache import get_session_page_paint, page_cache_catalog, page_paint_cache_key

    cache_catalog = page_cache_catalog(catalog, refs, mixed_list=bool(merged.get("mixed_list")))
    session_key = page_paint_cache_key(
        cache_catalog,
        refs,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        paint_profile=paint_profile,
        prefer_rich_payload=prefer_rich,
    )
    cached_page = get_session_page_paint(session_key)
    if cached_page is not None:
        from resources.lib.meta.paint_cache import mixed_page_paint_all_complete, overlay_display_meta_stamps
        from resources.lib.meta.paint_complete import rows_page_paint_ready

        cached_page = overlay_display_meta_stamps(cached_page)
        merged["preloaded_paint_rows"] = cached_page
        merged["preloaded_paint_complete"] = rows_page_paint_ready(cached_page, profile=paint_profile) or mixed_page_paint_all_complete(
            cached_page,
            profile=paint_profile,
        )
        return merged

    if payload_rows:
        page_sync = list(payload_rows)
    elif cache_catalog == "mixed":
        page_sync = sync_items_for_mixed_refs(refs)
    else:
        page_sync = sync_items_for_refs(catalog, refs)
    if not page_sync and cache_catalog != "mixed":
        page_sync = sync_items_for_refs(catalog, refs)
    if not page_sync:
        return merged

    from resources.lib.simkl.enrich import hydrate_sync_items_local

    if payload_rows:
        # Discover page payload already carries CDN list fields; hydrating would
        # call cdn_store.get_row -> _load_store() and re-download all trending JSON.
        pass
    else:
        page_sync = hydrate_sync_items_local(page_sync)

    if library_paint or simkl_detail_paint:
        from resources.lib.simkl.enrich import enrich_page_for_paint

        page_sync = enrich_page_for_paint(
            catalog,
            page_sync,
            force_detail=simkl_detail_paint,
        )
    else:
        from resources.lib.meta.paint_cache import publish_sync_rows_to_paint_store
        from resources.lib.meta.paint_stamp import page_refs_display_stamped
        from resources.lib.simkl.enrich import simkl_detail_needed

        rich_local = [item for item in page_sync if not simkl_detail_needed(item)]
        if rich_local and not page_refs_display_stamped(refs):
            publish_sync_rows_to_paint_store(catalog, rich_local)

    painted = paint_catalog_page_rows(
        refs,
        page_sync,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        prefer_rich_payload=prefer_rich,
        paint_profile=paint_profile,
        page_cache={"catalog": cache_catalog},
    )

    from resources.lib.meta.paint_cache import mixed_page_paint_all_complete, overlay_display_meta_stamps
    from resources.lib.meta.paint_complete import rows_page_paint_ready

    painted = overlay_display_meta_stamps(painted)
    merged["preloaded_paint_rows"] = painted
    merged["preloaded_paint_complete"] = rows_page_paint_ready(painted, profile=paint_profile) or mixed_page_paint_all_complete(
        painted,
        profile=paint_profile,
    )
    return merged


def sync_items_for_mixed_refs(refs: list[dict]) -> list[dict]:
    """Resolve ordered SyncRows for refs that may span movie, tv, and anime."""
    from resources.lib.discover.catalog_store import sync_items_for_refs

    by_catalog: dict[str, list[dict]] = {}
    for ref in refs or []:
        if not isinstance(ref, dict) or ref.get("simkl_id") is None:
            continue
        item_catalog = str(ref.get("catalog") or "movie")
        by_catalog.setdefault(item_catalog, []).append(ref)
    page_sync: list[dict] = []
    for item_catalog, catalog_refs in by_catalog.items():
        page_sync.extend(sync_items_for_refs(item_catalog, catalog_refs))
    return page_sync


def render_catalog_discover_refs(
    catalog: str,
    refs: list[dict],
    list_builder,
    *,
    list_kwargs: dict | None = None,
    library_paint: bool = False,
    simkl_detail_paint: bool = False,
    prefer_catalog_payload: bool = False,
    payload_rows: list[dict] | None = None,
    **kwargs,
) -> None:
    """Paint refs from catalog_items / display_meta, then render (Simkl-owned metadata)."""
    paint_overrides = dict(list_kwargs or {})
    paint_overrides.update(kwargs)
    library_paint, simkl_detail_paint, prefer_catalog_payload = _resolve_catalog_paint_flags(
        paint_overrides,
        library_paint=library_paint,
        simkl_detail_paint=simkl_detail_paint,
        prefer_catalog_payload=prefer_catalog_payload,
    )
    merged = attach_preloaded_catalog_paint(
        catalog,
        refs,
        browse_list_kwargs(**paint_overrides),
        library_paint=library_paint,
        simkl_detail_paint=simkl_detail_paint,
        prefer_catalog_payload=prefer_catalog_payload,
        payload_rows=payload_rows,
    )
    if catalog == "movie":
        list_builder.movie_discover_builder(refs, **merged)
    elif catalog == "anime":
        list_builder.anime_discover_builder(refs, **merged)
    else:
        list_builder.show_discover_builder(refs, **merged)


def rows_to_sync_items(rows: list[dict], catalog: str) -> list[dict]:
    """Normalize SQL/API rows into SyncRows for catalog_items upsert."""
    from resources.lib.simkl.media_ref import normalize_simkl_item

    items: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("simkl_id") and isinstance(row.get("simkl_object"), dict) and row.get("catalog"):
            sid = int(row["simkl_id"])
            if sid not in seen:
                items.append(row)
                seen.add(sid)
            continue
        obj = row.get("simkl_object")
        if isinstance(obj, dict) and row.get("simkl_id") is not None and isinstance(obj.get("info"), dict):
            info = dict(obj["info"])
            art = dict(obj.get("art") or {}) if isinstance(obj.get("art"), dict) else {}
            item_catalog = row.get("catalog") or info.get("catalog") or catalog
            sync_item = {
                "simkl_id": int(row["simkl_id"]),
                "catalog": item_catalog,
                "simkl_object": {"info": info, "art": art},
            }
            for ext in ("tmdb_id", "tvdb_id", "imdb_id"):
                if row.get(ext) is not None:
                    sync_item[ext] = row[ext]
                elif info.get(ext) is not None:
                    sync_item[ext] = info[ext]
            sid = int(sync_item["simkl_id"])
            if sid not in seen:
                items.append(sync_item)
                seen.add(sid)
            continue
        if row.get("simkl_id") is not None and isinstance(row.get("info"), dict) and row["info"]:
            info = dict(row["info"])
            art = dict(row.get("art") or {}) if isinstance(row.get("art"), dict) else {}
            item_catalog = row.get("catalog") or info.get("catalog") or catalog
            sync_item = {
                "simkl_id": int(row["simkl_id"]),
                "catalog": item_catalog,
                "simkl_object": {"info": info, "art": art},
            }
            for ext in ("tmdb_id", "tvdb_id", "imdb_id"):
                if row.get(ext) is not None:
                    sync_item[ext] = row[ext]
                elif info.get(ext) is not None:
                    sync_item[ext] = info[ext]
            sid = int(sync_item["simkl_id"])
            if sid not in seen:
                items.append(sync_item)
                seen.add(sid)
            continue
        if isinstance(obj, dict):
            normalized = normalize_simkl_item(obj, row.get("catalog") or catalog)
            if normalized:
                sid = int(normalized["simkl_id"])
                if sid not in seen:
                    items.append(normalized)
                    seen.add(sid)
            continue
        show = row.get("show")
        if isinstance(show, dict):
            normalized = normalize_simkl_item(show, catalog)
            if normalized:
                sid = int(normalized["simkl_id"])
                if sid not in seen:
                    items.append(normalized)
                    seen.add(sid)
            continue
        if row.get("simkl_id") is not None and (row.get("title") or row.get("ids")):
            normalized = normalize_simkl_item(row, catalog)
            if normalized:
                sid = int(normalized["simkl_id"])
                if sid not in seen:
                    items.append(normalized)
                    seen.add(sid)
    return items


def prepare_catalog_refs(
    catalog: str,
    rows: list[dict],
    *,
    sync_items: list[dict] | None = None,
) -> list[dict]:
    """Upsert rows into catalog_items and return ordered list-builder refs."""
    from resources.lib.discover.sync_bridge import insert_discover_page, simkl_refs

    items = sync_items or rows_to_sync_items(rows, catalog)
    if items:
        insert_discover_page(catalog, items)

    refs: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("simkl_id") or row.get("simkl_show_id")
        if sid is None:
            continue
        sid = int(sid)
        if sid in seen:
            continue
        seen.add(sid)
        refs.append({"simkl_id": sid, "catalog": row.get("catalog") or catalog})
    if refs:
        return refs
    return simkl_refs(items)


def render_catalog_rows(
    catalog: str,
    rows: list[dict],
    list_builder,
    *,
    sync_items: list[dict] | None = None,
    **kwargs,
) -> None:
    """Upsert DB/API rows, paint from catalog_items, render a single-catalog menu."""
    from resources.lib.modules.globals import g

    if not rows:
        g.cancel_directory()
        return
    refs = prepare_catalog_refs(catalog, rows, sync_items=sync_items)
    render_catalog_discover_refs(catalog, refs, list_builder, **kwargs)


def attach_preloaded_catalog_paint_mixed(
    sync_items: list[dict],
    list_kwargs: dict | None = None,
) -> dict:
    """Paint a mixed movie + show/anime ref page from catalog_items."""
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.discover.sync_bridge import simkl_refs
    from resources.lib.meta.paint_cache import (
        get_session_page_paint,
        mixed_page_paint_all_complete,
        overlay_display_meta_stamps,
        page_cache_catalog,
        page_paint_cache_key,
        paint_catalog_page_rows,
    )
    from resources.lib.meta.paint_complete import rows_page_paint_ready
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.simkl.media_ref import partition_by_catalog

    merged = dict(list_kwargs or {})
    library_paint, simkl_detail_paint, prefer_catalog_payload = _resolve_catalog_paint_flags(merged)
    hide_unaired = bool(merged.get("hide_unaired", False))
    hide_watched = bool(merged.get("hide_watched", False))

    if library_paint:
        paint_profile = MenuPaintProfile.LIBRARY.value
    elif simkl_detail_paint:
        paint_profile = MenuPaintProfile.SEARCH.value
    else:
        paint_profile = str(merged.get("paint_profile") or MenuPaintProfile.BROWSE.value)

    prefer_rich = prefer_catalog_payload or library_paint or simkl_detail_paint

    movies, tv, anime = partition_by_catalog(sync_items)
    all_refs: list[dict] = []
    all_payload: list[dict] = []
    for cat, group in (("movie", movies), ("tv", tv), ("anime", anime)):
        if not group:
            continue
        refs = simkl_refs(group)
        all_refs.extend(refs)
        all_payload.extend(sync_items_for_refs(cat, refs))

    if not all_refs or not all_payload:
        return merged

    cache_catalog = page_cache_catalog("mixed", all_refs, mixed_list=True)
    session_key = page_paint_cache_key(
        cache_catalog,
        all_refs,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        paint_profile=paint_profile,
        prefer_rich_payload=prefer_rich,
    )
    cached_page = get_session_page_paint(session_key)
    if cached_page is not None:
        cached_page = overlay_display_meta_stamps(cached_page)
        merged["preloaded_paint_rows"] = cached_page
        merged["preloaded_paint_complete"] = rows_page_paint_ready(cached_page, profile=paint_profile) or mixed_page_paint_all_complete(
            cached_page,
            profile=paint_profile,
        )
        return merged

    if library_paint or simkl_detail_paint:
        from resources.lib.simkl.enrich import enrich_page_for_paint

        for cat, group in (("movie", movies), ("tv", tv), ("anime", anime)):
            if not group:
                continue
            enrich_page_for_paint(
                cat,
                [item for item in all_payload if (item.get("catalog") or cat) == cat],
                force_detail=simkl_detail_paint,
            )
    else:
        from resources.lib.meta.paint_cache import publish_sync_rows_to_paint_store
        from resources.lib.meta.paint_stamp import page_refs_display_stamped
        from resources.lib.simkl.enrich import simkl_detail_needed

        if not page_refs_display_stamped(all_refs):
            for cat, group in (("movie", movies), ("tv", tv), ("anime", anime)):
                if not group:
                    continue
                rich_local = [item for item in group if not simkl_detail_needed(item)]
                if rich_local:
                    publish_sync_rows_to_paint_store(cat, rich_local)

    painted = paint_catalog_page_rows(
        all_refs,
        all_payload,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        prefer_rich_payload=prefer_rich,
        paint_profile=paint_profile,
        page_cache={"catalog": cache_catalog},
    )
    painted = overlay_display_meta_stamps(painted)
    merged["preloaded_paint_rows"] = painted
    merged["preloaded_paint_complete"] = rows_page_paint_ready(painted, profile=paint_profile) or mixed_page_paint_all_complete(
        painted,
        profile=paint_profile,
    )
    return merged


def episode_row_simkl_ids(episode_rows: list[dict]) -> list[int]:
    """Resolve episode Simkl IDs from mixed episode menu seed rows."""
    ids: list[int] = []
    seen: set[int] = set()
    for row in episode_rows or []:
        if not isinstance(row, dict):
            continue
        simkl_id = row.get("simkl_id")
        if simkl_id is None:
            episode = row.get("episode")
            if isinstance(episode, dict):
                simkl_id = episode.get("simkl_id")
        if simkl_id is None:
            continue
        sid = int(simkl_id)
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def peek_episode_page_cache(
    catalog: str,
    episode_rows: list[dict],
    *,
    paint_profile: str,
) -> list[dict] | None:
    """Return session-cached painted episode rows when the page key matches."""
    from resources.lib.meta.paint_cache import episode_page_paint_cache_key, get_session_page_paint

    episode_ids = episode_row_simkl_ids(episode_rows)
    if not episode_ids:
        return None
    cache_key = episode_page_paint_cache_key(catalog, episode_ids, paint_profile=paint_profile)
    return get_session_page_paint(cache_key)


def attach_preloaded_episode_paint(
    catalog: str,
    episode_rows: list[dict],
    list_kwargs: dict,
    db,
    *,
    filter_params: dict | None = None,
) -> dict:
    """Paint or load a library episode page from session cache (Next Up, Watched Episodes, etc.)."""
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.meta.paint_cache import (
        episode_page_paint_cache_key,
        get_session_page_paint,
        set_session_page_paint,
    )

    merged = dict(list_kwargs or {})
    paint_profile = str(merged.get("paint_profile") or MenuPaintProfile.LIBRARY_EPISODES.value)
    overlay_parents = bool(merged.get("overlay_parent_shows", False))
    episode_ids = episode_row_simkl_ids(episode_rows)
    if not episode_ids:
        return merged

    cache_key = episode_page_paint_cache_key(catalog, episode_ids, paint_profile=paint_profile)
    cached = get_session_page_paint(cache_key)
    if cached is not None:
        merged["preloaded_episode_rows"] = cached
        return merged

    load_params = dict(filter_params or {})
    rows = db.get_mixed_episode_list(episode_rows, **load_params)
    enrichment_refs = None
    enrichment_media_type = None
    if overlay_parents:
        rows, enrichment_refs, enrichment_media_type = finish_episode_parent_paint(
            rows,
            catalog,
            paint_profile=paint_profile,
        )
    if rows:
        set_session_page_paint(cache_key, rows)
        merged["preloaded_episode_rows"] = rows
    if enrichment_refs:
        merged["enrichment_refs"] = enrichment_refs
        merged["enrichment_media_type"] = enrichment_media_type
    return merged


def upsert_episode_parent_shows(episode_rows: list[dict], catalog: str) -> None:
    """Ensure parent shows for episode menus exist in catalog_items."""
    from resources.lib.discover.sync_bridge import insert_discover_page

    sync_items = rows_to_sync_items(episode_rows, catalog)
    if sync_items:
        insert_discover_page(catalog, sync_items)


def _parent_show_refs_from_episode_rows(episode_rows: list[dict], catalog: str) -> list[dict]:
    from resources.lib.simkl.ids import show_id_from_item

    seen: set[int] = set()
    refs: list[dict] = []
    for row in episode_rows:
        show_id = show_id_from_item(row)
        if show_id is None:
            continue
        sid = int(show_id)
        if sid in seen:
            continue
        seen.add(sid)
        refs.append({"simkl_id": sid, "catalog": catalog})
    return refs


def prefetch_episode_parent_simkl_detail(episode_rows: list[dict], catalog: str) -> None:
    """Blocking Simkl GET /tv|anime/{id} for parent shows before episode rows load from DB."""
    refs = _parent_show_refs_from_episode_rows(episode_rows, catalog)

    if not refs:
        return
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.simkl.enrich import enrich_page_for_paint

    page_sync = sync_items_for_refs(catalog, refs)
    if page_sync:
        enrich_page_for_paint(catalog, page_sync)


def _warm_parent_show_cast(show_ids: list[int]) -> None:
    """Synchronous cast/art prepare for parent shows on episode library menus."""
    if not show_ids:
        return
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile

    db = get_sync_database()
    ids_sql = ",".join(str(int(sid)) for sid in show_ids)
    rows = db.fetchall(
        f"SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id FROM shows WHERE simkl_id IN ({ids_sql})"
    )
    if not rows:
        return
    db.metadataHandler.prepare_list_rows_for_paint(
        rows,
        "tvshow",
        db=db,
        profile=MenuPaintProfile.LIBRARY.value,
        overlay_sync=True,
    )


def _episode_has_duration(info: dict) -> bool:
    if not isinstance(info, dict):
        return False
    if info.get("duration"):
        return True
    from resources.lib.discover.normalize import duration_from_runtime

    if duration_from_runtime(info.get("runtime")):
        return True
    if duration_from_runtime(info.get("tvshow.runtime")):
        return True
    tvshow = info.get("tvshow")
    return isinstance(tvshow, dict) and bool(duration_from_runtime(tvshow.get("runtime")))


def _show_sync_row_has_runtime(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    blob = row.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else row.get("info")
    if not isinstance(info, dict):
        return False
    from resources.lib.discover.normalize import duration_from_runtime

    return bool(duration_from_runtime(info.get("runtime")))


def _finalize_episode_paint_rows(episode_rows: list[dict], catalog: str) -> list[dict]:
    """Convert Simkl runtime → Kodi duration and backfill from parent show detail when thin."""
    from collections import defaultdict

    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.simkl.field_map import finalize_playback_info, inherit_show_fields
    from resources.lib.simkl.ids import show_id_from_item
    from resources.lib.simkl.enrich import enrich_page_for_paint

    finalized: list[dict] = []
    for row in episode_rows:
        if not isinstance(row, dict):
            finalized.append(row)
            continue
        item = dict(row)
        info = dict(item.get("info") or {})
        finalize_playback_info(info)
        item["info"] = info
        finalized.append(item)

    missing_by_show: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(finalized):
        info = row.get("info") or {}
        if not _episode_has_duration(info):
            show_id = show_id_from_item(row)
            if show_id is not None:
                missing_by_show[int(show_id)].append(idx)

    if missing_by_show:
        refs = [{"simkl_id": sid, "catalog": catalog} for sid in missing_by_show]
        page_sync = sync_items_for_refs(catalog, refs)
        thin = [row for row in (page_sync or []) if not _show_sync_row_has_runtime(row)]
        if thin:
            enrich_page_for_paint(catalog, thin, force_detail=True)

        for show_id, indices in missing_by_show.items():
            ctx = load_show_menu_context(show_id)
            show_info = (ctx or {}).get("show_info") or {}
            if not show_info:
                continue
            for idx in indices:
                row = dict(finalized[idx])
                info = dict(row.get("info") or {})
                inherit_show_fields(info, show_info)
                finalize_playback_info(info)
                row["info"] = info
                finalized[idx] = row

    return finalized


def finish_episode_parent_paint(
    episode_rows: list[dict],
    catalog: str,
    *,
    paint_profile: str | None = None,
) -> tuple[list[dict], list[dict], str]:
    """After DB episode load: warm parent cast, overlay Simkl show metadata, queue background enrich."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.meta.paint_cache import _prepare_paint_rows
    from resources.lib.simkl.ids import show_id_from_item

    show_ids = sorted(
        {
            int(show_id_from_item(row))
            for row in episode_rows
            if show_id_from_item(row) is not None
        }
    )
    _warm_parent_show_cast(show_ids)
    painted = overlay_mixed_episode_parent_context(episode_rows)
    painted = _finalize_episode_paint_rows(painted, catalog)

    profile = paint_profile or MenuPaintProfile.LIBRARY_EPISODES.value
    db = get_sync_database()
    painted, _episode_refs = _prepare_paint_rows(painted, "episode", db, profile=profile)

    refs = [{"simkl_id": sid, "catalog": catalog} for sid in show_ids]
    return painted, refs, "tvshow"


def merge_episode_playback_overlay(rows: list[dict], seed_rows: list[dict]) -> list[dict]:
    """Apply fresh bookmark fields from menu seeds onto painted episode rows (Continue Watching)."""
    if not rows or not seed_rows:
        return rows
    by_id: dict[int, dict] = {}
    for seed in seed_rows:
        if not isinstance(seed, dict):
            continue
        simkl_id = seed.get("simkl_id")
        if simkl_id is None:
            continue
        by_id[int(simkl_id)] = seed
    if not by_id:
        return rows

    merged: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            merged.append(row)
            continue
        simkl_id = row.get("simkl_id")
        if simkl_id is None:
            merged.append(row)
            continue
        seed = by_id.get(int(simkl_id))
        if not seed:
            merged.append(row)
            continue
        painted = dict(row)
        if seed.get("percent_played") is not None:
            painted["percent_played"] = seed["percent_played"]
        progress = seed.get("resume_time")
        if progress is None:
            progress = seed.get("progress")
        if progress is not None:
            painted["resume_time"] = progress
        if seed.get("force_resume_indicator"):
            painted["force_resume_indicator"] = True
        merged.append(painted)
    return merged


def render_catalog_episodes(
    catalog: str,
    episode_rows: list[dict],
    list_builder,
    **kwargs,
) -> None:
    """Episode menus: warm parent shows in catalog_items, then paint-first episode list."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.renderer import discover_list_kwargs
    from resources.lib.modules.globals import g

    if not episode_rows:
        g.cancel_directory()
        return

    list_kwargs = discover_list_kwargs(**kwargs)
    library_paint = bool(list_kwargs.pop("library_paint", False))
    list_kwargs["catalog"] = catalog
    list_kwargs["catalog_hint"] = catalog

    from resources.lib.meta.menu_paint_profile import MenuPaintProfile

    paint_profile = str(
        list_kwargs.get("paint_profile") or MenuPaintProfile.LIBRARY_EPISODES.value
    )
    cached_rows = peek_episode_page_cache(
        catalog,
        episode_rows,
        paint_profile=paint_profile,
    )

    db = get_sync_database()
    filter_params = list_builder._apply_list_filters(dict(list_kwargs))
    if cached_rows is not None:
        list_kwargs["preloaded_episode_rows"] = merge_episode_playback_overlay(cached_rows, episode_rows)
    else:
        upsert_episode_parent_shows(episode_rows, catalog)
        if library_paint:
            prefetch_episode_parent_simkl_detail(episode_rows, catalog)
        list_kwargs = attach_preloaded_episode_paint(
            catalog,
            episode_rows,
            list_kwargs,
            db,
            filter_params=filter_params,
        )
        preloaded = list_kwargs.get("preloaded_episode_rows")
        if preloaded is not None:
            list_kwargs["preloaded_episode_rows"] = merge_episode_playback_overlay(preloaded, episode_rows)
    list_builder.mixed_episode_builder(episode_rows, **list_kwargs)


def paint_drilldown_rows(
    rows: list[dict],
    provider_media_type: str,
    db,
    *,
    hide_unaired: bool = False,
    hide_watched: bool = False,
) -> list[dict]:
    """Simkl-only season/episode drilldown paint (no provider cast/art HTTP)."""
    if not rows:
        return rows

    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.meta.paint_cache import _apply_list_filters, _prepare_paint_rows
    from resources.lib.meta.paint_complete import row_paint_complete_drilldown

    if all(row_paint_complete_drilldown(row) for row in rows):
        filtered: list[dict] = []
        for row in rows:
            if _apply_list_filters(
                row,
                provider_media_type,
                hide_unaired=hide_unaired,
                hide_watched=hide_watched,
            ):
                continue
            filtered.append(row)
        return filtered

    prepared, _ = _prepare_paint_rows(
        rows,
        provider_media_type,
        db,
        profile=MenuPaintProfile.DRILLDOWN.value,
    )

    filtered: list[dict] = []
    for row in prepared:
        if _apply_list_filters(
            row,
            provider_media_type,
            hide_unaired=hide_unaired,
            hide_watched=hide_watched,
        ):
            continue
        filtered.append(row)
    return filtered


def _drilldown_catalog_for_show(show_id: int) -> str:
    from resources.lib.database.session import get_sync_database

    return get_sync_database().show_catalog(int(show_id)) or "tv"


def _warm_drilldown_parent_show(show_id: int, catalog: str) -> None:
    """Ensure parent show row exists locally — no provider HTTP on drilldown open."""
    ctx = load_show_menu_context(show_id)
    if ctx and not _show_menu_context_sparse(ctx):
        return

    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.discover.sync_bridge import insert_discover_page

    refs = [{"simkl_id": int(show_id), "catalog": catalog}]
    page_sync = sync_items_for_refs(catalog, refs)
    if page_sync:
        insert_discover_page(catalog, page_sync)


def render_catalog_seasons(show_id: int, list_builder, **kwargs) -> None:
    """Season drilldown through the unified paint pipeline (MenuPaintProfile.DRILLDOWN)."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.globals import g

    paint_kwargs = profile_list_kwargs(MenuPaintProfile.DRILLDOWN, **kwargs)
    show_ctx = load_show_menu_context(show_id)
    if _show_menu_context_sparse(show_ctx):
        sync_row = _fetch_show_sync_row(show_id)
        if isinstance(sync_row, dict) and _show_needs_provider_enrich(sync_row):
            ensure_show_metadata(show_id)
        show_ctx = load_show_menu_context(show_id)
    set_show_menu_plugin_category(show_ctx)

    catalog = _drilldown_catalog_for_show(show_id)
    _warm_drilldown_parent_show(show_id, catalog)

    db = get_sync_database()
    query_kwargs = {
        key: paint_kwargs[key]
        for key in ("hide_unaired", "hide_watched", "hide_specials", "skip_update", "skip_mill")
        if key in paint_kwargs
    }
    rows = db.get_season_list(show_id, **query_kwargs)
    if not rows:
        g.cancel_directory()
        return

    rows = overlay_parent_context_on_rows(rows, show_ctx, show_id=int(show_id))
    rows = paint_drilldown_rows(
        rows,
        "tvshow",
        db,
        hide_unaired=bool(paint_kwargs.get("hide_unaired", False)),
        hide_watched=bool(paint_kwargs.get("hide_watched", False)),
    )
    list_builder.season_list_builder(show_id, media_list=rows, **paint_kwargs)
    from resources.lib.modules.drilldown_prefetch import schedule_show_episode_premill

    schedule_show_episode_premill(show_id, catalog)


def render_catalog_drilldown_episodes(
    show_id: int,
    list_builder,
    *,
    season: int | None = None,
    **kwargs,
) -> None:
    """Episode drilldown (season or flat) through the unified paint pipeline."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.globals import g

    paint_kwargs = profile_list_kwargs(MenuPaintProfile.DRILLDOWN, **kwargs)
    show_ctx = load_show_menu_context(show_id)
    if _show_menu_context_sparse(show_ctx):
        sync_row = _fetch_show_sync_row(show_id)
        if isinstance(sync_row, dict) and _show_needs_provider_enrich(sync_row):
            ensure_show_metadata(show_id)
        show_ctx = load_show_menu_context(show_id)
    season_ctx = load_season_menu_context(show_id, season) if season is not None else None
    set_show_menu_plugin_category(show_ctx, season_num=season)

    catalog = _drilldown_catalog_for_show(show_id)
    _warm_drilldown_parent_show(show_id, catalog)

    flat_list = season is None
    db = get_sync_database()
    query_kwargs = {
        key: paint_kwargs[key]
        for key in ("hide_unaired", "hide_watched", "hide_specials", "skip_update", "skip_mill")
        if key in paint_kwargs
    }
    if flat_list:
        query_kwargs["flat_all"] = True
    rows = db.get_episode_list(show_id, season=season, **query_kwargs)
    if not rows:
        g.cancel_directory()
        return

    rows = overlay_parent_context_on_rows(
        rows,
        show_ctx,
        season_ctx=season_ctx,
        show_id=int(show_id),
    )
    rows = paint_drilldown_rows(
        rows,
        "tvshow",
        db,
        hide_unaired=bool(paint_kwargs.get("hide_unaired", False)),
        hide_watched=bool(paint_kwargs.get("hide_watched", False)),
    )
    builder_kwargs = dict(paint_kwargs)
    if flat_list:
        builder_kwargs["no_paging"] = True
    list_builder.episode_list_builder(
        show_id,
        season=season,
        media_list=rows,
        **builder_kwargs,
    )


def _cast_is_missing(row: dict) -> bool:
    cast = row.get("cast")
    return not cast or not isinstance(cast, list) or len(cast) == 0


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _fetch_show_sync_row(simkl_id: int) -> dict[str, Any] | None:
    from resources.lib.database.session import get_sync_database

    return get_sync_database().fetchone(
        """
        SELECT simkl_id, info, art, [cast], season_count, episode_count
        FROM shows
        WHERE simkl_id = ?
        """,
        (int(simkl_id),),
    )


def _show_context_from_sync_row(row: dict[str, Any]) -> dict[str, Any]:
    sid = int(row["simkl_id"])
    info = _copy_mapping(row.get("info"))
    info.setdefault("simkl_id", sid)
    if row.get("season_count") is not None:
        info.setdefault("season_count", row["season_count"])
    if row.get("episode_count") is not None:
        info.setdefault("episode_count", row["episode_count"])
    return {
        "title": info.get("title") or info.get("tvshowtitle"),
        "show_info": info,
        "show_art": _copy_mapping(row.get("art")),
        "show_cast": list(row.get("cast") or []),
    }


def _merge_painted_show_with_sync_row(
    painted_info: dict[str, Any],
    painted_art: dict[str, Any],
    painted_cast: list,
    sync_row: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list]:
    """Fill display-cache gaps from the sync DB after display_meta migration clears."""
    from resources.lib.common import tools as merge_tools
    from resources.lib.database.sync_meta_cache import row_has_plot_meta

    db_ctx = _show_context_from_sync_row(sync_row)
    db_info = db_ctx["show_info"]
    db_art = db_ctx["show_art"]
    db_cast = db_ctx["show_cast"]

    info = merge_tools.smart_merge_dictionary(
        dict(db_info),
        dict(painted_info),
        keep_original=True,
        extend_array=False,
    )
    if not row_has_plot_meta({"info": painted_info}) and row_has_plot_meta({"info": db_info}):
        for key in ("plot", "overview", "genre", "mpaa", "status", "studio"):
            if db_info.get(key) and not info.get(key):
                info[key] = db_info[key]
        for key in db_info:
            if str(key).startswith("rating.") and not info.get(key):
                info[key] = db_info[key]

    art = merge_tools.smart_merge_dictionary(
        dict(db_art),
        dict(painted_art),
        keep_original=True,
        extend_array=False,
    )
    cast = painted_cast if painted_cast else db_cast
    return info, art, cast


def _show_menu_context_sparse(show_ctx: dict[str, Any] | None) -> bool:
    if not show_ctx:
        return True
    from resources.lib.database.sync_meta_cache import row_has_display_meta, row_has_plot_meta

    pseudo = {
        "info": show_ctx.get("show_info") or {},
        "art": show_ctx.get("show_art") or {},
    }
    if not row_has_display_meta(pseudo):
        return True
    if not row_has_plot_meta(pseudo):
        return True
    cast = show_ctx.get("show_cast")
    return not cast or not isinstance(cast, list) or len(cast) == 0


def _show_needs_provider_enrich(row: dict) -> bool:
    if _cast_is_missing(row):
        return True
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    if not info.get("plot") and not info.get("overview"):
        return True
    art = row.get("art") if isinstance(row.get("art"), dict) else {}
    return not art.get("poster") and not art.get("thumb") and not art.get("fanart")


def load_show_menu_context(simkl_show_id: int) -> dict[str, Any] | None:
    """Load parent show info/art/cast for season and episode folder menus."""
    if not simkl_show_id:
        return None

    sid = int(simkl_show_id)

    from resources.lib.database.sync_meta_cache import row_has_display_meta
    from resources.lib.meta.display_store import get_display_meta_store

    sync_row = _fetch_show_sync_row(sid)
    painted = get_display_meta_store().get_row("tvshow", sid)
    if painted and row_has_display_meta(painted):
        info = _copy_mapping(painted.get("info"))
        art = _copy_mapping(painted.get("art"))
        cast = list(painted.get("cast") or [])
        if isinstance(sync_row, dict):
            from resources.lib.database.sync_meta_cache import row_has_plot_meta

            painted_sparse = not row_has_plot_meta(painted) or _cast_is_missing(painted)
            info, art, cast = _merge_painted_show_with_sync_row(info, art, cast, sync_row)
            if painted_sparse:
                get_display_meta_store().set_row(
                    "tvshow",
                    {
                        "simkl_id": sid,
                        "info": info,
                        "art": art,
                        "cast": cast,
                    },
                )
        show_ctx = {
            "title": info.get("title") or info.get("tvshowtitle"),
            "show_info": info,
            "show_art": art,
            "show_cast": cast,
        }
        return show_ctx

    if not isinstance(sync_row, dict):
        return None

    show_ctx = _show_context_from_sync_row(sync_row)
    return show_ctx


def load_season_menu_context(simkl_show_id: int, season_num: int) -> dict[str, Any] | None:
    """Optional season art/info when drilling into a single season's episodes."""
    if not simkl_show_id or season_num is None:
        return None

    from resources.lib.database.session import get_sync_database

    row = get_sync_database().fetchone(
        """
        SELECT info, art, [cast]
        FROM seasons
        WHERE simkl_show_id = ? AND season = ?
        """,
        (int(simkl_show_id), int(season_num)),
    )
    if not isinstance(row, dict):
        return None

    return {
        "season_info": _copy_mapping(row.get("info")),
        "season_art": _copy_mapping(row.get("art")),
        "season_cast": list(row.get("cast") or []),
    }


def overlay_mixed_episode_parent_context(episode_rows: list[dict]) -> list[dict]:
    """Merge parent show info/art/cast onto mixed-episode menu rows after DB load."""
    from resources.lib.simkl.ids import show_id_from_item

    show_ctx_cache: dict[int, dict[str, Any]] = {}
    enriched: list[dict] = []
    for row in episode_rows:
        if not isinstance(row, dict):
            enriched.append(row)
            continue
        show_id = show_id_from_item(row)
        if show_id is None:
            enriched.append(row)
            continue
        sid = int(show_id)
        if sid not in show_ctx_cache:
            show_ctx_cache[sid] = load_show_menu_context(sid)
        ctx = show_ctx_cache.get(sid)
        if ctx:
            enriched.append(overlay_parent_context_on_rows([row], ctx)[0])
        else:
            enriched.append(row)
    return enriched


def _fill_missing_display_fields(target_info: dict[str, Any], show_info: dict[str, Any]) -> None:
    """Copy show-level display fields when the child row is still sparse."""
    for key in ("plot", "overview", "genre", "genres", "studio", "status", "mpaa", "rating", "runtime"):
        if not target_info.get(key) and show_info.get(key):
            target_info[key] = show_info[key]
    for key in ("title_en", "title_romaji", "originaltitle"):
        if not target_info.get(key) and show_info.get(key):
            target_info[key] = show_info[key]


def _apply_parent_show_episode_thumb(merged: dict[str, Any]) -> None:
    """Use parent show/season poster as episode list thumb when no per-episode still exists."""
    info = merged.get("info") if isinstance(merged.get("info"), dict) else {}
    if (info.get("mediatype") or "").lower() != "episode":
        return
    art = merged.setdefault("art", {})
    if art.get("thumb") or info.get("thumb"):
        return
    thumb = (
        art.get("poster")
        or art.get("season.poster")
        or art.get("tvshow.poster")
        or art.get("fanart")
        or art.get("season.fanart")
        or art.get("tvshow.fanart")
    )
    if not thumb:
        return
    art["thumb"] = thumb
    info["thumb"] = thumb


def _parent_context_stamp(show_id: int, season_num: int | None = None) -> tuple[int, int | None]:
    return (int(show_id), int(season_num) if season_num is not None else None)


def _resolve_parent_episode_thumb(
    show_art: dict[str, Any] | None,
    season_art: dict[str, Any] | None,
) -> str | None:
    show_art = show_art if isinstance(show_art, dict) else {}
    season_art = season_art if isinstance(season_art, dict) else {}
    return (
        season_art.get("poster")
        or season_art.get("thumb")
        or show_art.get("poster")
        or show_art.get("thumb")
        or show_art.get("fanart")
    )


def _overlay_episode_row_fast(
    row: dict,
    *,
    show_info: dict[str, Any] | None,
    show_art: dict[str, Any] | None,
    season_info: dict[str, Any] | None,
    season_art: dict[str, Any] | None,
    parent_thumb: str | None,
    stamp: tuple[int, int | None],
) -> dict:
    item = dict(row)
    info = dict(item.get("info") or {})
    art = dict(item.get("art") or {})
    if show_info:
        if not info.get("tvshowtitle"):
            info["tvshowtitle"] = show_info.get("title")
        if not info.get("tmdb_show_id") and show_info.get("tmdb_id"):
            info["tmdb_show_id"] = show_info.get("tmdb_id")
        if not info.get("tvdb_show_id") and show_info.get("tvdb_id"):
            info["tvdb_show_id"] = show_info.get("tvdb_id")
        if not info.get("year") and show_info.get("year"):
            info["year"] = show_info.get("year")
        _fill_missing_display_fields(info, show_info)
    if season_info and not info.get("simkl_season_id"):
        info["simkl_season_id"] = season_info.get("simkl_id")
    if parent_thumb and not art.get("thumb") and not info.get("thumb"):
        art["thumb"] = parent_thumb
        info["thumb"] = parent_thumb
    item["info"] = info
    item["art"] = art
    item["_parent_ctx"] = stamp
    return item


def overlay_parent_context_on_rows(
    rows: list[dict],
    show_ctx: dict[str, Any] | None,
    *,
    season_ctx: dict[str, Any] | None = None,
    show_id: int | None = None,
) -> list[dict]:
    """Attach parent show (and optional season) metadata to season/episode menu rows."""
    if not rows or not show_ctx:
        return rows

    show_info = show_ctx.get("show_info")
    show_art = show_ctx.get("show_art")
    show_cast = show_ctx.get("show_cast")
    season_info = (season_ctx or {}).get("season_info")
    season_art = (season_ctx or {}).get("season_art")
    season_cast = (season_ctx or {}).get("season_cast")
    resolved_show_id = show_id
    if resolved_show_id is None and isinstance(show_info, dict):
        resolved_show_id = show_info.get("simkl_id")
    season_num = season_info.get("season") if isinstance(season_info, dict) else None
    stamp = _parent_context_stamp(int(resolved_show_id), season_num) if resolved_show_id else None

    if stamp and all(isinstance(row, dict) and row.get("_parent_ctx") == stamp for row in rows):
        return rows

    episode_only = all(
        isinstance(row, dict) and (row.get("info") or {}).get("mediatype", "").lower() == "episode"
        for row in rows
    )
    parent_thumb = _resolve_parent_episode_thumb(show_art, season_art) if episode_only else None

    if episode_only and stamp:
        return [
            _overlay_episode_row_fast(
                row,
                show_info=show_info,
                show_art=show_art,
                season_info=season_info,
                season_art=season_art,
                parent_thumb=parent_thumb,
                stamp=stamp,
            )
            if isinstance(row, dict)
            else row
            for row in rows
        ]

    from resources.lib.modules.metadataHandler import MetadataHandler

    enriched: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            enriched.append(row)
            continue

        item = dict(row)
        item["info"] = _copy_mapping(item.get("info"))
        item["art"] = _copy_mapping(item.get("art"))
        item.setdefault("cast", item.get("cast") or [])

        merged = {"info": item["info"], "art": item["art"], "cast": list(item.get("cast") or [])}
        MetadataHandler._show_season_art_fallback(merged, season_art, show_art)
        MetadataHandler._add_season_show_info(merged, season_info, show_info)
        MetadataHandler._add_season_show_art(merged, season_art, show_art)
        MetadataHandler._add_season_show_cast(merged, season_cast, show_cast)
        if show_info:
            _fill_missing_display_fields(merged["info"], show_info)
        _apply_parent_show_episode_thumb(merged)

        item["info"] = merged["info"]
        item["art"] = merged["art"]
        if merged.get("cast"):
            item["cast"] = merged["cast"]
        if show_info:
            mediatype = (merged["info"].get("mediatype") or "").lower()
            if mediatype != "episode":
                for count_key in ("season_count", "episode_count"):
                    if show_info.get(count_key) is not None:
                        item.setdefault(count_key, show_info[count_key])
        if stamp:
            item["_parent_ctx"] = stamp
        enriched.append(item)

    return enriched


def set_show_menu_plugin_category(
    show_ctx: dict[str, Any] | None,
    *,
    season_num: int | None = None,
) -> None:
    """Set Kodi folder header from parent show (and season when applicable)."""
    if not show_ctx:
        return
    title = show_ctx.get("title")
    if not title:
        return

    import xbmcplugin

    from resources.lib.modules.globals import g

    label = str(title)
    if season_num is not None:
        label = f"{label} - Season {int(season_num)}"
    xbmcplugin.setPluginCategory(g.PLUGIN_HANDLE, label)


def ensure_show_metadata(simkl_show_id: int) -> None:
    """Fetch missing cast/art for one show (blocking, used on season drill-in)."""
    if not simkl_show_id:
        return
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    row = db.fetchone(
        """
        SELECT simkl_id, info, [cast], art, tmdb_id, tvdb_id, imdb_id, last_updated
        FROM shows
        WHERE simkl_id = ?
        """,
        (int(simkl_show_id),),
    )
    if not isinstance(row, dict):
        return
    if not _show_needs_provider_enrich(row):
        return

    handler = db.metadataHandler
    merged, refs = handler.merge_list_meta_local([row], "tvshow", db=db)
    if not refs:
        refs = [{"simkl_id": int(simkl_show_id), "needs_update": True, "_provider_type": "tvshow"}]
    handler.enrich_list_meta_online(refs, "tvshow", db=db, persist=True)


def ensure_show_metadata_async(simkl_show_id: int) -> None:
    """Warm cast/art for a show the user is opening — does not block the menu."""
    if not simkl_show_id:
        return

    from resources.lib.modules.globals import g

    def _run() -> None:
        try:
            ensure_show_metadata(simkl_show_id)
        except Exception:
            g.log_stacktrace()

    threading.Thread(target=_run, daemon=True, name=f"prism-show-meta-{int(simkl_show_id)}").start()
