"""Build a4kScrapers simple_info fields for anime from Simkl episode/show metadata."""
from __future__ import annotations

from typing import Any

from resources.lib.discover.normalize import _int_or_none
from resources.lib.simkl.field_map import tvdb_from_episode


def is_anime_item(ep_info: dict[str, Any], item_info: dict[str, Any] | None) -> bool:
    """True when the episode should use anime torrent scraper filters."""
    item_info = item_info or {}
    if ep_info.get("catalog") == "anime" or item_info.get("catalog") == "anime":
        return True
    for key in ("mal_id", "mal_show_id", "anidb_id", "anilist_id", "kitsu_id"):
        if ep_info.get(key):
            return True
    genres = ep_info.get("genre", [])
    if not isinstance(genres, list):
        genres = [genres] if genres else []
    for genre in genres:
        genre_lower = str(genre).lower()
        if "anime" in genre_lower:
            return True
        if "animation" in genre_lower:
            country = (ep_info.get("country_origin") or "").upper()
            if country in ("JP", "JPN", "JAPAN", "CN", "CHN", "CHINA"):
                return True
    return False


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _status_value(ep_info: dict[str, Any], show_info: dict[str, Any] | None) -> str:
    for source in (ep_info, show_info or {}):
        status = source.get("status")
        if status:
            return str(status)
    return ""


def build_anime_simple_info_fields(
    ep_info: dict[str, Any],
    item_info: dict[str, Any] | None,
    show_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return extra simple_info keys for anime scrapers (empty dict for non-anime)."""
    item_info = item_info or {}
    if not is_anime_item(ep_info, item_info):
        return {}

    show_info = show_info or {}
    show_ids = show_info.get("ids") if isinstance(show_info.get("ids"), dict) else {}
    ep_ids = ep_info.get("ids") if isinstance(ep_info.get("ids"), dict) else {}

    fields: dict[str, Any] = {}

    for flat_key, sources in (
        ("mal_id", (ep_info, show_info, ep_ids, show_ids)),
        ("anidb_id", (ep_info, show_info, ep_ids, show_ids)),
        ("anilist_id", (ep_info, show_info, ep_ids, show_ids)),
        ("kitsu_id", (ep_info, show_info, ep_ids, show_ids)),
        ("tvdb_id", (ep_info, show_info, ep_ids, show_ids)),
        ("simkl_id", (ep_info, show_info, ep_ids, show_ids)),
    ):
        value = None
        for source in sources:
            if not isinstance(source, dict):
                continue
            if flat_key == "mal_id":
                value = source.get("mal_id") or source.get("mal") or source.get("mal_show_id")
            elif flat_key == "simkl_id":
                value = source.get("simkl_id") or source.get("simkl")
            else:
                id_key = flat_key.replace("_id", "")
                value = source.get(flat_key) or source.get(id_key)
            if value is not None:
                break
        if value is not None:
            if flat_key == "simkl_id":
                fields[flat_key] = value
            else:
                parsed = _int_or_none(value)
                if parsed is not None:
                    fields[flat_key] = parsed

    if fields.get("tvdb_id") is not None:
        fields["thetvdb_id"] = fields["tvdb_id"]

    status = _status_value(ep_info, show_info)
    if status:
        fields["status"] = status

    menu_season = _first_int(ep_info.get("season"))
    menu_episode = _first_int(ep_info.get("episode"), ep_info.get("number"))
    anime_season = _first_int(ep_info.get("anime_season"))
    anime_episode = _first_int(ep_info.get("anime_episode"))

    tvdb_season = ep_info.get("tvdb_season")
    if tvdb_season is not None:
        if int(tvdb_season) == 0:
            fields["thetvdb_season"] = "0"
        else:
            fields["thetvdb_season"] = tvdb_season

    if anime_season is not None and anime_episode is not None:
        if menu_season is None:
            menu_season = anime_season
        if menu_episode is None:
            menu_episode = anime_episode
        if anime_season != menu_season or anime_episode != menu_episode:
            fields["alternative_season"] = str(anime_season)
            fields["alternative_episode"] = str(anime_episode)

        if anime_season > 1:
            tvdb_bucket = _first_int(tvdb_season, menu_season)
            if tvdb_bucket is not None and anime_season != tvdb_bucket:
                fields["thetvdb_part"] = anime_season

    # Explicit scrape coordinates for cloud / torrent matchers.
    tvdb_bucket, tvdb_ep = tvdb_from_episode(ep_info)
    if tvdb_bucket is not None:
        fields["tvdb_season_number"] = str(1 if int(tvdb_bucket) == 0 else tvdb_bucket)
    if tvdb_ep is not None:
        fields["tvdb_episode_number"] = str(tvdb_ep)

    simkl_episode = _first_int(ep_info.get("anime_episode"), ep_info.get("episode"))
    if simkl_episode is not None:
        fields["simkl_episode_number"] = str(simkl_episode)

    return fields
