"""Seren-mode browse: simkl_sync seed → get_*_list → ListBuilder."""
from __future__ import annotations

from resources.lib.meta.list_paint import browse_list_kwargs
from resources.lib.modules.globals import g


def render_browse_sync_page(
    catalog: str,
    items: list[dict],
    list_builder,
    *,
    defer_catalog_seed: bool = True,
    list_kwargs: dict | None = None,
    **kwargs,
) -> None:
    """Insert browse page into simkl_sync and render via get_movie_list/get_show_list."""
    if not items:
        g.cancel_directory()
        return

    from resources.lib.simkl.enrich import hydrate_sync_items_local

    items = hydrate_sync_items_local(items)

    from resources.lib.database.session import get_sync_database
    from resources.lib.discover.browse_catalog_seed import defer_browse_catalog_seed

    db = get_sync_database()
    db.insert_browse_page(catalog, items)
    if defer_catalog_seed:
        defer_browse_catalog_seed(catalog, items)

    merged_input = dict(list_kwargs or {})
    merged_input.update(kwargs)
    merged = browse_list_kwargs(**merged_input)
    merged.setdefault("sync_path", True)
    merged.setdefault("skip_update", False)
    merged.setdefault("skip_mill", True)
    merged.pop("preloaded_paint_rows", None)
    merged.pop("preloaded_paint_complete", None)
    merged.pop("paint_only", None)

    if catalog == "movie":
        list_builder.movie_discover_builder(items, **merged)
    elif catalog == "anime":
        list_builder.anime_discover_builder(items, **merged)
    else:
        list_builder.show_discover_builder(items, **merged)
