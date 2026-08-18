"""Deferred catalog_items upsert for Seren-mode browse (foreground simkl_sync only)."""
from __future__ import annotations

from resources.lib.modules.globals import g

_PENDING_KEY = "browse_catalog_seed.pending"
_WAKE_KEY = "browse_catalog_seed.service_wake"
_MAX_PENDING = 12


def defer_browse_catalog_seed(catalog: str, items: list[dict]) -> None:
    if not items or not catalog:
        return
    pending = g.get_runtime_setting(_PENDING_KEY)
    if not isinstance(pending, list):
        pending = []
    pending.append({"catalog": str(catalog), "items": list(items)})
    if len(pending) > _MAX_PENDING:
        pending = pending[-_MAX_PENDING:]
    g.set_runtime_setting(_PENDING_KEY, pending)
    g.set_runtime_setting(_WAKE_KEY, True)


def browse_catalog_seed_wake_pending() -> bool:
    return g.get_bool_runtime_setting(_WAKE_KEY)


def clear_browse_catalog_seed_wake() -> None:
    g.set_runtime_setting(_WAKE_KEY, False)


def process_idle_browse_catalog_seed() -> bool:
    pending = g.get_runtime_setting(_PENDING_KEY)
    if not isinstance(pending, list) or not pending:
        if browse_catalog_seed_wake_pending():
            clear_browse_catalog_seed_wake()
        return False
    entry = pending.pop(0)
    g.set_runtime_setting(_PENDING_KEY, pending)
    if not isinstance(entry, dict):
        if not pending:
            clear_browse_catalog_seed_wake()
        return bool(pending)
    catalog = entry.get("catalog")
    items = entry.get("items")
    if catalog and isinstance(items, list) and items:
        try:
            from resources.lib.discover.catalog_store import upsert_sync_items

            upsert_sync_items(items, catalog_hint=str(catalog))
        except Exception:
            g.log_stacktrace()
    if not pending:
        clear_browse_catalog_seed_wake()
    return True
