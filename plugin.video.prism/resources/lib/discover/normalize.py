"""Normalize Simkl CDN/DB rows into sync-database insert dicts.

Output shape matches simkl_sync (see milling.py): top-level simkl_id plus
simkl_object.info / simkl_object.art. External IDs live once on info and in
info.ids using Simkl's simkl/tmdb/imdb keys — not duplicated simkl_id keys.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _parse_ids(blob: dict[str, Any]) -> dict[str, Any]:
    ids = blob.get("ids") or {}
    if not isinstance(ids, dict):
        return {}
    if ids.get("simkl_id") is None and ids.get("simkl") is not None:
        ids = {"simkl_id": ids["simkl"], **{k: v for k, v in ids.items() if k != "simkl"}}
    return ids


def _poster_url(path: str | None, *, kind: str = "posters") -> str | None:
    if not path:
        return None
    from resources.lib.simkl.images import simkl_image_url

    return simkl_image_url(path, kind=kind)


def _art_urls(item: dict[str, Any]) -> tuple[str | None, str | None]:
    poster = _poster_url(item.get("poster"), kind="posters")
    fanart = _poster_url(item.get("fanart"), kind="fanart")
    images = item.get("images") or {}
    if not poster:
        poster = _poster_url((images.get("poster") or {}).get("full"), kind="posters")
    if not fanart:
        fanart = _poster_url((images.get("fanart") or {}).get("full"), kind="fanart")
    return poster, fanart


def _normalize_air_date(raw: str | None) -> str | None:
    """Convert Simkl/MDBList date strings to ISO datetime for sync DB air_date."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    from resources.lib.modules.globals import g

    validated = g.validate_date(text)
    if validated:
        return validated

    if text.isdigit() and len(text) == 4:
        return g.validate_date(f"{text}-01-01")

    if "/" in text:
        parts = text.split("/")
        if len(parts) == 3:
            try:
                a, b, c = (int(p) for p in parts)
            except ValueError:
                return None
            if c < 1000:
                return None
            month, day, year = a, b, c
            if month > 12:
                day, month, year = a, b, c
            try:
                return g.validate_date(datetime(year, month, day).isoformat())
            except ValueError:
                return None

    return None


def _is_airing_status(status: str | None) -> bool | None:
    if not status:
        return None
    normalized = str(status).lower()
    if normalized in ("ended", "released", "canceled", "cancelled"):
        return False
    return True


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from_date(release_date: str | None) -> int | None:
    if not release_date:
        return None
    for part in str(release_date).replace("/", "-").split("-"):
        if len(part) == 4 and part.isdigit():
            return int(part)
    return None


def duration_from_runtime(runtime) -> int | None:
    """Convert Simkl runtime to Kodi duration in seconds.

    Simkl uses integer minutes (e.g. 52 on sync items) or strings like ``1h 45m`` / ``25m``.
    """
    if runtime is None:
        return None
    if isinstance(runtime, (int, float)):
        minutes = int(runtime)
        return minutes * 60 if minutes > 0 else None

    text = str(runtime).strip().lower()
    if not text:
        return None
    if text.isdigit():
        minutes = int(text)
        return minutes * 60 if minutes > 0 else None

    total_minutes = 0
    if "h" in text:
        try:
            total_minutes += int(text.split("h")[0].strip()) * 60
        except ValueError:
            pass
    if "m" in text:
        part = text.split("m")[0]
        if "h" in part:
            part = part.split("h")[-1]
        try:
            total_minutes += int(part.strip())
        except ValueError:
            pass
    return total_minutes * 60 if total_minutes > 0 else None


def runtime_minutes(runtime) -> int | None:
    seconds = duration_from_runtime(runtime)
    if seconds is None:
        return None
    return seconds // 60


def ensure_info_duration(info: dict[str, Any]) -> None:
    """Set ``duration`` (seconds) from Simkl ``runtime`` when TMDB has not enriched yet."""
    if not info or info.get("duration"):
        return

    for key in ("runtime", "tvshow.runtime"):
        duration = duration_from_runtime(info.get(key))
        if duration:
            info["duration"] = duration
            return

    tvshow = info.get("tvshow")
    if isinstance(tvshow, dict):
        duration = duration_from_runtime(tvshow.get("runtime"))
        if duration:
            info["duration"] = duration


