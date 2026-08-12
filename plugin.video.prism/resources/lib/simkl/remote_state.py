"""Resolve live Simkl list + watch state for a single item (POST /sync/watched)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resources.lib.simkl.payloads import _base_media


@dataclass
class SimklRemoteItemState:
    list_status: str | None = None
    in_library: bool = False
    on_watchlist: bool = False
    matched: bool = False
    last_watched_at: str | None = None


def fetch_remote_item_state(info: dict[str, Any]) -> SimklRemoteItemState | None:
    """Ask Simkl for authoritative list membership and watch history for one item."""
    from resources.lib.indexers.simkl import SimklAPI

    api = SimklAPI()
    if not api.is_authenticated():
        return None

    item = _base_media(info)
    if not item.get("ids"):
        return None

    response = api.post_json("/sync/watched", [item])
    if not response:
        return None
    if not isinstance(response, list):
        return None
    if not response:
        return SimklRemoteItemState()

    entry = response[0] if isinstance(response[0], dict) else {}
    result = entry.get("result")
    if result == "not_found":
        return SimklRemoteItemState(matched=False)

    list_status = entry.get("list")
    if list_status is not None:
        list_status = str(list_status)

    in_library = bool(result)
    return SimklRemoteItemState(
        list_status=list_status,
        in_library=in_library,
        on_watchlist=list_status is not None,
        matched=True,
        last_watched_at=entry.get("last_watched_at"),
    )


def remote_state_from_watched_entry(entry: dict) -> SimklRemoteItemState:
    """Build remote state from one POST /sync/watched response row."""
    if not isinstance(entry, dict):
        return SimklRemoteItemState()
    result = entry.get("result")
    if result == "not_found":
        return SimklRemoteItemState(matched=False)
    list_status = entry.get("list")
    if list_status is not None:
        list_status = str(list_status)
    in_library = bool(result)
    return SimklRemoteItemState(
        list_status=list_status,
        in_library=in_library,
        on_watchlist=list_status is not None,
        matched=True,
        last_watched_at=entry.get("last_watched_at"),
    )


def reconcile_local_item_state(item_or_info: dict[str, Any], remote: SimklRemoteItemState | None) -> None:
    """Align simklSync.db with remote Simkl state after a manager lookup."""
    from resources.lib.simkl.library_status import _library_db, _library_info
    from resources.lib.simkl.statuses import library_catalog, library_row_id

    info = _library_info(item_or_info)
    simkl_id = library_row_id(info)
    if simkl_id is None:
        return

    catalog = library_catalog(info)
    db = _library_db(catalog)

    if remote is None:
        return

    if not remote.matched:
        db.set_simkl_status(int(simkl_id), catalog, None)
        if catalog == "movie":
            db.execute_sql("UPDATE movies SET watched=0 WHERE simkl_id=?", (int(simkl_id),))
        if isinstance(item_or_info.get("info"), dict):
            item_or_info["info"]["simkl_status"] = None
        item_or_info["play_count"] = 0
        return

    db.set_simkl_status(int(simkl_id), catalog, remote.list_status)

    if catalog != "movie":
        return

    if not remote.in_library:
        watched = 0
    elif remote.list_status == "completed":
        watched = 1
    elif remote.list_status in ("plantowatch", "dropped", "hold", "watching"):
        watched = 0
    elif remote.last_watched_at:
        watched = 1
    else:
        watched = 0

    db.execute_sql("UPDATE movies SET watched=? WHERE simkl_id=?", (watched, int(simkl_id)))

    if isinstance(item_or_info.get("info"), dict):
        item_or_info["info"]["simkl_status"] = remote.list_status
    item_or_info["play_count"] = watched
