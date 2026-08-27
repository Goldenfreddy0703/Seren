"""TMDb Helper external player bridge — resolve TMDB ids to Simkl and dispatch getSources."""
from __future__ import annotations

import json
from typing import Any

import xbmcgui

from resources.lib.common import tools
from resources.lib.modules.globals import g
from resources.lib.simkl.browse import resolve_tmdb_to_simkl


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.startswith("{"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_player_info_string() -> dict[str, Any] | None:
    """Fallback when TMDb Helper player URL placeholders were not substituted."""
    try:
        raw = xbmcgui.Window(10000).getProperty("PlayerInfoString")
    except (RuntimeError, AttributeError):
        return None
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None

    tmdb_id = _coerce_int(meta.get("tmdb_id"))
    if tmdb_id is None:
        return None

    tmdb_type = str(meta.get("tmdb_type") or "").lower()
    parsed = {"tmdb_id": tmdb_id, "item_type": "movie" if tmdb_type == "movie" else "tv"}
    season = _coerce_int(meta.get("season"))
    episode = _coerce_int(meta.get("episode"))
    if season is not None and episode is not None:
        parsed["season"] = season
        parsed["episode"] = episode
    return parsed


def _parse_tmdb_helper_args(action_args: Any) -> dict[str, Any] | None:
    if isinstance(action_args, str):
        action_args = tools.deconstruct_action_args(action_args)
    if isinstance(action_args, dict):
        tmdb_id = _coerce_int(action_args.get("tmdb_id"))
        if tmdb_id is not None:
            return action_args

    return _parse_player_info_string()


def _resolve_tmdb_item(tmdb_id: int, *, is_movie: bool) -> dict | None:
    if is_movie:
        return resolve_tmdb_to_simkl(tmdb_id, "movie")
    for catalog in ("tv", "anime"):
        normalized = resolve_tmdb_to_simkl(tmdb_id, catalog)
        if normalized:
            return normalized
    return None


def _ensure_sync_item(normalized: dict) -> bool:
    catalog = normalized.get("catalog")
    if not catalog:
        mediatype = (normalized.get("simkl_object") or {}).get("info", {}).get("mediatype")
        catalog = "movie" if mediatype == "movie" else "tv"

    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.browse_catalog_seed import defer_browse_catalog_seed

    db = get_sync_database()
    db.insert_browse_page(catalog, [normalized])
    defer_browse_catalog_seed(catalog, [normalized])

    sync_params = {
        "sync_path": True,
        "skip_update": False,
        "hide_unaired": False,
        "hide_watched": False,
    }
    if catalog == "movie":
        rows = db.get_movie_list([normalized], **sync_params)
    else:
        rows = db.get_show_list([normalized], skip_mill=True, **sync_params)
    return bool(rows)


def _build_getsources_action_args(
    normalized: dict,
    *,
    season: int | None = None,
    episode: int | None = None,
    tmdb_id: int | None = None,
) -> dict[str, Any]:
    simkl_id = int(normalized["simkl_id"])
    catalog = normalized.get("catalog") or "tv"

    if season is not None and episode is not None:
        args: dict[str, Any] = {
            "mediatype": "episode",
            "simkl_show_id": simkl_id,
            "season": int(season),
            "episode": int(episode),
            "catalog": catalog,
            "external_play": True,
        }
        if tmdb_id is not None:
            args["tmdb_id"] = int(tmdb_id)
        return args

    return {
        "mediatype": "movie" if catalog == "movie" else "tvshow",
        "simkl_id": simkl_id,
        "catalog": catalog,
    }


def fetch_tmdb_season_episodes(tmdb_show_id: int, season_num: int, *, hide_unaired: bool = True) -> list[dict]:
    """Return aired TMDb episode payloads for a season (used for external SmartPlay playlists)."""
    from datetime import date

    from resources.lib.indexers.tmdb import TMDBAPI

    payload = TMDBAPI().get_json(f"tv/{int(tmdb_show_id)}/season/{int(season_num)}", raw=True)
    if not isinstance(payload, dict):
        return []

    today = date.today().isoformat()
    episodes: list[dict] = []
    for episode in payload.get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        ep_num = episode.get("episode_number")
        if ep_num is None:
            continue
        if hide_unaired:
            air_date = g.validate_date(episode.get("air_date"))
            if not air_date or air_date > today:
                continue
        episodes.append(episode)

    return sorted(episodes, key=lambda row: int(row.get("episode_number") or 0))


def fetch_tmdb_episode(
    tmdb_show_id: int,
    season_num: int,
    episode_num: int,
    *,
    hide_unaired: bool = False,
) -> dict | None:
    """Return a normalized TMDb episode payload (info + art) for external play."""
    from datetime import date

    from resources.lib.indexers.tmdb import TMDBAPI

    wrapped = TMDBAPI().get_episode(int(tmdb_show_id), int(season_num), int(episode_num))
    if not isinstance(wrapped, dict):
        return None
    tmdb_object = wrapped.get("tmdb_object")
    if not isinstance(tmdb_object, dict):
        return None

    if hide_unaired:
        info = tmdb_object.get("info") if isinstance(tmdb_object.get("info"), dict) else {}
        air_date = g.validate_date(info.get("premiered") or info.get("aired"))
        if not air_date or air_date > date.today().isoformat():
            return None

    return tmdb_object


def apply_tmdb_episode_meta(item: dict, tmdb_episode: dict | None) -> dict:
    """Overlay TMDb episode info/art onto a synthetic external-play item."""
    if not isinstance(tmdb_episode, dict):
        return item

    info = item.setdefault("info", {})
    tmdb_info = tmdb_episode.get("info") if isinstance(tmdb_episode.get("info"), dict) else tmdb_episode

    if tmdb_info.get("title"):
        info["title"] = tmdb_info["title"]
    elif tmdb_info.get("name"):
        info["title"] = tmdb_info["name"]

    plot = tmdb_info.get("plot") or tmdb_info.get("overview")
    if plot:
        info["plot"] = info["overview"] = plot

    aired = g.validate_date(tmdb_info.get("premiered") or tmdb_info.get("aired") or tmdb_info.get("air_date"))
    if aired:
        info["premiered"] = info["aired"] = aired

    if tmdb_info.get("duration"):
        info["duration"] = tmdb_info["duration"]
    elif tmdb_info.get("runtime"):
        try:
            info["duration"] = int(float(tmdb_info["runtime"])) * 60
        except (TypeError, ValueError):
            pass

    for key in ("episode", "season", "number"):
        if tmdb_info.get(key) is not None:
            info[key] = tmdb_info[key]

    tmdb_episode_id = tmdb_info.get("tmdb_id") or tmdb_info.get("id")
    if tmdb_episode_id is not None:
        info["tmdb_id"] = int(tmdb_episode_id)

    tmdb_art = tmdb_episode.get("art") if isinstance(tmdb_episode.get("art"), dict) else {}
    if tmdb_art:
        item_art = item.setdefault("art", {})
        for key, value in tmdb_art.items():
            if not value:
                continue
            if isinstance(value, list) and value:
                first = value[0]
                item_art[key] = first.get("url") if isinstance(first, dict) else first
            elif isinstance(value, str):
                item_art[key] = value

    return item


def build_external_episode_menu_item(
    show_id: int,
    season_num: int,
    episode_num: int,
    catalog: str,
    *,
    tmdb_show_id: int | None = None,
    tmdb_episode: dict | None = None,
) -> dict:
    """Build a TMDb-meta external SmartPlay row (Simkl show id + S/E for scrobble only)."""
    from resources.lib.database.session import get_sync_database

    action_args: dict[str, Any] = {
        "mediatype": "episode",
        "simkl_show_id": int(show_id),
        "season": int(season_num),
        "episode": int(episode_num),
        "catalog": catalog,
        "external_play": True,
    }
    if tmdb_show_id is not None:
        action_args["tmdb_id"] = int(tmdb_show_id)

    db = get_sync_database()
    item = tools._synthesize_external_episode_item(
        db, int(show_id), int(season_num), int(episode_num), action_args
    )

    if tmdb_episode is None and tmdb_show_id is not None:
        tmdb_episode = fetch_tmdb_episode(
            int(tmdb_show_id),
            int(season_num),
            int(episode_num),
            hide_unaired=False,
        )

    apply_tmdb_episode_meta(item, tmdb_episode or {})
    item["action_args"] = action_args
    return item


def _notify_resolution_failed(tmdb_id: int) -> None:
    g.log(f"tmdbHelper: could not resolve TMDB id {tmdb_id} to Simkl", "warning")
    message = g.get_language_string(30768) or "Title not found on Simkl"
    xbmcgui.Dialog().notification(
        g.ADDON_NAME,
        message,
        xbmcgui.NOTIFICATION_WARNING,
        4000,
    )


def play_from_tmdb_helper(params: dict[str, Any]) -> None:
    parsed = _parse_tmdb_helper_args(params.get("action_args"))
    if not parsed:
        g.log("tmdbHelper: invalid action_args", "warning")
        return

    tmdb_id = _coerce_int(parsed.get("tmdb_id"))
    if tmdb_id is None:
        g.log("tmdbHelper: missing tmdb_id", "warning")
        return

    item_type = str(parsed.get("item_type") or parsed.get("tmdb_type") or "").lower()
    season = _coerce_int(parsed.get("season"))
    episode = _coerce_int(parsed.get("episode"))

    if season is not None and episode is not None:
        is_movie = False
    elif item_type in ("movie", "movies"):
        is_movie = True
    elif item_type in ("tv", "tvshow", "show", "episode"):
        g.log("tmdbHelper: TV play requires season and episode", "warning")
        return
    else:
        is_movie = True

    normalized = _resolve_tmdb_item(tmdb_id, is_movie=is_movie)
    if not normalized:
        _notify_resolution_failed(tmdb_id)
        return

    if not _ensure_sync_item(normalized):
        _notify_resolution_failed(tmdb_id)
        return

    dispatch_args = _build_getsources_action_args(
        normalized,
        season=season,
        episode=episode,
        tmdb_id=tmdb_id if not is_movie else None,
    )

    dispatch_params = {
        "action": "getSources",
        "action_args": dispatch_args,
        "source_select": params.get("source_select", "false"),
        "smartPlay": params.get("smartPlay", "false"),
        "forceresumecheck": params.get("forceresumecheck", "true"),
        "tmdb_external_play": "true",
    }

    from resources.lib.modules import router

    # Nested dispatch in the same invoker — tmdbHelper is a threaded action so resolver/scrape pools work.
    router.dispatch(dispatch_params)
