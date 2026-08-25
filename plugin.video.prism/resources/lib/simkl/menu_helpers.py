"""Shared menu helpers for Simkl-backed browse and general settings."""
from __future__ import annotations

from resources.lib.modules.globals import g

GENRE_SORT_SEGMENTS = (
    "popular-this-week",
    "popular-all-time",
    "popular-this-month",
    "rank",
    "release-date",
    "voted",
    "watched",
)

GENRE_SORT_SETTING_KEYS = {
    "movie": "general.genres.endpoint.movies",
    "tv": "general.genres.endpoint.tv",
    "anime": "general.genres.endpoint.anime",
}

SIMKL_GENRE_MAX_PAGE = 20


def paginate_simkl_lists() -> bool:
    return g.get_bool_setting("general.paginatesimkllists")


def library_list_has_next(refs: list) -> bool:
    """True when another page exists for a pre-sorted library status list."""
    if not paginate_simkl_lists() or not refs:
        return False
    page_limit = g.get_int_setting("item.limit")
    return g.PAGE * page_limit < len(refs)


def library_status_list_kwargs(catalog: str, status: str, items: list) -> dict:
    """
    Shared list-builder kwargs for Simkl library status menus (watchlist, watching, etc.).

    Stamps catalog/status on REQUEST_PARAMS so Next Page links and prefetch use the canonical
    simklLibraryList route with genre-style browse paint defaults.
    """
    from resources.lib.discover.renderer import discover_list_kwargs
    from resources.lib.discover.sync_bridge import simkl_refs

    g.REQUEST_PARAMS["action"] = "simklLibraryList"
    g.REQUEST_PARAMS["catalog"] = catalog
    g.REQUEST_PARAMS["status"] = status

    refs = simkl_refs(items)
    return {
        **discover_list_kwargs(
            enrichment_reason="library",
            seeded=True,
            prefer_catalog_payload=True,
            hide_unaired=False,
            hide_watched=False,
        ),
        "library_status": status,
        "has_next_page": library_list_has_next(refs),
        "next_action": "simklLibraryList",
        "catalog_hint": catalog,
    }


def library_list_page(refs: list) -> tuple[list, bool]:
    """
    Apply Paginate Simkl Lists to a pre-sorted ref list.

    Returns (page_refs, no_paging). When pagination is off, or the list fits one
    page, behavior matches loading the full list with no Next Page row.
    """
    if not paginate_simkl_lists():
        return refs, True

    return paginate_refs_for_page(refs, g.PAGE), False


def paginate_refs_for_page(refs: list, page: int, *, page_limit: int | None = None) -> list:
    """Slice a pre-sorted ref list for a 1-based page number."""
    if not refs:
        return []
    page_limit = page_limit or g.get_int_setting("item.limit")
    start = (max(int(page), 1) - 1) * page_limit
    return refs[start : start + page_limit]


def list_filter_kwargs(**overrides) -> dict:
    """
    General-tab hide filters for sync DB list queries.
    Callers may override individual keys (e.g. discover keeps skip_mill separate).
    """
    defaults = {
        "hide_unaired": g.get_bool_setting("general.hideUnAired"),
        "hide_watched": g.get_bool_setting("general.hideWatched"),
        "hide_specials": g.get_bool_setting("general.hideSpecials"),
    }
    defaults.update(overrides)
    return defaults


def genre_sort_segment(catalog: str) -> str:
    """Simkl genre browse sort path segment from general.genres.endpoint.* settings."""
    key = GENRE_SORT_SETTING_KEYS.get(catalog, GENRE_SORT_SETTING_KEYS["movie"])
    index = g.get_int_setting(key, 1)
    if index < 0 or index >= len(GENRE_SORT_SEGMENTS):
        index = 1
    return GENRE_SORT_SEGMENTS[index]


def genre_page_has_next(page: int, result_count: int, page_limit: int) -> bool:
    """Fallback when Simkl pagination headers are missing."""
    if result_count < page_limit:
        return False
    return int(page) < SIMKL_GENRE_MAX_PAGE


def simkl_pagination_has_next(
    pagination: dict[str, int],
    *,
    fallback_page: int,
    fallback_count: int,
    fallback_limit: int,
) -> bool:
    """True when Simkl reports another page via ``X-Pagination-*`` headers."""
    current_page = pagination.get("page", int(fallback_page))
    page_count = pagination.get("page_count")
    if page_count is not None:
        return current_page < page_count
    return genre_page_has_next(fallback_page, fallback_count, fallback_limit)
