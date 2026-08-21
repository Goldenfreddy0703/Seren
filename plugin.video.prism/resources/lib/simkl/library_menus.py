"""Simkl My Library hub menus — flat status lists for movies, TV, and anime."""
from __future__ import annotations

from resources.lib.indexers import simkl_auth_guard
from resources.lib.modules.globals import g
from resources.lib.simkl.menu_helpers import (
    library_list_page,
    list_filter_kwargs,
    paginate_simkl_lists,
)
from resources.lib.simkl.statuses import MOVIE_STATUS_OPTIONS, SHOW_STATUS_OPTIONS

_MOVIE_META = {
    "plantowatch": ("movies_watched", 30740),
    "completed": ("movies_watched", 30741),
    "dropped": ("movies_watched", 30742),
}
# Icon stems shared by TV and anime library hubs (`shows_*` vs `anime_*`).
_SHOW_STATUS_ICONS = {
    "watching": "progress",
    "plantowatch": "watched",
    "hold": "collected",
    "completed": "watched",
    "dropped": "watched",
}
_TV_STATUS_DESCRIPTIONS = {
    "watching": 30743,
    "plantowatch": 30744,
    "hold": 30745,
    "completed": 30746,
    "dropped": 30747,
}
_ANIME_STATUS_DESCRIPTIONS = {
    "watching": 31035,
    "plantowatch": 31036,
    "hold": 31037,
    "completed": 31038,
    "dropped": 31039,
}
_ON_DECK_DESCRIPTIONS = {
    "movie": 30748,
    "tv": 30433,
    "anime": 30749,
}

_MOVIE_STATUSES = tuple((s, lid, *_MOVIE_META[s]) for s, lid in MOVIE_STATUS_OPTIONS)


def _show_pack_icon(stem: str, catalog: str) -> str:
    prefix = "anime" if catalog == "anime" else "shows"
    return f"{prefix}_{stem}"


def _show_status_items(catalog: str) -> tuple[tuple[str, int, str, int], ...]:
    descriptions = _ANIME_STATUS_DESCRIPTIONS if catalog == "anime" else _TV_STATUS_DESCRIPTIONS
    return tuple(
        (
            status,
            label_id,
            _show_pack_icon(_SHOW_STATUS_ICONS[status], catalog),
            descriptions[status],
        )
        for status, label_id in SHOW_STATUS_OPTIONS
    )


# Shared episode-library rows for TV and anime hubs (canonical actions + catalog param).
_SHOW_LIBRARY_ROWS = (
    ("libraryNextUp", "nextup", 30210),
    ("libraryRecentlyWatched", "recent", 30090),
    ("libraryWatchedEpisodes", "watched", 30325),
)
_SHOW_LIBRARY_DESCRIPTIONS = {
    "tv": (30436, 30479, 30442),
    "anime": (30750, 30751, 30442),
}


def _add_library_item(label_id: int, action: str, icon: str, desc_id: int, **params) -> None:
    g.add_directory_item(
        g.get_language_string(label_id),
        action=action,
        description=g.get_language_string(desc_id),
        menu_item=g.create_icon_dict(icon, g.ICONS_PATH),
        **params,
    )


def _add_status_item(catalog: str, status: str, label_id: int, icon: str, desc_id: int) -> None:
    _add_library_item(
        label_id,
        "simklLibraryList",
        icon,
        desc_id,
        catalog=catalog,
        status=status,
    )


def _add_show_library_rows(catalog: str) -> None:
    descriptions = _SHOW_LIBRARY_DESCRIPTIONS[catalog]
    for (action, icon_stem, label_id), desc_id in zip(_SHOW_LIBRARY_ROWS, descriptions):
        _add_library_item(label_id, action, _show_pack_icon(icon_stem, catalog), desc_id, catalog=catalog)


@simkl_auth_guard
def my_movies_hub() -> None:
    _add_library_item(30731, "libraryOnDeck", "movies_progress", _ON_DECK_DESCRIPTIONS["movie"], catalog="movie")
    for status, label_id, icon, desc_id in _MOVIE_STATUSES:
        _add_status_item("movie", status, label_id, icon, desc_id)
    _add_library_item(30090, "libraryRecentlyWatched", "shows_recent", 30760, catalog="movie")
    g.close_directory(g.CONTENT_MENU)


@simkl_auth_guard
def my_shows_hub() -> None:
    _add_library_item(30731, "libraryOnDeck", "shows_progress", _ON_DECK_DESCRIPTIONS["tv"], catalog="tv")
    for status, label_id, icon, desc_id in _show_status_items("tv"):
        _add_status_item("tv", status, label_id, icon, desc_id)
    _add_show_library_rows("tv")
    g.close_directory(g.CONTENT_MENU)


@simkl_auth_guard
def my_anime_hub() -> None:
    _add_library_item(30731, "libraryOnDeck", "anime_progress", _ON_DECK_DESCRIPTIONS["anime"], catalog="anime")
    for status, label_id, icon, desc_id in _show_status_items("anime"):
        _add_status_item("anime", status, label_id, icon, desc_id)
    _add_show_library_rows("anime")
    g.close_directory(g.CONTENT_MENU)


