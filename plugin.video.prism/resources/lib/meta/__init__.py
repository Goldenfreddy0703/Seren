"""Metadata paint pipeline, display cache, and provider orchestration.

Import submodules directly (e.g. ``meta.enrichment``, ``meta.display_store``).
This package ``__init__`` stays lightweight so compatibility shims can load
``meta.provider_settings`` without pulling the full meta stack.
"""

__all__ = (
    "MetaEnrichmentQueue",
    "MetaProfile",
    "MetaProviderRouter",
    "browse_list_kwargs",
    "enrich_simkl_sync_items",
    "filter_pending",
    "get_display_meta_store",
    "mark_enriched",
    "provider_enabled",
)


def __getattr__(name: str):
    if name == "MetaEnrichmentQueue":
        from resources.lib.meta.enrichment import MetaEnrichmentQueue

        return MetaEnrichmentQueue
    if name in ("enrich_simkl_sync_items",):
        from resources.lib.meta import enrichment as mod

        return getattr(mod, name)
    if name in ("MetaProfile", "profile_scope"):
        from resources.lib.meta import profiles as mod

        return getattr(mod, name)
    if name == "MetaProviderRouter":
        from resources.lib.meta.providers import MetaProviderRouter

        return MetaProviderRouter
    if name == "browse_list_kwargs":
        from resources.lib.meta.list_paint import browse_list_kwargs

        return browse_list_kwargs
    if name == "get_display_meta_store":
        from resources.lib.meta.display_store import get_display_meta_store

        return get_display_meta_store
    if name in ("filter_pending", "mark_enriched"):
        from resources.lib.meta import registry as mod

        return getattr(mod, name)
    if name == "provider_enabled":
        from resources.lib.meta.provider_settings import provider_enabled

        return provider_enabled
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
