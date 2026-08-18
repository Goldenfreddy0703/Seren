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
    "watching": ("progress", 30743),
    "plantowatch": ("watched", 30744),
    "hold": ("collected", 30745),
    "completed": ("watched", 30746),
    "dropped": ("watched", 30747),
}

_MOVIE_STATUSES = tuple((s, lid, *_MOVIE_META[s]) for s, lid in MOVIE_STATUS_OPTIONS)


def _show_pack_icon(stem: str, catalog: str) -> str:
    prefix = "anime" if catalog == "anime" else "shows"
    return f"{prefix}_{stem}"


def _show_status_items(catalog: str) -> tuple[tuple[str, int, str, int], ...]:
    return tuple(
        (status, label_id, _show_pack_icon(icon_stem, catalog), desc_id)
        for status, label_id in SHOW_STATUS_OPTIONS
        for icon_stem, desc_id in (_SHOW_STATUS_ICONS[status],)
    )


# Shared episode-library rows for TV and anime hubs (canonical actions + catalog param).
_SHOW_LIBRARY_ROWS = (
    ("libraryNextUp", "nextup", 30210),
    ("libraryRecentlyWatched", "recent", 30090),
    ("libraryWatchedEpisodes", "watched", 30325),
)
_SHOW_LIBRARY_DESCRIPTIONS = {
    "tv": (30436, 30479, 30442),
    "anime": (30750, 30751, 30752),
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
    _add_library_item(30731, "libraryOnDeck", "movies_progress", 30748, catalog="movie")
    for status, label_id, icon, desc_id in _MOVIE_STATUSES:
        _add_status_item("movie", status, label_id, icon, desc_id)
    _add_library_item(30090, "libraryRecentlyWatched", "shows_recent", 30760, catalog="movie")
    g.close_directory(g.CONTENT_MENU)


@simkl_auth_guard
def my_shows_hub() -> None:
    _add_library_item(30731, "libraryOnDeck", "shows_progress", 30433, catalog="tv")
    for status, label_id, icon, desc_id in _show_status_items("tv"):
        _add_status_item("tv", status, label_id, icon, desc_id)
    _add_show_library_rows("tv")
    g.close_directory(g.CONTENT_MENU)


@simkl_auth_guard
def my_anime_hub() -> None:
    _add_library_item(30731, "libraryOnDeck", "anime_progress", 30749, catalog="anime")
    for status, label_id, icon, desc_id in _show_status_items("anime"):
        _add_status_item("anime", status, label_id, icon, desc_id)
    _add_show_library_rows("anime")
    g.close_directory(g.CONTENT_MENU)


def render_status_list(catalog: str, status: str) -> None:
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.library_cache import load_library_list_refs
    from resources.lib.simkl.menu_helpers import library_list_page, library_status_list_kwargs

    refs = load_library_list_refs(catalog, status)

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


def render_recently_watched_shows(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_rows
    from resources.lib.meta.menu_paint_profile import current_action_profile_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    no_paging = not paginate_simkl_lists()
    items = get_sync_database().get_recently_watched_shows(
        g.PAGE,
        force_all=no_paging,
        catalog=catalog,
    )

    render_catalog_rows(
        catalog,
        items,
        ListBuilder(),
        list_kwargs=current_action_profile_kwargs(no_paging=no_paging),
    )


def render_watched_episodes(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.renderer import discover_list_kwargs
    from resources.lib.meta.list_paint import render_catalog_episodes
    from resources.lib.modules.globals import g
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    items = get_sync_database().get_watched_episodes(g.PAGE, catalog=catalog)
    render_catalog_episodes(
        catalog,
        items,
        ListBuilder(),
        **discover_list_kwargs(no_paging=not paginate_simkl_lists()),
    )


def render_next_up(catalog: str) -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.renderer import discover_list_kwargs
    from resources.lib.meta.list_paint import render_catalog_episodes
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.episode_catalog_sync import schedule_lazy_episode_warm

    show_db = get_sync_database()
    episodes = show_db.get_nextup_episodes(
        g.get_int_setting("nextup.sort") == 1,
        catalog=catalog,
    )
    if not episodes:
        watching_ids = {
            int(ref["simkl_id"])
            for ref in show_db.get_shows_by_simkl_status("watching", catalog=catalog)
            if ref.get("simkl_id") is not None
        }
        if watching_ids:
            schedule_lazy_episode_warm(show_db, watching_ids)
    if g.get_bool_setting("limit.nextup"):
        episodes = episodes[: g.get_int_setting("item.limit")]
    render_catalog_episodes(
        catalog,
        episodes,
        ListBuilder(),
        **discover_list_kwargs(no_paging=True),
    )


def render_recently_watched_movies() -> None:
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_rows
    from resources.lib.meta.menu_paint_profile import current_action_profile_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.menu_helpers import paginate_simkl_lists

    no_paging = not paginate_simkl_lists()
    items = get_sync_database().get_watched_movies(g.PAGE)
    for item in items:
        if isinstance(item, dict):
            item.setdefault("catalog", "movie")
    render_catalog_rows(
        "movie",
        items,
        ListBuilder(),
        list_kwargs=current_action_profile_kwargs(no_paging=no_paging),
    )


def render_continue_watching(catalog: str) -> None:
    from resources.lib.simkl.playback import render_continue_watching_menu

    render_continue_watching_menu(catalog)
