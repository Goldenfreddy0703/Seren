"""Insert discover items into Simkl sync DB."""
from __future__ import annotations

from resources.lib.modules.globals import g


def simkl_refs(items: list[dict]) -> list[dict]:
    """Minimal refs for get_movie_list / get_show_list — same shape discover menus use."""
    refs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        simkl_id = item.get("simkl_id")
        if simkl_id is None:
            continue
        ref: dict = {"simkl_id": int(simkl_id)}
        catalog = item.get("catalog")
        if catalog:
            ref["catalog"] = catalog
        refs.append(ref)
    return refs


def sync_rows_for_library_refs(catalog: str, refs: list[dict]) -> list[dict]:
    """Return page refs with catalog stamped for library sync_path menus."""
    rows: list[dict] = []
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("simkl_id") is None:
            continue
        rows.append({"simkl_id": int(ref["simkl_id"]), "catalog": ref.get("catalog") or catalog})
    return rows


def insert_discover_page(
    catalog: str,
    items: list[dict],
    *,
    force_simkl_meta: bool = False,
    catalog_only: bool = False,
) -> list[dict]:
    """Insert a browse page into simkl_sync.db and return simkl_id refs for list builders."""
    if not items:
        return []

    from resources.lib.discover.catalog_store import upsert_sync_items

    if catalog_only:
        upsert_sync_items(items, catalog_hint=catalog)
        return simkl_refs(items)

    movies = [i for i in items if i.get("catalog") == "movie"]
    shows = [i for i in items if i.get("catalog") in ("tv", "anime")]

    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    if movies:
        db.insert_simkl_movies(movies, force_meta=force_simkl_meta)

    if shows:
        db.insert_simkl_shows(shows, force_meta=force_simkl_meta)

    upsert_sync_items(items, catalog_hint=catalog)

    if catalog == "anime" and movies:
        g.log(f"Discover anime page: {len(movies)} movie(s), {len(shows)} series", "debug")

    # Preserve discovery order (Tenrai/TMDB/Simkl page order). Do not group movies before shows.
    return simkl_refs(items)


def paginate_items(items: list, page: int, page_size: int) -> list:
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]
