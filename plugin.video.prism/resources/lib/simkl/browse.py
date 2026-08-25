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


def _tmdb_runtime_enabled() -> bool:
    from resources.lib.meta.provider_settings import provider_enabled

    return provider_enabled("tmdb")

DISCOVER_ENDPOINTS: dict[str, dict[str, str]] = {
    "movie": {
        "trending": "movie_week",
        "popular": "movies_popular",
        "played": "movies_most_watched",
        "watched": "movies_most_watched",
        "anticipated": "movies_anticipated",
        "collected": "movies_top_simkl",
        "updated": "movies_new",
    },
    "tv": {
        "trending": "tv_week",
        "popular": "tv_popular",
        "played": "tv_most_watched",
        "watched": "tv_most_watched",
        "anticipated": "tv_anticipated",
        "collected": "tv_top_simkl",
        "new": "tv_new",
        "updated": "tv_month",
    },
    "anime": {
        "trending": "anime_week",
        "popular": "anime_popular",
        "played": "anime_most_watched",
        "watched": "anime_most_watched",
        "anticipated": "anime_anticipated",
        "collected": "anime_completed",
        "new": "anime_new",
        "popular_recent": "anime_new_year",
        "trending_recent": "anime_week",
    },
}


SIMKL_MOVIE_GENRE_SLUGS = (
    "action",
    "adventure",
    "animation",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "erotica",
    "family",
    "fantasy",
    "history",
    "horror",
    "music",
    "mystery",
    "romance",
    "science-fiction",
    "thriller",
    "tv-movie",
    "war",
    "western",
)

SIMKL_TV_GENRE_SLUGS = (
    "action",
    "adventure",
    "animation",
    "awards-show",
    "children",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "erotica",
    "family",
    "fantasy",
    "food",
    "game-show",
    "history",
    "home-and-garden",
    "horror",
    "indie",
    "korean-drama",
    "martial-arts",
    "mini-series",
    "musical",
    "mystery",
    "news",
    "podcast",
    "reality",
    "romance",
    "science-fiction",
    "soap",
    "special-interest",
    "sport",
    "suspense",
    "talk-show",
    "thriller",
    "travel",
    "video-game-play",
    "war",
    "western",
)

SIMKL_ANIME_GENRE_SLUGS = (
    "action",
    "adventure",
    "comedy",
    "drama",
    "ecchi",
    "educational",
    "fantasy",
    "gag-humor",
    "gore",
    "harem",
    "historical",
    "horror",
    "idol",
    "isekai",
    "josei",
    "kids",
    "magic",
    "martial-arts",
    "mecha",
    "military",
    "music",
    "mystery",
    "mythology",
    "parody",
    "psychological",
    "racing",
    "reincarnation",
    "romance",
    "samurai",
    "school",
    "sci-fi",
    "seinen",
    "shoujo",
    "shoujo-ai",
    "shounen",
    "shounen-ai",
    "slice-of-life",
    "space",
    "sports",
    "strategy-game",
    "super-power",
    "supernatural",
    "thriller",
    "vampire",
    "yaoi",
    "yuri",
)

SIMKL_GENRE_SLUGS = {
    "movie": SIMKL_MOVIE_GENRE_SLUGS,
    "tv": SIMKL_TV_GENRE_SLUGS,
    "anime": SIMKL_ANIME_GENRE_SLUGS,
}

ADULT_BLOCKED_SIMKL_GENRE_SLUGS = frozenset({"erotica"})

TENRAI_ANIME_GENRE_BUCKET_FILTERS = ("genres",)
TENRAI_ANIME_TAG_BUCKET_FILTERS = ("themes", "demographics")
TENRAI_ANIME_EXPLICIT_GENRE_FILTER = "explicit_genres"


def adult_content_enabled() -> bool:
    return g.get_bool_setting("general.adult.enabled")


def _visible_simkl_genre_slugs(catalog: str) -> tuple[str, ...]:
    slugs = SIMKL_GENRE_SLUGS.get(catalog, ())
    if adult_content_enabled():
        return slugs
    return tuple(slug for slug in slugs if slug not in ADULT_BLOCKED_SIMKL_GENRE_SLUGS)


class GenreBrowsePage(NamedTuple):
    items: list[dict]
    has_next_page: bool
    next_tmdb_page: int = 1
    next_tmdb_offset: int = 0
    next_imdb_cursor: str | None = None


def _slug_to_label(slug: str) -> str:
    return slug.replace("-", " ").title()


def get_simkl_genres(catalog: str) -> list[dict[str, str]]:
    """Return ``[{slug, label}]`` for Simkl genre browse pickers."""
    slugs = _visible_simkl_genre_slugs(catalog)
    if not slugs:
        return []
    return [{"slug": slug, "label": _slug_to_label(slug)} for slug in slugs]


GENRE_ICON_FOLDERS = {
    "movie": "movies",
    "tv": "tv",
    "anime": "Anime",
}


