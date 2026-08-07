"""Session registry to avoid duplicate metadata enrichment (prefetch vs background queue)."""
from __future__ import annotations

import threading
import time

from resources.lib.modules.globals import g

_REGISTRY_KEY = "meta_enrich.registry"
_MAX_IDS_PER_TYPE = 2048
_TTL_SECONDS = 6 * 3600
_lock = threading.RLock()


def _load_registry() -> dict:
    raw = g.get_runtime_setting(_REGISTRY_KEY)
    if not isinstance(raw, dict):
        return {"movie": {}, "tvshow": {}}
    for media_type in ("movie", "tvshow"):
        raw.setdefault(media_type, {})
    return raw


def _save_registry(registry: dict) -> None:
    g.set_runtime_setting(_REGISTRY_KEY, registry)


def _prune_bucket(bucket: dict, *, now: float) -> dict:
    if not bucket:
        return {}
    pruned = {
        int(simkl_id): float(ts)
        for simkl_id, ts in bucket.items()
        if now - float(ts) < _TTL_SECONDS
    }
    if len(pruned) > _MAX_IDS_PER_TYPE:
        ordered = sorted(pruned.items(), key=lambda item: item[1], reverse=True)
        pruned = dict(ordered[:_MAX_IDS_PER_TYPE])
    return pruned


def mark_enriched(media_type: str, simkl_ids: list[int], *, reason: str | None = None) -> None:
    if not simkl_ids:
        return
    normalized = "movie" if media_type == "movie" else "tvshow"
    now = time.time()
    with _lock:
        registry = _load_registry()
        bucket = _prune_bucket(registry.get(normalized) or {}, now=now)
        for simkl_id in simkl_ids:
            if simkl_id is not None:
                bucket[int(simkl_id)] = now
        registry[normalized] = bucket
        _save_registry(registry)
    if reason:
        g.log(f"enrich_registry: marked {len(simkl_ids)} {normalized} ({reason})", "debug")


def filter_pending(media_type: str, simkl_ids: list[int]) -> list[int]:
    if not simkl_ids:
        return []
    normalized = "movie" if media_type == "movie" else "tvshow"
    now = time.time()
    with _lock:
        registry = _load_registry()
        bucket = _prune_bucket(registry.get(normalized) or {}, now=now)
        registry[normalized] = bucket
        _save_registry(registry)
        pending = [int(simkl_id) for simkl_id in simkl_ids if int(simkl_id) not in bucket]
    return sorted(set(pending))
