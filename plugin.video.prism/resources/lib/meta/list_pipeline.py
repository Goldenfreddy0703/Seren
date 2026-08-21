"""Unified list pipeline: RAM cache, browse seed, paint flags, page render."""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Callable

from resources.lib.discover.catalog_store import (
    CATALOG_CACHE_TTL,
    get_list_order,
    save_list_order,
    sync_items_for_refs,
)
from resources.lib.discover.sync_bridge import simkl_refs

LIST_CACHE_TTL = CATALOG_CACHE_TTL

_ORDER_RAM_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
_ITEMS_RAM_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
_materialize_lock = threading.Lock()
_materialized_keys: set[tuple[str, str, str]] = set()
_catalog_upsert_keys: set[tuple[str, str, str]] = set()

_STORES: dict[str, PaginatedListStore] = {}


def make_list_id(*parts: str | int) -> str:
    """Stable list id from query slug, year, genre filter, etc."""
    raw = "|".join(str(part) for part in parts if part is not None and str(part) != "")
    if len(raw) <= 96:
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _page_list_id(list_id: str, page: int) -> str:
    return f"{list_id}::p{int(page)}"


class PaginatedListStore:
    """RAM-first list cache keyed by (namespace, catalog, list_id)."""

    def __init__(self, namespace: str, *, ttl: float = LIST_CACHE_TTL) -> None:
        self.namespace = namespace
        self.ttl = ttl

    def _key(self, catalog: str, list_id: str) -> tuple[str, str, str]:
        return (self.namespace, catalog, list_id)

    def _log(self, message: str) -> None:
        from resources.lib.modules.globals import g

        g.log(f"ListPipeline: namespace={self.namespace} {message}", "debug")

    def _memory_order(self, catalog: str, list_id: str) -> list[dict] | None:
        key = self._key(catalog, list_id)
        cached = _ORDER_RAM_CACHE.get(key)
        if not cached:
            return None
        if time.time() - cached[0] >= self.ttl:
            _ORDER_RAM_CACHE.pop(key, None)
            _ITEMS_RAM_CACHE.pop(key, None)
            return None
        return list(cached[1])

    def _remember_order(self, catalog: str, list_id: str, refs: list[dict]) -> list[dict]:
        key = self._key(catalog, list_id)
        _ORDER_RAM_CACHE[key] = (time.time(), list(refs))
        return refs

    def _memory_items(self, catalog: str, list_id: str) -> list[dict] | None:
        key = self._key(catalog, list_id)
        cached = _ITEMS_RAM_CACHE.get(key)
        if not cached:
            return None
        if time.time() - cached[0] >= self.ttl:
            _ITEMS_RAM_CACHE.pop(key, None)
            return None
        return list(cached[1])

    def remember_items(self, catalog: str, list_id: str, sync_items: list[dict]) -> list[dict]:
        key = self._key(catalog, list_id)
        _ITEMS_RAM_CACHE[key] = (time.time(), list(sync_items))
        return sync_items

    def load_page_items(
        self,
        catalog: str,
        list_id: str,
        page: int,
        loader: Callable[[], list[dict]],
        *,
        schedule_upsert: bool = True,
    ) -> list[dict]:
        """Cache API page payloads (search, genre, year, actor)."""
        page_id = _page_list_id(list_id, page)
        started = time.time()
        memory = self._memory_items(catalog, page_id)
        if memory:
            self._log(
                f"list={list_id} page={page} source=memory refs_ms=0 page_ms={int((time.time() - started) * 1000)} upsert=skipped"
            )
            return memory

        items = loader()
        if not items:
            return []
        self.remember_items(catalog, page_id, items)
        if schedule_upsert:
            self._schedule_catalog_upsert(catalog, page_id, items)
        self._log(
            f"list={list_id} page={page} source=loader refs_ms={int((time.time() - started) * 1000)} page_ms=0 upsert=deferred"
        )
        return items

    def load_cached_items(
        self,
        catalog: str,
        list_id: str,
        loader: Callable[[], list[dict]],
    ) -> list[dict]:
        """Cache a full unpaged list payload (Next Up, Continue Watching slice source, etc.)."""
        started = time.time()
        memory = self._memory_items(catalog, list_id)
        if memory:
            self._log(f"list={list_id} source=memory items={len(memory)}")
            return memory

        items = loader() or []
        if items:
            self.remember_items(catalog, list_id, items)
        loader_ms = int((time.time() - started) * 1000)
        self._log(f"list={list_id} source=loader items={len(items)} loader_ms={loader_ms}")
        return items

    def get_items(
        self,
        catalog: str,
        list_id: str,
        loader: Callable[[], list[dict]],
        *,
        materialize: bool = True,
        persist_order: bool = True,
    ) -> list[dict]:
        """Return full list payloads: RAM → discover_list_order → loader."""
        from resources.lib.modules.globals import g

        memory = self._memory_items(catalog, list_id)
        if memory:
            return memory

        refs = self._memory_order(catalog, list_id)
        if refs is None and persist_order and self.namespace == "discover":
            refs = get_list_order(catalog, list_id)
        if refs:
            self._remember_order(catalog, list_id, refs)
            items = sync_items_for_refs(catalog, refs)
            if items:
                return self.remember_items(catalog, list_id, items)

        loader_started = time.time()
        sync_items = loader()
        loader_ms = int((time.time() - loader_started) * 1000)
        if not sync_items:
            return []

        refs = simkl_refs(sync_items)
        if persist_order and self.namespace == "discover":
            save_list_order(catalog, list_id, refs)
        key = self._key(catalog, list_id)
        with _materialize_lock:
            _materialized_keys.discard(key)
        self._remember_order(catalog, list_id, refs)
        items = self.remember_items(catalog, list_id, sync_items)
        self._schedule_catalog_upsert(catalog, list_id, sync_items)
        if materialize:
            self._schedule_list_materialize(catalog, list_id, items)
        self._log(
            f"list={list_id} cold_load loader_ms={loader_ms} upsert=deferred items={len(items)}",
        )
        return items

    def load_refs(
        self,
        catalog: str,
        list_id: str,
        loader: Callable[[], list[dict]],
        *,
        materialize: bool = False,
        persist_order: bool = True,
    ) -> list[dict]:
        refs = self._memory_order(catalog, list_id)
        if refs is None and persist_order and self.namespace == "discover":
            refs = get_list_order(catalog, list_id)
        if refs:
            self._remember_order(catalog, list_id, refs)
            return refs

        sync_items = self.get_items(
            catalog,
            list_id,
            loader,
            materialize=materialize,
            persist_order=persist_order,
        )
        return simkl_refs(sync_items)

    def get_refs(
        self,
        catalog: str,
        list_id: str,
        loader: Callable[[], list[dict]],
    ) -> list[dict]:
        """Cache ref-only lists (library membership, etc.)."""
        refs = self._memory_order(catalog, list_id)
        if refs:
            self._log(f"list={list_id} refs_ms=0 source=memory")
            return refs

        started = time.time()
        refs = loader() or []
        if refs:
            self._remember_order(catalog, list_id, refs)
        self._log(f"list={list_id} refs_ms={int((time.time() - started) * 1000)} source=loader")
        return refs

    def paginate_items(
        self,
        catalog: str,
        list_id: str,
        refs: list[dict],
        loader: Callable[[], list[dict]],
    ) -> list[dict]:
        """Resolve SyncRows for a ref page slice without rebuilding the menu."""
        from resources.lib.modules.globals import g
        from resources.lib.simkl.enrich import _merge_sync_item_rows

        if not refs:
            return []

        resolve_started = time.time()

        page_items = self._sync_items_for_refs_from_memory(catalog, list_id, refs)
        finalized = self._finalize_discover_page(refs, page_items)
        if finalized is not None:
            self._log(
                f"list={list_id} page_resolve source=memory page_ms={int((time.time() - resolve_started) * 1000)}",
            )
            return finalized

        page_items = sync_items_for_refs(catalog, refs)
        page_items = self._merge_page_items_with_memory(catalog, list_id, refs, page_items)
        finalized = self._finalize_discover_page(refs, page_items)
        if finalized is not None:
            self._log(
                f"list={list_id} page_resolve source=catalog page_ms={int((time.time() - resolve_started) * 1000)}",
            )
            return finalized

        fresh_items = loader()
        if not fresh_items:
            self._log(
                f"list={list_id} page_resolve source=empty page_ms={int((time.time() - resolve_started) * 1000)}",
            )
            return page_items

        self.remember_items(catalog, list_id, fresh_items)
        self._schedule_catalog_upsert(catalog, list_id, fresh_items)
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
            self._log(
                f"list={list_id} page_resolve source=loader page_ms={int((time.time() - resolve_started) * 1000)}",
            )
            return merged_page
        return sync_items_for_refs(catalog, refs)

    def _sync_items_for_refs_from_memory(
        self,
        catalog: str,
        list_id: str,
        refs: list[dict],
    ) -> list[dict]:
        memory = self._memory_items(catalog, list_id)
        if not memory:
            return []
        by_id = {
            int(item["simkl_id"]): item
            for item in memory
            if isinstance(item, dict) and item.get("simkl_id") is not None
        }
        items: list[dict] = []
        for ref in refs:
            sid = ref.get("simkl_id")
            if sid is None:
                continue
            item = by_id.get(int(sid))
            if not item:
                continue
            row = dict(item)
            if ref.get("catalog"):
                row["catalog"] = ref["catalog"]
            items.append(row)
        return items

    def _merge_page_items_with_memory(
        self,
        catalog: str,
        list_id: str,
        refs: list[dict],
        page_items: list[dict],
    ) -> list[dict]:
        if len(page_items) >= len(refs):
            return page_items
        memory_items = self._sync_items_for_refs_from_memory(catalog, list_id, refs)
        if not memory_items:
            return page_items
        by_id = {
            int(item["simkl_id"]): item
            for item in page_items
            if isinstance(item, dict) and item.get("simkl_id") is not None
        }
        for item in memory_items:
            sid = item.get("simkl_id")
            if sid is not None:
                by_id[int(sid)] = item
        merged: list[dict] = []
        for ref in refs:
            sid = ref.get("simkl_id")
            if sid is None:
                continue
            row = by_id.get(int(sid))
            if not row:
                continue
            merged.append(dict(row))
        return merged or page_items

    @staticmethod
    def _finalize_discover_page(refs: list[dict], page_items: list[dict]) -> list[dict] | None:
        from resources.lib.simkl.enrich import _merge_discover_db_gaps, _row_needs_discover_gapfill

        if not page_items:
            return None

        thin_count = sum(1 for item in page_items if _row_needs_discover_gapfill(item))
        if len(page_items) == len(refs) and thin_count == 0:
            return page_items

        if thin_count > 0:
            by_id: dict[int, dict] = {}
            for item in page_items:
                sid = item.get("simkl_id")
                if sid is None:
                    continue
                row = dict(item)
                if _row_needs_discover_gapfill(row):
                    row = _merge_discover_db_gaps(row)
                by_id[int(sid)] = row
            merged_page: list[dict] = []
            for ref in refs:
                sid = ref.get("simkl_id")
                if sid is None:
                    continue
                row = by_id.get(int(sid))
                if not row:
                    continue
                row = dict(row)
                if ref.get("catalog"):
                    row["catalog"] = ref["catalog"]
                merged_page.append(row)
            if len(merged_page) == len(refs):
                return merged_page

        return None

    def _schedule_catalog_upsert(self, catalog: str, list_id: str, sync_items: list[dict]) -> None:
        key = self._key(catalog, list_id)
        with _materialize_lock:
            if key in _catalog_upsert_keys:
                return
            _catalog_upsert_keys.add(key)

        def _run() -> None:
            try:
                from resources.lib.discover.catalog_store import upsert_sync_items_batched

                upsert_sync_items_batched(sync_items, catalog_hint=catalog)
            except Exception:
                from resources.lib.modules.globals import g

                g.log_stacktrace()
            finally:
                with _materialize_lock:
                    _catalog_upsert_keys.discard(key)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"prism-list-upsert-{self.namespace}-{catalog}-{list_id[:12]}",
        ).start()

    def _schedule_list_materialize(self, catalog: str, list_id: str, sync_items: list[dict]) -> None:
        key = self._key(catalog, list_id)
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
            name=f"prism-list-materialize-{self.namespace}-{catalog}-{list_id[:12]}",
        ).start()


