"""Background prefetch for the next paginated list page (Otaku-style)."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable

from resources.lib.modules.globals import g

_DONE_KEY = "page_prefetch.done_keys"
_IN_FLIGHT_KEY = "page_prefetch.in_flight"
_MENU_ACTIVE_KEY = "browse.menu_active"
_PREFETCH_PAGES_DEFAULT = 0
_PREFETCH_PAGES_MAX = 5
_MAX_DONE_KEYS = 256
_prefetch_events: dict[str, threading.Event] = {}
_prefetch_events_lock = threading.Lock()


def prefetch_page_depth() -> int:
    """How many upcoming list pages to warm (general.prefetch.pages, 0 = off)."""
    depth = g.get_int_setting("general.prefetch.pages", _PREFETCH_PAGES_DEFAULT)
    return max(0, min(depth, _PREFETCH_PAGES_MAX))


def prefetch_next_page_enabled() -> bool:
    """Follow general menu caching — prefetch is only useful when cacheToDisc is on."""
    return g.kodi_menu_caching_enabled() and prefetch_page_depth() > 0


def set_foreground_menu_active(active: bool) -> None:
    """Mark a user-facing directory build so background work yields."""
    g.set_runtime_setting(_MENU_ACTIVE_KEY, bool(active))


def _menu_active() -> bool:
    return g.get_bool_runtime_setting(_MENU_ACTIVE_KEY)


def _schedule_background_prefetch(page_params: dict[str, Any]) -> None:
    """Wait for the foreground menu to close, then RunPlugin prefetch in a fresh process."""
    key = _prefetch_key(page_params)

    def _launch() -> None:
        try:
            for _ in range(48):
                if not _menu_active():
                    break
                time.sleep(0.25)
            if _menu_active():
                g.log(
                    f"page_prefetch deferred action={page_params.get('action')} page={page_params.get('page')}",
                    "debug",
                )
                return
            if key in _done_keys() or key in _in_flight_keys():
                return
            import xbmc

            launch = dict(page_params)
            launch["prefetch_action"] = launch["action"]
            launch["action"] = "pagePrefetch"
            launch["prefetch_key"] = key
            url = g.create_url(g.BASE_URL, launch)
            xbmc.executebuiltin(f'RunPlugin("{url}")')
        except Exception:
            g.log_stacktrace()

    threading.Thread(target=_launch, daemon=True, name="prism-prefetch-launch").start()


def run_page_prefetch_invoke(page_params: dict[str, Any] | None) -> None:
    """Router entry for background pagePrefetch action (-1 handle)."""
    if not isinstance(page_params, dict):
        return
    action = page_params.get("prefetch_action") or page_params.get("action")
    if not action:
        return
    work_params = {
        key: value
        for key, value in page_params.items()
        if key not in ("prefetch_action", "prefetch_key")
    }
    work_params["action"] = action
    key = str(page_params.get("prefetch_key") or _prefetch_key(work_params))
    if key in _done_keys():
        return
    if _menu_active():
        g.log(
            f"page_prefetch skipped action={action} page={work_params.get('page')} menu_active=1",
            "debug",
        )
        return
    if key in _in_flight_keys():
        return
    _set_in_flight(key, True)
    try:
        stamped = run_page_prefetch(work_params)
        if not stamped:
            warm_kodi_menu_page(work_params)
        _mark_done(key)
    except Exception:
        g.log_stacktrace()
    finally:
        _set_in_flight(key, False)
        _signal_prefetch_done(key)


def warm_kodi_menu_page(page_params: dict[str, Any]) -> None:
    """
    POV-style: silently build the next page via RunPlugin so Kodi cacheToDisc
    serves it instantly when the user clicks Next Page (no loading bar).
    """
    if not isinstance(page_params, dict) or not page_params.get("action"):
        return
    if not g.kodi_menu_caching_enabled():
        return

    for _ in range(40):
        if not foreground_browse_busy():
            break
        time.sleep(0.25)
    if foreground_browse_busy():
        g.log(
            f"menu_warmup deferred action={page_params.get('action')} page={page_params.get('page')}",
            "debug",
        )
        return
    import xbmc

    warm_params = dict(page_params)
    warm_params["menu_warmup"] = "1"
    url = g.create_url(g.BASE_URL, warm_params)
    g.log(
        f"menu_warmup action={page_params.get('action')} page={page_params.get('page')}",
        "debug",
    )
    xbmc.executebuiltin(f'RunPlugin("{url}")')


def _prefetch_key(page_params: dict[str, Any]) -> str:
    stable = {
        key: page_params[key]
        for key in sorted(page_params)
        if key not in ("special_sort", "reload")
    }
    return hashlib.md5(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def _done_keys() -> set[str]:
    raw = g.get_runtime_setting(_DONE_KEY)
    if isinstance(raw, list):
        return {str(key) for key in raw}
    return set()


def _mark_done(key: str) -> None:
    done = list(_done_keys())
    if key in done:
        return
    done.append(key)
    if len(done) > _MAX_DONE_KEYS:
        done = done[-_MAX_DONE_KEYS:]
    g.set_runtime_setting(_DONE_KEY, done)


def _in_flight_keys() -> set[str]:
    raw = g.get_runtime_setting(_IN_FLIGHT_KEY)
    if isinstance(raw, list):
        return {str(key) for key in raw}
    return set()


def _set_in_flight(key: str, active: bool) -> None:
    keys = _in_flight_keys()
    if active:
        keys.add(key)
    else:
        keys.discard(key)
    g.set_runtime_setting(_IN_FLIGHT_KEY, sorted(keys))


def schedule_refs_enrichment(
    refs: list[dict],
    catalog: str,
    *,
    reason: str = "prefetch",
    blocking: bool = False,
) -> None:
    if not refs:
        return
    from resources.lib.meta.enrichment import MetaEnrichmentQueue

    if blocking:
        _blocking_enrich_refs(refs, default_catalog=catalog, reason=reason)
        return

    movie_ids = sorted(
        {
            int(ref["simkl_id"])
            for ref in refs
            if ref.get("simkl_id") is not None and ref.get("catalog") == "movie"
        }
    )
    show_ids = sorted(
        {
            int(ref["simkl_id"])
            for ref in refs
            if ref.get("simkl_id") is not None and ref.get("catalog") in ("tv", "anime")
        }
    )
    if catalog == "movie" and not movie_ids:
        movie_ids = sorted({int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None})
    elif catalog in ("tv", "anime") and not show_ids and not movie_ids:
        show_ids = sorted({int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None})

    if movie_ids:
        MetaEnrichmentQueue.schedule_run_plugin(
            [{"simkl_id": simkl_id, "needs_update": True} for simkl_id in movie_ids],
            "movie",
            reason=reason,
            catalog="movie",
        )
    if show_ids:
        MetaEnrichmentQueue.schedule_run_plugin(
            [{"simkl_id": simkl_id, "needs_update": True} for simkl_id in show_ids],
            "tvshow",
            reason=reason,
            catalog=catalog if catalog in ("tv", "anime") else "tv",
        )


def enrich_refs_blocking(
    refs: list[dict],
    catalog: str,
    *,
    reason: str = "prefetch",
) -> None:
    schedule_refs_enrichment(refs, catalog, reason=reason, blocking=True)


def current_page_prefetch_params() -> dict[str, Any] | None:
    params = getattr(g, "REQUEST_PARAMS", None) or {}
    action = params.get("action")
    if not action:
        return None
    page_params: dict[str, Any] = {"action": action}
    for key in ("catalog", "list_id", "page", "action_args", "status", "mediatype"):
        if key in params and params[key] not in (None, ""):
            page_params[key] = params[key]
    if "page" not in page_params:
        try:
            page_params["page"] = int(g.PAGE or 1)
        except (TypeError, ValueError):
            page_params["page"] = 1
    return page_params


def _prefetch_event(key: str) -> threading.Event:
    with _prefetch_events_lock:
        event = _prefetch_events.get(key)
        if event is None:
            event = threading.Event()
            _prefetch_events[key] = event
        return event


def _signal_prefetch_done(key: str) -> None:
    event = _prefetch_event(key)
    event.set()
    with _prefetch_events_lock:
        if len(_prefetch_events) > _MAX_DONE_KEYS:
            for stale_key in list(_prefetch_events.keys())[:-_MAX_DONE_KEYS]:
                _prefetch_events.pop(stale_key, None)


def _prefetch_paint_catalog_page(
    catalog: str,
    page_refs: list[dict],
    *,
    payload_rows: list[dict] | None = None,
    paint_profile: str = "browse",
) -> bool:
    """Blocking list paint into display_meta for prefetch (cast + art on blocking scope)."""
    if not page_refs:
        return False
    from resources.lib.meta.menu_paint_profile import page_paint_flags_for_profile
    from resources.lib.meta.paint_stamp import page_refs_display_stamped
    from resources.lib.meta.paint_cache import page_cache_catalog

    cache_catalog = page_cache_catalog(catalog, page_refs)
    if page_refs_display_stamped(page_refs):
        return True
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.meta.list_paint import sync_items_for_mixed_refs
    from resources.lib.meta.paint_cache import (
        get_session_page_paint,
        page_paint_cache_key,
        paint_catalog_page_rows,
    )

    paint_flags = page_paint_flags_for_profile(paint_profile)
    hide_unaired = paint_flags["hide_unaired"]
    hide_watched = paint_flags["hide_watched"]
    prefer_rich = paint_flags["prefer_rich_payload"]
    cache_key = page_paint_cache_key(
        cache_catalog,
        page_refs,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        paint_profile=paint_profile,
        prefer_rich_payload=prefer_rich,
    )
    if get_session_page_paint(cache_key) is not None:
        return page_refs_display_stamped(page_refs)

    if payload_rows:
        page_sync = list(payload_rows)
    elif cache_catalog == "mixed":
        page_sync = sync_items_for_mixed_refs(page_refs)
    else:
        page_sync = sync_items_for_refs(catalog, page_refs)
    if not page_sync:
        return False

    if paint_profile in ("library", "search"):
        from resources.lib.simkl.enrich import enrich_page_for_paint

        page_sync = enrich_page_for_paint(
            catalog,
            page_sync,
            force_detail=False,
        )

    paint_catalog_page_rows(
        page_refs,
        page_sync,
        hide_unaired=hide_unaired,
        hide_watched=hide_watched,
        prefer_rich_payload=prefer_rich,
        paint_profile=paint_profile,
        page_cache={"catalog": cache_catalog},
    )
    db = get_sync_database()
    for refs, _media_type in db.consume_list_enrichment_batches():
        schedule_refs_enrichment(refs, catalog, reason="prefetch_paint")
    return page_refs_display_stamped(page_refs)


def _prefetch_discover(page_params: dict[str, Any]) -> bool:
    catalog = page_params.get("catalog")
    list_id = page_params.get("list_id")
    page = int(page_params.get("page") or 1)
    if not catalog or not list_id:
        return False
    return _prefetch_discover_list(str(catalog), str(list_id), page)


def _prefetch_search(page_params: dict[str, Any]) -> bool:
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.simkl.media_ref import persist_search_results
    from resources.lib.simkl.menu_helpers import paginate_refs_for_page
    from resources.lib.simkl.search import (
        fetch_all_search_results,
        simkl_search_page_limit,
        simkl_search_sort_mode,
    )
    from resources.lib.simkl.search_menus import filter_search_results, normalize_search_query

    action = page_params.get("action")
    search_map = {
        "moviesSearchResults": "movie",
        "showsSearchResults": "tv",
        "animeSearchResults": "anime",
    }
    catalog = search_map.get(action)
    if not catalog:
        return False
    query = normalize_search_query(page_params.get("action_args"))
    if not query:
        return False
    page = int(page_params.get("page") or 1)
    page_limit = simkl_search_page_limit()
    sort_mode = simkl_search_sort_mode()
    list_id = make_list_id("search", catalog, query, sort_mode)
    store = get_list_store("search")
    all_items = store.load_cached_items(
        catalog,
        list_id,
        lambda: filter_search_results(fetch_all_search_results(catalog, query)),
    )
    page_items = paginate_refs_for_page(all_items, page, page_limit=page_limit)
    if not page_items:
        return False
    refs = persist_search_results(catalog, page_items, enrich=False)
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.simkl.enrich import enrich_page_for_paint

    page_sync = sync_items_for_refs(catalog, refs)
    if page_sync:
        enrich_page_for_paint(catalog, page_sync, force_detail=False)
    return _prefetch_paint_catalog_page(
        catalog,
        refs,
        payload_rows=page_sync,
        paint_profile=MenuPaintProfile.SEARCH.value,
    )


_MEDIA_TYPE_CATALOG = {
    "movies": "movie",
    "shows": "tv",
    "anime": "anime",
}

_BROWSE_ACTION_ENDPOINTS: dict[str, tuple[str, str]] = {
    "moviesUpdated": ("movie", "updated"),
    "moviesRecommended": ("movie", "anticipated"),
    "moviePopularRecent": ("movie", "popular"),
    "movieTrendingRecent": ("movie", "trending"),
    "showsUpdated": ("tv", "updated"),
    "showsNew": ("tv", "new"),
    "showsRecommended": ("tv", "anticipated"),
    "showsPopularRecent": ("tv", "popular"),
    "showsTrendingRecent": ("tv", "trending"),
    "animePopularRecent": ("anime", "popular_recent"),
    "animeTrendingRecent": ("anime", "trending_recent"),
}


def _prefetch_discover_list(catalog: str, list_id: str, page: int) -> bool:
    from resources.lib.discover.renderer import DiscoverRenderer

    return DiscoverRenderer().prefetch_page(catalog, list_id, page)


def _prefetch_browse_endpoint(page_params: dict[str, Any]) -> bool:
    mapped = _BROWSE_ACTION_ENDPOINTS.get(str(page_params.get("action") or ""))
    if not mapped:
        return False
    catalog, endpoint = mapped
    from resources.lib.simkl.browse import DISCOVER_ENDPOINTS

    list_id = DISCOVER_ENDPOINTS.get(catalog, {}).get(endpoint)
    if not list_id:
        return False
    page = int(page_params.get("page") or 1)
    return _prefetch_discover_list(catalog, list_id, page)


def _prefetch_generic_endpoint(page_params: dict[str, Any]) -> bool:
    endpoint = page_params.get("endpoint")
    catalog = _MEDIA_TYPE_CATALOG.get(str(page_params.get("mediatype") or ""))
    if not catalog or not endpoint:
        return False
    from resources.lib.simkl.browse import DISCOVER_ENDPOINTS

    list_id = DISCOVER_ENDPOINTS.get(catalog, {}).get(str(endpoint))
    if not list_id:
        return False
    page = int(page_params.get("page") or 1)
    return _prefetch_discover_list(catalog, list_id, page)


def _prefetch_genre_slug(page_params: dict[str, Any]) -> bool:
    from urllib import parse

    from resources.lib.simkl import browse
    from resources.lib.simkl.media_ref import persist_genre_page

    action = page_params.get("action")
    catalog_map = {
        "movieGenresGet": "movie",
        "showGenresGet": "tv",
        "animeGenresGet": "anime",
    }
    catalog = catalog_map.get(action)
    if not catalog:
        return False
    slug = parse.unquote(str(page_params.get("action_args") or "")).strip().lower()
    if not slug:
        return False
    page = int(page_params.get("page") or 1)
    page_limit = g.get_int_setting("item.limit", 25)
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id

    result = browse.discover_by_genre_slug(catalog, slug, page, page_limit)
    if not result.items:
        return False
    list_id = make_list_id("slug", catalog, slug)
    get_list_store("genre").remember_items(catalog, list_id, result.items)
    refs = persist_genre_page(catalog, result.items, blocking_enrich=False, enrich_reason="prefetch_genre")
    if not refs:
        return False
    return _prefetch_paint_catalog_page(catalog, refs)


def _prefetch_multi_genre(page_params: dict[str, Any]) -> bool:
    from resources.lib.simkl import browse
    from resources.lib.simkl.genre_menus import (
        _parse_tenrai_multi_genre_action_args,
        _parse_tmdb_multi_genre_action_args,
    )
    from resources.lib.simkl.media_ref import persist_genre_page

    action = page_params.get("action")
    action_args = page_params.get("action_args")
    page_limit = g.get_int_setting("item.limit", 25)

    if action == "animeGenresMultiGet":
        genre_ids, tenrai_page, tenrai_offset = _parse_tenrai_multi_genre_action_args(action_args)
        if not genre_ids:
            return False
        result = browse.discover_by_tenrai_genres(
            genre_ids,
            page_limit,
            tenrai_page=tenrai_page,
            row_offset=tenrai_offset,
        )
        catalog = "anime"
    elif action in ("movieGenresMultiGet", "showGenresMultiGet"):
        genre_ids, tmdb_page, tmdb_offset = _parse_tmdb_multi_genre_action_args(action_args)
        if not genre_ids:
            return False
        catalog = "movie" if action == "movieGenresMultiGet" else "tv"
        result = browse.discover_by_tmdb_genres(
            catalog,
            genre_ids,
            page_limit,
            tmdb_page=tmdb_page,
            tmdb_offset=tmdb_offset,
        )
    else:
        return False

    if not result.items:
        return False
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id

    if action == "animeGenresMultiGet":
        list_id = make_list_id("tenrai", genre_ids, tenrai_page, tenrai_offset)
    else:
        list_id = make_list_id("tmdb", catalog, genre_ids, tmdb_page, tmdb_offset)
    get_list_store("genre").remember_items(catalog, list_id, result.items)
    refs = persist_genre_page(catalog, result.items, blocking_enrich=False, enrich_reason="prefetch_genre")
    if not refs:
        return False
    return _prefetch_paint_catalog_page(catalog, refs)


def _library_catalog_status(page_params: dict[str, Any]) -> tuple[str | None, str | None]:
    catalog = page_params.get("catalog")
    status = page_params.get("status")
    if catalog and status:
        return str(catalog), str(status)
    media_type = page_params.get("mediatype")
    if not media_type:
        return None, None
    if media_type in ("movie", "movies"):
        catalog = "movie"
    elif media_type == "anime":
        catalog = "anime"
    else:
        catalog = "tv"
    return catalog, str(status or "plantowatch")


def _schedule_prefetch_simkl_ids(
    simkl_ids: list[int],
    media_type: str,
    *,
    catalog: str | None,
    reason: str,
) -> None:
    if not simkl_ids:
        return
    refs = [{"simkl_id": int(simkl_id), "needs_update": True} for simkl_id in simkl_ids]
    schedule_refs_enrichment(refs, catalog or "movie", reason=reason, blocking=False)


def _schedule_prefetch_refs(refs: list[dict], *, default_catalog: str, reason: str) -> None:
    if not refs:
        return
    schedule_refs_enrichment(refs, default_catalog, reason=reason, blocking=False)


def _blocking_enrich_simkl_ids(
    simkl_ids: list[int],
    media_type: str,
    *,
    catalog: str | None,
    reason: str,
) -> None:
    if not simkl_ids:
        return
    from resources.lib.meta.enrichment import MetaEnrichmentQueue

    MetaEnrichmentQueue.enrich_simkl_ids_blocking(simkl_ids, media_type, reason=reason, catalog=catalog)


def _blocking_enrich_refs(refs: list[dict], *, default_catalog: str, reason: str) -> None:
    movie_ids = sorted(
        {
            int(ref["simkl_id"])
            for ref in refs
            if ref.get("simkl_id") is not None and ref.get("catalog") == "movie"
        }
    )
    show_ids = sorted(
        {
            int(ref["simkl_id"])
            for ref in refs
            if ref.get("simkl_id") is not None and ref.get("catalog") in ("tv", "anime")
        }
    )
    if not movie_ids and default_catalog == "movie":
        movie_ids = sorted({int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None})
    if not show_ids and default_catalog in ("tv", "anime"):
        show_ids = sorted({int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None})
    if movie_ids:
        _blocking_enrich_simkl_ids(movie_ids, "movie", catalog="movie", reason=reason)
    if show_ids:
        _blocking_enrich_simkl_ids(show_ids, "tvshow", catalog=default_catalog, reason=reason)


def _prefetch_library(page_params: dict[str, Any]) -> bool:
    catalog, status = _library_catalog_status(page_params)
    if not catalog or not status:
        return False
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.simkl.library_cache import load_library_list_refs
    from resources.lib.simkl.menu_helpers import paginate_refs_for_page

    list_id = make_list_id(status)
    store = get_list_store("library")
    refs = store.get_refs(catalog, list_id, lambda: load_library_list_refs(catalog, status))
    if not refs:
        return False
    page = int(page_params.get("page") or 1)
    page_refs = paginate_refs_for_page(refs, page)
    if not page_refs:
        return False
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile

    return _prefetch_paint_catalog_page(
        catalog,
        page_refs,
        paint_profile=MenuPaintProfile.LIBRARY.value,
    )


def _prefetch_actor(page_params: dict[str, Any]) -> bool:
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.meta.paint_cache import page_cache_catalog
    from resources.lib.simkl.enrich import enrich_sync_items
    from resources.lib.simkl.media_ref import enrich_and_persist
    from resources.lib.simkl.person_ref import fetch_filmography_page, normalize_person_ref

    args = normalize_person_ref(page_params.get("action_args"))
    person_id = args.get("person_id")
    if person_id is None:
        return False
    catalog_hint = args.get("catalog") or "movie"
    page = int(page_params.get("page") or 1)
    page_limit = g.get_int_setting("item.limit", 25)
    list_id = make_list_id("actor", person_id, catalog_hint)
    store = get_list_store("actor")
    items = store.load_page_items(
        catalog_hint,
        list_id,
        page,
        lambda: fetch_filmography_page(int(person_id), page, page_limit),
    )
    if not items:
        return False
    items = enrich_sync_items(items, fast=True)
    refs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_catalog = item.get("catalog") or catalog_hint
        refs.extend(
            enrich_and_persist(
                item_catalog,
                [item],
                force_simkl_meta=True,
                enrich=False,
            )
        )
    if not refs:
        return False
    cache_catalog = page_cache_catalog(catalog_hint, refs, mixed_list=True)
    return _prefetch_paint_catalog_page(
        cache_catalog,
        refs,
        payload_rows=items,
    )


def _prefetch_year(page_params: dict[str, Any]) -> bool:
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.simkl import browse
    from resources.lib.simkl.media_ref import enrich_and_persist

    action = page_params.get("action")
    catalog = "movie" if action == "movieYearsMovies" else "tv"
    try:
        year = int(page_params.get("action_args") or 0)
    except (TypeError, ValueError):
        return False
    if year <= 0:
        return False
    page = int(page_params.get("page") or 1)
    page_limit = g.get_int_setting("item.limit", 25)
    list_id = make_list_id(year)
    store = get_list_store("year")
    items = store.load_page_items(
        catalog,
        list_id,
        page,
        lambda: browse.discover_by_year(catalog, year, page, page_limit),
    )
    if not items:
        return False
    refs = enrich_and_persist(catalog, items, enrich=False)
    if not refs:
        return False
    return _prefetch_paint_catalog_page(catalog, refs)


def _prefetch_db_movie_page(page_params: dict[str, Any], *, method: str, reason: str) -> None:
    page = int(page_params.get("page") or 1)
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.catalog_store import sync_items_for_refs
    from resources.lib.simkl.enrich import enrich_page_for_paint

    db = get_sync_database()
    rows = getattr(db, method)(page) or []
    for row in rows:
        if isinstance(row, dict):
            row.setdefault("catalog", "movie")
    simkl_ids = sorted({int(row["simkl_id"]) for row in rows if row.get("simkl_id") is not None})
    if not simkl_ids:
        return
    from resources.lib.meta.list_paint import prepare_catalog_refs

    refs = prepare_catalog_refs("movie", rows)
    page_sync = sync_items_for_refs("movie", refs)
    if page_sync:
        enrich_page_for_paint("movie", page_sync)
    _schedule_prefetch_simkl_ids(simkl_ids, "movie", catalog="movie", reason=reason)


def _prefetch_db_show_page(
    page_params: dict[str, Any],
    *,
    method: str,
    reason: str,
    catalog: str | None = None,
) -> None:
    page = int(page_params.get("page") or 1)
    catalog = catalog or page_params.get("catalog") or "tv"
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    if method == "get_recently_watched_shows":
        rows = db.get_recently_watched_shows(page, catalog=catalog) or []
    else:
        rows = getattr(db, method)(page, catalog=catalog) or []
    simkl_ids = sorted({int(row["simkl_id"]) for row in rows if row.get("simkl_id") is not None})
    _schedule_prefetch_simkl_ids(simkl_ids, "tvshow", catalog=catalog, reason=reason)


def _prefetch_continue_watching(page_params: dict[str, Any]) -> None:
    from resources.lib.simkl.playback import prefetch_continue_watching

    catalog = page_params.get("catalog") or "tv"
    prefetch_continue_watching(catalog, page_params)


def _prefetch_episode_library_page(page_params: dict[str, Any], *, loader) -> None:
    """Warm session paint cache for library episode menus (Next Up, Watched Episodes)."""
    catalog = page_params.get("catalog") or "tv"
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import (
        attach_preloaded_episode_paint,
        upsert_episode_parent_shows,
    )
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import list_filter_kwargs

    page = int(page_params.get("page") or 1)
    action = str(page_params.get("action") or "")
    if action == "libraryNextUp":
        list_id = make_list_id(
            "next_up",
            catalog,
            g.get_int_setting("nextup.sort") == 1,
            g.get_bool_setting("limit.nextup"),
            g.get_int_setting("item.limit"),
        )
        episode_rows = get_list_store("library").load_cached_items(catalog, list_id, lambda: loader(catalog, page_params) or [])
    elif action == "libraryWatchedEpisodes":
        list_id = make_list_id("watched_episodes", catalog)
        episode_rows = get_list_store("library").load_page_items(
            catalog,
            list_id,
            page,
            lambda: loader(catalog, page_params) or [],
            schedule_upsert=False,
        )
    else:
        episode_rows = loader(catalog, page_params) or []
    if not episode_rows:
        return
    upsert_episode_parent_shows(episode_rows, catalog)
    list_kwargs = profile_list_kwargs(MenuPaintProfile.LIBRARY_EPISODES, catalog_hint=catalog)
    list_kwargs.update(list_filter_kwargs())
    builder = ListBuilder()
    filter_params = builder._apply_list_filters(dict(list_kwargs))
    attach_preloaded_episode_paint(
        catalog,
        episode_rows,
        list_kwargs,
        get_sync_database(),
        filter_params=filter_params,
    )


def _prefetch_next_up(page_params: dict[str, Any]) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.modules.globals import g

    def _load(cat: str, _params: dict[str, Any]) -> list[dict]:
        episodes = get_sync_database().get_nextup_episodes(
            g.get_int_setting("nextup.sort") == 1,
            catalog=cat,
        )
        if g.get_bool_setting("limit.nextup"):
            episodes = episodes[: g.get_int_setting("item.limit")]
        return episodes

    _prefetch_episode_library_page(page_params, loader=_load)


def _prefetch_watched_episodes(page_params: dict[str, Any]) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    def _load(cat: str, params: dict[str, Any]) -> list[dict]:
        page = int(params.get("page") or 1)
        return get_sync_database().get_watched_episodes(page, catalog=cat) or []

    if paginate_simkl_lists():
        _prefetch_episode_library_page(page_params, loader=_load)
        return
    _prefetch_episode_library_page({**page_params, "page": 1}, loader=_load)


def _prefetch_recently_watched_shows(page_params: dict[str, Any]) -> None:
    catalog = page_params.get("catalog") or "tv"
    page = int(page_params.get("page") or 1)
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import prepare_catalog_refs
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile
    from resources.lib.simkl.menu_helpers import paginate_refs_for_page, paginate_simkl_lists

    list_id = make_list_id("recently_watched", catalog)
    store = get_list_store("library")
    if paginate_simkl_lists():
        rows = store.load_page_items(
            catalog,
            list_id,
            page,
            lambda: get_sync_database().get_recently_watched_shows(page, catalog=catalog) or [],
            schedule_upsert=False,
        )
    else:
        rows = store.load_cached_items(
            catalog,
            list_id,
            lambda: get_sync_database().get_recently_watched_shows(1, force_all=True, catalog=catalog) or [],
        )
    refs = prepare_catalog_refs(catalog, rows)
    page_refs = paginate_refs_for_page(refs, page) if refs else []
    if not page_refs:
        return
    _prefetch_paint_catalog_page(
        catalog,
        page_refs,
        paint_profile=MenuPaintProfile.LIBRARY.value,
    )


def _library_prefetch_handler(route: str, catalog: str):
    if route == "on_deck":
        return lambda p, cat=catalog: _prefetch_continue_watching({**p, "catalog": cat})
    if route == "next_up":
        return lambda p, cat=catalog: _prefetch_next_up({**p, "catalog": cat})
    if route in ("watched_movies", "recently_watched") and catalog == "movie":
        return lambda p: _prefetch_db_movie_page(p, method="get_watched_movies", reason="prefetch_watched")
    if route == "watched_episodes":
        return lambda p, cat=catalog: _prefetch_watched_episodes({**p, "catalog": cat})
    if route == "recently_watched":
        return lambda p, cat=catalog: _prefetch_recently_watched_shows({**p, "catalog": cat})
    return None


def _build_library_prefetch_handlers() -> dict[str, Callable[[dict[str, Any]], None]]:
    from resources.lib.simkl.library_routes import _CANONICAL_ROUTES

    handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
    for action, route in _CANONICAL_ROUTES.items():

        def _canonical(route_name: str = route):
            def _handler(page_params: dict[str, Any]) -> None:
                catalog = page_params.get("catalog") or ("movie" if route_name == "watched_movies" else "tv")
                bound = _library_prefetch_handler(route_name, catalog)
                if bound is not None:
                    bound(page_params)

            return _handler

        handlers[action] = _canonical()
    return handlers


_PREFETCH_HANDLERS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "simklDiscoverList": _prefetch_discover,
    "genericEndpoint": _prefetch_generic_endpoint,
    "moviesUpdated": _prefetch_browse_endpoint,
    "moviesRecommended": _prefetch_browse_endpoint,
    "moviePopularRecent": _prefetch_browse_endpoint,
    "movieTrendingRecent": _prefetch_browse_endpoint,
    "showsUpdated": _prefetch_browse_endpoint,
    "showsNew": _prefetch_browse_endpoint,
    "showsRecommended": _prefetch_browse_endpoint,
    "showsPopularRecent": _prefetch_browse_endpoint,
    "showsTrendingRecent": _prefetch_browse_endpoint,
    "animePopularRecent": _prefetch_browse_endpoint,
    "animeTrendingRecent": _prefetch_browse_endpoint,
    "moviesSearchResults": _prefetch_search,
    "showsSearchResults": _prefetch_search,
    "animeSearchResults": _prefetch_search,
    "movieGenresGet": _prefetch_genre_slug,
    "showGenresGet": _prefetch_genre_slug,
    "animeGenresGet": _prefetch_genre_slug,
    "movieGenresMultiGet": _prefetch_multi_genre,
    "showGenresMultiGet": _prefetch_multi_genre,
    "animeGenresMultiGet": _prefetch_multi_genre,
    "simklLibraryList": _prefetch_library,
    "actorCredits": _prefetch_actor,
    "movieYearsMovies": _prefetch_year,
    "showYears": _prefetch_year,
    **_build_library_prefetch_handlers(),
}


def prefetch_threads_active() -> bool:
    """True while a background prefetch thread is still running."""
    return bool(_in_flight_keys())


def foreground_browse_busy() -> bool:
    """True while prefetch, meta-enrich, playback, or a foreground menu is active."""
    if _menu_active():
        return True
    if prefetch_threads_active():
        return True
    if g.get_bool_runtime_setting("meta_enrich.in_flight"):
        return True
    if g.get_bool_runtime_setting("playback.pipeline_busy"):
        return True
    try:
        import xbmc

        if xbmc.Player().isPlayingVideo():
            return True
    except Exception:
        pass
    return False


def run_page_prefetch(page_params: dict[str, Any]) -> bool:
    """Run background page paint prefetch. Returns True when the page is stamp-ready."""
    if not prefetch_next_page_enabled() or not isinstance(page_params, dict):
        return False
    g.ensure_addon()
    action = page_params.get("action")
    if not action:
        return False
    handler = _PREFETCH_HANDLERS.get(str(action))
    if handler is None:
        return False
    start = time.time()
    stamped = False
    try:
        result = handler(page_params)
        stamped = bool(result)
        g.log(
            f"page_prefetch_ms={(time.time() - start) * 1000:.0f} action={action} page={page_params.get('page')} stamped={int(stamped)}",
            "debug",
        )
    except Exception:
        g.log_stacktrace()
    return stamped


def schedule_page_prefetch_chain(page_params: dict[str, Any] | None) -> None:
    """Schedule background prefetch for the next N pages (see general.prefetch.pages)."""
    if not isinstance(page_params, dict) or not page_params.get("action"):
        return
    if str(g.REQUEST_PARAMS.get("menu_warmup", "")).lower() in ("1", "true"):
        return
    depth = prefetch_page_depth()
    if depth <= 0 or not g.kodi_menu_caching_enabled():
        return
    try:
        current_page = int(g.PAGE or 1)
    except (TypeError, ValueError):
        current_page = 1
    try:
        start_page = int(page_params.get("page") or 1)
    except (TypeError, ValueError):
        start_page = 1
    # page_params come from _build_next_page_params (page = g.PAGE + 1) — never prefetch the visible page.
    if start_page <= current_page:
        g.log(
            f"page_prefetch skipped — target page {start_page} is not after current page {current_page}",
            "debug",
        )
        return
    for offset in range(depth):
        params = dict(page_params)
        params["page"] = start_page + offset
        PagePrefetch.schedule(params)


class PagePrefetch:
    @staticmethod
    def schedule(page_params: dict[str, Any] | None) -> None:
        if not prefetch_next_page_enabled() or not isinstance(page_params, dict):
            return
        if not page_params.get("action"):
            return
        _schedule_background_prefetch(page_params)
