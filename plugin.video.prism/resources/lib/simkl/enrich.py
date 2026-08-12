"""Simkl detail enrichment for search and browse lists."""

from __future__ import annotations

import copy
from functools import partial

from resources.lib.database.cache import use_cache
from resources.lib.modules.globals import g
from resources.lib.simkl.catalog import resolve_item_catalog


@use_cache(cache_hours=12)
def _simkl_detail_sync_dict(simkl_id: int, catalog: str) -> dict | None:
    g.ensure_addon()
    from resources.lib.simkl.api_normalize import api_detail_to_sync_dict
    from resources.lib.simkl.related import _fetch_detail

    detail = _fetch_detail(catalog, int(simkl_id))
    if not detail:
        return None
    resolved_catalog = resolve_item_catalog(detail, catalog)
    sync = api_detail_to_sync_dict(detail, resolved_catalog)
    if sync:
        sync["catalog"] = resolved_catalog
    return sync


def _sync_dict_from_db_row(row: dict, catalog: str) -> dict | None:
    """Rebuild a sync dict from a movies/shows SQL row (no API)."""
    info = row.get("info")
    if not isinstance(info, dict) or not info:
        return None
    art = row.get("art")
    if not isinstance(art, dict):
        art = {}
    simkl_id = int(row["simkl_id"])
    sync = {
        "simkl_id": simkl_id,
        "catalog": catalog,
        "simkl_object": {"info": copy.deepcopy(info), "art": copy.deepcopy(art)},
    }
    for ext in ("tmdb_id", "tvdb_id", "imdb_id"):
        if row.get(ext) is not None:
            sync[ext] = row[ext]
    return sync


def _batch_load_sync_cache(items: list[dict]) -> dict[int, dict]:
    """Load display-ready sync rows from session cache, then simkl_sync.db for misses."""
    from resources.lib.database.sync_meta_cache import SyncMetaCache

    movie_ids: list[int] = []
    show_ids: list[int] = []
    catalog_by_id: dict[int, str] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        simkl_id = item.get("simkl_id")
        catalog = item.get("catalog")
        if simkl_id is None or catalog not in ("movie", "tv", "anime"):
            continue
        sid = int(simkl_id)
        catalog_by_id[sid] = catalog
        if catalog == "movie":
            movie_ids.append(sid)
        else:
            show_ids.append(sid)

    meta_cache = SyncMetaCache()
    cache: dict[int, dict] = {}

    movie_hits, movie_misses = meta_cache.partition_complete("movie", movie_ids)
    show_hits, show_misses = meta_cache.partition_complete("show", show_ids)

    for sid, row in movie_hits.items():
        sync = _sync_dict_from_db_row(row, "movie")
        if sync:
            cache[sid] = sync
    for sid, row in show_hits.items():
        catalog = catalog_by_id.get(sid) or "tv"
        sync = _sync_dict_from_db_row(row, catalog)
        if sync:
            cache[sid] = sync

    if movie_misses:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        placeholders = ",".join("?" * len(movie_misses))
        rows = db.fetchall(
            f"SELECT simkl_id, info, art, tmdb_id, tvdb_id, imdb_id FROM movies WHERE simkl_id IN ({placeholders})",
            tuple(movie_misses),
        )
        meta_cache.set_many_rows("movie", rows or [])
        for row in rows or []:
            sync = _sync_dict_from_db_row(row, "movie")
            if sync:
                cache[int(row["simkl_id"])] = sync

    if show_misses:
        from resources.lib.database.session import get_sync_database

        shows_db = get_sync_database()
        placeholders = ",".join("?" * len(show_misses))
        rows = shows_db.fetchall(
            f"SELECT simkl_id, info, art, tmdb_id, tvdb_id, imdb_id FROM shows WHERE simkl_id IN ({placeholders})",
            tuple(show_misses),
        )
        meta_cache.set_many_rows("show", rows or [])
        for row in rows or []:
            sid = int(row["simkl_id"])
            catalog = catalog_by_id.get(sid) or shows_db.show_catalog(sid)
            sync = _sync_dict_from_db_row(row, catalog)
            if sync:
                cache[sid] = sync

    return cache


