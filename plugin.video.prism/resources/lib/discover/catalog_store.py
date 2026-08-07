"""Unified per-catalog item store: one row per title, merged from CDN + Simkl sync."""
from __future__ import annotations

import json
import time
from typing import Any

CATALOG_CACHE_TTL = 3600.0

_DISCOVER_ROW_KEYS = frozenset(
    {
        "rank",
        "watched",
        "plan_to_watch",
        "drop_rate",
        "mdblist_score",
        "status",
        "runtime",
        "release_date",
        "total_episodes",
        "ratings_json",
        "genres_json",
    }
)

_LIBRARY_INFO_KEYS = frozenset(
    {
        "simkl_status",
        "user_rating",
        "watched",
        "last_watched_at",
        "watched_episodes_count",
        "total_episodes_count",
        "playcount",
        "play_count",
        "dateadded",
        "dateadded_watchlist",
    }
)

_RAM_ITEMS: dict[tuple[str, int], dict[str, Any]] = {}
_MIGRATED = False
_OBSOLETE_DROPPED = False


def ensure_catalog_tables(db=None) -> None:
    from resources.lib.database.session import get_sync_database

    db = db or get_sync_database()
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS catalog_items (
            catalog TEXT NOT NULL,
            simkl_id INTEGER NOT NULL,
            base_json TEXT NOT NULL,
            discover_json TEXT,
            library_json TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (catalog, simkl_id)
        )
        """
    )
    db.execute_sql(
        """
        CREATE INDEX IF NOT EXISTS idx_catalog_items_catalog
        ON catalog_items(catalog)
        """
    )
    db.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS discover_list_order (
            catalog TEXT NOT NULL,
            list_id TEXT NOT NULL,
            simkl_ids TEXT NOT NULL,
            last_updated INTEGER NOT NULL,
            PRIMARY KEY (catalog, list_id)
        )
        """
    )
    _maybe_migrate_legacy_discover_tables(db)
    _maybe_drop_obsolete_tables(db)


def _maybe_drop_obsolete_tables(db) -> None:
    global _OBSOLETE_DROPPED
    if _OBSOLETE_DROPPED:
        return
    _OBSOLETE_DROPPED = True
    try:
        db.execute_sql("DROP TABLE IF EXISTS discover_page_paint")
        db.execute_sql("DROP TABLE IF EXISTS lists")
    except Exception:
        from resources.lib.modules.globals import g

        g.log_stacktrace()


