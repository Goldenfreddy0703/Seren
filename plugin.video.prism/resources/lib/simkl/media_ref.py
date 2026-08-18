"""Unified Simkl media reference model for Prism list + playback pipelines.

Three layers (keep these separate):

1. **SyncRow** — inserted into ``simkl_sync.db``
   ``{simkl_id, catalog, tmdb_id?, imdb_id?, simkl_object: {info, art}}``

2. **MenuRow** — Kodi list item payload
   ``{info, art, cast, args, simkl_id?}``  (``args`` = ActionArgs dict; encode only at URL)

3. **ActionArgs** — minimal play/navigation contract on URLs
   ``{simkl_id, mediatype, catalog?, season?}``
   Stored in DB as plain JSON via :func:`serialize_action_args`; URL-encoded via
   :func:`encode_action_args` / :func:`tools.construct_action_args` only at boundaries.

Enrichment order (Simkl-first):
  Simkl CDN / REST detail → discover DB (MDBList columns) → TMDB → TVDB (on read/mill)

All list entry points should funnel through this module before :class:`ListBuilder`.
"""
from __future__ import annotations

from typing import Any, Callable

from resources.lib.discover.normalize import db_row_to_sync_dict
from resources.lib.discover.sync_bridge import insert_discover_page, paginate_items, simkl_refs
from resources.lib.database.cache import use_cache
from resources.lib.simkl.api_normalize import api_detail_to_sync_dict
from resources.lib.simkl.catalog import resolve_item_catalog
from resources.lib.simkl.enrich import enrich_sync_items

_CATALOG_TO_SIMKL_TYPE = {
    "movie": "movies",
    "tv": "shows",
    "anime": "anime",
}

_SIMKL_SINGULAR = {
    "movies": "movie",
    "shows": "show",
    "anime": "anime",
}

_SEARCH_SIMKL_TYPE = {
    "movie": "movie",
    "tv": "tv",
    "anime": "anime",
}


def normalize_simkl_item(raw: dict[str, Any], catalog_hint: str = "") -> dict[str, Any] | None:
    """Normalize any Simkl CDN/search/library/related row into a SyncRow."""
    if not isinstance(raw, dict):
        return None
    catalog = resolve_item_catalog(raw, catalog_hint or "")
    from resources.lib.discover.normalize import cdn_item_to_sync_dict

    return cdn_item_to_sync_dict(raw, catalog)


def normalize_simkl_items(
    rows: list[dict[str, Any]],
    catalog_hint: str = "",
) -> list[dict[str, Any]]:
    """Batch normalize, skipping invalid rows."""
    items: list[dict[str, Any]] = []
    for row in rows:
        sync = normalize_simkl_item(row, catalog_hint)
        if sync:
            items.append(sync)
    return items