def _is_anime_info(info: dict) -> bool:
    if not isinstance(info, dict):
        return False
    if info.get("catalog") == "anime":
        return True
    if info.get("mal_id"):
        return True
    ids = info.get("ids")
    return isinstance(ids, dict) and ids.get("mal") is not None


def gapfill_anime_title_rows(rows: list) -> list:
    """Fill anime title_en/title_romaji from stored title fields — never calls Simkl API."""
    if not rows:
        return rows

    from resources.lib.simkl.field_map import ensure_anime_title_slots

    for row in rows:
        if not isinstance(row, dict):
            continue
        info = row.get("info")
        if isinstance(info, dict):
            ensure_anime_title_slots(info)
    return rows


def _merge_sync_item_rows(base: dict, overlay: dict) -> dict:
    """Merge a thin list row into a richer cached sync dict."""
    result = copy.deepcopy(base)
    overlay_blob = overlay.get("simkl_object") or {}
    overlay_info = overlay_blob.get("info") or {}
    overlay_art = overlay_blob.get("art") or {}
    dst_blob = result.setdefault("simkl_object", {})
    dst_info = dst_blob.setdefault("info", {})
    dst_art = dst_blob.setdefault("art", {})

    for key, val in overlay_info.items():
        if val is not None and val != "" and not dst_info.get(key):
            dst_info[key] = val
    for key, val in overlay_art.items():
        if val and not dst_art.get(key):
            dst_art[key] = val
    for ext_key in ("tmdb_id", "imdb_id", "tvdb_id", "mal_id"):
        if overlay.get(ext_key) and not result.get(ext_key):
            result[ext_key] = overlay[ext_key]
        if overlay_info.get(ext_key) and not dst_info.get(ext_key):
            dst_info[ext_key] = overlay_info[ext_key]
    if overlay.get("catalog") and not result.get("catalog"):
        result["catalog"] = overlay["catalog"]
    if _is_anime_info(dst_info):
        from resources.lib.simkl.field_map import merge_anime_title_slots

        merge_anime_title_slots(dst_info, overlay_info)
    return result


def _apply_overlay_fields(src: dict, dst: dict) -> dict:
    """Preserve search/browse-only fields from the incoming row."""
    for key in ("_credit_role",):
        if key in src:
            dst[key] = src[key]

    src_info = (src.get("simkl_object") or {}).get("info") or {}
    dst_info = dst.setdefault("simkl_object", {}).setdefault("info", {})
    for key in ("score", "rank"):
        if src_info.get(key) is not None:
            dst_info[key] = src_info[key]
    if src.get("tmdb_id") is not None:
        dst["tmdb_id"] = src["tmdb_id"]
        dst_info.setdefault("tmdb_id", src["tmdb_id"])
    return dst


def _row_has_list_paint_fields(item: dict) -> bool:
    """True when a sync row already has title + poster for list paint."""
    if not isinstance(item, dict):
        return False
    blob = item.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else {}
    art = blob.get("art") if isinstance(blob.get("art"), dict) else {}
    if not info and isinstance(item.get("info"), dict):
        info = item["info"]
    if not art and isinstance(item.get("art"), dict):
        art = item["art"]
    title = info.get("title") or item.get("title")
    poster = art.get("poster") or art.get("thumb") or info.get("poster")
    return bool(title and poster)


def _anime_row_needs_title_gapfill(item: dict) -> bool:
    from resources.lib.simkl.field_map import anime_title_slots_collapsed

    blob = item.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else {}
    if not info and isinstance(item.get("info"), dict):
        info = item["info"]
    return _is_anime_info(info) and anime_title_slots_collapsed(info)


