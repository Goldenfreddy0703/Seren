"""Build Simkl API request bodies from Prism item info dicts."""

from __future__ import annotations

from typing import Any

from resources.lib.simkl.ids import show_id_from_info


def _ids_from_info(info: dict[str, Any]) -> dict[str, Any]:
    raw = info.get("ids") or {}
    simkl_id = show_id_from_info(info) or info.get("simkl_id") or raw.get("simkl") or raw.get("simkl_id")
    payload = {}
    if simkl_id:
        payload["simkl"] = int(simkl_id)
    for key in ("imdb", "tmdb", "tvdb", "mal", "anidb", "slug"):
        value = raw.get(key) or info.get(f"{key}_id")
        if value:
            payload[key] = value
    return payload


def _base_media(info: dict[str, Any]) -> dict[str, Any]:
    item = {"ids": _ids_from_info(info)}
    if info.get("title"):
        item["title"] = info["title"]
    if info.get("year"):
        item["year"] = info["year"]
    return item


def _is_anime(info: dict[str, Any]) -> bool:
    """Match anime detection used elsewhere (catalog + MAL/AniDB, not MAL alone)."""
    if not isinstance(info, dict):
        return False
    if info.get("catalog") == "anime":
        return True
    if info.get("mal_id") or info.get("anidb_id"):
        return True
    ids = info.get("ids") or {}
    return bool(ids.get("mal") or ids.get("anidb"))


def _show_info_for_child(info: dict[str, Any]) -> dict[str, Any]:
    show_id = show_id_from_info(info)
    child: dict[str, Any] = {
        "simkl_id": show_id,
        "title": info.get("tvshowtitle"),
        "ids": {"simkl": int(show_id)} if show_id else {},
    }
    if show_id:
        child["simkl_id"] = int(show_id)
    raw = info.get("ids") or {}
    for key in ("imdb", "tmdb", "tvdb", "mal", "anidb", "slug"):
        value = raw.get(key) or info.get(f"{key}_id")
        if value is not None and key not in child["ids"]:
            child["ids"][key] = value
    if info.get("mal_show_id") and "mal" not in child["ids"]:
        child["ids"]["mal"] = info["mal_show_id"]
    return child


def _simkl_episode_coords(info: dict[str, Any]) -> tuple[int, int]:
    """Simkl API season/episode — use native anime cour numbers when available."""
    season = info.get("season", 0)
    episode = info.get("episode", 0)
    if _is_anime(info):
        if info.get("anime_season") is not None:
            season = info["anime_season"]
        if info.get("anime_episode") is not None:
            episode = info["anime_episode"]
    return int(season or 0), int(episode or 0)


def _show_level_provider_ids(info: dict[str, Any]) -> dict[str, str]:
    """Parent-show provider ids — must not be sent as episode.ids (causes 404)."""
    show_ids: dict[str, str] = {}
    for provider, flat_keys in (
        ("tvdb", ("tvdb_show_id", "tvdb_id")),
        ("anidb", ("anidb_show_id", "anidb_id")),
        ("mal", ("mal_show_id", "mal_id")),
    ):
        for flat_key in flat_keys:
            value = info.get(flat_key)
            if value is not None:
                show_ids[provider] = str(value)
                break
    return show_ids


def _simkl_episode_id_lookup(info: dict[str, Any]) -> dict[str, str] | None:
    """Simkl scrobble episode.ids only accepts tvdb and anidb (not simkl_id)."""
    ep_ids = info.get("ids") or {}
    show_ids = _show_level_provider_ids(info)
    payload: dict[str, str] = {}
    tvdb = ep_ids.get("tvdb")
    if tvdb is not None and str(tvdb) != show_ids.get("tvdb"):
        payload["tvdb"] = str(tvdb)
    anidb = ep_ids.get("anidb")
    if anidb is not None and str(anidb) != show_ids.get("anidb"):
        payload["anidb"] = str(anidb)
    return payload or None


