"""Simkl identity helpers — one unique simkl_id per Simkl entity.

Simkl model (see simkl.apib):
  - Movie, show, and each episode each have their own unique numeric Simkl id
  - A "show id", "episode id", or "movie id" in code **is** that entity's simkl_id
  - Season is **not** a Simkl entity — seasons are identified by show simkl_id + season number

Public action_args (routing / menus):
  tvshow:  {mediatype: tvshow,  simkl_id: <show_simkl_id>, catalog?}
  season:  {mediatype: season,  simkl_id: <show_simkl_id>, season: <number>, catalog?}
  episode: {mediatype: episode, simkl_id: <episode_simkl_id>, catalog?}
  movie:   {mediatype: movie,   simkl_id: <movie_simkl_id>, catalog?}

Secondary ids (TMDB, TVDB, IMDB, MAL, …) live in ``info.ids`` and are mirrored to flat
``info.tmdb_id`` / SQL columns only for Kodi labels and meta-table JOINs.

Internal simklSync.db only (not Simkl API ids):
  ``seasons.simkl_id`` = synthetic ``season_key(show_simkl_id, season_num)`` for SQL FKs
  ``simkl_show_id`` / ``simkl_season_id`` on child rows = parent show / season row refs
"""
from __future__ import annotations

import json
from typing import Any
from urllib import parse

SEASON_ID_FACTOR = 100_000
# Season menus only use small integers; real Simkl show ids can exceed SEASON_ID_FACTOR and
# would otherwise decode as fake synthetic rows (e.g. 2561864 -> show 25 season 61864).
MAX_SYNTHETIC_SEASON = 999

# Map info.ids keys → flat info keys (secondary ids only; simkl handled separately).
_IDS_TO_FLAT: dict[str, str] = {
    "tmdb": "tmdb_id",
    "tvdb": "tvdb_id",
    "imdb": "imdb_id",
    "mal": "mal_id",
    "anidb": "anidb_id",
    "anilist": "anilist_id",
    "kitsu": "kitsu_id",
    "slug": "slug",
}


def entity_simkl_id(info: dict[str, Any] | None) -> int | None:
    """Return the real Simkl API id for this entity (not synthetic season row ids)."""
    if not info:
        return None
    mediatype = info.get("mediatype")
    raw = info.get("simkl_id")
    if raw is None:
        ids = info.get("ids")
        if isinstance(ids, dict):
            raw = ids.get("simkl_id") or ids.get("simkl")
    if raw is None:
        return None
    value = int(raw)
    if mediatype == "season" or is_synthetic_season_id(value):
        return None
    return value


def parent_show_simkl_id(info: dict[str, Any] | None) -> int | None:
    """Parent show simkl_id for season/episode rows (same value as tvshow.simkl_id)."""
    if not info:
        return None
    show_id = show_id_from_info(info)
    if show_id is not None:
        return int(show_id)
    legacy = info.get("simkl_show_id")
    return int(legacy) if legacy is not None else None


def ensure_info_ids(info: dict[str, Any]) -> dict[str, Any]:
    """Ensure ``info.ids`` exists; entity simkl id is stored as both ``simkl`` and ``simkl_id``."""
    nested = info.get("ids")
    if not isinstance(nested, dict):
        nested = {}
        info["ids"] = nested
    entity_id = entity_simkl_id(info)
    if entity_id is not None:
        nested.setdefault("simkl", entity_id)
        nested.setdefault("simkl_id", entity_id)
        info.setdefault("simkl_id", entity_id)
    return nested


def sync_flat_ids_from_ids(info: dict[str, Any]) -> None:
    """Mirror secondary ids from ``info.ids`` onto flat fields (ids blob is canonical)."""
    ids = info.get("ids")
    if not isinstance(ids, dict):
        return
    from resources.lib.discover.normalize import _int_or_none
    from resources.lib.simkl.field_map import _normalize_imdb_id

    for ids_key, flat_key in _IDS_TO_FLAT.items():
        value = ids.get(ids_key)
        if value is None or info.get(flat_key) is not None:
            continue
        if flat_key == "imdb_id":
            info[flat_key] = _normalize_imdb_id(value)
        elif flat_key.endswith("_id"):
            info[flat_key] = _int_or_none(value)
        else:
            info[flat_key] = value