def genre_icons_path(catalog: str) -> str:
    """Per-catalog genre art folder; anime uses shared pack-agnostic assets when present."""
    import xbmcvfs

    folder = GENRE_ICON_FOLDERS.get(catalog, catalog)
    pack_path = f"{g.GENRES_PATH}{folder}/"
    if catalog == "anime":
        shared_path = f"{g.SHARED_GENRES_PATH}{folder}/"
        if xbmcvfs.exists(shared_path):
            return shared_path
    return pack_path


def genre_icon_art_path(catalog: str, slug: str) -> str:
    """Resolve a genre image path, allowing per-pack anime overrides over shared art."""
    import xbmcvfs

    filename = f"{slug}.png"
    if catalog == "anime":
        pack_path = f"{g.GENRES_PATH}{GENRE_ICON_FOLDERS['anime']}/{filename}"
        if xbmcvfs.exists(pack_path):
            return pack_path
        shared_path = f"{g.SHARED_GENRES_PATH}{GENRE_ICON_FOLDERS['anime']}/{filename}"
        if xbmcvfs.exists(shared_path):
            return shared_path
    return f"{genre_icons_path(catalog)}{filename}"


def genre_icon_dict(catalog: str, slug: str) -> dict:
    """Genre menu art from ``genres/{movies|tv|Anime}/{slug}.png``."""
    art_path = genre_icon_art_path(catalog, slug)
    return {"art": dict.fromkeys(["icon", "poster", "thumb"], art_path)}


def discover_genre_menu_icon(catalog: str) -> dict:
    """Discover submenu ``Genres`` row — prefer catalog icons pack, else list fallback."""
    icon_slug = {
        "movie": "movies_genres",
        "tv": "shows_genres",
        "anime": "anime_genres",
    }.get(catalog, "list")
    icon_path = f"{g.ICONS_PATH}{icon_slug}.png"
    import xbmcvfs

    if xbmcvfs.exists(icon_path):
        return g.create_icon_dict(icon_slug, g.ICONS_PATH)
    return g.create_icon_dict("list", g.ICONS_PATH)