def render_status_list(catalog: str, status: str) -> None:
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.simkl.library_cache import load_library_list_refs
    from resources.lib.simkl.menu_helpers import library_list_page, library_status_list_kwargs

    list_id = make_list_id(status)
    store = get_list_store("library")

    refs = store.get_refs(
        catalog,
        list_id,
        lambda: load_library_list_refs(catalog, status),
    )

    if not refs:
        g.cancel_directory()
        return

    list_kwargs = library_status_list_kwargs(catalog, status, refs)
    page_refs, no_paging = library_list_page(refs)
    list_kwargs.update(
        {
            "no_paging": no_paging,
            "library_paint": True,
            "library_status": status,
            "prefer_catalog_payload": True,
        }
    )

    from resources.lib.meta.list_paint import render_catalog_discover_refs

    render_catalog_discover_refs(
        catalog,
        page_refs,
        ListBuilder(),
        list_kwargs=list_kwargs,
    )


def _library_store():
    from resources.lib.meta.list_pipeline import get_list_store

    return get_list_store("library")


def _refs_from_library_rows(rows: list[dict], catalog: str) -> list[dict]:
    refs: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("simkl_id")
        if sid is None:
            continue
        sid_int = int(sid)
        if sid_int in seen:
            continue
        seen.add(sid_int)
        refs.append({"simkl_id": sid_int, "catalog": row.get("catalog") or catalog})
    return refs


def render_recently_watched_shows(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_discover_refs
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    no_paging = not paginate_simkl_lists()
    page = g.PAGE
    db = get_sync_database()
    items = db.get_recently_watched_shows(
        page,
        force_all=no_paging,
        catalog=catalog,
    )

    if not items:
        g.cancel_directory()
        return

    refs = _refs_from_library_rows(items, catalog)
    list_kwargs = profile_list_kwargs(
        MenuPaintProfile.LIBRARY,
        no_paging=no_paging,
        seeded=True,
        library_paint=True,
        prefer_catalog_payload=True,
    )

    render_catalog_discover_refs(
        catalog,
        refs,
        ListBuilder(),
        list_kwargs=list_kwargs,
        payload_rows=items,
        prefer_catalog_payload=True,
    )


def render_watched_episodes(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_episodes
    from resources.lib.meta.list_pipeline import make_list_id
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    no_paging = not paginate_simkl_lists()
    page = g.PAGE
    list_id = make_list_id("watched_episodes", catalog)
    store = _library_store()

    if no_paging:
        items = store.load_cached_items(
            catalog,
            list_id,
            lambda: get_sync_database().get_watched_episodes(1, catalog=catalog) or [],
        )
    else:
        items = store.load_page_items(
            catalog,
            list_id,
            page,
            lambda: get_sync_database().get_watched_episodes(page, catalog=catalog) or [],
            schedule_upsert=False,
        )

    if not items:
        db = get_sync_database()
        refreshed = db.refresh_watched_episodes_if_empty(catalog)
        if refreshed:
            items = db.get_watched_episodes(page if not no_paging else 1, catalog=catalog) or []

    render_catalog_episodes(
        catalog,
        items,
        ListBuilder(),
        **profile_list_kwargs(
            MenuPaintProfile.LIBRARY_EPISODES,
            no_paging=no_paging,
            seeded=True,
            overlay_parent_shows=True,
        ),
    )


def render_next_up(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_episodes
    from resources.lib.meta.list_pipeline import make_list_id
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.episode_catalog_sync import schedule_lazy_episode_warm

    sort_last = g.get_int_setting("nextup.sort") == 1
    limit_nextup = g.get_bool_setting("limit.nextup")
    page_limit = g.get_int_setting("item.limit")
    list_id = make_list_id("next_up", catalog, sort_last, limit_nextup, page_limit)
    store = _library_store()
    show_db = get_sync_database()

    def _load_next_up() -> list[dict]:
        rows = show_db.get_nextup_episodes(sort_last, catalog=catalog)
        if not rows:
            watching_ids = {
                int(ref["simkl_id"])
                for ref in show_db.get_shows_by_simkl_status("watching", catalog=catalog)
                if ref.get("simkl_id") is not None
            }
            if watching_ids:
                schedule_lazy_episode_warm(show_db, watching_ids)
        if limit_nextup:
            rows = rows[:page_limit]
        return rows or []

    episodes = store.load_cached_items(catalog, list_id, _load_next_up)

    render_catalog_episodes(
        catalog,
        episodes,
        ListBuilder(),
        **profile_list_kwargs(
            MenuPaintProfile.LIBRARY_EPISODES,
            no_paging=True,
            seeded=True,
            overlay_parent_shows=True,
        ),
    )


def render_recently_watched_movies() -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_discover_refs
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    no_paging = not paginate_simkl_lists()
    page = g.PAGE
    db = get_sync_database()
    items = db.get_watched_movies(page if not no_paging else 1) or []
    for item in items:
        if isinstance(item, dict):
            item.setdefault("catalog", "movie")

    if not items:
        g.cancel_directory()
        return

    refs = _refs_from_library_rows(items, "movie")
    list_kwargs = profile_list_kwargs(
        MenuPaintProfile.LIBRARY,
        no_paging=no_paging,
        seeded=True,
        library_paint=True,
        prefer_catalog_payload=True,
    )

    render_catalog_discover_refs(
        "movie",
        refs,
        ListBuilder(),
        list_kwargs=list_kwargs,
        payload_rows=items,
        prefer_catalog_payload=True,
    )


def render_continue_watching(catalog: str) -> None:
    from resources.lib.simkl.playback import render_continue_watching_menu

    render_continue_watching_menu(catalog)