def sync_ids_from_flat(info: dict[str, Any]) -> None:
    """Back-fill ``info.ids`` from legacy flat secondary id fields."""
    nested = ensure_info_ids(info)
    for ids_key, flat_key in _IDS_TO_FLAT.items():
        if info.get(flat_key) is not None and nested.get(ids_key) is None:
            nested[ids_key] = info[flat_key]


def align_parent_show_identity(info: dict[str, Any]) -> None:
    """Point ``simkl_show_id`` and ``tvshow.simkl_id`` at the same parent show simkl_id."""
    mediatype = info.get("mediatype")
    if mediatype not in ("episode", "season"):
        if mediatype == "tvshow" and info.get("simkl_id") is not None:
            attach_show_identity(info, int(info["simkl_id"]), slug_from_info(info))
        return

    show_id = parent_show_simkl_id(info)
    if show_id is None:
        return

    info["simkl_show_id"] = show_id
    season_num = info.get("season")
    season_row_id = info.get("simkl_season_id")
    if season_num is not None:
        season_row_id = season_row_id or season_key(show_id, int(season_num))
    attach_tv_context(
        info,
        show_id,
        season_num=int(season_num) if season_num is not None else None,
        season_row_id=int(season_row_id) if season_row_id is not None else None,
        slug=slug_from_info(info),
    )


def canonicalize_info_identity(info: dict[str, Any] | None) -> None:
    """Unify simkl_id, info.ids, flat secondary ids, and parent show refs on one info dict."""
    if not info:
        return
    sync_ids_from_flat(info)
    sync_flat_ids_from_ids(info)
    align_parent_show_identity(info)
    canonicalize_sync_identity(info)
    sync_ids_from_flat(info)
    sync_flat_ids_from_ids(info)


def secondary_id(info: dict[str, Any] | None, provider: str) -> Any:
    """Read a secondary provider id from ``info.ids`` (canonical), then flat ``info`` fields."""
    if not info:
        return None
    ids = info.get("ids")
    if isinstance(ids, dict):
        if ids.get(provider) is not None:
            return ids[provider]
        alt = f"{provider}_id" if provider != "imdb" else None
        if alt and ids.get(alt) is not None:
            return ids[alt]
    flat_key = _IDS_TO_FLAT.get(provider, f"{provider}_id")
    return info.get(flat_key)


def sync_sql_columns_from_info(item: dict[str, Any]) -> None:
    """Canonicalize info ids and mirror secondary ids onto SyncRow SQL join columns."""
    if not item or not isinstance(item, dict):
        return
    blob = item.get("simkl_object") or {}
    info = blob.get("info") if isinstance(blob.get("info"), dict) else item.get("info")
    if isinstance(info, dict):
        canonicalize_info_identity(info)
        entity_id = entity_simkl_id(info)
        if entity_id is not None:
            item["simkl_id"] = entity_id
    if not isinstance(info, dict):
        return
    from resources.lib.discover.normalize import _int_or_none

    for sql_col, provider in (("tmdb_id", "tmdb"), ("tvdb_id", "tvdb"), ("imdb_id", "imdb")):
        value = secondary_id(info, provider)
        if value is None:
            continue
        if sql_col == "imdb_id":
            item[sql_col] = value
        else:
            parsed = _int_or_none(value)
            if parsed is not None:
                item[sql_col] = parsed


def canonicalize_sync_row(item: dict[str, Any] | None) -> None:
    """Unify ids on a SyncRow before DB insert (top-level SQL columns follow info.ids)."""
    sync_sql_columns_from_info(item)
def season_key(show_id: int, season_num: int) -> int:
    """Synthetic season row id used in simklSync.db (show_id * 100000 + season)."""
    return int(show_id) * SEASON_ID_FACTOR + int(season_num)


def synthetic_episode_id(show_id: int, season_num: int, episode_num: int) -> int:
    """Fallback episode row id when Simkl sync payload has no per-episode simkl id."""
    return int(show_id) * 1_000_000 + int(season_num) * 1_000 + int(episode_num)


def is_synthetic_season_id(value: int | None) -> bool:
    if value is None:
        return False
    value = int(value)
    if value <= 0:
        return False
    show_id, season_num = split_synthetic_season_id(value)
    if show_id <= 0 or season_num < 0 or season_num > MAX_SYNTHETIC_SEASON:
        return False
    if season_key(show_id, season_num) != value:
        return False
    # Synthetic row ids are always >= show_id * FACTOR; plain show ids are smaller.
    return value >= show_id * SEASON_ID_FACTOR