def _merge_discover_db_gaps(item: dict) -> dict:
    """Gap-fill from cached Simkl CDN discover rows when Simkl detail is thin."""
    simkl_id = item.get("simkl_id")
    catalog = item.get("catalog")
    if simkl_id is None or catalog not in ("movie", "tv", "anime"):
        return item

    if _row_has_list_paint_fields(item) and not _anime_row_needs_title_gapfill(item):
        return item

    from resources.lib.discover.catalog_store import get_items_batch

    cached = get_items_batch(catalog, [int(simkl_id)]).get(int(simkl_id))
    if cached:
        return _merge_sync_item_rows(item, cached)

    from resources.lib.discover.cdn_store import get_row

    row = get_row(catalog, int(simkl_id))
    if not row:
        return item

    from resources.lib.discover.normalize import db_row_to_sync_dict

    discover_sync = db_row_to_sync_dict(row, catalog)
    if not discover_sync:
        return item

    return _merge_sync_item_rows(item, discover_sync)


def _sync_row_display_ready(item: dict) -> bool:
    """True when Simkl detail fields are present (plot + poster at minimum)."""
    if not isinstance(item, dict):
        return False
    blob = item.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else {}
    art = blob.get("art") if isinstance(blob.get("art"), dict) else {}
    title = info.get("title") or item.get("title")
    poster = art.get("poster") or art.get("thumb") or info.get("poster")
    plot = info.get("plot") or info.get("overview")
    return bool(title and poster and plot)


def _has_simkl_owned_metadata(info: dict) -> bool:
    """True when info carries Simkl detail fields (not just title/poster/plot gap-fill)."""
    if not isinstance(info, dict) or not info:
        return False
    genres = info.get("genre") or info.get("genres")
    if genres:
        return True
    return any(str(key).startswith("rating.") for key in info)


def simkl_detail_needed(item: dict) -> bool:
    """True when a row still needs GET /movies|tv|anime/{id} detail enrichment."""
    if not _sync_row_display_ready(item):
        return True
    blob = item.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else {}
    if not info and isinstance(item.get("info"), dict):
        info = item["info"]
    if not _has_simkl_owned_metadata(info):
        return True
    if _is_anime_info(info):
        from resources.lib.simkl.field_map import anime_title_slots_collapsed

        if anime_title_slots_collapsed(info):
            return True
    return False


def _hydrate_sync_item_local(
    item: dict,
    *,
    sync_cache: dict[int, dict] | None = None,
) -> dict:
    """Merge simkl_sync.db + discover CDN gaps into a list row — no Simkl API."""
    if not isinstance(item, dict):
        return item

    simkl_id = item.get("simkl_id")
    catalog = item.get("catalog")
    if simkl_id is None or catalog not in ("movie", "tv", "anime"):
        return item

    sid = int(simkl_id)
    working = copy.deepcopy(item)
    cached = (sync_cache or {}).get(sid)
    if cached:
        working = _merge_sync_item_rows(cached, working)
    working = _merge_discover_db_gaps(working)

    blob_info = (working.get("simkl_object") or {}).get("info") or {}
    if _is_anime_info(blob_info):
        from resources.lib.simkl.field_map import ensure_anime_title_slots

        ensure_anime_title_slots(blob_info)
    return _apply_overlay_fields(item, working)


def hydrate_sync_items_local(items: list[dict]) -> list[dict]:
    """Gap-fill list rows from local simkl_sync + discover stores (no HTTP)."""
    if not items:
        return []

    rows = [item for item in items if isinstance(item, dict)]
    if not rows:
        return []

    sync_cache = _batch_load_sync_cache(rows)
    hydrator = partial(_hydrate_sync_item_local, sync_cache=sync_cache)

    if len(rows) == 1:
        return [hydrator(rows[0])]

    from resources.lib.common.thread_pool import get_shared_executor

    return list(get_shared_executor().map(hydrator, rows))


