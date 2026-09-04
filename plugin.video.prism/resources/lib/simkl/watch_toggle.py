"""Shared mark watched / unwatched logic for Simkl Manager and Kodi native menu bridge."""
from __future__ import annotations

import time

from resources.lib.database.session import get_sync_database
from resources.lib.modules.globals import g
from resources.lib.simkl.library_status import (
    _library_info,
    apply_local_library_status,
    apply_local_status_after_watch,
)
from resources.lib.simkl.payloads import info_to_history_payload
from resources.lib.simkl.statuses import resolved_watched_status_from_response


def finish_library_action(item_information: dict, *, refresh_container: bool = True) -> None:
    """Refresh visible lists after a watch-state edit."""
    from resources.lib.meta.paint_cache import clear_session_page_paint_for_item
    from resources.lib.simkl.library_list_sync import mark_library_catalog_verified
    from resources.lib.simkl.statuses import library_hub_catalog

    info = _library_info(item_information)
    hub_catalog = library_hub_catalog(info)
    mark_library_catalog_verified(hub_catalog)
    if info.get("simkl_id") is not None:
        clear_session_page_paint_for_item(int(info["simkl_id"]), info.get("mediatype"))
    if refresh_container:
        refreshed = g.refresh_visible_container()
        if not refreshed:
            g.container_refresh()
    g.trigger_widget_refresh(if_playing=False)


def _simkl_api():
    from resources.lib.indexers.simkl import SimklAPI

    return SimklAPI()


def _history_success(response, key: str) -> bool:
    if not response:
        return False
    added = response.get("added") or {}
    if isinstance(added.get(key), int) and added[key] > 0:
        return True
    if isinstance(added.get(key), list) and added[key]:
        return True
    if added.get("statuses"):
        return True
    return bool(added.get("episodes") or added.get("shows") or added.get("movies"))


def _remove_success(response, key: str) -> bool:
    if not response:
        return False
    deleted = response.get("deleted") or response.get("removed") or {}
    if isinstance(deleted.get(key), int) and deleted[key] > 0:
        return True
    return bool(deleted)


def _get_show_id(item_information: dict) -> int | None:
    from resources.lib.simkl.ids import show_id_from_info

    info = (
        item_information.get("info")
        if isinstance(item_information, dict) and "info" in item_information
        else item_information
    )
    if not isinstance(info, dict):
        return None
    mediatype = info.get("mediatype")
    if mediatype == "tvshow":
        raw = info.get("simkl_id")
        return int(raw) if raw is not None else None
    return show_id_from_info(info)


def _watch_info(item_or_full: dict) -> dict:
    return _library_info(item_or_full)


def apply_mark_watched(
    item_information: dict,
    *,
    silent: bool = False,
    refresh: bool = True,
) -> bool:
    """
    Mark item watched via Simkl API and local sync DB.
    Returns True when the local DB was updated successfully.
    """
    info = _watch_info(item_information)
    mediatype = (info.get("mediatype") or "").lower()
    if not mediatype or info.get("simkl_id") is None:
        return False

    payload = info_to_history_payload(info)
    response = _simkl_api().add_to_history(payload)

    if mediatype == "movie":
        if not _history_success(response, "movies"):
            if not silent:
                g.notification(
                    f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                    g.get_language_string(30287),
                )
            return False
        from resources.lib.simkl.library_status import ensure_library_row

        ensure_library_row(info)
        get_sync_database().mark_movie_watched(info["simkl_id"])
    else:
        if not _history_success(response, "shows") and not _history_success(response, "anime"):
            if not silent:
                g.notification(
                    f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                    g.get_language_string(30287),
                )
            return False
        if mediatype == "episode":
            show_id = _get_show_id(item_information)
            if show_id is None or info.get("season") is None or info.get("episode") is None:
                return False
            get_sync_database().mark_episode_watched(
                show_id,
                info["season"],
                info["episode"],
            )
        elif mediatype == "season":
            show_id = _get_show_id(item_information)
            if show_id is None or info.get("season") is None:
                return False
            get_sync_database().mark_season_watched(show_id, info["season"], 1)
        elif mediatype == "tvshow":
            get_sync_database().mark_show_watched(info["simkl_id"], 1)

    resolved = resolved_watched_status_from_response(response, info)
    if resolved:
        apply_local_library_status(item_information, resolved, touch_last_watched=True)
    else:
        apply_local_status_after_watch(item_information)

    show_id = _get_show_id(item_information)
    if show_id is not None and mediatype in ("tvshow", "episode", "season"):
        try:
            get_sync_database().refresh_show_episode_watch_state(int(show_id), force=True)
        except Exception:
            g.log_stacktrace()

    if not silent:
        g.notification(
            f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
            g.get_language_string(30288),
        )
        if show_id is not None:
            g.set_runtime_setting(
                f"episode_watch_refresh_cooldown_{int(show_id)}",
                str(int(time.time())),
            )
        if refresh:
            finish_library_action(item_information, refresh_container=refresh)
    elif refresh:
        finish_library_action(item_information, refresh_container=refresh)

    return True


def apply_mark_unwatched(
    item_information: dict,
    *,
    silent: bool = False,
    refresh: bool = True,
) -> bool:
    """Mark item unwatched via Simkl API and local sync DB."""
    info = _watch_info(item_information)
    mediatype = (info.get("mediatype") or "").lower()
    if not mediatype or info.get("simkl_id") is None:
        return False

    payload = info_to_history_payload(info)
    response = _simkl_api().remove_from_history(payload)

    if mediatype == "movie":
        if not _remove_success(response, "movies"):
            if not silent:
                g.notification(
                    f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                    g.get_language_string(30287),
                )
            return False
        get_sync_database().mark_movie_unwatched(info["simkl_id"])
    else:
        if not _remove_success(response, "episodes"):
            if not silent:
                g.notification(
                    f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                    g.get_language_string(30287),
                )
            return False
        if mediatype == "episode":
            show_id = _get_show_id(item_information)
            if show_id is None or info.get("season") is None or info.get("episode") is None:
                return False
            get_sync_database().mark_episode_unwatched(
                show_id,
                info["season"],
                info["episode"],
            )
        elif mediatype == "season":
            show_id = _get_show_id(item_information)
            if show_id is None or info.get("season") is None:
                return False
            get_sync_database().mark_season_watched(show_id, info["season"], 0)
        elif mediatype == "tvshow":
            get_sync_database().mark_show_watched(info["simkl_id"], 0)

    get_sync_database().remove_bookmark(info["simkl_id"])
    show_id = _get_show_id(item_information)
    if show_id is not None and mediatype in ("tvshow", "episode", "season"):
        try:
            get_sync_database().refresh_show_episode_watch_state(int(show_id), force=True)
        except Exception:
            g.log_stacktrace()
    if not silent:
        g.notification(
            f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
            g.get_language_string(30289),
        )
    if show_id is not None:
        g.set_runtime_setting(
            f"episode_watch_refresh_cooldown_{int(show_id)}",
            str(int(time.time())),
        )
    if refresh:
        finish_library_action(item_information, refresh_container=refresh)
    return True
