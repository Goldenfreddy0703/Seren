"""Discover list cache: thin menu orders + catalog_items lookups."""
from __future__ import annotations

import threading
import time
from typing import Callable

from resources.lib.discover.catalog_store import (
    CATALOG_CACHE_TTL,
    get_list_order,
    save_list_order,
    sync_items_for_refs,
    upsert_sync_items,
)
from resources.lib.discover.sync_bridge import simkl_refs

CDN_LIST_CACHE_TTL = CATALOG_CACHE_TTL

_ORDER_RAM_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_ITEMS_RAM_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_materialize_lock = threading.Lock()
_materialized_keys: set[tuple[str, str]] = set()


def clear_discover_ram_cache() -> None:
    """Drop in-memory discover list order and payload caches."""
    _ORDER_RAM_CACHE.clear()
    _ITEMS_RAM_CACHE.clear()
    with _materialize_lock:
        _materialized_keys.clear()


def _cache_key(catalog: str, list_id: str) -> tuple[str, str]:
    return (catalog, list_id)


def _memory_order(catalog: str, list_id: str) -> list[dict] | None:
    key = _cache_key(catalog, list_id)
    cached = _ORDER_RAM_CACHE.get(key)
    if not cached:
        return None
    if time.time() - cached[0] >= CDN_LIST_CACHE_TTL:
        _ORDER_RAM_CACHE.pop(key, None)
        _ITEMS_RAM_CACHE.pop(key, None)
        return None
    return list(cached[1])


def _remember_order(catalog: str, list_id: str, refs: list[dict]) -> list[dict]:
    key = _cache_key(catalog, list_id)
    now = time.time()
    _ORDER_RAM_CACHE[key] = (now, list(refs))
    return refs


def _memory_items(catalog: str, list_id: str) -> list[dict] | None:
    key = _cache_key(catalog, list_id)
    cached = _ITEMS_RAM_CACHE.get(key)
    if not cached:
        return None
    if time.time() - cached[0] >= CDN_LIST_CACHE_TTL:
        _ITEMS_RAM_CACHE.pop(key, None)
        return None
    return list(cached[1])


def _remember_items(catalog: str, list_id: str, sync_items: list[dict]) -> list[dict]:
    key = _cache_key(catalog, list_id)
    _ITEMS_RAM_CACHE[key] = (time.time(), list(sync_items))
    return sync_items


def _build_items_from_refs(catalog: str, refs: list[dict]) -> list[dict]:
    return sync_items_for_refs(catalog, refs)


def get_discover_list_items(
    catalog: str,
    list_id: str,
    loader: Callable[[], list[dict]],
    *,
    materialize: bool = True,
) -> list[dict]:
    """
    Return the full normalized discover list.

    Resolution order: RAM → discover_list_order + catalog_items → loader.
    """
    memory = _memory_items(catalog, list_id)
    if memory:
        return memory

    refs = _memory_order(catalog, list_id)
    if refs is None:
        refs = get_list_order(catalog, list_id)
    if refs:
        _remember_order(catalog, list_id, refs)
        items = _build_items_from_refs(catalog, refs)
        if items:
            return _remember_items(catalog, list_id, items)

    sync_items = loader()
    if not sync_items:
        return []

    upsert_sync_items(sync_items, catalog_hint=catalog)
    refs = simkl_refs(sync_items)
    save_list_order(catalog, list_id, refs)
    key = _cache_key(catalog, list_id)
    with _materialize_lock:
        _materialized_keys.discard(key)
    _remember_order(catalog, list_id, refs)
    items = _remember_items(catalog, list_id, sync_items)
    if materialize:
        _schedule_list_materialize(catalog, list_id, items)
    return items


def load_discover_list_refs(
    catalog: str,
    list_id: str,
    loader: Callable[[], list[dict]],
    *,
    materialize: bool = False,
) -> list[dict]:
    """Return ordered list-builder refs for a full discover menu.

    ``materialize=False`` by default: the render/prefetch paint path writes
    display_meta for the visible page; background materialize races with that
    on first open and is redundant here.
    """
    refs = _memory_order(catalog, list_id)
    if refs is None:
        refs = get_list_order(catalog, list_id)
    if refs:
        _remember_order(catalog, list_id, refs)
        return refs

    sync_items = get_discover_list_items(catalog, list_id, loader, materialize=materialize)
    return simkl_refs(sync_items)


def paginate_sync_items_for_refs(
    catalog: str,
    list_id: str,
    refs: list[dict],
    loader: Callable[[], list[dict]],
) -> list[dict]:
    """Resolve SyncRows for a ref page slice without rebuilding the menu."""
    if not refs:
        return []

    from resources.lib.simkl.enrich import _merge_sync_item_rows, _row_needs_discover_gapfill

    page_items = sync_items_for_refs(catalog, refs)
    thin_count = sum(1 for item in page_items if _row_needs_discover_gapfill(item))
    if page_items and thin_count == 0:
        return page_items

    fresh_items = loader()
    if not fresh_items:
        return page_items

    from resources.lib.discover.catalog_store import upsert_sync_items

    upsert_sync_items(fresh_items, catalog_hint=catalog)
    by_id = {
        int(item["simkl_id"]): item
        for item in fresh_items
        if isinstance(item, dict) and item.get("simkl_id") is not None
    }
    cached_by_id = {
        int(item["simkl_id"]): item
        for item in page_items
        if isinstance(item, dict) and item.get("simkl_id") is not None
    }
    merged_page: list[dict] = []
    for ref in refs:
        sid = ref.get("simkl_id")
        if sid is None:
            continue
        sid_int = int(sid)
        fresh = by_id.get(sid_int)
        cached = cached_by_id.get(sid_int)
        if fresh and cached:
            row = dict(_merge_sync_item_rows(fresh, cached))
        elif fresh:
            row = dict(fresh)
        elif cached:
            row = dict(cached)
        else:
            continue
        if ref.get("catalog"):
            row["catalog"] = ref["catalog"]
        merged_page.append(row)

    if merged_page:
        return merged_page
    return sync_items_for_refs(catalog, refs)


def _schedule_list_materialize(catalog: str, list_id: str, sync_items: list[dict]) -> None:
    """Background: write display_meta for paint."""
    key = _cache_key(catalog, list_id)
    with _materialize_lock:
        if key in _materialized_keys:
            return
        _materialized_keys.add(key)

    def _run() -> None:
        try:
            from resources.lib.meta.paint_cache import publish_sync_rows_to_paint_store

            batch_size = 40
            for start in range(0, len(sync_items), batch_size):
                chunk = sync_items[start : start + batch_size]
                publish_sync_rows_to_paint_store(catalog, chunk)
        except Exception:
            from resources.lib.modules.globals import g

            g.log_stacktrace()
            with _materialize_lock:
                _materialized_keys.discard(key)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"prism-discover-{catalog}-{list_id[:12]}",
    ).start()