def _enrich_sync_item(
    item: dict,
    *,
    sync_cache: dict[int, dict] | None = None,
) -> dict:
    """Hydrate from sync DB, gap-fill from discover CDN, then API only when still thin."""
    if not isinstance(item, dict):
        return item

    simkl_id = item.get("simkl_id")
    catalog = item.get("catalog")
    if simkl_id is None or catalog not in ("movie", "tv", "anime"):
        return item

    sid = int(simkl_id)
    working = copy.deepcopy(item)
    cached = (sync_cache or {}).get(sid)
    if cached:
        working = _merge_sync_item_rows(cached, working)
    working = _merge_discover_db_gaps(working)

    blob_info = (working.get("simkl_object") or {}).get("info") or {}
    if _is_anime_info(blob_info):
        from resources.lib.simkl.field_map import ensure_anime_title_slots

        ensure_anime_title_slots(blob_info)
    if not simkl_detail_needed(working):
        if cached:
            g.log(f"Simkl enrich skipped API (sync cache): {sid}", "debug")
        return _apply_overlay_fields(item, working)

    from resources.lib.database.sync_meta_cache import SyncMetaCache

    meta_cache = SyncMetaCache()
    if meta_cache.is_enrich_miss(catalog, sid):
        return _apply_overlay_fields(item, working)

    enriched = _simkl_detail_sync_dict(sid, catalog)
    if not enriched:
        meta_cache.mark_enrich_miss(catalog, sid)
        return _apply_overlay_fields(item, working)

    meta_cache.clear_enrich_miss(catalog, sid)

    enriched = copy.deepcopy(enriched)
    enriched = _merge_discover_db_gaps(enriched)
    return _apply_overlay_fields(item, enriched)


def enrich_sync_items(
    items: list[dict],
    *,
    parallel: bool | None = None,
    fast: bool = False,
) -> list[dict]:
    """Fetch Simkl detail records for a page of sync dicts."""
    if not items:
        return []

    rows = [item for item in items if isinstance(item, dict)]
    if not rows:
        return []

    sync_cache = _batch_load_sync_cache(rows)
    enricher = partial(_enrich_sync_item, sync_cache=sync_cache)

    def _enrich_row(row: dict) -> dict:
        g.ensure_addon()
        return enricher(row)

    use_parallel = parallel
    if use_parallel is None:
        use_parallel = len(rows) > 1

    if not use_parallel or len(rows) == 1:
        enriched = [_enrich_row(row) for row in rows]
    else:
        from resources.lib.common.thread_pool import get_shared_executor

        enriched = list(get_shared_executor().map(_enrich_row, rows))

    enriched = [row for row in enriched if isinstance(row, dict)]
    if enriched:
        mode = "fast" if fast else "detail"
        g.log(f"Simkl {mode} enrich: {len(enriched)}/{len(rows)} items", "debug")
    return enriched


def persist_enriched_items(catalog: str, items: list[dict]) -> None:
    if not items:
        return
    from resources.lib.discover.sync_bridge import insert_discover_page
    from resources.lib.meta.paint_cache import publish_sync_rows_to_paint_store
    from resources.lib.simkl.ids import canonicalize_sync_row

    for row in items:
        canonicalize_sync_row(row)
    insert_discover_page(catalog, items, force_simkl_meta=True)
    publish_sync_rows_to_paint_store(catalog, items)


def _fetch_simkl_detail(item: dict, *, force: bool = False) -> dict:
    """Blocking Simkl detail fetch — GET /movies|tv|anime/{id} for each row when forced or thin."""
    if not isinstance(item, dict):
        return item
    if not force and not simkl_detail_needed(item):
        return item

    simkl_id = item.get("simkl_id")
    catalog = item.get("catalog")
    if simkl_id is None or catalog not in ("movie", "tv", "anime"):
        return item

    from resources.lib.simkl.api_normalize import api_detail_to_sync_dict
    from resources.lib.simkl.catalog import resolve_item_catalog
    from resources.lib.simkl.related import _fetch_detail

    sid = int(simkl_id)
    detail = _fetch_detail(catalog, sid)
    if not detail:
        g.log(f"Simkl detail: API miss {sid} ({catalog})", "debug")
        return item

    resolved_catalog = resolve_item_catalog(detail, catalog)
    enriched = api_detail_to_sync_dict(detail, resolved_catalog)
    if not enriched:
        return item

    enriched = copy.deepcopy(enriched)
    enriched["catalog"] = resolved_catalog
    merged = _merge_sync_item_rows(item, enriched)
    return _apply_overlay_fields(item, merged)


