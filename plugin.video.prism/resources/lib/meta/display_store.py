"""POV-style display metadata store (RAM → prism_meta.db → simkl_sync)."""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import sqlite3
import threading
from typing import Any

from resources.lib.common import tools
from resources.lib.database.sync_meta_cache import SyncMetaCache, row_has_display_meta
from resources.lib.modules.globals import g

_STORE_LOCK = threading.Lock()
_STORE: DisplayMetaStore | None = None

_PREFETCH_LIMIT = 750
_MAX_ROWS = 8000


def get_display_meta_store() -> DisplayMetaStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = DisplayMetaStore()
    return _STORE


def reset_display_meta_store() -> None:
    """Close prism_meta.db and drop the session singleton (backup import, tests)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            with _STORE._db_lock:
                if _STORE._conn is not None:
                    with contextlib.suppress(Exception):
                        _STORE._conn.close()
                    _STORE._conn = None
        _STORE = None


class DisplayMetaStore:
    """Kodi-ready list metadata cache separate from simkl_sync provider blobs."""

    def __init__(self) -> None:
        g.ensure_addon()
        self._path = g.PRISM_META_DB_PATH
        tools.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._ram = SyncMetaCache()
        self._conn: sqlite3.Connection | None = None
        self._db_lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Match main Prism/POV SQLite settings (database/__init__.py).
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA page_size = 32768")
            self._conn.execute("PRAGMA journal_mode = OFF")
            self._conn.execute("PRAGMA synchronous = OFF")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute("PRAGMA mmap_size = 268435456")
            self._conn.execute("PRAGMA busy_timeout = 15000")
        return self._conn

    def _init_schema(self) -> None:
        with self._db_lock:
            conn = self._connect()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS display_meta (
                    media_type TEXT NOT NULL,
                    simkl_id INTEGER NOT NULL,
                    info TEXT,
                    art TEXT,
                    [cast] TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (media_type, simkl_id)
                );
                CREATE INDEX IF NOT EXISTS idx_display_meta_updated
                    ON display_meta(updated_at DESC);
                """
            )
            conn.commit()
            self._ensure_paint_stamp_column(conn)

    def _ensure_paint_stamp_column(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(display_meta)").fetchall()}
        if "paint_stamp" not in columns:
            conn.execute("ALTER TABLE display_meta ADD COLUMN paint_stamp TEXT")
            conn.commit()

    @staticmethod
    def _media_key(media_type: str) -> str:
        return "movie" if media_type == "movie" else "show"

    @staticmethod
    def _encode_blob(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _decode_blob(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _row_from_record(self, record: sqlite3.Row) -> dict[str, Any]:
        row = {
            "simkl_id": int(record["simkl_id"]),
            "info": self._decode_blob(record["info"]) or {},
            "art": self._decode_blob(record["art"]) or {},
            "cast": self._decode_blob(record["cast"]) or [],
        }
        stamp = record["paint_stamp"] if "paint_stamp" in record.keys() else None
        if stamp:
            row["_paint_stamp"] = stamp
        return row

    @staticmethod
    def _select_columns() -> str:
        return "media_type, simkl_id, info, art, [cast], paint_stamp"

    def get_row(self, media_type: str, simkl_id: int) -> dict[str, Any] | None:
        cache_type = self._media_key(media_type)
        ram_row = self._ram.get_row(cache_type, int(simkl_id))
        if ram_row and row_has_display_meta(ram_row) and ram_row.get("_paint_stamp"):
            return ram_row

        with self._db_lock:
            conn = self._connect()
            record = conn.execute(
                f"""
                SELECT {self._select_columns()}
                FROM display_meta
                WHERE media_type = ? AND simkl_id = ?
                """,
                (cache_type, int(simkl_id)),
            ).fetchone()
        if not record:
            return None
        row = self._row_from_record(record)
        if row_has_display_meta(row):
            self._ram.set_row(cache_type, row)
            return row
        return None

    def get_batch(self, media_type: str, simkl_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not simkl_ids:
            return {}
        cache_type = self._media_key(media_type)
        hits: dict[int, dict[str, Any]] = {}
        misses: list[int] = []

        for simkl_id in simkl_ids:
            sid = int(simkl_id)
            ram_row = self._ram.get_row(cache_type, sid)
            if ram_row and row_has_display_meta(ram_row):
                if ram_row.get("_paint_stamp"):
                    hits[sid] = ram_row
                else:
                    misses.append(sid)
            else:
                misses.append(sid)

        if misses:
            placeholders = ",".join("?" * len(misses))
            with self._db_lock:
                conn = self._connect()
                records = conn.execute(
                    f"""
                    SELECT {self._select_columns()}
                    FROM display_meta
                    WHERE media_type = ? AND simkl_id IN ({placeholders})
                    """,
                    (cache_type, *misses),
                ).fetchall()
            for record in records or []:
                row = self._row_from_record(record)
                if row_has_display_meta(row):
                    sid = int(row["simkl_id"])
                    hits[sid] = row
                    self._ram.set_row(cache_type, row)

        return hits

    def set_row(self, media_type: str, row: dict[str, Any]) -> None:
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            return
        from resources.lib.meta.storage import slim_db_row
        from resources.lib.meta.profiles import MetaProfile

        slim = slim_db_row(row, profile=MetaProfile.LIST)
        if not row_has_display_meta(slim):
            return

        cache_type = self._media_key(media_type)
        sid = int(slim["simkl_id"])
        self._ram.set_row(cache_type, slim)

        now = datetime.datetime.now().isoformat()
        paint_stamp = slim.get("_paint_stamp")
        with self._db_lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO display_meta (media_type, simkl_id, info, art, [cast], updated_at, paint_stamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_type, simkl_id) DO UPDATE SET
                    info = excluded.info,
                    art = excluded.art,
                    [cast] = excluded.[cast],
                    updated_at = excluded.updated_at,
                    paint_stamp = COALESCE(excluded.paint_stamp, display_meta.paint_stamp)
                """,
                (
                    cache_type,
                    sid,
                    self._encode_blob(slim.get("info")),
                    self._encode_blob(slim.get("art")),
                    self._encode_blob(slim.get("cast")),
                    now,
                    paint_stamp,
                ),
            )
            conn.commit()
            self._enforce_row_cap_locked(conn)

    def merge_art_cast_row(self, media_type: str, row: dict[str, Any]) -> None:
        """Update display_meta art/cast without replacing Simkl-owned info."""
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            return

        cache_type = self._media_key(media_type)
        sid = int(row["simkl_id"])
        existing = self.get_batch(media_type, [sid]).get(sid)
        if existing and row_has_display_meta(existing):
            updated = dict(existing)
            if isinstance(row.get("art"), dict) and row["art"]:
                updated["art"] = tools.smart_merge_dictionary(
                    dict(existing.get("art") or {}),
                    dict(row["art"]),
                    keep_original=True,
                    extend_array=False,
                )
            if row.get("cast"):
                updated["cast"] = row["cast"]
            self.set_row(media_type, updated)
            return

        self.set_row(media_type, row)

    def set_rows_batch(self, media_type: str, rows: list[dict[str, Any]]) -> int:
        """Persist multiple paint rows in one transaction."""
        from resources.lib.meta.storage import slim_db_row
        from resources.lib.meta.profiles import MetaProfile

        if not rows:
            return 0
        cache_type = self._media_key(media_type)
        now = datetime.datetime.now().isoformat()
        payload_rows: list[tuple] = []
        written = 0
        for row in rows:
            if not isinstance(row, dict) or row.get("simkl_id") is None:
                continue
            slim = slim_db_row(row, profile=MetaProfile.LIST)
            if not row_has_display_meta(slim):
                continue
            sid = int(slim["simkl_id"])
            self._ram.set_row(cache_type, slim)
            payload_rows.append(
                (
                    cache_type,
                    sid,
                    self._encode_blob(slim.get("info")),
                    self._encode_blob(slim.get("art")),
                    self._encode_blob(slim.get("cast")),
                    now,
                    slim.get("_paint_stamp"),
                )
            )
            written += 1
        if not payload_rows:
            return 0
        with self._db_lock:
            conn = self._connect()
            conn.executemany(
                """
                INSERT INTO display_meta (media_type, simkl_id, info, art, [cast], updated_at, paint_stamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_type, simkl_id) DO UPDATE SET
                    info = excluded.info,
                    art = excluded.art,
                    [cast] = excluded.[cast],
                    updated_at = excluded.updated_at,
                    paint_stamp = COALESCE(excluded.paint_stamp, display_meta.paint_stamp)
                """,
                payload_rows,
            )
            conn.commit()
            self._enforce_row_cap_locked(conn)
        return written

    def merge_art_cast_rows_batch(self, media_type: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        simkl_ids = [int(row["simkl_id"]) for row in rows if isinstance(row, dict) and row.get("simkl_id") is not None]
        if not simkl_ids:
            return 0
        existing = self.get_batch(media_type, simkl_ids)
        to_write: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("simkl_id") is None:
                continue
            sid = int(row["simkl_id"])
            base = existing.get(sid)
            if base and row_has_display_meta(base):
                updated = dict(base)
                if isinstance(row.get("art"), dict) and row["art"]:
                    updated["art"] = tools.smart_merge_dictionary(
                        dict(base.get("art") or {}),
                        dict(row["art"]),
                        keep_original=True,
                        extend_array=False,
                    )
                if row.get("cast"):
                    updated["cast"] = row["cast"]
                to_write.append(updated)
            else:
                to_write.append(row)
        return self.set_rows_batch(media_type, to_write)

    def set_stamped_rows_batch(self, media_type: str, rows: list[dict[str, Any]]) -> int:
        """Persist paint-complete rows with the current trust stamp."""
        from resources.lib.meta.paint_stamp import attach_paint_stamp_batch

        return self.set_rows_batch(media_type, attach_paint_stamp_batch(rows))

    def set_many_rows(self, media_type: str, rows: list[dict[str, Any]]) -> None:
        self.set_rows_batch(media_type, rows or [])

    def overlay_rows(self, rows: list[dict[str, Any]], media_type: str) -> list[dict[str, Any]]:
        """Replace row info/art/cast with display-cache hits when available."""
        if not rows:
            return rows
        simkl_ids = [int(row["simkl_id"]) for row in rows if isinstance(row, dict) and row.get("simkl_id") is not None]
        if not simkl_ids:
            return rows
        hits = self.get_batch(media_type, simkl_ids)
        if not hits:
            return rows

        from resources.lib.common import tools as merge_tools

        merged: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                merged.append(row)
                continue
            sid = row.get("simkl_id")
            if sid is None:
                merged.append(row)
                continue
            cached = hits.get(int(sid))
            if not cached:
                merged.append(row)
                continue
            updated = dict(row)
            merged_info = merge_tools.smart_merge_dictionary(
                dict(row.get("info") or {}),
                dict(cached.get("info") or {}),
                keep_original=True,
                extend_array=False,
            )
            from resources.lib.simkl.field_map import sanitize_list_info

            updated["info"] = sanitize_list_info(
                merged_info,
                catalog=row.get("catalog") or updated.get("catalog"),
            )
            updated["art"] = merge_tools.smart_merge_dictionary(
                dict(row.get("art") or {}),
                dict(cached.get("art") or {}),
                keep_original=True,
                extend_array=False,
            )
            if cached.get("cast"):
                updated["cast"] = cached["cast"]
            merged.append(updated)
        return merged

    def prefetch(self, limit: int | None = None) -> int:
        """Warm recent display rows into RAM (service idle)."""
        if limit is None:
            try:
                from resources.lib.modules.cache_maintenance import DISPLAY_META_PREFETCH_LIMIT

                limit = int(DISPLAY_META_PREFETCH_LIMIT)
            except Exception:
                limit = _PREFETCH_LIMIT
        if limit <= 0:
            return 0
        warmed = 0
        half = max(1, limit // 2)
        with self._db_lock:
            conn = self._connect()
            for cache_type, sql_limit in (("movie", half), ("show", max(1, limit - half))):
                records = conn.execute(
                    f"""
                    SELECT {self._select_columns()}
                    FROM display_meta
                    WHERE media_type = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (cache_type, sql_limit),
                ).fetchall()
                for record in records or []:
                    row = self._row_from_record(record)
                    if row_has_display_meta(row):
                        self._ram.set_row(cache_type, row)
                        warmed += 1
        return warmed

    def _library_exempt_simkl_ids(self) -> set[int]:
        try:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()
            rows = db.fetchall(
                """
                SELECT simkl_id FROM movies
                WHERE COALESCE(watched, 0) > 0 OR simkl_status IS NOT NULL
                UNION
                SELECT simkl_id FROM shows
                WHERE COALESCE(watched_episodes, 0) > 0 OR simkl_status IS NOT NULL
                UNION
                SELECT simkl_id FROM bookmarks WHERE simkl_id IS NOT NULL
                """
            )
            return {int(row["simkl_id"]) for row in rows or [] if row.get("simkl_id") is not None}
        except Exception:
            g.log_stacktrace()
            return set()

    def _enforce_row_cap_locked(self, conn: sqlite3.Connection) -> None:
        count_row = conn.execute("SELECT COUNT(*) AS count FROM display_meta").fetchone()
        total = int(count_row["count"]) if count_row else 0
        if total <= _MAX_ROWS:
            return
        trim = total - _MAX_ROWS
        exempt = self._library_exempt_simkl_ids()
        if exempt:
            placeholders = ",".join("?" * len(exempt))
            conn.execute(
                f"""
                DELETE FROM display_meta
                WHERE rowid IN (
                    SELECT rowid FROM display_meta
                    WHERE simkl_id NOT IN ({placeholders})
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (*exempt, trim),
            )
        else:
            conn.execute(
                """
                DELETE FROM display_meta
                WHERE rowid IN (
                    SELECT rowid FROM display_meta
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (trim,),
            )
        conn.commit()

    def delete_row(self, media_type: str, simkl_id: int) -> None:
        """Drop one cached display row (RAM + prism_meta.db)."""
        cache_type = self._media_key(media_type)
        sid = int(simkl_id)
        self._ram.delete_row(cache_type, sid)
        with self._db_lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM display_meta WHERE media_type = ? AND simkl_id = ?",
                (cache_type, sid),
            )
            conn.commit()

    def clear_paint_stamps(self) -> None:
        """Invalidate trust stamps without deleting paint blobs."""
        with self._db_lock:
            conn = self._connect()
            self._ensure_paint_stamp_column(conn)
            conn.execute("UPDATE display_meta SET paint_stamp = NULL")
            conn.commit()
        self._ram.clear_session()

    def _enforce_row_cap(self) -> None:
        with self._db_lock:
            self._enforce_row_cap_locked(self._connect())

    def clear_all(self) -> None:
        self._ram.clear_session()
        with self._db_lock:
            conn = self._connect()
            conn.execute("DELETE FROM display_meta")
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
