"""Build Simkl-shaped season/episode trees from Simkl episode endpoints."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from resources.lib.discover.normalize import _normalize_air_date
from resources.lib.modules.globals import g

_RAW_EPISODES_CACHE: dict[tuple[int, str, str], list[dict[str, Any]]] = {}


def clear_raw_episodes_cache(show_id: int | None = None) -> None:
    """Drop in-process Simkl episode JSON (e.g. after sync invalidates a show)."""
    if show_id is None:
        _RAW_EPISODES_CACHE.clear()
        return
    sid = int(show_id)
    for key in [cache_key for cache_key in _RAW_EPISODES_CACHE if cache_key[0] == sid]:
        _RAW_EPISODES_CACHE.pop(key, None)


def fetch_raw_show_episodes(
    show_id: int,
    catalog: str,
    slug: str | None = None,
    *,
    api=None,
) -> list[dict[str, Any]]:
    """Simkl /tv|anime/episodes JSON with per-process reuse (disk cache via use_cache)."""
    cache_key = (int(show_id), str(catalog), slug or "")
    cached = _RAW_EPISODES_CACHE.get(cache_key)
    if cached is not None:
        g.log(f"[season trace] raw episodes process-cache hit show={show_id}", "debug")
        return cached

    if api is None:
        from resources.lib.indexers.simkl import thread_simkl_api

        api = thread_simkl_api()
    if catalog == "anime":
        raw_episodes = api.get_anime_episodes(int(show_id), slug=slug) or []
    else:
        raw_episodes = api.get_tv_episodes(int(show_id), slug=slug) or []

    normalized = [episode for episode in raw_episodes if isinstance(episode, dict)]
    _RAW_EPISODES_CACHE[cache_key] = normalized
    return normalized


def season_simkl_id(show_id: int, season_num: int) -> int:
    from resources.lib.simkl.ids import season_key

    return season_key(show_id, season_num)


def _episode_num(episode: dict[str, Any]) -> int | None:
    for key in ("episode", "number"):
        value = episode.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _season_num(episode: dict[str, Any], catalog: str = "tv") -> int:
    if episode.get("type") == "special":
        return 0
    if catalog == "anime":
        from resources.lib.simkl.field_map import anime_menu_season

        return anime_menu_season(episode)

    value = episode.get("season")
    if value is None:
        return 1
    try:
        season = int(value)
    except (TypeError, ValueError):
        return 1
    return 1 if season == 0 else season


def _debug_unique_seasons(raw_episodes: list[Any]) -> tuple[list[Any], list[Any]]:
    """Collect distinct API season fields for trace logging (None-safe)."""
    top: set[Any] = set()
    tvdb: set[Any] = set()
    for episode in raw_episodes:
        if not isinstance(episode, dict):
            continue
        if episode.get("season") is not None:
            top.add(episode.get("season"))
        tvdb_block = episode.get("tvdb")
        if isinstance(tvdb_block, dict) and tvdb_block.get("season") is not None:
            tvdb.add(tvdb_block.get("season"))
    return sorted(top, key=lambda value: (isinstance(value, str), value)), sorted(
        tvdb, key=lambda value: (isinstance(value, str), value)
    )


def _episode_simkl_id(episode: dict[str, Any]) -> int | None:
    ids = episode.get("ids") or {}
    simkl_id = ids.get("simkl_id") or ids.get("simkl")
    if simkl_id is None:
        return None
    return int(simkl_id)


def _build_episode_dict(
    show_id: int,
    season_id: int,
    season_num: int,
    episode: dict[str, Any],
    slug: str | None = None,
    *,
    catalog: str = "tv",
    episode_num_override: int | None = None,
) -> dict[str, Any]:
    from resources.lib.simkl.ids import attach_tv_context

    episode_num = _episode_num(episode)
    if catalog == "anime":
        from resources.lib.simkl.field_map import anime_menu_episode_number

        episode_num = anime_menu_episode_number(episode, season_num, episode_num)
    if episode_num is None:
        episode_num = episode_num_override
    ep_id = _episode_simkl_id(episode)
    if ep_id is None or episode_num is None:
        return {}

    aired = _normalize_air_date(episode.get("date"))
    description = episode.get("description")
    info = {
        "simkl_id": ep_id,
        "mediatype": "episode",
        "catalog": catalog,
        "season": season_num,
        "episode": episode_num,
        "number": episode_num,
        "title": episode.get("title"),
        "overview": description,
        "plot": description,
        "aired": aired,
        "first_aired": aired,
    }
    if episode.get("runtime") is not None:
        info["runtime"] = episode.get("runtime")
    attach_tv_context(info, show_id, season_num=season_num, season_row_id=season_id, slug=slug)
    from resources.lib.simkl.field_map import enrich_episode_from_simkl_api, finalize_playback_info

    enrich_episode_from_simkl_api(info, episode)
    info["season"] = season_num
    info["episode"] = episode_num
    info["number"] = episode_num
    finalize_playback_info(info)
    from resources.lib.simkl.ids import canonicalize_info_identity

    canonicalize_info_identity(info)
    from resources.lib.simkl.images import attach_episode_still

    art = attach_episode_still(info, episode) or {}
    simkl_object: dict[str, Any] = {"info": info}
    if art:
        simkl_object["art"] = art
    return {
        "simkl_id": ep_id,
        "simkl_show_id": show_id,
        "simkl_season_id": season_id,
        "catalog": catalog,
        "season": season_num,
        "episode": episode_num,
        "simkl_object": simkl_object,
    }


def _build_season_dict(
    show_id: int,
    season_num: int,
    episodes: list[dict[str, Any]],
    mill_episodes: bool,
    aired_episodes: int,
    slug: str | None = None,
    *,
    catalog: str = "tv",
) -> dict[str, Any]:
    from resources.lib.simkl.ids import attach_tv_context

    season_id = season_simkl_id(show_id, season_num)
    info = {
        "simkl_id": season_id,
        "mediatype": "season",
        "catalog": catalog,
        "season": season_num,
        "number": season_num,
        "aired_episodes": aired_episodes,
    }
    if season_num == 0:
        info["title"] = "Specials"
        info["sorttitle"] = "Specials"
    else:
        info["title"] = g.get_language_string(30528).format(season_num)
        info["sorttitle"] = info["title"]
    attach_tv_context(info, show_id, season_row_id=season_id, slug=slug)
    season = {
        "simkl_id": season_id,
        "simkl_show_id": show_id,
        "season": season_num,
        "aired_episodes": aired_episodes,
        "simkl_object": {"info": info},
    }
    if mill_episodes:
        season["episodes"] = episodes
    return season


def pull_show_seasons(
    show_id: int,
    catalog: str,
    mill_episodes: bool,
    slug: str | None = None,
    *,
    mill_season_filter: set[int] | frozenset[int] | None = None,
) -> list[dict[str, Any]]:
    endpoint = "anime/episodes" if catalog == "anime" else "tv/episodes"
    g.log(
        f"[season trace] pull_show_seasons show={show_id} catalog={catalog} endpoint={endpoint} slug={slug or show_id}",
        "debug",
    )
    raw_episodes = fetch_raw_show_episodes(show_id, catalog, slug=slug)

    if not raw_episodes:
        g.log(f"Simkl milling: no episodes returned for show {show_id} ({catalog})", "warning")
        return []

    sample = next((e for e in raw_episodes if isinstance(e, dict)), None)
    if sample:
        tvdb = sample.get("tvdb") or {}
        g.log(
            "[season trace] API sample ep="
            f"{sample.get('episode')} simkl.season={sample.get('season')} "
            f"tvdb.season={tvdb.get('season')} tvdb.episode={tvdb.get('episode')} type={sample.get('type')}",
            "debug",
        )
        top_seasons, tvdb_seasons = _debug_unique_seasons(raw_episodes)
        g.log(f"[season trace] API unique simkl.season={top_seasons} tvdb.season={tvdb_seasons}", "debug")

    grouped_raw: dict[int, list[dict[str, Any]]] = defaultdict(list)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    special_counters: dict[int, int] = defaultdict(int)
    for raw in raw_episodes:
        if not isinstance(raw, dict):
            continue
        season_num = _season_num(raw, catalog)
        grouped_raw[season_num].append(raw)
        if not mill_episodes:
            continue
        if mill_season_filter is not None and season_num not in mill_season_filter:
            continue
        episode_num_override = None
        if season_num == 0 and _episode_num(raw) is None:
            special_counters[season_num] += 1
            episode_num_override = special_counters[season_num]
        episode = _build_episode_dict(
            show_id,
            season_simkl_id(show_id, season_num),
            season_num,
            raw,
            slug=slug,
            catalog=catalog,
            episode_num_override=episode_num_override,
        )
        if episode:
            grouped[season_num].append(episode)

    seasons = []
    for season_num in sorted(grouped_raw.keys()):
        aired_count = sum(1 for raw in grouped_raw[season_num] if raw.get("aired") is True)
        if mill_episodes:
            episodes = sorted(grouped[season_num], key=lambda ep: ep.get("episode") or 0)
        else:
            episodes = []
        seasons.append(
            _build_season_dict(show_id, season_num, episodes, mill_episodes, aired_count, slug=slug, catalog=catalog)
        )

    g.log(
        "[season trace] milled buckets="
        f"{ {k: len(grouped_raw[k]) for k in sorted(grouped_raw.keys())} }",
        "debug",
    )
    return seasons


def count_special_episodes(show_id: int, catalog: str = "tv", slug: str | None = None) -> int:
    """Return how many Simkl episodes are tagged as specials for a show."""
    raw_episodes = fetch_raw_show_episodes(show_id, catalog, slug=slug)
    return sum(1 for raw in raw_episodes if raw.get("type") == "special")


def refresh_listed_episodes_from_simkl(db, media_items) -> int:
    """Fetch Simkl episode plot/still for visible menu rows without milling the whole show."""
    from resources.lib.simkl.ids import slug_from_info, show_id_from_item

    media_items = db._resolve_episode_list_ids(media_items)
    target_coords: set[tuple[int, int, int]] = set()
    target_ids: set[int] = set()
    shows_needed: set[int] = set()
    for item in media_items or []:
        if not isinstance(item, dict):
            continue
        coords = db._episode_coords_from_list_item(item)
        if coords:
            target_coords.add(coords)
            shows_needed.add(int(coords[0]))
        simkl_id = item.get("simkl_id")
        show_id = item.get("simkl_show_id") or show_id_from_item(item)
        if simkl_id is not None:
            target_ids.add(int(simkl_id))
        if show_id is not None:
            shows_needed.add(int(show_id))

    if not target_coords and not target_ids:
        g.log("Simkl listed-episode refresh: no episode targets", "debug")
        return 0

    show_meta: dict[int, tuple[str, str | None]] = {}
    for show_id in shows_needed:
        catalog = db.show_catalog(show_id) or "tv"
        row = db.fetchone("SELECT info FROM shows WHERE simkl_id=?", (show_id,))
        slug = slug_from_info((row or {}).get("info") or {}) if row else None
        show_meta[show_id] = (catalog, slug)

    refreshed = 0

    def _raw_matches(show_id: int, catalog: str, raw: dict[str, Any]) -> bool:
        ep_id = _episode_simkl_id(raw)
        if ep_id is not None and ep_id in target_ids:
            return True
        season_num = _season_num(raw, catalog)
        ep_num = _episode_num(raw)
        if catalog == "anime":
            from resources.lib.simkl.field_map import anime_menu_episode_number

            ep_num = anime_menu_episode_number(raw, season_num, ep_num)
        if ep_num is None:
            return False
        return (int(show_id), int(season_num), int(ep_num)) in target_coords

    for show_id in sorted(shows_needed):
        catalog, slug = show_meta[show_id]
        raw_episodes = fetch_raw_show_episodes(show_id, catalog, slug=slug, api=db.simkl_api)
        built: list[dict[str, Any]] = []
        for raw in raw_episodes or []:
            if not isinstance(raw, dict):
                continue
            if not _raw_matches(show_id, catalog, raw):
                continue
            season_num = _season_num(raw, catalog)
            episode = _build_episode_dict(
                show_id,
                season_simkl_id(show_id, season_num),
                season_num,
                raw,
                slug=slug,
                catalog=catalog,
            )
            if episode:
                built.append(episode)
        if not built:
            g.log(
                f"Simkl listed-episode refresh: 0 matched for show {show_id} ({catalog})",
                "debug",
            )
            continue
        db.insert_simkl_episodes(built)
        db._format_episodes(built, preloaded=True)
        refreshed += len(built)
        g.log(
            f"Simkl listed-episode refresh: {len(built)} episode(s) for show {show_id} ({catalog})",
            "debug",
        )
        try:
            from resources.lib.meta.paint_cache import clear_session_page_paint_for_show

            clear_session_page_paint_for_show(show_id)
        except Exception:
            pass
    return refreshed
