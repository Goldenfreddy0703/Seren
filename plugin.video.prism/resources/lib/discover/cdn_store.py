"""In-memory discover row store built from live Simkl CDN JSON."""
from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from resources.lib.database.cache import use_cache
from resources.lib.indexers.simkl_cdn import SimklCDN

RowKey = Tuple[str, int]
RowStore = Dict[RowKey, Dict[str, Any]]

_LIST_SIZE = 500


def _non_empty_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return str(v)


def _blank_row(catalog: str, simkl_id: int) -> dict[str, Any]:
    return {
        "simkl_id": simkl_id,
        "catalog": catalog,
        "slug": None,
        "title": None,
        "url": None,
        "poster": None,
        "fanart": None,
        "overview": None,
        "release_date": None,
        "rank": None,
        "drop_rate": None,
        "watched": None,
        "plan_to_watch": None,
        "country": None,
        "runtime": None,
        "status": None,
        "network": None,
        "metadata_line": None,
        "anime_type": None,
        "total_episodes": None,
        "trailer": None,
        "genres_json": None,
        "ids_json": None,
        "ratings_json": None,
        "mdblist_score": None,
        "streams_json": None,
        "watch_providers_json": None,
        "extras_json": None,
    }


def _ensure_row(store: RowStore, catalog: str, simkl_id: int) -> dict[str, Any]:
    key: RowKey = (catalog, int(simkl_id))
    if key not in store:
        store[key] = _blank_row(catalog, int(simkl_id))
    return store[key]


def _merge_str_field(row: dict[str, Any], key: str, val: Any) -> None:
    s = _non_empty_str(val)
    if not s:
        return
    if row.get(key) in (None, ""):
        row[key] = s


def _merge_rank(row: dict[str, Any], new_rank: Any) -> None:
    if new_rank is None:
        return
    try:
        rank = int(new_rank)
    except (TypeError, ValueError):
        return
    if rank <= 0:
        return
    old = row.get("rank")
    row["rank"] = rank if old is None or old <= 0 else min(int(old), rank)


def _merge_watched_max(row: dict[str, Any], val: Any) -> None:
    if val is None:
        return
    try:
        watched = int(val)
    except (TypeError, ValueError):
        return
    old = row.get("watched")
    row["watched"] = watched if old is None else max(int(old), watched)


def _merge_plan_to_watch(row: dict[str, Any], val: Any) -> None:
    if val is None:
        return
    try:
        plan = int(val)
    except (TypeError, ValueError):
        return
    old = row.get("plan_to_watch")
    if old is None or plan > int(old):
        row["plan_to_watch"] = plan


def _merge_from_trending_blob(row: dict[str, Any], blob: dict[str, Any]) -> None:
    ids = blob.get("ids") or {}
    genres = blob.get("genres")
    _merge_str_field(row, "slug", ids.get("slug"))
    _merge_str_field(row, "title", blob.get("title"))
    _merge_str_field(row, "url", blob.get("url"))
    _merge_str_field(row, "poster", blob.get("poster"))
    _merge_str_field(row, "fanart", blob.get("fanart"))
    _merge_str_field(row, "overview", blob.get("overview"))
    _merge_str_field(row, "release_date", blob.get("release_date"))
    _merge_rank(row, blob.get("rank"))
    _merge_str_field(row, "drop_rate", blob.get("drop_rate"))
    _merge_str_field(row, "country", blob.get("country"))
    _merge_str_field(row, "runtime", blob.get("runtime"))
    _merge_str_field(row, "status", blob.get("status"))
    _merge_str_field(row, "network", blob.get("network"))
    _merge_str_field(row, "metadata_line", blob.get("metadata"))
    _merge_str_field(row, "anime_type", blob.get("anime_type"))
    _merge_str_field(row, "trailer", blob.get("trailer"))
    total_episodes = blob.get("total_episodes")
    if total_episodes is not None and row.get("total_episodes") is None:
        try:
            row["total_episodes"] = int(total_episodes)
        except (TypeError, ValueError):
            pass
    if genres is not None:
        row["genres_json"] = json.dumps(genres, ensure_ascii=False)
    if ids:
        row["ids_json"] = json.dumps(ids, ensure_ascii=False)
    if blob.get("ratings") is not None:
        row["ratings_json"] = json.dumps(blob["ratings"], ensure_ascii=False)


def _simkl_id_from_blob(blob: dict[str, Any]) -> Any:
    ids = blob.get("ids") or {}
    simkl_id = ids.get("simkl_id")
    if simkl_id is None:
        simkl_id = ids.get("simkl")
    return simkl_id


def merge_trending_into_row(store: RowStore, catalog: str, blob: dict[str, Any]) -> None:
    simkl_id = _simkl_id_from_blob(blob)
    if simkl_id is None:
        return
    try:
        simkl_id_int = int(simkl_id)
    except (TypeError, ValueError):
        return
    if simkl_id_int <= 0:
        return
    row = _ensure_row(store, catalog, simkl_id_int)
    _merge_from_trending_blob(row, blob)
    _merge_plan_to_watch(row, blob.get("plan_to_watch"))
    _merge_watched_max(row, blob.get("watched"))


def _ingest_combined(store: RowStore, cdn: SimklCDN) -> None:
    for window in ("today", "week", "month"):
        data = cdn.trending_combined(window, _LIST_SIZE)
        if not isinstance(data, dict):
            continue
        for cat_key, catalog in (("movies", "movie"), ("tv", "tv"), ("anime", "anime")):
            items = data.get(cat_key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    merge_trending_into_row(store, catalog, item)


def _ingest_dvd(store: RowStore, cdn: SimklCDN) -> None:
    for item in cdn.trending_dvd(_LIST_SIZE):
        if isinstance(item, dict):
            merge_trending_into_row(store, "movie", item)


@use_cache(cache_hours=1)
def _load_store() -> RowStore:
    store: RowStore = {}
    cdn = SimklCDN()
    _ingest_combined(store, cdn)
    _ingest_dvd(store, cdn)
    _persist_catalog_store(store)
    return store


def _persist_catalog_store(store: RowStore) -> None:
    from resources.lib.discover.catalog_store import upsert_cdn_rows

    for catalog in ("movie", "tv", "anime"):
        rows = [row for (cat, _sid), row in store.items() if cat == catalog]
        if rows:
            upsert_cdn_rows(catalog, rows)


def rows_for_catalog(catalog: str) -> list[dict[str, Any]]:
    store = _load_store()
    return [row for (cat, _), row in store.items() if cat == catalog]


def get_row(catalog: str, simkl_id: int) -> dict[str, Any] | None:
    return _load_store().get((catalog, int(simkl_id)))


def get_rows_by_ids(catalog: str, simkl_ids: list[int]) -> dict[int, dict[str, Any]]:
    store = _load_store()
    out: dict[int, dict[str, Any]] = {}
    for simkl_id in simkl_ids:
        row = store.get((catalog, int(simkl_id)))
        if row:
            out[int(simkl_id)] = row
    return out
