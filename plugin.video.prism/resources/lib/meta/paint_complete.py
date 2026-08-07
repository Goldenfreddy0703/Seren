"""Paint-row completeness gates (Seren-style skip when row is ready to render)."""
from __future__ import annotations

from typing import Any

from resources.lib.database.sync_meta_cache import row_has_display_meta

_BLOCKING_SCOPE_PROFILES = frozenset({"browse", "related", "airing"})

_LIST_ART_GAP_KEYS = frozenset({"clearlogo", "clearart", "discart", "banner", "landscape", "fanart"})
_BLOCKING_ART_GAP_KEYS = frozenset({"fanart"})


def gap_scope_for_profile(profile: str) -> str:
    """Return gap scope for list paint. Always full so clearlogo/discart paint on menu open."""
    _ = profile
    return "full"


def art_gap_keys_for_scope(scope: str) -> frozenset[str]:
    """Art keys eligible for blocking provider HTTP on menu open."""
    if str(scope).lower() == "blocking":
        return _BLOCKING_ART_GAP_KEYS
    return _LIST_ART_GAP_KEYS


def row_paint_complete(
    row: dict[str, Any] | None,
    media_type: str,
    *,
    handler=None,
    art_profile: str | None = None,
    paint_profile: str = "browse",
    scope: str | None = None,
) -> bool:
    """True when a movie/show list row needs no provider HTTP for cast or art."""
    if not row_has_display_meta(row):
        return False
    from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp

    if row_has_trusted_paint_stamp(row):
        return True
    if handler is None:
        from resources.lib.modules.metadataHandler import MetadataHandler

        handler = MetadataHandler()
    from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

    profile = art_profile or artwork_profile_for_row(row, default_media_type=media_type)
    provider_type = provider_media_type(profile)
    gap_scope = scope or gap_scope_for_profile(paint_profile)
    gaps = handler._row_meta_gaps(row, provider_type, art_profile=profile, scope=gap_scope)
    return not gaps


def row_paint_complete_drilldown(row: dict[str, Any] | None) -> bool:
    """Simkl-only completeness for season/episode drilldown lists (no provider art HTTP)."""
    if not isinstance(row, dict):
        return False
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    mediatype = (info.get("mediatype") or "").lower()
    title = info.get("title") or info.get("tvshowtitle")
    if mediatype == "episode":
        if not title:
            return False
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        thumb = art.get("thumb") or info.get("thumb")
        if thumb:
            return True
        from resources.lib.simkl.images import episode_thumb_url

        img = info.get("simkl_img") or info.get("img")
        return bool(episode_thumb_url(img))
    if mediatype == "season":
        if not title and not info.get("season"):
            return False
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        poster = art.get("poster") or art.get("thumb") or info.get("poster")
        return bool(poster or title)
    return row_has_display_meta(row)


def partition_paint_rows(
    rows: list[dict[str, Any]],
    media_type: str,
    *,
    profile: str = "browse",
    gap_scope: str | None = None,
    handler=None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows into paint-complete (skip provider work) vs incomplete."""
    if handler is None:
        from resources.lib.modules.metadataHandler import MetadataHandler

        handler = MetadataHandler()
    complete: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    drilldown = str(profile).lower() == "drilldown"
    resolved_scope = gap_scope or gap_scope_for_profile(profile)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if drilldown:
            is_complete = row_paint_complete_drilldown(row)
        else:
            is_complete = row_paint_complete(
                row,
                media_type,
                handler=handler,
                paint_profile=profile,
                scope=resolved_scope,
            )
        if is_complete:
            complete.append(row)
        else:
            incomplete.append(row)
    return complete, incomplete


def rows_page_paint_ready(
    rows: list[dict[str, Any]],
    *,
    profile: str = "browse",
) -> bool:
    """True when every row on a mixed page can skip provider prepare."""
    if not rows:
        return True
    movies = [row for row in rows if isinstance(row, dict) and row.get("catalog") == "movie"]
    shows = [row for row in rows if isinstance(row, dict) and row.get("catalog") in ("tv", "anime")]
    if movies:
        _complete, incomplete = partition_paint_rows(movies, "movie", profile=profile)
        if incomplete:
            return False
    if shows:
        _complete, incomplete = partition_paint_rows(shows, "tvshow", profile=profile)
        if incomplete:
            return False
    return True
