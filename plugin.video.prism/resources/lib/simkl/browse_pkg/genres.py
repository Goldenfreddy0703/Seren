"""Browse helpers — Simkl discover, genres, TMDB year/discover, Simkl airing."""
from __future__ import annotations

import copy
from typing import Any, NamedTuple

from resources.lib.simkl.media_ref import normalize_simkl_item
from resources.lib.discover.renderer import DiscoverRenderer
from resources.lib.database.cache import use_cache
from resources.lib.indexers.simkl import SimklAPI
from resources.lib.indexers.tmdb import TMDBAPI
from resources.lib.modules.globals import g
from resources.lib.simkl.enrich import _simkl_detail_sync_dict
from resources.lib.simkl.menu_helpers import genre_sort_segment



def discover_by_genre_slug(catalog: str, genre_slug: str, page: int, page_limit: int) -> GenreBrowsePage:
    """Fetch one page of Simkl genre browse for a single slug."""
    slug = str(genre_slug or "").strip().lower()
    if not slug or slug not in SIMKL_GENRE_SLUGS.get(catalog, ()):
        return GenreBrowsePage([], False)
    if slug in ADULT_BLOCKED_SIMKL_GENRE_SLUGS and not adult_content_enabled():
        return GenreBrowsePage([], False)
    return _simkl_genre_browse_page(catalog, slug, page, page_limit)