def split_synthetic_season_id(season_row_id: int) -> tuple[int, int]:
    season_row_id = int(season_row_id)
    show_id = season_row_id // SEASON_ID_FACTOR
    season_num = season_row_id % SEASON_ID_FACTOR
    return show_id, season_num


def api_ref(simkl_id: int | str | None = None, slug: str | None = None) -> int | str | None:
    """Prefer slug for website/deep links only — Simkl API GET paths use numeric simkl IDs."""
    if simkl_id is not None:
        return int(simkl_id)
    if slug:
        return str(slug).strip()
    return None


def api_path_id(simkl_id: int | str | None) -> int:
    """Numeric Simkl ID for API URL paths (never slug — slug is response-only on Simkl API)."""
    return int(simkl_id)


def slug_from_info(info: dict[str, Any] | None) -> str | None:
    if not info:
        return None
    tvshow = info.get("tvshow")
    if isinstance(tvshow, dict) and tvshow.get("slug"):
        return str(tvshow["slug"]).strip()
    ids = info.get("ids")
    if isinstance(ids, dict) and ids.get("slug"):
        return str(ids["slug"]).strip()
    for key in ("slug", "tvshow.slug"):
        if info.get(key):
            return str(info[key]).strip()
    return None


def slug_from_item(item: dict[str, Any] | None) -> str | None:
    if not item:
        return None
    slug = slug_from_info(item.get("info"))
    if slug:
        return slug
    simkl_object = item.get("simkl_object")
    if isinstance(simkl_object, dict):
        slug = slug_from_info(simkl_object.get("info"))
        if slug:
            return slug
    return slug_from_info(item)


def show_api_path(simkl_id: int, slug: str | None = None, suffix: str = "") -> str:
    path = f"/tv/{api_path_id(simkl_id)}"
    if suffix:
        path = f"{path}/{suffix.lstrip('/')}"
    return path


def movie_api_path(simkl_id: int, slug: str | None = None, suffix: str = "") -> str:
    path = f"/movies/{api_path_id(simkl_id)}"
    if suffix:
        path = f"{path}/{suffix.lstrip('/')}"
    return path


def tv_episodes_api_path(simkl_id: int, slug: str | None = None) -> str:
    return f"/tv/episodes/{api_path_id(simkl_id)}"


def anime_episodes_api_path(simkl_id: int, slug: str | None = None) -> str:
    return f"/anime/episodes/{api_path_id(simkl_id)}"


def anime_api_path(simkl_id: int, slug: str | None = None, suffix: str = "") -> str:
    path = f"/anime/{api_path_id(simkl_id)}"
    if suffix:
        path = f"{path}/{suffix.lstrip('/')}"
    return path


def attach_show_identity(info: dict[str, Any], show_id: int, slug: str | None = None) -> None:
    """Canonical tvshow block on show-level info (discover/sync ingest)."""
    show_id = int(show_id)
    tvshow = info.setdefault("tvshow", {})
    if isinstance(tvshow, dict):
        tvshow.setdefault("simkl_id", show_id)
        resolved_slug = slug or slug_from_info(info)
        if resolved_slug:
            tvshow.setdefault("slug", resolved_slug)


def attach_tv_context(
    info: dict[str, Any],
    show_id: int,
    *,
    season_num: int | None = None,
    season_row_id: int | None = None,
    show_info: dict[str, Any] | None = None,
    slug: str | None = None,
) -> None:
    """Write canonical tvshow block plus legacy FK aliases on season/episode info."""
    info.setdefault("simkl_show_id", int(show_id))
    tvshow = info.setdefault("tvshow", {})
    if isinstance(tvshow, dict):
        tvshow.setdefault("simkl_id", int(show_id))
        resolved_slug = slug or slug_from_info(show_info) or slug_from_info(info)
        if resolved_slug:
            tvshow.setdefault("slug", resolved_slug)
    if season_row_id is not None:
        info.setdefault("simkl_season_id", int(season_row_id))
    elif season_num is not None:
        info.setdefault("simkl_season_id", season_key(int(show_id), int(season_num)))


def show_id_from_info(info: dict[str, Any] | None) -> int | None:
    if not info:
        return None
    tvshow = info.get("tvshow")
    if isinstance(tvshow, dict) and tvshow.get("simkl_id") is not None:
        return int(tvshow["simkl_id"])
    for key in ("simkl_show_id", "tvshow.simkl_id", "show_id"):
        if info.get(key) is not None:
            return int(info[key])
    if info.get("mediatype") == "tvshow" and info.get("simkl_id") is not None:
        return int(info["simkl_id"])
    return None


