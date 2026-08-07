"""Central Simkl GET /sync/activities access (shared by sync, library cache, maintenance)."""
from __future__ import annotations

import time

from resources.lib.indexers.simkl import SimklAPI
from resources.lib.modules.globals import g

_CACHE_KEY = "simkl.activities.cached_payload"
_LAST_FETCH_KEY = "simkl.activities.last_fetch"
_SESSION_TTL_SECONDS = 30

_CATALOG_ACTIVITY_SECTION = {
    "movie": "movies",
    "tv": "shows",
    "anime": "anime",
}


def get_activities_payload(*, force: bool = False) -> dict | None:
    """Return Simkl activities JSON, with a short session cache for redundant callers."""
    api = SimklAPI()
    if not api.is_authenticated():
        return None

    if not force:
        cached = g.get_runtime_setting(_CACHE_KEY)
        last = g.get_runtime_setting(_LAST_FETCH_KEY)
        if isinstance(cached, dict) and last:
            try:
                if time.time() - float(last) < _SESSION_TTL_SECONDS:
                    return cached
            except (TypeError, ValueError):
                pass

    payload = api.get_activities()
    if isinstance(payload, dict):
        g.set_runtime_setting(_CACHE_KEY, payload)
        g.set_runtime_setting(_LAST_FETCH_KEY, str(time.time()))
        return payload
    return None


def library_activity_timestamp(catalog: str, *, force: bool = False) -> str | None:
    """Latest watchlist-related activity timestamp for a catalog section."""
    payload = get_activities_payload(force=force)
    if not isinstance(payload, dict):
        return None
    section_key = _CATALOG_ACTIVITY_SECTION.get(catalog, "shows")
    section = payload.get(section_key) or {}
    if not isinstance(section, dict):
        return None
    timestamps = [
        value
        for value in (
            section.get("all"),
            section.get("rated_at"),
            section.get("watchlist"),
            section.get("dropped_at"),
            section.get("hold_at"),
            section.get("completed_at"),
        )
        if value
    ]
    return max(timestamps) if timestamps else None