def _enrich_items(items: list[dict], *, parallel: bool, force_detail: bool = False) -> list[dict]:
    if not items:
        return []

    def _enrich_row(row: dict) -> dict:
        g.ensure_addon()
        return _fetch_simkl_detail(row, force=force_detail)

    if not parallel or len(items) == 1:
        return [_enrich_row(row) for row in items]

    from resources.lib.common.thread_pool import get_shared_executor

    with get_shared_executor() as executor:
        return list(executor.map(_enrich_row, items))


def enrich_sync_items_persisted(
    catalog: str,
    items: list[dict],
    *,
    parallel: bool | None = None,
    force_detail: bool = False,
) -> list[dict]:
    """Fetch Simkl detail rows, persist to sync DB + catalog_items + display_meta."""
    if not items:
        return items

    if force_detail:
        targets = []
        for item in items:
            if not isinstance(item, dict) or item.get("simkl_id") is None:
                continue
            row = dict(item)
            if not row.get("catalog"):
                row["catalog"] = catalog
            targets.append(row)
    else:
        targets = [item for item in items if isinstance(item, dict) and simkl_detail_needed(item)]
    if parallel is None:
        parallel = len(targets) > 1

    enriched_by_id: dict[int, dict] = {}
    if targets:
        for row in _enrich_items(targets, parallel=parallel, force_detail=force_detail):
            if isinstance(row, dict) and row.get("simkl_id") is not None:
                enriched_by_id[int(row["simkl_id"])] = row

    merged: list[dict] = []
    for item in items:
        sid = item.get("simkl_id")
        if sid is not None and int(sid) in enriched_by_id:
            merged.append(enriched_by_id[int(sid)])
        else:
            merged.append(item)

    to_persist = [row for row in merged if _sync_row_display_ready(row)]
    if to_persist:
        persist_enriched_items(catalog, to_persist)

    fetched = sum(
        1
        for row in targets
        if isinstance(row, dict)
        and row.get("simkl_id") is not None
        and not simkl_detail_needed(enriched_by_id.get(int(row["simkl_id"]), row))
    )
    g.log(
        f"Simkl detail: API {fetched}/{len(targets)}, published {len(to_persist)}/{len(items)} "
        f"({catalog}, force={force_detail})",
        "debug",
    )
    return merged


def enrich_page_for_paint(
    catalog: str,
    page_sync: list[dict],
    *,
    force_detail: bool = False,
) -> list[dict]:
    """Blocking Simkl detail fetch for the visible list page."""
    return enrich_sync_items_persisted(catalog, page_sync, force_detail=force_detail)


def enrich_sync_ids_persisted(catalog: str, simkl_ids: list[int], db=None) -> None:
    """Blocking Simkl detail fetch for sync DB ids (shared by paint queue and discover enrich)."""
    if not simkl_ids:
        return

    if db is None:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()

    ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
    table = "movies" if catalog == "movie" else "shows"
    rows = db.fetchall(
        f"""
        SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id
        FROM {table}
        WHERE simkl_id IN ({ids_sql})
        """
    )
    if not rows:
        return

    sync_items: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {
            "simkl_id": row["simkl_id"],
            "catalog": catalog,
            "simkl_object": {
                "info": row.get("info") or {},
                "art": row.get("art") or {},
                "cast": row.get("cast") or [],
            },
        }
        for ext in ("tmdb_id", "tvdb_id", "imdb_id"):
            if row.get(ext) is not None:
                item[ext] = row[ext]
        if simkl_detail_needed(item):
            sync_items.append(item)
    if sync_items:
        enrich_sync_items_persisted(catalog, sync_items)
