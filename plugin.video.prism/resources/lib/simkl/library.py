"""Fetch Simkl user library buckets and normalize for sync DB insertion."""
from __future__ import annotations

from resources.lib.simkl.media_ref import normalize_library_entry

_SIMKL_SINGULAR = {
    "movies": "movie",
    "shows": "show",
    "anime": "anime",
}


def sync_entry_media_blob(entry: dict, media_key: str) -> dict:
    """Extract the title blob from a Simkl sync list row."""
    if not isinstance(entry, dict):
        return {}
    if media_key == "anime":
        # Anime rows use the ``show`` key even inside the ``anime`` array (simkl_ids_only too).
        return entry.get("anime") or entry.get("show") or entry
    singular = _SIMKL_SINGULAR.get(media_key, "show")
    return entry.get(singular) or entry


def _unwrap_sync_items(payload, media_key: str) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if media_key in payload and isinstance(payload[media_key], list):
            return payload[media_key]
        fallback_keys = {
            "movies": ("movies", "items"),
            "shows": ("shows", "items"),
            "anime": ("anime",),
        }.get(media_key, (media_key, "items"))
        for key in fallback_keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def simkl_entry_to_sync_dict(entry: dict, catalog: str) -> dict | None:
    """Backward-compatible alias — prefer :func:`normalize_library_entry`."""
    return normalize_library_entry(entry, catalog)
