"""Convert raw Simkl API detail responses into sync-DB insert shapes."""
from __future__ import annotations

from typing import Any

from resources.lib.discover.normalize import _art_urls, _build_info, _normalize_air_date


def _simkl_id_from_api(payload: dict[str, Any]) -> int | None:
    ids = payload.get("ids") or {}
    value = ids.get("simkl") or ids.get("simkl_id") or payload.get("simkl_id")
    if value is None:
        return None
    return int(value)


def api_detail_to_sync_dict(payload: dict[str, Any] | None, catalog: str) -> dict[str, Any] | None:
    """Map GET /tv/{id}, GET /movies/{id}, or GET /anime/{id} JSON to cdn_item_to_sync_dict shape."""
    if not payload or not isinstance(payload, dict):
        return None

    simkl_id = _simkl_id_from_api(payload)
    if simkl_id is None:
        return None

    ids = dict(payload.get("ids") or {})
    if ids.get("simkl_id") is None and ids.get("simkl") is not None:
        ids["simkl_id"] = ids["simkl"]

    air_date = _normalize_air_date(
        payload.get("released")
        or payload.get("first_aired")
        or payload.get("last_aired")
        or payload.get("year")
    )
    poster, fanart = _art_urls(payload)
    if not poster and payload.get("poster"):
        poster = payload.get("poster")
    if not fanart and payload.get("fanart"):
        fanart = payload.get("fanart")

    item = {
        "title": payload.get("title"),
        "en_title": payload.get("en_title"),
        "overview": payload.get("overview") or payload.get("description"),
        "release_date": air_date,
        "poster": poster,
        "fanart": fanart,
        "runtime": payload.get("runtime"),
        "status": payload.get("status"),
        "country": payload.get("country"),
        "genres": payload.get("genres") or [],
        "anime_type": payload.get("anime_type"),
        "type": payload.get("type"),
        "ids": ids,
        "ratings": payload.get("ratings"),
        "release_dates": payload.get("release_dates"),
        "network": payload.get("network"),
        "certification": payload.get("certification"),
        "trailer": payload.get("trailer"),
        "season_count": payload.get("season_count"),
        "total_episodes": payload.get("total_episodes"),
        "episode_count": payload.get("episode_count"),
    }
    if catalog != "anime":
        item["title_en"] = payload.get("title_en") or payload.get("en_title")
    if payload.get("title_romaji"):
        item["title_romaji"] = payload.get("title_romaji")

    from resources.lib.discover.normalize import cdn_item_to_sync_dict

    sync_dict = cdn_item_to_sync_dict(item, catalog)
    if not sync_dict:
        return None

    info = sync_dict.setdefault("simkl_object", {}).setdefault("info", {})
    if payload.get("season_count") is not None:
        info["season_count"] = payload.get("season_count")
    if payload.get("total_episodes") is not None:
        info["episode_count"] = payload.get("total_episodes")
    if payload.get("episode_count") is not None and info.get("episode_count") is None:
        info["episode_count"] = payload.get("episode_count")

    from resources.lib.simkl.field_map import enrich_info_from_simkl, finalize_playback_info

    enrich_info_from_simkl(info, payload, catalog=sync_dict.get("catalog", catalog), mediatype=info.get("mediatype"))
    finalize_playback_info(info)

    from resources.lib.simkl.images import attach_show_art

    simkl_art = attach_show_art(payload)
    if simkl_art:
        art = sync_dict.setdefault("simkl_object", {}).setdefault("art", {})
        for key, value in simkl_art.items():
            if value:
                art[key] = value

    return sync_dict
