"""Simkl detail recommendations and anime franchise relations."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode

import xbmc

from resources.lib.indexers.simkl import SimklAPI
from resources.lib.modules.globals import g
from resources.lib.simkl.ids import (
    normalize_action_args,
    show_id_for_episode_action,
    show_id_from_args,
)
from resources.lib.simkl.media_ref import normalize_simkl_item, render_mixed_sync_list


def _infer_show_detail_catalog(action_args: dict[str, Any], show_id: int) -> str:
    if action_args.get("catalog") == "anime":
        return "anime"

    params = g.REQUEST_PARAMS or {}
    if params.get("catalog") == "anime":
        return "anime"

    path = str(params.get("url") or params.get("action") or "").lower()
    if "anime" in path or "myanime" in path:
        return "anime"

    from resources.lib.database.session import get_sync_database

    if get_sync_database().show_catalog(show_id) == "anime":
        return "anime"
    return "tv"


def detail_target_from_action_args(action_args: dict[str, Any] | None) -> tuple[str, int] | None:
    args = normalize_action_args(action_args or {})
    if not args:
        return None

    mediatype = (args.get("mediatype") or "").lower()
    if mediatype == "movie":
        simkl_id = args.get("simkl_id")
        return ("movie", int(simkl_id)) if simkl_id is not None else None

    show_id = show_id_from_args(args)
    if show_id is None and mediatype == "episode":
        show_id = show_id_for_episode_action(args)
    if show_id is None:
        return None

    catalog = _infer_show_detail_catalog(args, int(show_id))
    return catalog, int(show_id)


def _fetch_detail(catalog: str, simkl_id: int, *, prefer_anime: bool = False) -> dict[str, Any] | None:
    from resources.lib.simkl.ids import anime_api_path, movie_api_path, show_api_path

    api = SimklAPI()
    params = {"client_id": api.client_id}
    paths = []
    if catalog == "movie":
        paths.append(movie_api_path(simkl_id))
    elif catalog == "anime" or prefer_anime:
        paths.append(anime_api_path(simkl_id))
        paths.append(show_api_path(simkl_id))
    else:
        paths.append(show_api_path(simkl_id))
        paths.append(anime_api_path(simkl_id))

    for path in paths:
        data = api.get_json(path, authorized=False, **params)
        if isinstance(data, dict) and not data.get("error"):
            return data
    return None


def _related_entry_to_sync(entry: dict[str, Any], *, relation_label: bool = False) -> dict[str, Any] | None:
    from resources.lib.simkl.catalog import resolve_item_catalog

    catalog = resolve_item_catalog(entry, "")
    item: dict[str, Any] = {
        "title": entry.get("title"),
        "en_title": entry.get("en_title"),
        "title_en": entry.get("title_en") or entry.get("en_title"),
        "title_romaji": entry.get("title_romaji"),
        "year": entry.get("year"),
        "release_date": f"{entry.get('year')}-01-01" if entry.get("year") else None,
        "poster": entry.get("poster"),
        "url": entry.get("url"),
        "type": entry.get("type"),
        "anime_type": entry.get("anime_type"),
        "ids": entry.get("ids") or {},
    }
    if entry.get("users_percent") is not None:
        item["rank"] = entry.get("users_percent")

    sync = normalize_simkl_item(item, catalog)
    if not sync:
        return None

    info = sync.get("simkl_object", {}).get("info")
    if isinstance(info, dict) and (catalog == "anime" or info.get("mal_id")):
        from resources.lib.simkl.field_map import ensure_anime_title_slots

        ensure_anime_title_slots(info)

    if relation_label and entry.get("relation_type") and isinstance(info, dict):
        info["relation_type"] = str(entry["relation_type"])
    return sync


def _entries_to_sync(entries: list[dict[str, Any]], *, relation_label: bool = False) -> list[dict[str, Any]]:
    sync_items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sync = _related_entry_to_sync(entry, relation_label=relation_label)
        if not sync:
            continue
        simkl_id = sync.get("simkl_id")
        if simkl_id is None or int(simkl_id) in seen:
            continue
        seen.add(int(simkl_id))
        sync_items.append(sync)
    return sync_items


def _render_sync_items(sync_items: list[dict[str, Any]]) -> None:
    render_mixed_sync_list(sync_items)


def _context_menu_request() -> bool:
    params = g.REQUEST_PARAMS or {}
    return str(params.get("context_menu", "")).lower() in ("1", "true")


def _cancel_unless_context_menu() -> None:
    if not _context_menu_request():
        g.cancel_directory()


def _activate_browse_window(action: str, action_args: dict[str, Any] | None) -> None:
    from resources.lib.common import tools

    encoded = tools.construct_action_args(action_args or {})
    path = f"plugin://{g.ADDON_ID}/?{urlencode({'action': action, 'action_args': encoded}, quote_via=quote)}"
    xbmc.executebuiltin(f"ActivateWindow(Videos,{path},return)")


def render_recommendations(action_args: dict[str, Any] | None) -> None:
    target = detail_target_from_action_args(action_args)
    if not target:
        _cancel_unless_context_menu()
        return

    catalog, simkl_id = target
    detail = _fetch_detail(catalog, simkl_id)
    if not detail:
        _cancel_unless_context_menu()
        return

    entries = detail.get("users_recommendations") or []
    sync_items = _entries_to_sync(entries)
    if not sync_items:
        g.notification(g.ADDON_NAME, g.get_language_string(30763))
        _cancel_unless_context_menu()
        return

    if _context_menu_request():
        _activate_browse_window("simklRecommendations", action_args)
        return

    _render_sync_items(sync_items)


def render_relations(action_args: dict[str, Any] | None) -> None:
    target = detail_target_from_action_args(action_args)
    if not target:
        _cancel_unless_context_menu()
        return

    catalog, simkl_id = target
    if catalog != "anime":
        g.notification(g.ADDON_NAME, g.get_language_string(30765))
        _cancel_unless_context_menu()
        return

    detail = _fetch_detail(catalog, simkl_id)
    if not detail:
        _cancel_unless_context_menu()
        return

    entries = detail.get("relations") or []
    sync_items = _entries_to_sync(entries, relation_label=True)
    if not sync_items:
        g.notification(g.ADDON_NAME, g.get_language_string(30764))
        _cancel_unless_context_menu()
        return

    if _context_menu_request():
        _activate_browse_window("simklRelations", action_args)
        return

    _render_sync_items(sync_items)
