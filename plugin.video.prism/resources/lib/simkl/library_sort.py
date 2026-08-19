"""Watchlist sort helpers (Otaku-style / Simkl all-items fields)."""
from __future__ import annotations

from resources.lib.modules.globals import g
from resources.lib.simkl.statuses import MOVIE_STATUS_OPTIONS, SHOW_STATUS_OPTIONS

SORT_TITLE = 0
SORT_RATING = 1
SORT_PROGRESS = 2
SORT_LAST_WATCHED = 3
SORT_DATE_ADDED = 4

NEXTUP_SORT_OPTIONS = [30089, 30090]
WATCHLIST_FIELD_OPTIONS = [30860, 30861, 30862, 30863, 30864]
WATCHLIST_ORDER_OPTIONS = [30866, 30867]

CATALOG_LABEL_IDS = {"movie": 30833, "tv": 30834, "anime": 30494}
GLOBAL_CATALOG = "global"
GLOBAL_LABEL_ID = 31045

_DEFAULT_NEXTUP_SORT = 0
_DEFAULT_WATCHLIST_SORTFIELD = SORT_LAST_WATCHED
_DEFAULT_WATCHLIST_ORDER = 1

_LAST_CATALOG_KEY = "general.librarysort.lastcatalog"
_LAST_STATUS_KEY = "general.librarysort.laststatus"

_UNKNOWN_DATE_CUTOFF = "2000-01-01"
_UNSET = -1


def statuses_for_catalog(catalog: str) -> tuple[str, ...]:
    if catalog == "movie":
        return tuple(status for status, _ in MOVIE_STATUS_OPTIONS)
    return tuple(status for status, _ in SHOW_STATUS_OPTIONS)


def is_global_catalog(catalog: str | None) -> bool:
    return normalize_selection_catalog(catalog) == GLOBAL_CATALOG


def normalize_selection_catalog(catalog: str | None) -> str:
    if catalog in (GLOBAL_CATALOG, "movie", "tv", "anime"):
        return catalog
    return GLOBAL_CATALOG


def normalize_catalog(catalog: str | None) -> str:
    if catalog in ("movie", "tv", "anime"):
        return catalog
    return "tv"


def normalize_status(catalog: str, status: str | None) -> str:
    if is_global_catalog(catalog):
        return "watching"
    statuses = statuses_for_catalog(catalog)
    if status in statuses:
        return status
    return statuses[0]


def default_sortfield_for_status(status: str) -> int:
    if status == "watching":
        return SORT_LAST_WATCHED
    return SORT_DATE_ADDED


def default_order_for_status(_status: str) -> bool:
    return True


def _option_label(option_ids: list[int], index: int) -> str:
    if not option_ids:
        return ""
    index = index % len(option_ids)
    return g.get_language_string(option_ids[index])


def _status_sortfield_key(catalog: str, status: str) -> str:
    return f"general.watchlist.sortfield.{catalog}.{status}"


def _status_order_key(catalog: str, status: str) -> str:
    return f"general.watchlist.order.{catalog}.{status}"


def get_last_catalog() -> str:
    return normalize_selection_catalog(g.get_setting(_LAST_CATALOG_KEY))


def get_last_status(catalog: str | None = None) -> str:
    catalog = normalize_selection_catalog(catalog or get_last_catalog())
    if is_global_catalog(catalog):
        return "watching"
    return normalize_status(catalog, g.get_setting(_LAST_STATUS_KEY))


def set_last_selection(catalog: str, status: str) -> None:
    catalog = normalize_selection_catalog(catalog)
    g.set_setting(_LAST_CATALOG_KEY, catalog)
    if not is_global_catalog(catalog):
        g.set_setting(_LAST_STATUS_KEY, normalize_status(catalog, status))


def global_selection_label() -> str:
    return g.get_language_string(GLOBAL_LABEL_ID)


def selection_label(catalog: str, status: str) -> str:
    if is_global_catalog(catalog):
        return global_selection_label()

    from resources.lib.simkl.statuses import STATUS_LABEL_IDS

    status_key = normalize_status(catalog, status)
    status_text = g.get_language_string(STATUS_LABEL_IDS[status_key])
    catalog_text = g.get_language_string(CATALOG_LABEL_IDS[normalize_catalog(catalog)])
    return f"{status_text} - {catalog_text}"


def get_nextup_sort() -> int:
    value = g.get_int_setting("nextup.sort", _DEFAULT_NEXTUP_SORT)
    if value < 0 or value >= len(NEXTUP_SORT_OPTIONS):
        value = _DEFAULT_NEXTUP_SORT
    return value


def set_nextup_sort(value: int) -> None:
    if value < 0 or value >= len(NEXTUP_SORT_OPTIONS):
        value = _DEFAULT_NEXTUP_SORT
    g.set_setting("nextup.sort", str(value))


def get_nextup_sort_label() -> str:
    return _option_label(NEXTUP_SORT_OPTIONS, get_nextup_sort())


def cycle_nextup_sort() -> None:
    set_nextup_sort((get_nextup_sort() + 1) % len(NEXTUP_SORT_OPTIONS))


def get_watchlist_sortfield() -> int:
    value = g.get_int_setting("general.watchlist.sortfield", _DEFAULT_WATCHLIST_SORTFIELD)
    if value < SORT_TITLE or value > SORT_DATE_ADDED:
        value = _DEFAULT_WATCHLIST_SORTFIELD
    return value