def discover_by_tenrai_genres(
    genre_ids: str,
    page_limit: int,
    *,
    tenrai_page: int = 1,
    row_offset: int = 0,
) -> GenreBrowsePage:
    """Tenrai anime search for comma-separated MAL genre ids, bridged through Simkl."""
    from resources.lib.indexers.tenrai import TenraiAPI

    parsed_ids: list[int] = []
    for part in str(genre_ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            parsed_ids.append(int(part))
    if not parsed_ids:
        return GenreBrowsePage([], False)

    with_genres = ",".join(str(genre_id) for genre_id in parsed_ids)
    order_by, sort = tenrai_sort_for_genre_setting()
    sfw = not adult_content_enabled()
    g.log(
        f"Tenrai anime discover genres={with_genres} order_by={order_by} sort={sort} "
        f"sfw={sfw} (base list={genre_sort_segment('anime')})",
        "debug",
    )
    tenrai = TenraiAPI()

    if len(parsed_ids) == 1:
        return _discover_by_tenrai_genres_and(
            tenrai,
            with_genres,
            page_limit,
            tenrai_page=tenrai_page,
            row_offset=row_offset,
            order_by=order_by,
            sort=sort,
            sfw=sfw,
        )

    and_page = _discover_by_tenrai_genres_and(
        tenrai,
        with_genres,
        page_limit,
        tenrai_page=tenrai_page,
        row_offset=row_offset,
        order_by=order_by,
        sort=sort,
        sfw=sfw,
    )
    if and_page.items:
        return and_page

    g.log(
        f"Tenrai AND genre query returned no rows for {with_genres}; retrying with OR",
        "debug",
    )
    return _discover_by_tenrai_genres_or(
        tenrai,
        parsed_ids,
        page_limit,
        tenrai_page=tenrai_page,
        order_by=order_by,
        sort=sort,
        sfw=sfw,
    )

def discover_by_tmdb_genres(
    catalog: str,
    genre_ids: str,
    page_limit: int,
    *,
    tmdb_page: int = 1,
    tmdb_offset: int = 0,
) -> GenreBrowsePage:
    """TMDB discover for comma-separated genre IDs (AND), bridged through Simkl."""
    if not _tmdb_runtime_enabled():
        return GenreBrowsePage([], False)
    if catalog not in ("movie", "tv"):
        return GenreBrowsePage([], False)

    parsed_ids: list[int] = []
    for part in str(genre_ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            parsed_ids.append(int(part))
    if not parsed_ids:
        return GenreBrowsePage([], False)

    with_genres = ",".join(str(genre_id) for genre_id in parsed_ids)
    sort_by = tmdb_sort_for_genre_setting(catalog)
    tmdb = TMDBAPI()
    media_type = "movie" if catalog == "movie" else "tv"

    results: list[dict] = []
    seen_simkl_ids: set[int] = set()
    current_page = max(1, int(tmdb_page))
    row_offset = max(0, int(tmdb_offset))
    total_pages = current_page

    while len(results) < page_limit:
        if current_page > total_pages:
            break

        response = tmdb.get_json(
            f"discover/{media_type}",
            raw=True,
            page=current_page,
            language=tmdb.lang_full_code,
            sort_by=sort_by,
            with_genres=with_genres,
            include_adult=False,
        )
        if not response:
            break

        total_pages = int(response.get("total_pages") or 1)
        if current_page > total_pages:
            break

        rows = response.get("results") or []
        if row_offset >= len(rows):
            current_page += 1
            row_offset = 0
            continue

        for index in range(row_offset, len(rows)):
            row = rows[index]
            tmdb_id = row.get("id")
            if not tmdb_id:
                continue
            normalized = resolve_tmdb_to_simkl(int(tmdb_id), catalog)
            if not normalized:
                continue
            if _should_exclude_anime_from_genre_browse(normalized, catalog):
                continue
            simkl_id = normalized.get("simkl_id")
            if simkl_id is not None:
                key = int(simkl_id)
                if key in seen_simkl_ids:
                    continue
                seen_simkl_ids.add(key)
            results.append(normalized)
            if len(results) >= page_limit:
                next_offset = index + 1
                has_next = (next_offset < len(rows)) or (current_page < total_pages)
                return GenreBrowsePage(
                    results,
                    has_next,
                    current_page if next_offset < len(rows) else current_page + 1,
                    next_offset if next_offset < len(rows) else 0,
                )

        current_page += 1
        row_offset = 0

    return GenreBrowsePage(results, False)

def get_tenrai_anime_picker_items() -> dict[str, list[dict[str, Any]]]:
    return _get_tenrai_anime_picker_items_cached(adult_content_enabled())

def get_tmdb_genres(catalog: str) -> list[dict[str, Any]]:
    """Return ``[{id, name}]`` from TMDB genre list endpoints."""
    if not _tmdb_runtime_enabled():
        return []
    if catalog not in ("movie", "tv"):
        return []
    tmdb = TMDBAPI()
    media_type = "movie" if catalog == "movie" else "tv"
    payload = tmdb.get_json_cached(
        f"genre/{media_type}/list",
        raw=True,
        language=tmdb.lang_full_code,
    )
    if not payload:
        return []
    genres: list[dict[str, Any]] = []
    for row in payload.get("genres") or []:
        genre_id = row.get("id")
        name = row.get("name")
        if genre_id is None or not name:
            continue
        genres.append({"id": int(genre_id), "name": str(name)})
    if not adult_content_enabled():
        genres = [genre for genre in genres if genre["name"].strip().lower() != "erotica"]
    return sorted(genres, key=lambda item: item["name"].lower())

def tenrai_sort_for_genre_setting() -> tuple[str, str]:
    """Map general.genres.endpoint.anime to Tenrai ``order_by`` + ``sort``.

    MAL ``popularity`` and ``rank`` are rank indices (1 = best) — use ``asc``.
    ``members``, ``score``, and ``scored_by`` use ``desc`` for highest-first.
    """
    segment = genre_sort_segment("anime")
    mapping: dict[str, tuple[str, str]] = {
        "popular-this-week": ("members", "desc"),
        "popular-all-time": ("popularity", "asc"),
        "popular-this-month": ("members", "desc"),
        "rank": ("rank", "asc"),
        "release-date": ("start_date", "desc"),
        "voted": ("score", "desc"),
        "watched": ("members", "desc"),
    }
    return mapping.get(segment, ("popularity", "asc"))

def tmdb_sort_for_genre_setting(catalog: str) -> str:
    """Map general.genres.endpoint.* to TMDB discover ``sort_by``."""
    segment = genre_sort_segment(catalog)
    if segment == "release-date":
        return "primary_release_date.desc" if catalog == "movie" else "first_air_date.desc"
    if segment == "rank":
        return "vote_average.desc"
    if segment == "voted":
        return "vote_count.desc"
    if segment in TMDB_GENRE_POPULARITY_SORTS:
        return "popularity.desc"
    return "popularity.desc"
