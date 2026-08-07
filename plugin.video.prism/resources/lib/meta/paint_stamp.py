"""POV-style trust stamp for paint-complete display_meta rows."""
from __future__ import annotations

import hashlib
from typing import Any

_STAMP_CACHE: str | None = None

_ART_SETTING_KEYS = (
    ("movies.clearlogo", "movie"),
    ("movies.clearart", "movie"),
    ("movies.discart", "movie"),
    ("movies.banner", "movie"),
    ("movies.landscape", "movie"),
    ("tvshows.clearlogo", "tvshow"),
    ("tvshows.clearart", "tvshow"),
    ("tvshows.banner", "tvshow"),
    ("tvshows.landscape", "tvshow"),
    ("anime.clearlogo", "anime_series"),
    ("anime.clearart", "anime_series"),
    ("anime.banner", "anime_series"),
    ("anime.landscape", "anime_series"),
    ("anime.clearlogo", "anime_movie"),
    ("anime.clearart", "anime_movie"),
    ("anime.discart", "anime_movie"),
    ("anime.banner", "anime_movie"),
    ("anime.landscape", "anime_movie"),
)


def invalidate_paint_stamp_cache() -> None:
    """Drop in-process stamp fingerprint (call after art settings change)."""
    global _STAMP_CACHE
    _STAMP_CACHE = None


def current_paint_stamp() -> str:
    """Fingerprint of enabled art/cast providers and fanart.tv config."""
    global _STAMP_CACHE
    if _STAMP_CACHE:
        return _STAMP_CACHE

    from resources.lib.meta.provider_settings import art_option_enabled, provider_enabled

    parts: list[str] = ["paint_stamp_v1"]
    for setting_id, media_type in _ART_SETTING_KEYS:
        parts.append(f"{setting_id}={int(art_option_enabled(setting_id, media_type))}")
    for provider in ("tmdb", "tvdb", "fanart", "imdb"):
        parts.append(f"prov_{provider}={int(provider_enabled(provider))}")
    try:
        from resources.lib.modules.metadataHandler import MetadataHandler

        parts.append(f"fanart_hash={MetadataHandler().fanarttv_api.meta_hash}")
    except Exception:
        parts.append("fanart_hash=0")

    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    _STAMP_CACHE = digest[:16]
    return _STAMP_CACHE


def row_has_trusted_paint_stamp(row: dict[str, Any] | None) -> bool:
    """True when display_meta has a paint stamp (invalidated on art settings change)."""
    if not isinstance(row, dict):
        return False
    return bool(row.get("_paint_stamp"))


def page_refs_display_stamped(refs: list[dict[str, Any]]) -> bool:
    """True when every ref on a page has a trusted display_meta stamp."""
    if not refs:
        return True
    from resources.lib.meta.display_store import get_display_meta_store

    store = get_display_meta_store()
    movie_ids = [
        int(ref["simkl_id"])
        for ref in refs
        if isinstance(ref, dict) and ref.get("simkl_id") is not None and ref.get("catalog") == "movie"
    ]
    show_ids = [
        int(ref["simkl_id"])
        for ref in refs
        if isinstance(ref, dict) and ref.get("simkl_id") is not None and ref.get("catalog") in ("tv", "anime")
    ]
    if movie_ids:
        hits = store.get_batch("movie", movie_ids)
        if not all(row_has_trusted_paint_stamp(hits.get(sid)) for sid in movie_ids):
            return False
    if show_ids:
        hits = store.get_batch("show", show_ids)
        if not all(row_has_trusted_paint_stamp(hits.get(sid)) for sid in show_ids):
            return False
    return True


def row_ready_to_stamp(row: dict[str, Any] | None) -> bool:
    """True when a row has enough list metadata to trust on repeat open."""
    if not isinstance(row, dict):
        return False
    from resources.lib.database.sync_meta_cache import row_has_display_meta

    if not row_has_display_meta(row):
        return False
    art = row.get("art") if isinstance(row.get("art"), dict) else {}
    return bool(art.get("poster") or art.get("thumb") or art.get("fanart"))


def attach_paint_stamp(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of row tagged with the current paint stamp."""
    updated = dict(row)
    updated["_paint_stamp"] = current_paint_stamp()
    return updated


def attach_paint_stamp_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stamp = current_paint_stamp()
    stamped: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        updated = dict(row)
        updated["_paint_stamp"] = stamp
        stamped.append(updated)
    return stamped


def attach_stamps_to_complete_rows(
    rows: list[dict[str, Any]],
    media_type: str,
    *,
    handler=None,
) -> list[dict[str, Any]]:
    """Tag in-memory rows that are paint-complete so session/page caches honor trust."""
    from resources.lib.meta.paint_complete import row_paint_complete

    if handler is None:
        from resources.lib.modules.metadataHandler import MetadataHandler

        handler = MetadataHandler()
    stamped: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row_has_trusted_paint_stamp(row):
            stamped.append(row)
            continue
        if row_ready_to_stamp(row) or row_paint_complete(row, media_type, handler=handler, scope="full"):
            stamped.append(attach_paint_stamp(row))
        else:
            stamped.append(row)
    return stamped