def _encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _decode(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _slim_sync_row(row: dict[str, Any]) -> dict[str, Any] | None:
    from resources.lib.meta.profiles import MetaProfile
    from resources.lib.meta.storage import slim_db_row

    if not isinstance(row, dict) or row.get("simkl_id") is None:
        return None
    slim = slim_db_row(row, profile=MetaProfile.LIST)
    if slim.get("simkl_id") is None:
        return None
    return slim


def _discover_fragment_from_cdn_row(row: dict[str, Any]) -> dict[str, Any]:
    fragment: dict[str, Any] = {}
    for key in _DISCOVER_ROW_KEYS:
        if row.get(key) is not None:
            fragment[key] = row[key]
    return fragment


def _library_fragment_from_sync_row(row: dict[str, Any]) -> dict[str, Any]:
    fragment: dict[str, Any] = {}
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    simkl_obj = row.get("simkl_object") if isinstance(row.get("simkl_object"), dict) else {}
    obj_info = simkl_obj.get("info") if isinstance(simkl_obj.get("info"), dict) else {}
    for source in (info, obj_info, row):
        if not isinstance(source, dict):
            continue
        for key in _LIBRARY_INFO_KEYS:
            if source.get(key) is not None and key not in fragment:
                fragment[key] = source[key]
    for key in ("watched", "user_rating"):
        if row.get(key) is not None and key not in fragment:
            fragment[key] = row[key]
    return fragment


def _merge_fragments(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value
    return merged


def _upsert_catalog_record(
    db,
    catalog: str,
    simkl_id: int,
    *,
    base_json: dict[str, Any] | None = None,
    discover_json: dict[str, Any] | None = None,
    library_json: dict[str, Any] | None = None,
) -> None:
    if base_json is None and discover_json is None and library_json is None:
        return
    existing = db.fetchone(
        """
        SELECT base_json, discover_json, library_json
        FROM catalog_items
        WHERE catalog=? AND simkl_id=?
        """,
        (catalog, int(simkl_id)),
    )
    now = int(time.time())
    if existing:
        final_base = base_json if base_json is not None else _decode(existing.get("base_json")) or {}
        final_discover = _merge_fragments(_decode(existing.get("discover_json")), discover_json or {})
        final_library = _merge_fragments(_decode(existing.get("library_json")), library_json or {})
    else:
        final_base = base_json or {}
        final_discover = discover_json or {}
        final_library = library_json or {}
    if not final_base:
        return
    db.execute_sql(
        """
        INSERT INTO catalog_items (catalog, simkl_id, base_json, discover_json, library_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(catalog, simkl_id) DO UPDATE SET
            base_json=excluded.base_json,
            discover_json=excluded.discover_json,
            library_json=excluded.library_json,
            updated_at=excluded.updated_at
        """,
        (
            catalog,
            int(simkl_id),
            _encode(final_base),
            _encode(final_discover) if final_discover else None,
            _encode(final_library) if final_library else None,
            now,
        ),
    )
    _RAM_ITEMS[(catalog, int(simkl_id))] = {
        "base_json": final_base,
        "discover_json": final_discover or None,
        "library_json": final_library or None,
    }


def upsert_sync_items(sync_items: list[dict[str, Any]], *, catalog_hint: str = "") -> int:
    """Upsert normalized SyncRows from discover loaders or Simkl sync."""
    from resources.lib.database.session import get_sync_database

    if not sync_items:
        return 0
    db = get_sync_database()
    ensure_catalog_tables(db)
    written = 0
    for item in sync_items:
        if not isinstance(item, dict) or item.get("simkl_id") is None:
            continue
        catalog = item.get("catalog") or catalog_hint or "movie"
        slim = _slim_sync_row(item)
        if not slim:
            continue
        library_json = _library_fragment_from_sync_row(slim)
        _upsert_catalog_record(
            db,
            catalog,
            int(slim["simkl_id"]),
            base_json=slim,
            library_json=library_json,
        )
        written += 1
    return written


def upsert_cdn_rows(catalog: str, rows: list[dict[str, Any]]) -> int:
    """Upsert raw cdn_store SQL rows into catalog_items with discover fragments."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.simkl.media_ref import normalize_discover_db_row

    if not rows:
        return 0
    db = get_sync_database()
    ensure_catalog_tables(db)
    written = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            continue
        sync_item = normalize_discover_db_row(row, catalog)
        if not sync_item:
            continue
        slim = _slim_sync_row(sync_item)
        if not slim:
            continue
        discover_json = _discover_fragment_from_cdn_row(row)
        _upsert_catalog_record(
            db,
            catalog,
            int(slim["simkl_id"]),
            base_json=slim,
            discover_json=discover_json,
        )
        written += 1
    return written


def catalog_record_to_sync_item(catalog: str, record: dict[str, Any]) -> dict[str, Any] | None:
    from resources.lib.simkl.field_map import sanitize_list_info

    base = _decode(record.get("base_json"))
    if not isinstance(base, dict) or base.get("simkl_id") is None:
        return None
    item = dict(base)
    item["catalog"] = item.get("catalog") or catalog
    info = dict(item.get("info") or {})
    if not info and isinstance(item.get("simkl_object"), dict):
        info = dict(item["simkl_object"].get("info") or {})
    discover = _decode(record.get("discover_json"))
    if isinstance(discover, dict):
        from resources.lib.simkl.field_map import _DISCOVER_POPULARITY_INFO_KEYS

        for key, value in discover.items():
            if key in _DISCOVER_POPULARITY_INFO_KEYS:
                continue
            if value is not None and info.get(key) is None:
                info[key] = value
    library = _decode(record.get("library_json"))
    if isinstance(library, dict):
        info.update({k: v for k, v in library.items() if v is not None})
    info = sanitize_list_info(info, catalog=item.get("catalog") or catalog)
    if info:
        item["info"] = info
        if isinstance(item.get("simkl_object"), dict):
            item["simkl_object"] = dict(item["simkl_object"])
            item["simkl_object"]["info"] = dict(info)
    return item


def get_items_batch(catalog: str, simkl_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Return SyncRow dicts keyed by simkl_id."""
    if not simkl_ids:
        return {}
    from resources.lib.database.session import get_sync_database

    ensure_catalog_tables()
    hits: dict[int, dict[str, Any]] = {}
    misses: list[int] = []
    for simkl_id in simkl_ids:
        sid = int(simkl_id)
        cached = _RAM_ITEMS.get((catalog, sid))
        if cached:
            item = catalog_record_to_sync_item(catalog, cached)
            if item:
                hits[sid] = item
                continue
        misses.append(sid)

    if misses:
        db = get_sync_database()
        placeholders = ",".join("?" * len(misses))
        records = db.fetchall(
            f"""
            SELECT catalog, simkl_id, base_json, discover_json, library_json
            FROM catalog_items
            WHERE catalog=? AND simkl_id IN ({placeholders})
            """,
            (catalog, *misses),
        )
        for record in records or []:
            sid = int(record["simkl_id"])
            _RAM_ITEMS[(catalog, sid)] = {
                "base_json": _decode(record.get("base_json")),
                "discover_json": _decode(record.get("discover_json")),
                "library_json": _decode(record.get("library_json")),
            }
            item = catalog_record_to_sync_item(catalog, record)
            if item:
                hits[sid] = item

    still_missing = [sid for sid in misses if sid not in hits]
    if still_missing:
        hits.update(_fallback_sync_items_from_library_db(catalog, still_missing))
    return hits


def _fallback_sync_items_from_library_db(catalog: str, simkl_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Build paint rows from movies/shows when catalog_items miss (pre-sync edge case)."""
    from resources.lib.database.session import get_sync_database

    if not simkl_ids:
        return {}
    db = get_sync_database()
    table = "movies" if catalog == "movie" else "shows"
    placeholders = ",".join("?" * len(simkl_ids))
    rows = db.fetchall(
        f"""
        SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id, args
        FROM {table}
        WHERE simkl_id IN ({placeholders})
        """,
        tuple(simkl_ids),
    )
    hits: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        info = _decode(row.get("info")) if isinstance(row.get("info"), str) else row.get("info")
        art = _decode(row.get("art")) if isinstance(row.get("art"), str) else row.get("art")
        if not isinstance(info, dict):
            continue
        item = {
            "simkl_id": int(row["simkl_id"]),
            "catalog": catalog,
            "info": info,
            "art": art if isinstance(art, dict) else {},
            "cast": _decode(row.get("cast")) if row.get("cast") else [],
            "tmdb_id": row.get("tmdb_id"),
            "tvdb_id": row.get("tvdb_id"),
            "imdb_id": row.get("imdb_id"),
        }
        if row.get("args"):
            item["args"] = _decode(row.get("args")) if isinstance(row.get("args"), str) else row.get("args")
        slim = _slim_sync_row(item)
        if slim:
            hits[int(row["simkl_id"])] = slim
    return hits


def sync_items_for_refs(catalog: str, refs: list[dict]) -> list[dict]:
    """Resolve ordered SyncRows for list refs."""
    if not refs:
        return []
    simkl_ids = [int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None]
    batch = get_items_batch(catalog, simkl_ids)
    items: list[dict] = []
    for ref in refs:
        sid = ref.get("simkl_id")
        if sid is None:
            continue
        item = batch.get(int(sid))
        if item:
            row = dict(item)
            if ref.get("catalog"):
                row["catalog"] = ref["catalog"]
            items.append(row)
    return items


def catalog_refs_need_seed(catalog: str, refs: list[dict]) -> bool:
    """True when any list ref is missing from catalog_items."""
    simkl_ids = [
        int(ref["simkl_id"])
        for ref in refs or []
        if isinstance(ref, dict) and ref.get("simkl_id") is not None
    ]
    if not simkl_ids:
        return False
    hits = get_items_batch(catalog, simkl_ids)
    return len(hits) < len(simkl_ids)


def save_list_order(catalog: str, list_id: str, refs: list[dict]) -> None:
    from resources.lib.database.session import get_sync_database

    if not refs:
        return
    db = get_sync_database()
    ensure_catalog_tables(db)
    payload = [
        {"simkl_id": int(ref["simkl_id"]), "catalog": ref.get("catalog") or catalog}
        for ref in refs
        if ref.get("simkl_id") is not None
    ]
    db.execute_sql(
        """
        INSERT INTO discover_list_order (catalog, list_id, simkl_ids, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(catalog, list_id) DO UPDATE SET
            simkl_ids=excluded.simkl_ids,
            last_updated=excluded.last_updated
        """,
        (catalog, list_id, _encode(payload), int(time.time())),
    )


def get_list_order(catalog: str, list_id: str) -> list[dict] | None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_catalog_tables(db)
    row = db.fetchone(
        """
        SELECT simkl_ids, last_updated
        FROM discover_list_order
        WHERE catalog=? AND list_id=?
        """,
        (catalog, list_id),
    )
    if not row:
        return None
    if time.time() - int(row.get("last_updated") or 0) >= CATALOG_CACHE_TTL:
        return None
    payload = _decode(row.get("simkl_ids"))
    if not isinstance(payload, list) or not payload:
        return None
    refs: list[dict] = []
    for entry in payload:
        if isinstance(entry, dict) and entry.get("simkl_id") is not None:
            refs.append(
                {
                    "simkl_id": int(entry["simkl_id"]),
                    "catalog": entry.get("catalog") or catalog,
                }
            )
        elif isinstance(entry, int):
            refs.append({"simkl_id": int(entry), "catalog": catalog})
    return refs or None


def invalidate_list_order(catalog: str | None = None, list_id: str | None = None) -> None:
    from resources.lib.database.session import get_sync_database

    db = get_sync_database()
    ensure_catalog_tables(db)
    if catalog is None and list_id is None:
        db.execute_sql("DELETE FROM discover_list_order")
        return
    if catalog is not None and list_id is not None:
        db.execute_sql(
            "DELETE FROM discover_list_order WHERE catalog=? AND list_id=?",
            (catalog, list_id),
        )
    elif catalog is not None:
        db.execute_sql("DELETE FROM discover_list_order WHERE catalog=?", (catalog,))
    elif list_id is not None:
        db.execute_sql("DELETE FROM discover_list_order WHERE list_id=?", (list_id,))


def _maybe_migrate_legacy_discover_tables(db) -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    try:
        legacy = db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='discover_list_payload'"
        )
        if not legacy:
            return
        rows = db.fetchall("SELECT catalog, list_id, payload FROM discover_list_payload")
        for row in rows or []:
            payload = _decode(row.get("payload"))
            if not isinstance(payload, list) or not payload:
                continue
            catalog = row["catalog"]
            list_id = row["list_id"]
            upsert_sync_items(payload, catalog_hint=catalog)
            from resources.lib.discover.sync_bridge import simkl_refs

            save_list_order(catalog, list_id, simkl_refs(payload))
        db.execute_sql("DROP TABLE IF EXISTS discover_list_payload")
        db.execute_sql("DROP TABLE IF EXISTS discover_list_cache")
    except Exception:
        from resources.lib.modules.globals import g

        g.log_stacktrace()


def trim_stale_catalog_items(
    max_rows: int | None = None,
    max_age_days: int | None = None,
) -> int:
    """Drop old browse-only catalog_items rows to keep simklSync compact."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.modules.cache_maintenance import CATALOG_ITEMS_MAX_AGE_DAYS, CATALOG_ITEMS_MAX_ROWS
    from resources.lib.modules.globals import g

    if max_rows is None:
        max_rows = CATALOG_ITEMS_MAX_ROWS
    if max_age_days is None:
        max_age_days = CATALOG_ITEMS_MAX_AGE_DAYS
    if max_rows <= 0:
        return 0

    db = get_sync_database()
    ensure_catalog_tables(db)
    removed = 0
    try:
        cutoff = int(time.time()) - int(max_age_days) * 86400
        before = db.fetchone("SELECT COUNT(*) AS count FROM catalog_items")
        db.execute_sql(
            """
            DELETE FROM catalog_items
            WHERE updated_at < ?
              AND simkl_id NOT IN (
                SELECT simkl_id FROM movies
                WHERE COALESCE(watched, 0) > 0 OR simkl_status IS NOT NULL
                UNION
                SELECT simkl_id FROM shows
                WHERE COALESCE(watched_episodes, 0) > 0 OR simkl_status IS NOT NULL
              )
            """,
            (cutoff,),
        )
        after = db.fetchone("SELECT COUNT(*) AS count FROM catalog_items")
        if before and after:
            removed += max(0, int(before.get("count") or 0) - int(after.get("count") or 0))

        count_row = db.fetchone("SELECT COUNT(*) AS count FROM catalog_items")
        total = int(count_row["count"]) if count_row else 0
        if total > max_rows:
            trim = total - max_rows
            db.execute_sql(
                """
                DELETE FROM catalog_items
                WHERE rowid IN (
                    SELECT rowid FROM catalog_items
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (trim,),
            )
            after_trim = db.fetchone("SELECT COUNT(*) AS count FROM catalog_items")
            if count_row and after_trim:
                removed += max(0, total - int(after_trim.get("count") or 0))
        if removed:
            g.log(f"Trimmed {removed} stale catalog_items rows", "debug")
    except Exception:
        g.log_stacktrace()
    return removed