def normalize_discover_db_row(row: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    """Normalize a ``simkl_cdn.db`` SQL row."""
    return db_row_to_sync_dict(row, catalog)


def normalize_discover_db_rows(rows: list[dict[str, Any]], catalog: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        sync = normalize_discover_db_row(row, catalog)
        if sync:
            items.append(sync)
    return items


def normalize_api_detail(payload: dict[str, Any] | None, catalog: str) -> dict[str, Any] | None:
    """Normalize ``GET /movies|tv|anime/{id}`` JSON."""
    return api_detail_to_sync_dict(payload, catalog)


def _unwrap_sync_items(payload: Any, media_key: str) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if media_key in payload and isinstance(payload[media_key], list):
            return payload[media_key]
        for key in ("movies", "shows", "anime", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _simkl_media_blob(entry: dict, media_key: str) -> dict | None:
    for key in (media_key, "movie", "show", "anime"):
        blob = entry.get(key)
        if isinstance(blob, dict):
            return blob
    if entry.get("ids") or entry.get("title"):
        return entry
    return None


def normalize_library_entry(entry: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    """Normalize a Simkl ``/sync/all-items/...`` row into a SyncRow."""
    simkl_type = _CATALOG_TO_SIMKL_TYPE.get(catalog, "shows")
    media_key = _SIMKL_SINGULAR.get(simkl_type, "show")
    media = _simkl_media_blob(entry, media_key)
    if not media:
        return None

    ids = media.get("ids") or {}
    if ids.get("simkl") is not None:
        ids = {"simkl_id": ids.get("simkl"), **{k: v for k, v in ids.items() if k != "simkl"}}
    elif ids.get("simkl_id") is None and media.get("simkl_id") is not None:
        ids = {"simkl_id": media.get("simkl_id"), **ids}

    ratings = media.get("ratings") or {}
    if isinstance(ratings, list):
        ratings = {
            str(r.get("source")): {
                "rating": r.get("value"),
                "votes": r.get("votes"),
                "score": r.get("score"),
            }
            for r in ratings
            if isinstance(r, dict) and r.get("source")
        }

    item = {
        "title": media.get("title"),
        "en_title": media.get("en_title"),
        "title_en": media.get("title_en") or media.get("en_title"),
        "title_romaji": media.get("title_romaji"),
        "overview": media.get("overview") or media.get("description"),
        "year": media.get("year"),
        "release_date": media.get("released") or media.get("release_date") or media.get("first_aired"),
        "poster": media.get("poster"),
        "fanart": media.get("fanart") or media.get("backdrop"),
        "url": media.get("url"),
        "type": media.get("type"),
        "anime_type": media.get("anime_type"),
        "runtime": media.get("runtime"),
        "status": media.get("status"),
        "country": media.get("country"),
        "network": media.get("network"),
        "certification": media.get("certification"),
        "trailer": media.get("trailer"),
        "total_episodes": media.get("total_episodes") or media.get("episode_count"),
        "season_count": media.get("season_count"),
        "genres": media.get("genres"),
        "ratings": ratings,
        "ids": ids,
    }
    if entry.get("listed_at"):
        item["dateadded"] = entry.get("listed_at")

    normalized = normalize_simkl_item(item, catalog)
    if not normalized:
        return None

    info = normalized.get("simkl_object", {}).get("info", {})
    if entry.get("listed_at"):
        info["dateadded"] = entry.get("listed_at")
    if entry.get("added_to_watchlist_at"):
        info["dateadded"] = entry.get("added_to_watchlist_at")
    if entry.get("last_watched_at"):
        info["last_watched_at"] = entry.get("last_watched_at")
    if entry.get("user_rating") is not None:
        info["user_rating"] = entry.get("user_rating")
    if entry.get("status"):
        info["simkl_status"] = entry.get("status")
    if entry.get("watched_episodes_count") is not None:
        info["watched_episodes_count"] = entry.get("watched_episodes_count")
    if entry.get("total_episodes_count") is not None:
        info["total_episodes_count"] = entry.get("total_episodes_count")
    return normalized


def normalize_search_row(row: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    """Normalize a Simkl ``/search/{type}`` result row."""
    if not isinstance(row, dict):
        return None

    ids = row.get("ids") or {}
    if ids.get("simkl_id") is None and ids.get("simkl") is not None:
        ids = {"simkl_id": ids.get("simkl"), **{k: v for k, v in ids.items() if k != "simkl"}}

    item = {
        "title": row.get("title"),
        "overview": row.get("overview"),
        "release_date": row.get("released") or row.get("first_aired"),
        "poster": row.get("poster"),
        "url": row.get("url"),
        "runtime": row.get("runtime"),
        "status": row.get("status"),
        "anime_type": row.get("anime_type"),
        "type": row.get("type"),
        "ids": ids,
    }
    normalized = normalize_simkl_item(item, catalog)
    if not normalized:
        return None
    info = normalized.get("simkl_object", {}).get("info", {})
    info["score"] = row.get("score") or row.get("rank") or 1.0
    return normalized


def fetch_search_page(catalog: str, query: str, page: int, page_limit: int) -> list[dict[str, Any]]:
    """Fetch and normalize one page of Simkl title search (no enrich/insert)."""
    return _fetch_search_page_cached(catalog, query.strip().lower(), page, page_limit)


@use_cache(cache_hours=1)
def _fetch_search_page_cached(catalog: str, query: str, page: int, page_limit: int) -> list[dict[str, Any]]:
    return _fetch_search_page_uncached(catalog, query, page, page_limit)


def _fetch_search_page_uncached(catalog: str, query: str, page: int, page_limit: int) -> list[dict[str, Any]]:
    from resources.lib.indexers.simkl import SimklAPI
    from resources.lib.modules.globals import g

    simkl_type = _SEARCH_SIMKL_TYPE.get(catalog, "tv")
    api = SimklAPI()
    results = api.get_json(
        f"/search/{simkl_type}",
        authorized=False,
        client_id=api.client_id,
        q=query,
        page=page,
        limit=page_limit,
        extended="full",
    )
    if not results:
        return []

    sync_items = []
    for row in results:
        normalized = normalize_search_row(row, catalog)
        if normalized:
            sync_items.append(normalized)
    g.log(f"Simkl search normalized {len(sync_items)} items for {query!r}", "debug")
    return sync_items


def enrich_and_persist(
    catalog_hint: str,
    items: list[dict[str, Any]],
    *,
    force_simkl_meta: bool = False,
    enrich: bool = True,
    fast_path: bool | None = None,
) -> list[dict[str, Any]]:
    """Enrich SyncRows (Simkl detail + discover DB gaps) and insert into sync DB."""
    if not items:
        return []
    working = list(items)
    if enrich:
        from resources.lib.modules.globals import g

        if fast_path is None:
            fast_path = len(working) > 1
        working = enrich_sync_items(working, fast=bool(fast_path))
    from resources.lib.simkl.ids import canonicalize_sync_row

    for row in working:
        canonicalize_sync_row(row)
    return insert_discover_page(catalog_hint, working, force_simkl_meta=force_simkl_meta)


def persist_search_results(
    catalog: str,
    items: list[dict[str, Any]],
    *,
    force_simkl_meta: bool = True,
    enrich: bool | None = None,
) -> list[dict[str, Any]]:
    """Enrich + insert search results; return list-builder refs."""
    if enrich is None:
        enrich = False
    return enrich_and_persist(
        catalog,
        items,
        force_simkl_meta=force_simkl_meta,
        enrich=enrich,
    )


def persist_genre_results(
    catalog: str,
    items: list[dict[str, Any]],
    *,
    force_simkl_meta: bool = True,
) -> list[dict[str, Any]]:
    """Enrich + insert genre browse results; return list-builder refs."""
    return enrich_and_persist(
        catalog,
        items,
        force_simkl_meta=force_simkl_meta,
        enrich=True,
        fast_path=True,
    )


def persist_genre_page(
    catalog: str,
    items: list[dict[str, Any]],
    *,
    blocking_enrich: bool = False,
    enrich_reason: str = "genre",
) -> list[dict[str, Any]]:
    """Persist a genre page and optionally block on Simkl detail + provider gap-fill."""
    refs = persist_genre_results(catalog, items)
    if blocking_enrich and refs:
        from resources.lib.modules.page_prefetch import enrich_refs_blocking

        enrich_refs_blocking(refs, catalog, reason=enrich_reason)
    return refs


def persist_library_entries(
    catalog: str,
    entries: list[dict[str, Any]],
    sync_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert watchlist/library SyncRows and apply Simkl watch state."""
    refs = insert_discover_page(catalog, sync_items)
    from resources.lib.database.session import get_sync_database

    get_sync_database().apply_library_watch_state(catalog, entries, sync_items)
    return refs


def refs_for_list_builder(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Minimal ``{simkl_id, catalog}`` refs after insert."""
    return simkl_refs(items)


def menu_action_args(item: dict[str, Any]) -> dict[str, Any]:
    """Build ActionArgs dict for a MenuRow (no URL encoding)."""
    from resources.lib.simkl.ids import build_action_args

    return build_action_args(item)


def encode_menu_action_args(item: dict[str, Any]) -> str:
    """URL-encode ActionArgs for plugin:// URLs."""
    from resources.lib.simkl.ids import encode_action_args

    return encode_action_args(item)


def sync_catalog(item: dict[str, Any]) -> str | None:
    """Read storage catalog from a SyncRow (top-level, not ``info``)."""
    catalog = item.get("catalog")
    if catalog in ("movie", "tv", "anime"):
        return catalog
    info = (item.get("simkl_object") or {}).get("info") or item.get("info") or {}
    if isinstance(info, dict):
        catalog = info.get("catalog")
        if catalog in ("movie", "tv", "anime"):
            return catalog
    return None


def partition_by_catalog(items: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split SyncRows into movie / tv / anime buckets."""
    movies: list[dict] = []
    tv: list[dict] = []
    anime: list[dict] = []
    for item in items:
        catalog = sync_catalog(item)
        mediatype = ((item.get("simkl_object") or {}).get("info") or item.get("info") or {}).get("mediatype")
        if catalog == "movie" or mediatype == "movie":
            movies.append(item)
        elif catalog == "anime":
            anime.append(item)
        else:
            tv.append(item)
    return movies, tv, anime


def render_mixed_sync_list(
    sync_items: list[dict[str, Any]],
    *,
    catalog_hint: str | None = None,
    label2_for_item: Callable[[dict[str, Any]], str | None] | None = None,
    **list_kwargs,
) -> None:
    """Render a mixed movie + show/anime Kodi directory via simkl_sync (Seren-mode browse)."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.browse_catalog_seed import defer_browse_catalog_seed
    from resources.lib.meta.list_paint import browse_list_kwargs
    from resources.lib.meta.menu_paint_profile import current_action_profile_kwargs
    from resources.lib.modules.globals import g
    from resources.lib.modules.list_builder import ListBuilder

    if not sync_items:
        g.cancel_directory()
        return

    db = get_sync_database()
    movies, tv, anime = partition_by_catalog(sync_items)
    for cat, group in (("movie", movies), ("tv", tv), ("anime", anime)):
        if group:
            db.insert_browse_page(cat, group)
            defer_browse_catalog_seed(cat, group)

    builder = ListBuilder()
    paint_overrides = current_action_profile_kwargs()
    paint_overrides.setdefault("no_paging", True)
    paint_overrides.update(list_kwargs or {})
    kwargs = browse_list_kwargs(**paint_overrides)
    kwargs.setdefault("sync_path", True)
    kwargs.setdefault("skip_update", False)

    if movies and (tv or anime):
        builder._mixed_media_from_sync_dicts(
            sync_items,
            catalog_hint=catalog_hint,
            label2_for_item=label2_for_item,
            **kwargs,
        )
        return
    if movies and not tv and not anime:
        builder.movie_discover_builder(movies, **kwargs)
        return
    if anime and not tv and not movies:
        builder.anime_discover_builder(anime, **kwargs)
        return
    if tv and not movies and not anime:
        builder.show_discover_builder(tv, **kwargs)
        return
    builder.show_discover_builder(sync_items, **kwargs)


__all__ = [
    "encode_menu_action_args",
    "enrich_and_persist",
    "fetch_search_page",
    "menu_action_args",
    "normalize_api_detail",
    "normalize_discover_db_row",
    "normalize_discover_db_rows",
    "normalize_library_entry",
    "normalize_search_row",
    "normalize_simkl_item",
    "normalize_simkl_items",
    "paginate_items",
    "partition_by_catalog",
    "persist_genre_results",
    "persist_library_entries",
    "persist_search_results",
    "refs_for_list_builder",
    "render_mixed_sync_list",
    "sync_catalog",
]