def _simkl_api_episode_block(info: dict[str, Any], *, for_history: bool = False) -> dict[str, Any]:
    """Episode object for Simkl scrobble / sync/history."""
    if _is_anime(info):
        ep_num = info.get("anime_episode")
        if ep_num is None:
            ep_num = info.get("episode") or info.get("number") or 0
        ep_num = int(ep_num or 0)

        tvdb_season = info.get("tvdb_season")
        tvdb_episode = info.get("tvdb_episode")
        if tvdb_season is not None:
            season = 1 if int(tvdb_season) == 0 else int(tvdb_season)
            number = int(tvdb_episode if tvdb_episode is not None else ep_num)
            block: dict[str, Any] = {"season": season, "number": number}
        elif info.get("anime_season") is not None:
            block = {"season": int(info["anime_season"]), "number": ep_num}
        elif for_history:
            # Each Simkl anime entry is its own cour; history uses a season bucket.
            block = {"season": 1, "number": ep_num}
        else:
            # Anime-native scrobble: flat episode number within the cour title.
            block = {"number": ep_num}
    else:
        season_num, episode_num = _simkl_episode_coords(info)
        block = {"season": season_num, "number": episode_num}

    episode_ids = _simkl_episode_id_lookup(info)
    if episode_ids:
        block["ids"] = episode_ids
    return block


def info_to_history_payload(info: dict[str, Any], force_show: bool = False) -> dict[str, Any]:
    mediatype = info.get("mediatype", "").lower()
    if mediatype == "movie":
        return {"movies": [_base_media(info)]}

    if force_show or mediatype == "tvshow":
        key = "anime" if _is_anime(info) else "shows"
        return {key: [_base_media(info)]}

    if mediatype == "season":
        key = "anime" if _is_anime(info) else "shows"
        item = _base_media(_show_info_for_child(info))
        item["seasons"] = [{"number": int(info.get("season", 0))}]
        return {key: [item]}

    if mediatype == "episode":
        key = "anime" if _is_anime(info) else "shows"
        item = _base_media(_show_info_for_child(info))
        episode_block = _simkl_api_episode_block(info, for_history=True)
        season_num = int(episode_block.get("season") or 1)
        episode_num = int(episode_block.get("number") or 0)
        item["seasons"] = [
            {
                "number": season_num,
                "episodes": [{"number": episode_num}],
            }
        ]
        return {key: [item]}

    key = "anime" if _is_anime(info) else "shows"
    return {key: [_base_media(info)]}


def info_to_list_payload(info: dict[str, Any], status: str, force_show: bool = False) -> dict[str, Any]:
    payload = info_to_history_payload(info, force_show=force_show)
    for key in ("movies", "shows", "anime"):
        if key in payload:
            for item in payload[key]:
                item["to"] = status
    return payload


def info_to_ratings_payload(info: dict[str, Any], rating: int, force_show: bool = False) -> dict[str, Any]:
    payload = info_to_history_payload(info, force_show=force_show)
    for key in ("movies", "shows", "anime"):
        if key in payload:
            for item in payload[key]:
                item["rating"] = int(rating)
    return payload


def ratings_force_show(info: dict[str, Any]) -> bool:
    return (info.get("mediatype") or "").lower() in ("episode", "season")


def info_to_scrobble_payload(info: dict[str, Any], progress: float) -> dict[str, Any]:
    mediatype = info.get("mediatype", "").lower()
    body: dict[str, Any] = {"progress": round(float(progress), 2)}

    if mediatype == "movie":
        body["movie"] = _base_media(info)
        return body

    show_info = _show_info_for_child(info)
    if not show_info.get("title"):
        show_info["title"] = info.get("tvshowtitle") or info.get("title")

    episode = _simkl_api_episode_block(info)

    is_anime = _is_anime(info)
    media_key = "anime" if is_anime else "show"
    body[media_key] = _base_media(show_info)
    body["episode"] = episode

    return body