_STORES: dict[str, PaginatedListStore] = {}


def get_list_store(namespace: str) -> PaginatedListStore:
    store = _STORES.get(namespace)
    if store is None:
        store = PaginatedListStore(namespace)
        _STORES[namespace] = store
    return store


def clear_ram_cache(namespace: str | None = None) -> None:
    """Drop in-memory list order and payload caches."""
    if namespace is None:
        _ORDER_RAM_CACHE.clear()
        _ITEMS_RAM_CACHE.clear()
        with _materialize_lock:
            _materialized_keys.clear()
            _catalog_upsert_keys.clear()
        return

    drop_keys = [key for key in _ORDER_RAM_CACHE if key[0] == namespace]
    for key in drop_keys:
        _ORDER_RAM_CACHE.pop(key, None)
        _ITEMS_RAM_CACHE.pop(key, None)
    with _materialize_lock:
        _materialized_keys.difference_update(key for key in _materialized_keys if key[0] == namespace)
        _catalog_upsert_keys.difference_update(key for key in _catalog_upsert_keys if key[0] == namespace)


def seed_browse_page(catalog: str, items: list[dict]) -> list[dict]:
    """Hydrate sync rows and seed simkl_sync for the visible browse page.

    ``insert_browse_page`` already batch-upserts catalog_items, so we do not
    queue ``defer_browse_catalog_seed`` (that would duplicate catalog work).
    """
    if not items:
        return []

    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.enrich import hydrate_sync_items_local

    items = hydrate_sync_items_local(items)
    get_sync_database().insert_browse_page(catalog, items)
    return items