def set_watchlist_sortfield(value: int) -> None:
    if value < SORT_TITLE or value > SORT_DATE_ADDED:
        value = _DEFAULT_WATCHLIST_SORTFIELD
    g.set_setting("general.watchlist.sortfield", str(value))
    propagate_global_to_all_menus()


def get_watchlist_sortfield_label() -> str:
    return _option_label(WATCHLIST_FIELD_OPTIONS, get_watchlist_sortfield())


def cycle_watchlist_sortfield() -> None:
    set_watchlist_sortfield((get_watchlist_sortfield() + 1) % len(WATCHLIST_FIELD_OPTIONS))


def get_watchlist_order_desc() -> bool:
    return g.get_int_setting("general.watchlist.order", _DEFAULT_WATCHLIST_ORDER) == 1


def set_watchlist_order_desc(descending: bool) -> None:
    g.set_setting("general.watchlist.order", "1" if descending else "0")
    propagate_global_to_all_menus()


def get_watchlist_order_label() -> str:
    return _option_label(WATCHLIST_ORDER_OPTIONS, 1 if get_watchlist_order_desc() else 0)


def toggle_watchlist_order() -> None:
    set_watchlist_order_desc(not get_watchlist_order_desc())


def propagate_global_to_all_menus() -> None:
    """Push global watchlist sort settings to every library menu."""
    field = get_watchlist_sortfield()
    descending = get_watchlist_order_desc()
    for catalog in ("movie", "tv", "anime"):
        for status in statuses_for_catalog(catalog):
            g.set_setting(_status_sortfield_key(catalog, status), str(field))
            g.set_setting(_status_order_key(catalog, status), "1" if descending else "0")


def reset_catalog_sort_to_global(catalog: str) -> None:
    """Reset every menu in a catalog to the current global sort settings."""
    catalog = normalize_catalog(catalog)
    field = get_watchlist_sortfield()
    descending = get_watchlist_order_desc()
    statuses = statuses_for_catalog(catalog)
    for status in statuses:
        g.clear_setting(_status_sortfield_key(catalog, status))
        g.clear_setting(_status_order_key(catalog, status))
    for status in statuses:
        set_status_sortfield(catalog, status, field)
        set_status_order_desc(catalog, status, descending)


def get_status_sortfield(catalog: str, status: str) -> int:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    value = g.get_int_setting(_status_sortfield_key(catalog, status), _UNSET)
    if SORT_TITLE <= value <= SORT_DATE_ADDED:
        return value
    return get_watchlist_sortfield()


def set_status_sortfield(catalog: str, status: str, value: int) -> None:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    if value < SORT_TITLE or value > SORT_DATE_ADDED:
        value = default_sortfield_for_status(status)
    g.set_setting(_status_sortfield_key(catalog, status), str(value))


def get_status_sortfield_label(catalog: str, status: str) -> str:
    return _option_label(WATCHLIST_FIELD_OPTIONS, get_status_sortfield(catalog, status))


def cycle_status_sortfield(catalog: str, status: str) -> None:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    current = get_status_sortfield(catalog, status)
    set_status_sortfield(catalog, status, (current + 1) % len(WATCHLIST_FIELD_OPTIONS))


def get_status_order_desc(catalog: str, status: str) -> bool:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    value = g.get_int_setting(_status_order_key(catalog, status), _UNSET)
    if value < 0:
        return get_watchlist_order_desc()
    return value == 1


def set_status_order_desc(catalog: str, status: str, descending: bool) -> None:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    g.set_setting(_status_order_key(catalog, status), "1" if descending else "0")


def get_status_order_label(catalog: str, status: str) -> str:
    return _option_label(WATCHLIST_ORDER_OPTIONS, 1 if get_status_order_desc(catalog, status) else 0)


def toggle_status_order(catalog: str, status: str) -> None:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    set_status_order_desc(catalog, status, not get_status_order_desc(catalog, status))


def reset_status_sort_defaults(catalog: str, status: str) -> None:
    catalog = normalize_catalog(catalog)
    status = normalize_status(catalog, status)
    set_status_sortfield(catalog, status, default_sortfield_for_status(status))
    set_status_order_desc(catalog, status, default_order_for_status(status))


def reset_global_sort_defaults() -> None:
    g.set_setting("nextup.sort", str(_DEFAULT_NEXTUP_SORT))
    g.set_setting("general.watchlist.sortfield", str(_DEFAULT_WATCHLIST_SORTFIELD))
    g.set_setting("general.watchlist.order", str(_DEFAULT_WATCHLIST_ORDER))
    propagate_global_to_all_menus()


def reset_library_sort_defaults() -> None:
    reset_global_sort_defaults()
    for catalog in ("movie", "tv", "anime"):
        for status in statuses_for_catalog(catalog):
            reset_status_sort_defaults(catalog, status)


def get_watchlist_sort_config(
    catalog: str | None = None,
    status: str | None = None,
) -> tuple[int, bool]:
    """Return (sort_field, descending)."""
    if catalog and status:
        return get_status_sortfield(catalog, status), get_status_order_desc(catalog, status)
    return get_watchlist_sortfield(), get_watchlist_order_desc()


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


def sort_library_refs(
    refs: list[dict],
    catalog: str,
    status: str | None = None,
) -> list[dict]:
    if len(refs) < 2:
        return refs
    sort_field, descending = get_watchlist_sort_config(catalog, status)
    meta_by_id = _load_db_sort_meta(refs, catalog)
    refs.sort(
        key=lambda ref: _sort_key(meta_by_id.get(int(ref["simkl_id"]), {}), catalog, sort_field),
        reverse=descending,
    )
    return refs