def _simkl_genre_row_to_sync(row: dict, catalog: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    from resources.lib.simkl.media_ref import _simkl_api_list_row_to_item

    item = _simkl_api_list_row_to_item(row)
    normalized = normalize_simkl_item(item, catalog)
    if normalized:
        info = normalized.get("simkl_object", {}).get("info", {})
        if row.get("rank") is not None:
            info["score"] = row.get("rank")
    return normalized


def _simkl_genre_browse_page(
    catalog: str,
    genre_slug: str,
    page: int,
    page_limit: int,
) -> GenreBrowsePage:
    from resources.lib.simkl.menu_helpers import genre_page_has_next, genre_sort_segment, simkl_pagination_has_next

    sort = genre_sort_segment(catalog)
    api = SimklAPI()
    if catalog == "movie":
        path = f"/movies/genres/{genre_slug}/movies/all/all/{sort}"
    elif catalog == "anime":
        path = f"/anime/genres/{genre_slug}/all/all/all/{sort}"
    else:
        path = f"/tv/genres/{genre_slug}/all/all/all/all/{sort}"

    request_limit = min(page_limit, 60)
    payload, pagination = api.get_json_with_pagination(
        path,
        page=page,
        limit=request_limit,
        authorized=False,
        client_id=api.client_id,
    )
    if payload is None:
        return GenreBrowsePage([], False)
    if not isinstance(payload, list):
        return GenreBrowsePage([], False)

    results = []
    for row in payload:
        normalized = _simkl_genre_row_to_sync(row, catalog)
        if normalized:
            results.append(normalized)
        if len(results) >= page_limit:
            break

    has_next = simkl_pagination_has_next(
        pagination,
        fallback_page=page,
        fallback_count=len(payload),
        fallback_limit=request_limit,
    )
    if not has_next and not pagination:
        has_next = genre_page_has_next(page, len(payload), request_limit)

    return GenreBrowsePage(results, has_next)


def discover_by_genre_slug(catalog: str, genre_slug: str, page: int, page_limit: int) -> GenreBrowsePage:
    """Fetch one page of Simkl genre browse for a single slug."""
    slug = str(genre_slug or "").strip().lower()
    if not slug or slug not in SIMKL_GENRE_SLUGS.get(catalog, ()):
        return GenreBrowsePage([], False)
    if slug in ADULT_BLOCKED_SIMKL_GENRE_SLUGS and not adult_content_enabled():
        return GenreBrowsePage([], False)
    return _simkl_genre_browse_page(catalog, slug, page, page_limit)


TMDB_GENRE_POPULARITY_SORTS = frozenset(
    {
        "popular-this-week",
        "popular-all-time",
        "popular-this-month",
        "watched",
    }
)


@use_cache(cache_hours=24)
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


def _should_exclude_anime_from_genre_browse(item: dict, _browse_catalog: str) -> bool:
    """Drop Simkl anime rows from movie/TV multi-genre TMDB discover."""
    from resources.lib.simkl.catalog import is_anime_movie_info, resolve_item_catalog

    blob = item.get("simkl_object") or item
    info = (blob.get("info") or {}) if isinstance(blob, dict) else {}
    catalog = item.get("catalog") or info.get("catalog")

    if catalog == "anime":
        return True

    ids = info.get("ids") or (blob.get("ids") if isinstance(blob, dict) else {}) or {}
    if ids.get("mal") or info.get("mal_id"):
        return True

    if (blob.get("anime_type") if isinstance(blob, dict) else None) or info.get("anime_type"):
        return True

    if is_anime_movie_info(info):
        return True

    if isinstance(blob, dict) and resolve_item_catalog(blob, "") == "anime":
        return True

    return False


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


def _merge_tenrai_genre_rows(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for rows in lists:
        for row in rows:
            mal_id = row.get("mal_id")
            if mal_id is None:
                continue
            merged[int(mal_id)] = row
    return sorted(merged.values(), key=lambda item: str(item.get("name") or "").lower())


def get_tenrai_anime_picker_items() -> dict[str, list[dict[str, Any]]]:
    return _get_tenrai_anime_picker_items_cached(adult_content_enabled())


@use_cache(cache_hours=24)
def _get_tenrai_anime_picker_items_cached(include_adult: bool) -> dict[str, list[dict[str, Any]]]:
    """MAL genres + themes/demographics (+ explicit_genres when adult is enabled)."""
    from resources.lib.indexers.tenrai import TenraiAPI

    tenrai = TenraiAPI()
    genre_filter_names = list(TENRAI_ANIME_GENRE_BUCKET_FILTERS)
    if include_adult:
        genre_filter_names.append(TENRAI_ANIME_EXPLICIT_GENRE_FILTER)

    genre_sources = [tenrai.get_anime_genres_cached(name) for name in genre_filter_names]
    tag_sources = [tenrai.get_anime_genres_cached(name) for name in TENRAI_ANIME_TAG_BUCKET_FILTERS]

    for filter_name, rows in zip(genre_filter_names, genre_sources):
        if not rows:
            g.log(f"Tenrai genres/anime filter={filter_name} returned no rows", "warning")

    for filter_name, rows in zip(TENRAI_ANIME_TAG_BUCKET_FILTERS, tag_sources):
        if not rows:
            g.log(f"Tenrai genres/anime filter={filter_name} returned no rows", "warning")

    return {
        "genres": _merge_tenrai_genre_rows(*genre_sources),
        "tags": _merge_tenrai_genre_rows(*tag_sources),
    }


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


def _tenrai_genre_row_to_result(row: dict, seen_simkl_ids: set[int]) -> dict | None:
    mal_id = row.get("mal_id")
    if not mal_id:
        return None
    normalized = resolve_mal_to_simkl(int(mal_id))
    if not normalized:
        return None
    simkl_id = normalized.get("simkl_id")
    if simkl_id is not None:
        key = int(simkl_id)
        if key in seen_simkl_ids:
            return None
        seen_simkl_ids.add(key)
    return normalized


def _discover_by_tenrai_genres_and(
    tenrai,
    genre_ids: str,
    page_limit: int,
    *,
    tenrai_page: int,
    row_offset: int,
    order_by: str,
    sort: str,
    sfw: bool,
) -> GenreBrowsePage:
    """Tenrai comma-separated genres are ANDed (all tags must match)."""
    from resources.lib.indexers.tenrai import TENRAI_PAGE_SIZE

    results: list[dict] = []
    seen_simkl_ids: set[int] = set()
    current_page = max(1, int(tenrai_page))
    offset = max(0, int(row_offset))

    while len(results) < page_limit:
        response = tenrai.search_anime(
            page=current_page,
            limit=TENRAI_PAGE_SIZE,
            genres=genre_ids,
            order_by=order_by,
            sort=sort,
            sfw=sfw,
        )
        if not response:
            break

        pagination = response.get("pagination") or {}
        has_next_page = bool(pagination.get("has_next_page"))
        rows = response.get("data") or []

        if offset >= len(rows):
            if not has_next_page:
                break
            current_page += 1
            offset = 0
            continue

        for index in range(offset, len(rows)):
            normalized = _tenrai_genre_row_to_result(rows[index], seen_simkl_ids)
            if not normalized:
                continue
            results.append(normalized)
            if len(results) >= page_limit:
                next_offset = index + 1
                page_has_next = (next_offset < len(rows)) or has_next_page
                return GenreBrowsePage(
                    results,
                    page_has_next,
                    current_page if next_offset < len(rows) else current_page + 1,
                    next_offset if next_offset < len(rows) else 0,
                )

        if not has_next_page:
            break
        current_page += 1
        offset = 0

    return GenreBrowsePage(results, False)


def _discover_by_tenrai_genres_or(
    tenrai,
    parsed_ids: list[int],
    page_limit: int,
    *,
    tenrai_page: int,
    order_by: str,
    sort: str,
    sfw: bool,
) -> GenreBrowsePage:
    """Union browse when AND returns nothing (e.g. Hentai + Ecchi + Erotica)."""
    from resources.lib.indexers.tenrai import TENRAI_PAGE_SIZE

    streams: list[dict] = []
    has_next_page = False
    for genre_id in parsed_ids:
        response = tenrai.search_anime(
            page=max(1, int(tenrai_page)),
            limit=TENRAI_PAGE_SIZE,
            genres=str(genre_id),
            order_by=order_by,
            sort=sort,
            sfw=sfw,
        )
        if not response:
            streams.append({"rows": [], "index": 0})
            continue
        pagination = response.get("pagination") or {}
        has_next_page = has_next_page or bool(pagination.get("has_next_page"))
        streams.append({"rows": response.get("data") or [], "index": 0})

    results: list[dict] = []
    seen_simkl_ids: set[int] = set()
    while len(results) < page_limit:
        progressed = False
        for stream in streams:
            rows = stream["rows"]
            index = stream["index"]
            if index >= len(rows):
                continue
            normalized = _tenrai_genre_row_to_result(rows[index], seen_simkl_ids)
            stream["index"] = index + 1
            progressed = True
            if normalized:
                results.append(normalized)
                if len(results) >= page_limit:
                    return GenreBrowsePage(results, has_next_page)
        if not progressed:
            break

    return GenreBrowsePage(results, has_next_page)


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


def render_discover_endpoint(catalog: str, endpoint: str) -> bool:
    list_id = DISCOVER_ENDPOINTS.get(catalog, {}).get(endpoint)
    if not list_id:
        g.log(f"No Simkl discover mapping for {catalog}/{endpoint}", "warning")
        return False
    DiscoverRenderer().render_list(catalog, list_id)
    return True


def _catalog_from_simkl_match(row: dict, fallback: str) -> str:
    from resources.lib.simkl.catalog import resolve_item_catalog

    return resolve_item_catalog(row, fallback)


def _simkl_id_from_db(tmdb_id: int, catalog: str) -> int | None:
    from resources.lib.database.simkl_sync.movies import SimklSyncDatabase as MoviesDB
    from resources.lib.database.simkl_sync.shows import SimklSyncDatabase as ShowsDB

    if catalog == "movie":
        row = MoviesDB().fetchone("SELECT simkl_id FROM movies WHERE tmdb_id = ?", (int(tmdb_id),))
    else:
        row = ShowsDB().fetchone("SELECT simkl_id FROM shows WHERE tmdb_id = ?", (int(tmdb_id),))
    if row and row.get("simkl_id") is not None:
        return int(row["simkl_id"])
    return None


def _simkl_id_from_search_row(row: dict) -> int | None:
    ids = row.get("ids") or {}
    value = ids.get("simkl_id") if ids.get("simkl_id") is not None else ids.get("simkl")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _simkl_search_id_match(tmdb_id: int, catalog: str) -> tuple[int, str] | None:
    """Resolve TMDB id via GET /search/id; return (simkl_id, catalog) or None."""
    api = SimklAPI()
    lookup_type = "movie" if catalog == "movie" else "tv"
    payload = api.get_json(
        "/search/id",
        authorized=False,
        client_id=api.client_id,
        tmdb=int(tmdb_id),
        type=lookup_type,
    )
    if not payload:
        return None
    row = payload[0] if isinstance(payload, list) else payload
    if not isinstance(row, dict):
        return None
    simkl_id = _simkl_id_from_search_row(row)
    if simkl_id is None:
        return None
    return simkl_id, _catalog_from_simkl_match(row, catalog)


def _attach_tmdb_id(sync: dict, tmdb_id: int) -> dict:
    sync["tmdb_id"] = int(tmdb_id)
    info = sync.setdefault("simkl_object", {}).setdefault("info", {})
    info["tmdb_id"] = int(tmdb_id)
    if not sync.get("catalog"):
        sync["catalog"] = "movie" if info.get("mediatype") == "movie" else "tv"
    return sync


def _simkl_lookup_by_tmdb_api(tmdb_id: int, catalog: str) -> dict | None:
    match = _simkl_search_id_match(tmdb_id, catalog)
    if not match:
        return None
    simkl_id, resolved_catalog = match
    sync = _simkl_detail_sync_dict(simkl_id, resolved_catalog)
    if not sync:
        g.log(
            f"Simkl detail fetch failed for tmdb={tmdb_id} ({catalog}) simkl_id={simkl_id}",
            "debug",
        )
        return None
    return _attach_tmdb_id(sync, tmdb_id)


@use_cache(cache_hours=24)
def _simkl_lookup_by_tmdb_cached(tmdb_id: int, catalog: str) -> dict | None:
    return _simkl_lookup_by_tmdb_api(tmdb_id, catalog)


def _simkl_lookup_by_tmdb(tmdb_id: int, catalog: str) -> dict | None:
    return _simkl_lookup_by_tmdb_cached(int(tmdb_id), catalog)


def resolve_tmdb_to_simkl(tmdb_id: int, catalog: str) -> dict | None:
    normalized = _simkl_lookup_by_tmdb_cached(tmdb_id, catalog)
    if normalized:
        normalized = copy.deepcopy(normalized)
        if not normalized.get("catalog"):
            normalized["catalog"] = catalog
        return normalized

    simkl_id = _simkl_id_from_db(tmdb_id, catalog)
    if simkl_id is not None:
        g.log(
            f"Simkl /search/id miss for tmdb={tmdb_id} ({catalog}); trying local simkl_id={simkl_id}",
            "debug",
        )
        sync = _simkl_detail_sync_dict(simkl_id, catalog)
        if sync:
            return copy.deepcopy(_attach_tmdb_id(sync, tmdb_id))
    return None


def _imdb_runtime_enabled() -> bool:
    from resources.lib.meta.provider_settings import provider_enabled

    return provider_enabled("imdb")


def _imdb_sql_lookup_values(imdb_id: str) -> list[int | str]:
    from resources.lib.indexers.imdb import imdb_numeric_id
    from resources.lib.simkl.field_map import _normalize_imdb_id

    values: list[int | str] = []
    normalized = _normalize_imdb_id(imdb_id)
    if normalized:
        values.append(normalized)
    numeric = imdb_numeric_id(imdb_id)
    if numeric is not None:
        values.append(numeric)
    return list(dict.fromkeys(values))


def _simkl_id_from_db_by_imdb(imdb_id: str, catalog: str) -> int | None:
    from resources.lib.database.simkl_sync.movies import SimklSyncDatabase as MoviesDB
    from resources.lib.database.simkl_sync.shows import SimklSyncDatabase as ShowsDB

    lookup_values = _imdb_sql_lookup_values(imdb_id)
    if not lookup_values:
        return None
    placeholders = ",".join("?" for _ in lookup_values)
    if catalog == "movie":
        row = MoviesDB().fetchone(
            f"SELECT simkl_id FROM movies WHERE imdb_id IN ({placeholders})",
            tuple(lookup_values),
        )
    else:
        row = ShowsDB().fetchone(
            f"SELECT simkl_id FROM shows WHERE imdb_id IN ({placeholders})",
            tuple(lookup_values),
        )
    if row and row.get("simkl_id") is not None:
        return int(row["simkl_id"])
    return None


def _catalog_hint_from_imdb_title(title: dict, catalog_hint: str) -> str:
    title_type = title.get("titleType")
    if title_type in ("movie", "tvMovie"):
        return "movie"
    if title_type in ("tvSeries", "tvMiniSeries", "tvEpisode"):
        return "tv"
    return catalog_hint if catalog_hint in ("movie", "tv", "anime") else "movie"


def _attach_imdb_id(sync: dict, imdb_id: str) -> dict:
    from resources.lib.simkl.field_map import _normalize_imdb_id

    normalized = _normalize_imdb_id(imdb_id)
    if not normalized:
        return sync
    sync["imdb_id"] = normalized
    info = sync.setdefault("simkl_object", {}).setdefault("info", {})
    info["imdb_id"] = normalized
    info.setdefault("ids", {})["imdb"] = normalized
    return sync


def imdb_title_to_sync_dict(title: dict, catalog_hint: str) -> dict | None:
    if not isinstance(title, dict) or not title.get("id"):
        return None
    imdb_id = str(title["id"])
    catalog = _catalog_hint_from_imdb_title(title, catalog_hint)
    title_text = title.get("titleText") or title.get("primaryTitle") or ""
    if isinstance(title_text, dict):
        title_text = title_text.get("text") or ""
    year = None
    release_year = title.get("releaseYear")
    if isinstance(release_year, dict):
        year = release_year.get("year")
    item = {
        "title": title_text,
        "catalog": catalog,
        "ids": {"imdb": imdb_id},
        "imdb_id": imdb_id,
    }
    if year:
        item["release_date"] = str(year)
    image = title.get("primaryImage") or {}
    if isinstance(image, dict) and image.get("url"):
        item["poster"] = image["url"]
    plot_blob = title.get("plot")
    if isinstance(plot_blob, dict):
        plot_text = plot_blob.get("plotText")
        if isinstance(plot_text, dict) and plot_text.get("plainText"):
            item["overview"] = plot_text["plainText"]
    normalized = normalize_simkl_item(item, catalog)
    if not normalized:
        return None
    return _attach_imdb_id(normalized, imdb_id)


def _simkl_redirect_match(imdb_id: str, catalog_hint: str) -> tuple[int, str] | None:
    api = SimklAPI()
    lookup_type = "movie" if catalog_hint == "movie" else "tv"
    return api.redirect_simkl_id(imdb=imdb_id, type=lookup_type)


def _simkl_lookup_by_imdb_api(imdb_id: str, catalog_hint: str) -> dict | None:
    from resources.lib.simkl.field_map import _normalize_imdb_id

    normalized = _normalize_imdb_id(imdb_id)
    if not normalized:
        return None
    match = _simkl_redirect_match(normalized, catalog_hint)
    if not match:
        return None
    simkl_id, resolved_catalog = match
    sync = _simkl_detail_sync_dict(simkl_id, resolved_catalog)
    if not sync:
        g.log(
            f"Simkl detail fetch failed for imdb={normalized} ({catalog_hint}) simkl_id={simkl_id}",
            "debug",
        )
        return None
    return _attach_imdb_id(sync, normalized)


@use_cache(cache_hours=24)
def _simkl_lookup_by_imdb_cached(imdb_id: str, catalog_hint: str) -> dict | None:
    return _simkl_lookup_by_imdb_api(imdb_id, catalog_hint)


def resolve_imdb_to_simkl(imdb_id: str, catalog_hint: str = "movie") -> dict | None:
    from resources.lib.simkl.field_map import _normalize_imdb_id

    normalized = _normalize_imdb_id(imdb_id)
    if not normalized:
        return None
    hint = catalog_hint if catalog_hint in ("movie", "tv", "anime") else "movie"

    for catalog in (hint, "movie", "tv"):
        simkl_id = _simkl_id_from_db_by_imdb(normalized, catalog)
        if simkl_id is not None:
            sync = _simkl_detail_sync_dict(simkl_id, catalog)
            if sync:
                result = copy.deepcopy(_attach_imdb_id(sync, normalized))
                if not result.get("catalog"):
                    result["catalog"] = catalog
                return result

    normalized_row = _simkl_lookup_by_imdb_cached(normalized, hint)
    if normalized_row:
        result = copy.deepcopy(normalized_row)
        if not result.get("catalog"):
            result["catalog"] = _catalog_from_simkl_match(result, hint)
        return result
    return None


def get_imdb_genres(catalog: str) -> list[dict[str, Any]]:
    from resources.lib.indexers.imdb import get_imdb_genres as _get_imdb_genres

    return _get_imdb_genres(catalog)


def discover_by_imdb_genres(
    catalog: str,
    genre_slugs: str,
    page_limit: int,
    *,
    imdb_cursor: str | None = None,
) -> GenreBrowsePage:
    if not _imdb_runtime_enabled():
        return GenreBrowsePage([], False)
    if catalog not in ("movie", "tv"):
        return GenreBrowsePage([], False)

    parsed = [part.strip() for part in str(genre_slugs or "").split(",") if part.strip()]
    if not parsed:
        return GenreBrowsePage([], False)

    from resources.lib.indexers.imdb import cached_advanced_title_search
    from resources.lib.simkl.menu_helpers import genre_sort_segment

    sort_segment = genre_sort_segment(catalog)
    genre_key = ",".join(parsed)
    fetch_limit = max(page_limit * 3, page_limit + 10)
    titles, next_cursor, has_next = cached_advanced_title_search(
        catalog,
        genre_key,
        fetch_limit,
        imdb_cursor,
        sort_segment,
    )
    results: list[dict] = []
    seen_simkl: set[int] = set()
    for title in titles:
        if not isinstance(title, dict):
            continue
        imdb_id = title.get("id")
        if not imdb_id:
            continue
        normalized = resolve_imdb_to_simkl(str(imdb_id), catalog)
        if not normalized:
            continue
        if _should_exclude_anime_from_genre_browse(normalized, catalog):
            continue
        simkl_id = normalized.get("simkl_id")
        if simkl_id is not None:
            key = int(simkl_id)
            if key in seen_simkl:
                continue
            seen_simkl.add(key)
        results.append(normalized)
        if len(results) >= page_limit:
            break

    page_has_next = has_next and bool(next_cursor)
    g.log(
        f"IMDb genre page: {len(titles)} titles, {len(results)} resolved to Simkl ({catalog})",
        "debug",
    )
    return GenreBrowsePage(results, page_has_next, next_imdb_cursor=next_cursor if page_has_next else None)


def search_person_id(query: str) -> int | None:
    people = search_people(query, limit=1)
    if not people:
        return None
    return int(people[0]["id"])


def search_people(query: str, limit: int = 20) -> list[dict]:
    if not _tmdb_runtime_enabled():
        return []
    tmdb = TMDBAPI()
    response = tmdb.get_json("search/person", raw=True, query=query, page=1, language=tmdb.lang_full_code)
    results = (response or {}).get("results") or []
    if not results:
        g.log(f"TMDB person search returned no results for {query!r}", "debug")
    return [row for row in results if isinstance(row, dict)][:limit]


def get_person_combined_cast(person_id: int) -> list[dict]:
    return _fetch_person_combined_cast(int(person_id))


@use_cache(cache_hours=24)
def _fetch_person_combined_cast(person_id: int) -> list[dict]:
    if not _tmdb_runtime_enabled():
        return []
    tmdb = TMDBAPI()
    response = tmdb.get_json(
        f"person/{person_id}/combined_credits",
        raw=True,
        language=tmdb.lang_full_code,
    )
    if not response:
        return []
    cast = [
        row
        for row in (response.get("cast") or [])
        if isinstance(row, dict) and row.get("media_type") in ("movie", "tv")
    ]
    cast.sort(key=lambda c: c.get("popularity") or 0, reverse=True)
    return cast


def credit_catalog(credit: dict) -> str:
    return "movie" if credit.get("media_type") == "movie" else "tv"


def _tmdb_id_from_credit(credit: dict) -> int | None:
    for key in ("id", "tmdb_id"):
        value = credit.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _dedupe_person_credits(cast: list[dict]) -> list[dict]:
    seen: set[tuple[str, int]] = set()
    results: list[dict] = []
    for credit in cast:
        if not isinstance(credit, dict):
            continue
        tmdb_id = _tmdb_id_from_credit(credit)
        media_type = credit.get("media_type")
        if tmdb_id is None or media_type not in ("movie", "tv"):
            continue
        key = (media_type, tmdb_id)
        if key in seen:
            continue
        seen.add(key)
        results.append(credit)
    return results


@use_cache(cache_hours=24)
def get_person_details(person_id: int) -> dict:
    tmdb = TMDBAPI()
    response = tmdb.get_json(
        f"person/{int(person_id)}",
        raw=True,
        language=tmdb.lang_full_code,
    )
    return response if isinstance(response, dict) else {}


def combined_credits_by_person(person_id: int, page: int, page_limit: int) -> list[dict]:
    """Page through TMDB cast credits; keep only titles Simkl resolves via /search/id."""
    cast = _dedupe_person_credits(get_person_combined_cast(person_id))
    skip = max(0, (page - 1) * page_limit)
    results: list[dict] = []
    mapped = 0
    seen_simkl: set[int] = set()

    for credit in cast:
        tmdb_id = _tmdb_id_from_credit(credit)
        if tmdb_id is None:
            continue
        catalog = credit_catalog(credit)
        normalized = resolve_tmdb_to_simkl(tmdb_id, catalog)
        if not normalized:
            continue
        simkl_id = normalized.get("simkl_id")
        if simkl_id is None:
            continue
        simkl_id = int(simkl_id)
        if simkl_id in seen_simkl:
            continue
        seen_simkl.add(simkl_id)
        mapped += 1
        if skip > 0:
            skip -= 1
            continue
        role = credit.get("character") or credit.get("job")
        if role:
            normalized["_credit_role"] = str(role)
            results.append(normalized)
        if len(results) >= page_limit:
            break

    g.log(
        f"Actor filmography person={person_id} page={page}: "
        f"{len(results)} Simkl titles ({mapped} mapped in scan, {len(cast)} TMDB credits total)",
        "debug",
    )
    return results


def search_people_imdb(query: str, limit: int = 20) -> list[dict]:
    if not _imdb_runtime_enabled():
        return []
    from resources.lib.indexers.imdb import _imdb_image_url, imdb_person_display_fields, thread_imdb_api

    api = thread_imdb_api()
    people = api.search_people(query, limit=limit)
    details = api.person_details_batch([str(person.get("id")) for person in people if person.get("id")])
    mapped: list[dict] = []
    for person in people:
        if not isinstance(person, dict) or not person.get("id"):
            continue
        person_id = str(person["id"])
        detail = details.get(person_id) or {}
        fields = imdb_person_display_fields(detail)
        image = fields.get("profile_path") or _imdb_image_url(person.get("primaryImage"))
        mapped.append(
            {
                "id": person_id,
                "name": fields.get("name") or person.get("name") or "Unknown",
                "profile_path": image,
                "known_for_department": fields.get("known_for_department") or "Actor",
                "biography": fields.get("biography"),
            }
        )
    return mapped


def filmography_by_imdb_person(
    person_id: str,
    page: int,
    page_limit: int,
    *,
    imdb_cursor: str | None = None,
) -> tuple[list[dict], str | None, bool]:
    if not _imdb_runtime_enabled():
        return [], None, False
    from resources.lib.indexers.imdb import cached_person_credits

    cursor = imdb_cursor
    if page > 1 and not cursor:
        cursor = None
    titles, next_cursor, has_next = cached_person_credits(
        str(person_id),
        max(page_limit * 3, page_limit + 10),
        cursor,
    )
    skip = max(0, (page - 1) * page_limit) if page > 1 and not imdb_cursor else 0
    results: list[dict] = []
    seen_simkl: set[int] = set()

    for title in titles:
        if not isinstance(title, dict) or not title.get("id"):
            continue
        catalog = _catalog_hint_from_imdb_title(title, "movie")
        normalized = resolve_imdb_to_simkl(str(title["id"]), catalog)
        if not normalized:
            continue
        simkl_id = normalized.get("simkl_id")
        if simkl_id is not None:
            key = int(simkl_id)
            if key in seen_simkl:
                continue
            seen_simkl.add(key)
        if skip > 0:
            skip -= 1
            continue
        results.append(normalized)
        if len(results) >= page_limit:
            break

    g.log(
        f"IMDb filmography person={person_id} page={page}: {len(results)} Simkl rows",
        "debug",
    )
    return results, next_cursor if has_next else None, has_next and len(results) >= page_limit


def _simkl_search_mal_match(mal_id: int) -> tuple[int, str] | None:
    """Resolve MAL id via GET /search/id; return (simkl_id, catalog) or None."""
    api = SimklAPI()
    payload = api.get_json(
        "/search/id",
        authorized=False,
        client_id=api.client_id,
        mal=int(mal_id),
        type="anime",
    )
    if not payload:
        return None
    row = payload[0] if isinstance(payload, list) else payload
    if not isinstance(row, dict):
        return None
    simkl_id = _simkl_id_from_search_row(row)
    if simkl_id is None:
        return None
    return simkl_id, _catalog_from_simkl_match(row, "anime")


def _attach_mal_id(sync: dict, mal_id: int) -> dict:
    sync["mal_id"] = int(mal_id)
    info = sync.setdefault("simkl_object", {}).setdefault("info", {})
    info["mal_id"] = int(mal_id)
    ids = info.setdefault("ids", {})
    ids["mal"] = int(mal_id)
    if not sync.get("catalog"):
        sync["catalog"] = "anime"
    return sync


def _simkl_lookup_by_mal_api(mal_id: int) -> dict | None:
    match = _simkl_search_mal_match(mal_id)
    if not match:
        return None
    simkl_id, resolved_catalog = match
    sync = _simkl_detail_sync_dict(simkl_id, resolved_catalog)
    if not sync:
        g.log(
            f"Simkl detail fetch failed for mal={mal_id} simkl_id={simkl_id}",
            "debug",
        )
        return None
    return _attach_mal_id(sync, mal_id)


@use_cache(cache_hours=24)
def _simkl_lookup_by_mal_cached(mal_id: int) -> dict | None:
    return _simkl_lookup_by_mal_api(mal_id)


def resolve_mal_to_simkl(mal_id: int) -> dict | None:
    normalized = _simkl_lookup_by_mal_cached(int(mal_id))
    if not normalized:
        return None
    normalized = copy.deepcopy(normalized)
    if not normalized.get("catalog"):
        normalized["catalog"] = "anime"
    return normalized


def tmdb_discover_page(catalog: str, page: int, page_limit: int, **filters) -> list[dict]:
    if not _tmdb_runtime_enabled():
        return []
    tmdb = TMDBAPI()
    media_type = "movie" if catalog == "movie" else "tv"
    params: dict[str, Any] = {
        "page": page,
        "language": tmdb.lang_full_code,
        "sort_by": filters.pop("sort_by", "popularity.desc"),
        "include_adult": False,
    }
    params.update(filters)
    response = tmdb.get_json(f"discover/{media_type}", raw=True, **params)
    if not response:
        return []
    results = []
    for row in response.get("results") or []:
        tmdb_id = row.get("id")
        if not tmdb_id:
            continue
        normalized = _simkl_lookup_by_tmdb(int(tmdb_id), catalog)
        if normalized:
            results.append(normalized)
        if len(results) >= page_limit:
            break
    return results


def discover_by_year(catalog: str, year: int, page: int, page_limit: int) -> list[dict]:
    if catalog == "movie":
        return tmdb_discover_page(
            catalog,
            page,
            page_limit,
            primary_release_year=year,
            sort_by="popularity.desc",
        )
    return tmdb_discover_page(
        catalog,
        page,
        page_limit,
        first_air_date_year=year,
        sort_by="popularity.desc",
    )


def airing_episodes(date: str = "today") -> list[dict]:
    """Return mixed-episode shaped rows from Simkl /tv/airing."""
    api = SimklAPI()
    rows = api.get_json("/tv/airing", authorized=False, client_id=api.client_id, date=date, sort="time")
    if not rows:
        return []

    episodes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ids = row.get("ids") or {}
        simkl_id = ids.get("simkl_id") or ids.get("simkl")
        ep = row.get("episode") or {}
        if simkl_id is None or ep.get("season") is None or ep.get("episode") is None:
            continue
        show_id = int(simkl_id)
        season = int(ep["season"])
        episode_num = int(ep["episode"])
        from resources.lib.simkl.ids import attach_tv_context, season_key

        season_id = season_key(show_id, season)
        ep_id = ep.get("ids", {}).get("simkl_id") or (show_id * 1000000 + season * 1000 + episode_num)
        show_slug = ids.get("slug")
        info = {
            "simkl_id": int(ep_id),
            "mediatype": "episode",
            "season": season,
            "episode": episode_num,
            "title": row.get("title"),
            "aired": row.get("date"),
            "first_aired": row.get("date"),
            "tvshowtitle": row.get("title"),
            "score": row.get("rank") or 1.0,
        }
        attach_tv_context(info, show_id, season_num=season, season_row_id=season_id, slug=show_slug)
        episodes.append(
            {
                "simkl_id": int(ep_id),
                "simkl_show_id": show_id,
                "simkl_season_id": season_id,
                "season": season,
                "episode": episode_num,
                "first_aired": row.get("date"),
                "simkl_object": {"info": info},
            }
        )
    return episodes