def _build_info(item: dict[str, Any], catalog: str, simkl_id: int, ids: dict[str, Any], air_date: str | None) -> dict[str, Any]:
    from resources.lib.simkl.catalog import is_anime_movie_item, simkl_anime_type

    is_movie = catalog == "movie"
    mediatype = "movie" if is_movie else "tvshow"
    overview = item.get("overview")
    tmdb_id = _int_or_none(ids.get("tmdb"))
    tvdb_id = _int_or_none(ids.get("tvdb"))
    mal_id = _int_or_none(ids.get("mal"))
    imdb_id = ids.get("imdb")
    anime_type = simkl_anime_type(item)

    info: dict[str, Any] = {
        "simkl_id": simkl_id,
        "mediatype": mediatype,
        "type": "movie" if is_movie else "show",
        "title": item.get("title"),
        "plot": overview,
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id,
        "tvdb_id": tvdb_id,
        "mal_id": mal_id,
        "released": air_date,
        "premiered": air_date,
        "aired": air_date,
        "year": _year_from_date(air_date),
        "runtime": item.get("runtime"),
        "status": item.get("status"),
        "country": item.get("country"),
        "genres": item.get("genres") or [],
        "ids": {
            "simkl": simkl_id,
            "tmdb": ids.get("tmdb"),
            "imdb": imdb_id,
            "tvdb": ids.get("tvdb"),
            "mal": ids.get("mal"),
            "slug": ids.get("slug"),
        },
        "ratings": item.get("ratings") or {},
    }
    if anime_type:
        info["anime_type"] = anime_type

    if not is_movie:
        from resources.lib.simkl.ids import attach_show_identity

        attach_show_identity(info, simkl_id, ids.get("slug"))
        info["tvshowtitle"] = item.get("title")
        info["episode_count"] = _int_or_none(item.get("total_episodes"))

    airing = _is_airing_status(item.get("status"))
    if airing is not None:
        info["is_airing"] = airing
    if item.get("dateadded"):
        info["dateadded"] = item.get("dateadded")

    from resources.lib.simkl.field_map import enrich_info_from_simkl, finalize_playback_info
    from resources.lib.simkl.ids import canonicalize_info_identity

    enrich_info_from_simkl(info, item, catalog=catalog, mediatype=mediatype)
    if catalog == "anime" or is_anime_movie_item(item, catalog):
        info["catalog"] = "anime"
    elif catalog in ("movie", "tv"):
        info["catalog"] = catalog
    finalize_playback_info(info)
    canonicalize_info_identity(info)
    from resources.lib.simkl.field_map import sanitize_list_info

    return sanitize_list_info(info, catalog=catalog)


def cdn_item_to_sync_dict(item: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    from resources.lib.simkl.catalog import resolve_item_catalog

    storage_catalog = resolve_item_catalog(item, catalog)
    ids = _parse_ids(item)
    simkl_id = ids.get("simkl_id")
    if simkl_id is None:
        return None
    simkl_id = int(simkl_id)

    air_date = _normalize_air_date(item.get("release_date"))
    from resources.lib.simkl.images import attach_show_art

    art = attach_show_art(item)
    if not art.get("poster") or not art.get("fanart"):
        poster_url, fanart_url = _art_urls(item)
        if poster_url and not art.get("poster"):
            art["poster"] = poster_url
        if fanart_url and not art.get("fanart"):
            art["fanart"] = fanart_url
    info = _build_info(item, storage_catalog, simkl_id, ids, air_date)

    return {
        "simkl_id": simkl_id,
        "catalog": storage_catalog,
        "tmdb_id": info.get("tmdb_id"),
        "imdb_id": info.get("imdb_id"),
        "tvdb_id": info.get("tvdb_id"),
        "mal_id": info.get("mal_id"),
        "type": info.get("type"),
        "simkl_object": {
            "info": info,
            "art": art,
        },
    }


def db_row_to_sync_dict(row: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    """Convert simkl_cdn.db row dict to sync insert dict."""
    simkl_id = row.get("simkl_id")
    if simkl_id is None:
        return None

    ids: dict[str, Any] = {}
    if row.get("ids_json"):
        try:
            ids = json.loads(row["ids_json"])
        except json.JSONDecodeError:
            ids = {}

    ratings = {}
    if row.get("ratings_json"):
        try:
            ratings = json.loads(row["ratings_json"])
        except json.JSONDecodeError:
            ratings = {}

    genres = None
    if row.get("genres_json"):
        try:
            genres = json.loads(row["genres_json"])
        except json.JSONDecodeError:
            genres = None

    item = {
        "title": row.get("title"),
        "title_romaji": row.get("title_romaji"),
        "overview": row.get("overview"),
        "release_date": row.get("release_date"),
        "poster": row.get("poster"),
        "fanart": row.get("fanart"),
        "runtime": row.get("runtime"),
        "status": row.get("status"),
        "country": row.get("country"),
        "total_episodes": row.get("total_episodes"),
        "anime_type": row.get("anime_type"),
        "type": row.get("type"),
        "genres": genres,
        "ratings": ratings,
        "ids": {"simkl_id": int(simkl_id), **ids},
    }
    return cdn_item_to_sync_dict(item, catalog)
