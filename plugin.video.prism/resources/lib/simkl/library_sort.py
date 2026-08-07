"""Watchlist sort helpers (Otaku-style / Simkl all-items fields)."""
from __future__ import annotations

from resources.lib.modules.globals import g

SORT_TITLE = 0
SORT_RATING = 1
SORT_PROGRESS = 2
SORT_LAST_WATCHED = 3
SORT_DATE_ADDED = 4

_UNKNOWN_DATE_CUTOFF = "2000-01-01"


def get_watchlist_sort_config() -> tuple[int, bool]:
    """Return (sort_field, descending)."""
    sort_field = g.get_int_setting("general.watchlist.sortfield", SORT_LAST_WATCHED)
    if sort_field < SORT_TITLE or sort_field > SORT_DATE_ADDED:
        sort_field = SORT_LAST_WATCHED
    descending = g.get_int_setting("general.watchlist.order", 1) == 1
    return sort_field, descending


def _normalize_timestamp(value) -> str:
    if not value:
        return ""
    text = str(value)
    if text[:4] < _UNKNOWN_DATE_CUTOFF[:4]:
        return ""
    return text


def _info_from_sync_row(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    info = (item.get("simkl_object") or {}).get("info")
    if isinstance(info, dict):
        return info
    info = item.get("info")
    return info if isinstance(info, dict) else {}


def _sort_key(info: dict, catalog: str, sort_field: int):
    if sort_field == SORT_TITLE:
        title = info.get("title") or info.get("name") or info.get("tvshowtitle") or ""
        return (str(title).lower(),)

    if sort_field == SORT_RATING:
        rating = info.get("user_rating")
        try:
            return (int(rating) if rating is not None else 0,)
        except (TypeError, ValueError):
            return (0,)

    if sort_field == SORT_PROGRESS:
        watched = info.get("watched_episodes_count")
        if watched is None:
            watched = info.get("watched") or 0
        total = (
            info.get("total_episodes_count")
            or info.get("episode_count")
            or info.get("total_episodes")
            or 0
        )
        try:
            watched = int(watched or 0)
            total = int(total or 0)
        except (TypeError, ValueError):
            return (0,)
        if catalog == "movie":
            return (1.0 if watched else 0.0,)
        if total <= 0:
            return (0,)
        return (watched / total,)

    if sort_field == SORT_LAST_WATCHED:
        return (_normalize_timestamp(info.get("last_watched_at")),)

    if sort_field == SORT_DATE_ADDED:
        return (_normalize_timestamp(info.get("dateadded")),)

    return ("",)


def sort_sync_items(items: list[dict], catalog: str) -> list[dict]:
    if len(items) < 2:
        return items
    sort_field, descending = get_watchlist_sort_config()
    items.sort(
        key=lambda row: _sort_key(_info_from_sync_row(row), catalog, sort_field),
        reverse=descending,
    )
    return items


def _load_db_sort_meta(refs: list[dict], catalog: str) -> dict[int, dict]:
    if not refs:
        return {}

    from resources.lib.database.session import get_sync_database

    ids = [int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None]
    if not ids:
        return {}

    placeholders = ",".join("?" * len(ids))
    if catalog == "movie":
        query = f"""
        SELECT simkl_id, info, last_watched_at, user_rating, watched
        FROM movies
        WHERE simkl_id IN ({placeholders})
        """
    else:
        query = f"""
        SELECT simkl_id, info, last_watched_at, user_rating, episode_count, watched_episodes
        FROM shows
        WHERE simkl_id IN ({placeholders})
        """

    rows = get_sync_database().fetchall(query, tuple(ids))

    meta: dict[int, dict] = {}
    for row in rows:
        simkl_id = int(row["simkl_id"])
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        merged = dict(info)
        if row.get("last_watched_at"):
            merged["last_watched_at"] = row.get("last_watched_at")
        if row.get("user_rating") is not None:
            merged["user_rating"] = row.get("user_rating")
        if catalog == "movie":
            if row.get("watched") is not None:
                merged["watched"] = row.get("watched")
        else:
            if row.get("episode_count") is not None and not merged.get("total_episodes_count"):
                merged["total_episodes_count"] = row.get("episode_count")
            if row.get("watched_episodes") is not None:
                merged["watched_episodes_count"] = row.get("watched_episodes")
        meta[simkl_id] = merged
    return meta


def sort_library_refs(refs: list[dict], catalog: str) -> list[dict]:
    if len(refs) < 2:
        return refs
    sort_field, descending = get_watchlist_sort_config()
    meta_by_id = _load_db_sort_meta(refs, catalog)
    refs.sort(
        key=lambda ref: _sort_key(meta_by_id.get(int(ref["simkl_id"]), {}), catalog, sort_field),
        reverse=descending,
    )
    return refs
