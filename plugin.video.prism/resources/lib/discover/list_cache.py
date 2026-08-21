"""Discover list cache: thin menu orders + catalog_items lookups."""
from __future__ import annotations

from typing import Callable

from resources.lib.meta.list_pipeline import clear_ram_cache, get_list_store

_DISCOVER = get_list_store("discover")


def clear_discover_ram_cache() -> None:
    """Drop in-memory discover list order and payload caches."""
    clear_ram_cache("discover")


def get_discover_list_items(
    catalog: str,
    list_id: str,
    loader: Callable[[], list[dict]],
    *,
    materialize: bool = True,
) -> list[dict]:
    """Return the full normalized discover list (RAM → DB → loader)."""
    return _DISCOVER.get_items(catalog, list_id, loader, materialize=materialize)


def load_discover_list_refs(
    catalog: str,
    list_id: str,
    loader: Callable[[], list[dict]],
    *,
    materialize: bool = False,
) -> list[dict]:
    """Return ordered list-builder refs for a full discover menu."""
    return _DISCOVER.load_refs(catalog, list_id, loader, materialize=materialize)


def paginate_sync_items_for_refs(
    catalog: str,
    list_id: str,
    refs: list[dict],
    loader: Callable[[], list[dict]],
) -> list[dict]:
    """Resolve SyncRows for a ref page slice without rebuilding the menu."""
    return _DISCOVER.paginate_items(catalog, list_id, refs, loader)
