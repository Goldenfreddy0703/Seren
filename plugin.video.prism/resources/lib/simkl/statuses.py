"""Simkl watchlist status keys and labels shared by library menus and context menu."""
from __future__ import annotations

from typing import Any

from resources.lib.modules.globals import g
from resources.lib.simkl.ids import show_id_from_info

# (status_key, label_string_id) — label IDs match My Library hubs
MOVIE_STATUS_OPTIONS = (
    ("plantowatch", 30732),
    ("completed", 30736),
    ("dropped", 30735),
)

SHOW_STATUS_OPTIONS = (
    ("watching", 30733),
    ("plantowatch", 30732),
    ("hold", 30734),
    ("completed", 30736),
    ("dropped", 30735),
)

STATUS_LABEL_IDS = {status: label_id for status, label_id in MOVIE_STATUS_OPTIONS + SHOW_STATUS_OPTIONS}


def status_label(status: str) -> str:
    label_id = STATUS_LABEL_IDS.get(status)
    return g.get_language_string(label_id) if label_id else status


def _catalog_for_info(info: dict[str, Any]) -> str:
    if info.get("mediatype") == "movie":
        return "movie"
    ids = info.get("ids") or {}
    if info.get("catalog") == "anime" or info.get("mal_id") or ids.get("mal"):
        return "show"
    return "show"


def effective_list_status(
    info: dict[str, Any],
    library_status: str | None = None,
    *,
    remote: "SimklRemoteItemState | None" = None,
) -> str | None:
    """List bucket from live Simkl, menu context, or persisted simkl_status."""
    if remote is not None and remote.matched:
        return remote.list_status
    if library_status:
        return library_status
    return info.get("library_status") or current_simkl_status(info)


def movie_show_mark_watched(
    info: dict[str, Any],
    *,
    library_status: str | None = None,
    remote: "SimklRemoteItemState | None" = None,
) -> bool:
    """True when Simkl manager should offer Mark as Watched for a movie."""
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype not in ("movie", "movies"):
        return False

    if remote is not None and remote.matched:
        if remote.list_status in ("plantowatch", "dropped", "hold", "watching"):
            return True
        if remote.list_status == "completed":
            return False
        if not remote.in_library:
            return True
        return False

    status = effective_list_status(info, library_status, remote=remote)
    if status in ("plantowatch", "dropped", "hold", "watching"):
        return True
    if status == "completed":
        return False
    try:
        return int(info.get("play_count") or 0) <= 0
    except (TypeError, ValueError):
        return True


def on_simkl_watchlist(
    info: dict[str, Any],
    *,
    library_status: str | None = None,
    remote: "SimklRemoteItemState | None" = None,
) -> bool:
    """True when the item is on a Simkl watchlist bucket (not merely watch history)."""
    if remote is not None and remote.matched:
        return remote.on_watchlist
    return effective_list_status(info, library_status, remote=remote) is not None


def status_options_for_info(
    info: dict[str, Any],
    *,
    exclude_current: bool = True,
    library_status: str | None = None,
    remote: "SimklRemoteItemState | None" = None,
) -> list[tuple[str, int]]:
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype == "movies":
        mediatype = "movie"
    options = MOVIE_STATUS_OPTIONS if mediatype == "movie" else SHOW_STATUS_OPTIONS
    if exclude_current:
        current = effective_list_status(info, library_status, remote=remote)
        if current:
            options = tuple((s, lid) for s, lid in options if s != current)
    return list(options)


def in_simkl_library(item: dict[str, Any]) -> bool:
    """True when the item is on a Simkl list or has local watch history."""
    info = item.get("info") if isinstance(item.get("info"), dict) else item
    if current_simkl_status(info):
        return True
    try:
        if int(item.get("play_count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype in ("tvshow", "season") and item.get("unwatched_episodes") is not None:
        return True
    return False


def current_simkl_status(info: dict[str, Any]) -> str | None:
    status = info.get("simkl_status")
    if status:
        return status

    mediatype = (info.get("mediatype") or "").lower()
    if mediatype == "movie":
        simkl_id = info.get("simkl_id")
        if not simkl_id:
            return None
        from resources.lib.database.session import get_sync_database

        row = get_sync_database().fetchone(
            "SELECT simkl_status, info FROM movies WHERE simkl_id=?",
            (int(simkl_id),),
        )
    else:
        show_id = show_id_from_info(info) if mediatype != "tvshow" else info.get("simkl_id")
        if not show_id:
            return None
        from resources.lib.database.session import get_sync_database

        row = get_sync_database().fetchone(
            "SELECT simkl_status, info FROM shows WHERE simkl_id=?",
            (int(show_id),),
        )

    if not row:
        return None
    if row.get("simkl_status"):
        return row.get("simkl_status")
    stored = row.get("info")
    if isinstance(stored, dict):
        return stored.get("simkl_status")
    return None


def resolved_list_status_from_response(
    response: dict[str, Any] | None,
    *,
    requested: str | None = None,
) -> str | None:
    """Status after add-to-list; prefer API resolution, then the status the user picked."""
    if response:
        for entry in (response.get("added") or {}).get("statuses") or []:
            if not isinstance(entry, dict):
                continue
            resolved = (entry.get("response") or {}).get("status")
            if resolved:
                return resolved
            req_to = (entry.get("request") or {}).get("to")
            if req_to:
                return req_to
    return requested


def resolved_watched_status_from_response(
    response: dict[str, Any] | None,
    info: dict[str, Any],
) -> str | None:
    """Status after mark-watched; movies default to completed when the API is silent."""
    status = resolved_list_status_from_response(response)
    if status:
        return status
    if info.get("mediatype") == "movie":
        return "completed"
    return None


def resolved_status_from_response(response: dict[str, Any] | None, info: dict[str, Any]) -> str | None:
    """Backward-compatible alias for mark-watched flows."""
    return resolved_watched_status_from_response(response, info)


def library_row_id(info: dict[str, Any]) -> int | None:
    """Simkl row id used in movies/shows table for list status updates."""
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype == "movie":
        return int(info["simkl_id"]) if info.get("simkl_id") else None
    if mediatype == "tvshow":
        return int(info["simkl_id"]) if info.get("simkl_id") else None
    show_id = show_id_from_info(info)
    return int(show_id) if show_id else None


def library_catalog(info: dict[str, Any]) -> str:
    return "movie" if info.get("mediatype") == "movie" else _catalog_for_info(info)