def resolve_list_flags(
    paint_profile: str | dict | None = None,
    *,
    seeded: bool = False,
    preloaded: bool = False,
    preloaded_paint_complete: bool = False,
) -> dict[str, bool]:
    """Central skip_update / sync_path policy after browse seed or paint."""
    profile_name = ""
    if isinstance(paint_profile, dict):
        profile_name = str(paint_profile.get("paint_profile") or "")
    elif paint_profile:
        profile_name = str(paint_profile)

    profile_name = profile_name.lower()
    skip_update = seeded or preloaded or preloaded_paint_complete
    flags: dict[str, bool] = {}

    if profile_name in ("browse", "related", "airing", ""):
        if skip_update:
            flags["skip_update"] = True
            flags.setdefault("skip_mill", True)
            flags.setdefault("sync_path", True)
        else:
            flags.setdefault("sync_path", True)
    elif skip_update:
        flags["skip_update"] = True

    return flags


def render_list_page(
    strategy: str,
    catalog: str,
    items: list[dict],
    list_builder,
    *,
    list_kwargs: dict | None = None,
    refs: list[dict] | None = None,
    payload_rows: list[dict] | None = None,
    seeded: bool = True,
    **kwargs,
) -> None:
    """Route browse menus through paint-first, browse_sync, or mixed_sync paths."""
    from resources.lib.modules.globals import g

    if not items and not refs:
        g.cancel_directory()
        return

    merged = dict(list_kwargs or {})
    merged.update(kwargs)
    flags = resolve_list_flags(
        merged,
        seeded=seeded,
        preloaded=bool(merged.get("preloaded_paint_rows")),
        preloaded_paint_complete=bool(merged.get("preloaded_paint_complete")),
    )
    merged.update(flags)

    if strategy == "paint_first":
        from resources.lib.discover.sync_bridge import simkl_refs
        from resources.lib.meta.list_paint import render_catalog_discover_refs

        page_refs = refs or simkl_refs(items)
        render_catalog_discover_refs(
            catalog,
            page_refs,
            list_builder,
            list_kwargs=merged,
            payload_rows=payload_rows or items,
            prefer_catalog_payload=bool(merged.get("prefer_catalog_payload", True)),
        )
        return

    if strategy == "browse_sync":
        from resources.lib.meta.browse_sync import render_browse_sync_page

        render_browse_sync_page(
            catalog,
            items,
            list_builder,
            list_kwargs=merged,
            defer_catalog_seed=False,
        )
        return

    if strategy == "mixed_sync":
        from resources.lib.simkl.media_ref import render_mixed_sync_list

        render_mixed_sync_list(items, catalog_hint=catalog, **merged)
        return

    raise ValueError(f"Unknown list pipeline strategy: {strategy}")
