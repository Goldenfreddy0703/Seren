"""Simkl search helpers — delegates to media_ref."""
from __future__ import annotations

from resources.lib.database.cache import use_cache
from resources.lib.modules.globals import g
from resources.lib.simkl.media_ref import fetch_search_pool

SORT_RELEVANCE = 0
SORT_YEAR = 1
SORT_RANK = 2

_RANK_NULL_SENTINEL = 999_999


def simkl_search_exact_enabled() -> bool:
    """Whether Simkl title search should pass ``exact=true``."""
    return g.get_bool_setting("simkl.search.exact", False)


def simkl_search_sort_mode() -> int:
    """Global search sort mode (relevance / year / rank)."""
    mode = g.get_int_setting("simkl.search.sort", SORT_RANK)
    if mode not in (SORT_RELEVANCE, SORT_YEAR, SORT_RANK):
        return SORT_RELEVANCE
    return mode


def simkl_search_page_limit() -> int:
    """Items per page for Simkl title search menus."""
    limit = g.get_int_setting("simkl.search.limit", 20)
    return max(10, min(60, int(limit or 20)))


def _catalog_from_search_context(url: str, media_type: str) -> str:
    if "anime" in (url or "") or "anime" in (media_type or ""):
        return "anime"
    if "movie" in (url or "") or media_type == "movies":
        return "movie"
    return "tv"


def _info_blob(item: dict) -> dict:
    return (item.get("simkl_object") or {}).get("info") or {}


def _rank_sort_key(item: dict) -> tuple[int, int]:
    rank = _info_blob(item).get("rank")
    if rank is None:
        return (1, _RANK_NULL_SENTINEL)
    try:
        value = int(rank)
    except (TypeError, ValueError):
        return (1, _RANK_NULL_SENTINEL)
    if value >= _RANK_NULL_SENTINEL:
        return (1, _RANK_NULL_SENTINEL)
    return (0, value)


def _year_sort_key(item: dict) -> tuple[int, int]:
    info = _info_blob(item)
    year = info.get("year")
    if year is None:
        year = info.get("premiered")
    if year is None:
        return (1, 0)
    try:
        return (0, -int(year))
    except (TypeError, ValueError):
        return (1, 0)


def sort_search_results(items: list[dict], sort_mode: int | None = None) -> list[dict]:
    """Sort normalized search rows using the global search sort setting."""
    if not items:
        return []
    mode = simkl_search_sort_mode() if sort_mode is None else sort_mode
    if mode == SORT_RELEVANCE:
        return list(items)
    if mode == SORT_YEAR:
        return sorted(items, key=_year_sort_key)
    if mode == SORT_RANK:
        return sorted(items, key=_rank_sort_key)
    return list(items)


@use_cache(cache_hours=1)
def _fetch_all_search_results_cached(
    catalog: str,
    query: str,
    exact: bool,
    sort_mode: int,
) -> list[dict]:
    pool = fetch_search_pool(catalog, query, exact=exact)
    return sort_search_results(pool, sort_mode)


def fetch_all_search_results(catalog: str, query: str) -> list[dict]:
    """Fetch every Simkl search page, then apply the configured sort."""
    exact = simkl_search_exact_enabled()
    sort_mode = simkl_search_sort_mode()
    return _fetch_all_search_results_cached(
        catalog,
        query.strip().lower(),
        exact,
        sort_mode,
    )


def search_page(
    url: str,
    media_type: str,
    page: int,
    page_limit: int,
    query: str,
) -> list[dict]:
    """Fetch + normalize one page of sorted search results."""
    catalog = _catalog_from_search_context(url, media_type)
    limit = simkl_search_page_limit()
    all_items = fetch_all_search_results(catalog, query)
    start = (max(int(page), 1) - 1) * limit
    return all_items[start : start + limit]


__all__ = [
    "SORT_RANK",
    "SORT_RELEVANCE",
    "SORT_YEAR",
    "fetch_all_search_results",
    "search_page",
    "simkl_search_exact_enabled",
    "simkl_search_page_limit",
    "simkl_search_sort_mode",
    "sort_search_results",
]