def show_id_from_item(item: dict[str, Any] | None) -> int | None:
    """Resolve parent show id from a menu/sync item wrapper or flat info dict."""
    if not item:
        return None
    show_id = item.get("simkl_show_id")
    if show_id is not None:
        return int(show_id)
    show_id = show_id_from_info(item.get("info"))
    if show_id is not None:
        return show_id
    nested = item.get("show")
    if isinstance(nested, dict):
        show_id = nested.get("simkl_id") or show_id_from_info(nested.get("simkl_object", {}).get("info"))
        if show_id is not None:
            return int(show_id)
    return show_id_from_info(item)


def season_row_id_from_info(info: dict[str, Any] | None) -> int | None:
    """Synthetic season row id for sync DB / torrent season packs."""
    if not info:
        return None
    if info.get("simkl_season_id") is not None:
        return int(info["simkl_season_id"])
    show_id = show_id_from_info(info)
    season_num = info.get("season")
    if show_id is not None and season_num is not None:
        return season_key(show_id, int(season_num))
    item_id = info.get("simkl_id")
    if item_id is not None and is_synthetic_season_id(item_id):
        return int(item_id)
    return None


def release_title_cache_key(info: dict[str, Any] | None) -> str | None:
    show_id = show_id_from_info(info)
    if show_id is None:
        return None
    return f"last_resolved_release_title.{show_id}"


def torrent_cache_id_keys(item_meta: dict[str, Any]) -> tuple[str, int, int | None, int | None]:
    """Return (cache_table, episode_id, season_row_id, show_id) for torrent cache lookups."""
    info = item_meta.get("info") or {}
    simkl_id = int(item_meta["simkl_id"])
    if info.get("mediatype") == "episode":
        show_id = show_id_from_info(info)
        season_row_id = season_row_id_from_info(info)
        return "tvshows", simkl_id, season_row_id, show_id
    return "movies", simkl_id, None, None


def attach_tv_show_id(items: list[dict[str, Any]], show_id: int, slug: str | None = None) -> None:
    """Ensure episode dicts carry parent show context for mixed-list builders."""
    for item in items:
        item["simkl_show_id"] = show_id
        info = item.get("info")
        if isinstance(info, dict):
            attach_tv_context(info, show_id, slug=slug)
        simkl_object = item.get("simkl_object")
        if isinstance(simkl_object, dict) and isinstance(simkl_object.get("info"), dict):
            attach_tv_context(simkl_object["info"], show_id, slug=slug)


def normalize_action_args(action_args: dict[str, Any] | None) -> dict[str, Any] | None:
    if not action_args:
        return action_args

    args = dict(action_args)
    mediatype = args.get("mediatype")

    if mediatype == "tvshow":
        args.pop("simkl_show_id", None)
        args.pop("simkl_season_id", None)
        args.pop("season", None)
        return args

    if mediatype == "movie":
        args.pop("simkl_show_id", None)
        args.pop("simkl_season_id", None)
        return args

    if mediatype == "season":
        show_id = args.get("simkl_show_id")
        season_num = args.get("season")
        item_id = args.get("simkl_id")

        # Canonical menu shape: show simkl_id + season number.
        if show_id is None and item_id is not None and season_num is not None:
            explicit_show = int(item_id)
            explicit_season = int(season_num)
            if is_synthetic_season_id(explicit_show):
                split_show, split_season = split_synthetic_season_id(explicit_show)
                if explicit_season != split_season:
                    # e.g. simkl_id=2561864 + season=4 — show id, not synthetic row 256186400004.
                    args["simkl_id"] = explicit_show
                    args["season"] = explicit_season
                else:
                    args["simkl_id"] = split_show
                    args["season"] = split_season
            else:
                args["simkl_id"] = explicit_show
                args["season"] = explicit_season
            args.pop("simkl_show_id", None)
            args.pop("simkl_season_id", None)
            return args

        if show_id is not None:
            show_id = int(show_id)
            if season_num is None and item_id is not None and is_synthetic_season_id(item_id):
                _, season_num = split_synthetic_season_id(int(item_id))
            if season_num is not None:
                args["simkl_id"] = show_id
                args["season"] = int(season_num)
        elif item_id is not None and is_synthetic_season_id(item_id):
            show_id, season_num = split_synthetic_season_id(int(item_id))
            args["simkl_id"] = show_id
            args["season"] = season_num

        args.pop("simkl_show_id", None)
        args.pop("simkl_season_id", None)
        return args

    if mediatype == "episode":
        args.pop("simkl_season_id", None)
        return args

    return args


def show_id_from_args(action_args: dict[str, Any] | None) -> int | None:
    args = normalize_action_args(action_args)
    if not args:
        return None
    mediatype = args.get("mediatype")
    if mediatype == "tvshow":
        return int(args["simkl_id"]) if args.get("simkl_id") is not None else None
    if mediatype == "season":
        return int(args["simkl_id"]) if args.get("simkl_id") is not None else None
    return None


def season_num_from_args(action_args: dict[str, Any] | None) -> int | None:
    args = normalize_action_args(action_args)
    if not args or args.get("mediatype") != "season":
        return None
    season = args.get("season")
    return int(season) if season is not None else None


def episode_num_from_info(info: dict[str, Any] | None) -> int | None:
    if not info:
        return None
    number = info.get("number")
    episode = info.get("episode")
    if number is not None and episode is not None:
        try:
            num, ep = int(number), int(episode)
            if num != ep:
                return num
        except (TypeError, ValueError):
            pass
    for key in ("number", "episode"):
        if info.get(key) is not None:
            return int(info[key])
    return None


def canonicalize_sync_identity(info: dict[str, Any] | None) -> None:
    """Align synthetic season row ids and episode numbers before simklSync.db writes."""
    if not info:
        return

    mediatype = info.get("mediatype")
    show_id = info.get("simkl_show_id")
    if show_id is None:
        show_id = show_id_from_info(info)

    season_num = info.get("season")
    if season_num is not None:
        season_num = int(season_num)

    if mediatype == "season" and show_id is not None and season_num is not None:
        row_id = season_key(int(show_id), season_num)
        info["simkl_id"] = row_id
        info["simkl_season_id"] = row_id
        info["simkl_show_id"] = int(show_id)
        info["number"] = season_num

    if mediatype == "episode" and show_id is not None and season_num is not None:
        info["simkl_show_id"] = int(show_id)
        info["simkl_season_id"] = season_key(int(show_id), season_num)
        ep_num = episode_num_from_info(info)
        if ep_num is not None:
            info["episode"] = ep_num
            info["number"] = ep_num

    align_parent_show_identity(info)
def resolve_season_filter(
    show_id: int | None,
    *,
    season: int | None = None,
    season_row_id: int | None = None,
) -> tuple[int | None, int | None]:
    """Map public season number or legacy synthetic row id to (show_id, season_number)."""
    if season is not None:
        return show_id, int(season)
    if season_row_id is None or show_id is None:
        return show_id, None
    if is_synthetic_season_id(season_row_id):
        _, season_num = split_synthetic_season_id(int(season_row_id))
        return show_id, season_num
    if int(season_row_id) < SEASON_ID_FACTOR:
        return show_id, int(season_row_id)
    return show_id, None


def season_row_id_from_args(action_args: dict[str, Any] | None) -> int | None:
    show_id = show_id_from_args(action_args)
    season_num = season_num_from_args(action_args)
    if show_id is None or season_num is None:
        return None
    return season_key(show_id, season_num)


def episode_id_from_args(action_args: dict[str, Any] | None) -> int | None:
    args = normalize_action_args(action_args)
    if not args or args.get("mediatype") != "episode":
        return None
    value = args.get("simkl_id")
    return int(value) if value is not None else None


def show_id_for_episode_action(action_args: dict[str, Any] | None) -> int | None:
    """Resolve parent show id when action_args only contains the episode simkl_id."""
    args = normalize_action_args(action_args)
    if not args or args.get("mediatype") != "episode":
        return None
    if args.get("simkl_show_id") is not None:
        return int(args["simkl_show_id"])
    episode_id = episode_id_from_args(args)
    if episode_id is None:
        return None
    from resources.lib.database.session import get_sync_database

    row = get_sync_database().fetchone(
        "SELECT simkl_show_id FROM episodes WHERE simkl_id = ?",
        (episode_id,),
    )
    return int(row["simkl_show_id"]) if row and row.get("simkl_show_id") is not None else None


def build_action_args(item: dict[str, Any]) -> dict[str, Any]:
    """Build canonical plugin action_args from a sync item or meta wrapper."""
    from resources.lib.modules.globals import g
    from resources.lib.modules.metadataHandler import MetadataHandler

    get = MetadataHandler.get_simkl_info
    info = MetadataHandler.info(item)
    mediatype = get(item, "mediatype", info.get("mediatype"))
    simkl_id = get(item, "simkl_id", info.get("simkl_id"))

    args: dict[str, Any] = {"simkl_id": simkl_id, "mediatype": mediatype}

    catalog = _resolve_action_catalog(item, info, mediatype, simkl_id)
    if catalog in ("movie", "tv", "anime"):
        args["catalog"] = catalog

    if simkl_id is None:
        import inspect

        g.log("Simkl ID not found in item!", "error")
        g.log(inspect.stack(), "error")
        g.log(item, "error")

    if mediatype == "season":
        show_id = show_id_from_info(info) or get(item, "simkl_show_id", info.get("simkl_show_id"))
        season_num = get(item, "season", info.get("season"))
        if show_id is None and simkl_id is not None and is_synthetic_season_id(simkl_id):
            show_id, season_num = split_synthetic_season_id(int(simkl_id))
        if show_id is not None:
            args["simkl_id"] = int(show_id)
        if season_num is not None:
            args["season"] = int(season_num)

    if mediatype == "episode":
        show_id = show_id_from_info(info) or get(item, "simkl_show_id", info.get("simkl_show_id"))
        if show_id is not None:
            args["simkl_show_id"] = int(show_id)

    normalized = normalize_action_args(args)
    return normalized if normalized is not None else args


def _resolve_action_catalog(
    item: dict[str, Any],
    info: dict[str, Any],
    mediatype: str | None,
    simkl_id: int | str | None,
) -> str | None:
    """Resolve movie / tv / anime for action_args (widgets + scrape-time catalog)."""
    blob_info = (item.get("simkl_object") or {}).get("info") if isinstance(item.get("simkl_object"), dict) else {}
    for source in (
        item.get("catalog"),
        info.get("catalog") if isinstance(info, dict) else None,
        blob_info.get("catalog") if isinstance(blob_info, dict) else None,
    ):
        if source in ("movie", "tv", "anime"):
            return str(source)

    if mediatype == "movie":
        return "movie"

    if mediatype in ("tvshow", "season", "episode"):
        show_id = show_id_from_info(info)
        if mediatype == "tvshow":
            show_id = show_id or simkl_id
        elif mediatype == "season":
            show_id = show_id or simkl_id
        elif mediatype == "episode" and show_id is None and simkl_id is not None:
            show_id = show_id_for_episode_action({"mediatype": "episode", "simkl_id": simkl_id})
        if show_id is not None:
            from resources.lib.database.session import get_sync_database

            catalog = get_sync_database().show_catalog(int(show_id))
            if catalog in ("movie", "tv", "anime"):
                return catalog
        return "tv"

    return None


def parse_stored_action_args(raw: Any) -> dict[str, Any] | None:
    """Parse action_args from DB (JSON or legacy URL-encoded JSON) or an in-memory dict."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return normalize_action_args(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(parse.unquote(raw))
            if isinstance(parsed, dict):
                return normalize_action_args(parsed)
        except (ValueError, TypeError):
            return None
    return None


def serialize_action_args(item: dict[str, Any]) -> str:
    """Persist action_args in simklSync.db as plain JSON (encode only at URL boundary)."""
    return json.dumps(build_action_args(item), sort_keys=True)


def encode_action_args(item: dict[str, Any]) -> str:
    """URL-encode action_args for plugin:// URLs and playlists."""
    if isinstance(item, dict) and item.get("simkl_id") and item.get("mediatype") and not item.get("simkl_object"):
        payload = normalize_action_args(item) or item
    else:
        payload = build_action_args(item)
    return parse.quote(json.dumps(payload, sort_keys=True))


def show_id_from_playlist_action_args(action_args: dict[str, Any] | str) -> int | None:
    """Resolve show id from encoded or decoded playlist action_args."""
    if isinstance(action_args, str):
        action_args = parse.unquote(action_args)
        try:
            action_args = json.loads(action_args)
        except ValueError:
            return None
    args = normalize_action_args(action_args)
    if not args:
        return None
    mediatype = args.get("mediatype")
    if mediatype in ("tvshow", "season"):
        return show_id_from_args(args)
    if mediatype == "episode":
        return show_id_for_episode_action(args)
    return None
