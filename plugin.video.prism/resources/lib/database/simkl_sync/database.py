from __future__ import annotations

import collections
import datetime
import json
import time
from functools import cached_property
from urllib import parse

import xbmcgui

from resources.lib.common import tools
from resources.lib.common.thread_pool import ThreadPool
from resources.lib.database import Database
from resources.lib.modules.exceptions import InvalidMediaTypeException
from resources.lib.modules.exceptions import UnsupportedProviderType
from resources.lib.modules.globals import g
from resources.lib.discover.normalize import _int_or_none
from resources.lib.modules.metadataHandler import MetadataHandler
from resources.lib.modules.sync_lock import SyncLock

schema = {
    "shows_meta": {
        "columns": collections.OrderedDict(
            [
                ("id", ["INTEGER", "NOT NULL"]),
                ("type", ["TEXT", "NOT NULL"]),
                ("meta_hash", ["TEXT", "NOT NULL"]),
                ("value", ["PICKLE", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["UNIQUE(id, type)"],
        "default_seed": [],
    },
    "seasons_meta": {
        "columns": collections.OrderedDict(
            [
                ("id", ["INTEGER", "NOT NULL"]),
                ("type", ["TEXT", "NOT NULL"]),
                ("meta_hash", ["TEXT", "NOT NULL"]),
                ("value", ["PICKLE", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["UNIQUE(id, type)"],
        "default_seed": [],
    },
    "episodes_meta": {
        "columns": collections.OrderedDict(
            [
                ("id", ["INTEGER", "NOT NULL"]),
                ("type", ["TEXT", "NOT NULL"]),
                ("meta_hash", ["TEXT", "NOT NULL"]),
                ("value", ["PICKLE", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["UNIQUE(id, type)"],
        "default_seed": [],
    },
    "movies_meta": {
        "columns": collections.OrderedDict(
            [
                ("id", ["INTEGER", "NOT NULL"]),
                ("type", ["TEXT", "NOT NULL"]),
                ("meta_hash", ["TEXT", "NOT NULL"]),
                ("value", ["PICKLE", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["UNIQUE(id, type)"],
        "default_seed": [],
    },
    "shows": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "PRIMARY KEY", "NOT NULL"]),
                ("tvdb_id", ["INTEGER", "NULL"]),
                ("tmdb_id", ["INTEGER", "NULL"]),
                ("imdb_id", ["INTEGER", "NULL"]),
                ("info", ["PICKLE", "NULL"]),
                ("cast", ["PICKLE", "NULL"]),
                ("art", ["PICKLE", "NULL"]),
                ("meta_hash", ["TEXT", "NULL"]),
                ("season_count", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("episode_count", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("unwatched_episodes", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("watched_episodes", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("last_updated", ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"]),
                ("args", ["TEXT", "NOT NULL"]),
                ("air_date", ["TEXT"]),
                ("is_airing", ["BOOLEAN"]),
                ("last_watched_at", ["TEXT"]),
                ("user_rating", ["INTEGER", "NULL"]),
                ("needs_update", ["BOOLEAN", "NOT NULL", "DEFAULT 1"]),
                ("needs_milling", ["BOOLEAN", "NOT NULL", "DEFAULT 1"]),
                ("simkl_status", ["TEXT", "NULL"]),
            ]
        ),
        "table_constraints": [],
        "indices": [
            ("idx_shows_simkl_status", ["simkl_status"]),
        ],
        "default_seed": [],
    },
    "seasons": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "NOT NULL"]),
                ("simkl_show_id", ["INTEGER", "NOT NULL"]),
                ("tvdb_id", ["INTEGER", "NULL"]),
                ("tmdb_id", ["INTEGER", "NULL"]),
                ("season", ["INTEGER", "NOT NULL"]),
                ("info", ["PICKLE", "NULL"]),
                ("cast", ["PICKLE", "NULL"]),
                ("art", ["PICKLE", "NULL"]),
                ("meta_hash", ["TEXT", "NULL"]),
                ("episode_count", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("unwatched_episodes", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("watched_episodes", ["INTEGER", "NULL", "DEFAULT 0"]),
                ("last_updated", ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"]),
                ("args", ["TEXT", "NOT NULL"]),
                ("air_date", ["TEXT"]),
                ("is_airing", ["BOOLEAN"]),
                ("last_watched_at", ["TEXT"]),
                ("user_rating", ["INTEGER", "NULL"]),
                ("needs_update", ["BOOLEAN", "NOT NULL", "DEFAULT 1"]),
            ]
        ),
        "table_constraints": [
            "PRIMARY KEY(simkl_show_id, season)",
            "UNIQUE(simkl_id)"
            "FOREIGN KEY(simkl_show_id) REFERENCES shows(simkl_id) ON UPDATE CASCADE ON DELETE CASCADE",
        ],
        "default_seed": [],
    },
    "episodes": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "NOT NULL"]),
                ("simkl_show_id", ["INTEGER", "NOT NULL"]),
                ("simkl_season_id", ["INTEGER", "NOT NULL"]),
                ("season", ["INTEGER", "NOT NULL"]),
                ("tvdb_id", ["INTEGER", "NULL"]),
                ("tmdb_id", ["INTEGER", "NULL"]),
                ("imdb_id", ["INTEGER", "NULL"]),
                ("info", ["PICKLE", "NULL"]),
                ("cast", ["PICKLE", "NULL"]),
                ("art", ["PICKLE", "NULL"]),
                ("meta_hash", ["TEXT", "NULL"]),
                ("last_updated", ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"]),
                ("watched", ["INTEGER", "NOT NULL", "DEFAULT 0"]),
                ("number", ["INTEGER", "NOT NULL"]),
                ("args", ["TEXT", "NOT NULL"]),
                ("air_date", ["TEXT"]),
                ("last_watched_at", ["TEXT"]),
                ("user_rating", ["INTEGER", "NULL"]),
                ("needs_update", ["BOOLEAN", "NOT NULL", "DEFAULT 1"]),
            ]
        ),
        "table_constraints": [
            "PRIMARY KEY(simkl_show_id, season, number)",
            "UNIQUE(simkl_id)"
            "FOREIGN KEY(simkl_season_id) REFERENCES seasons(simkl_id) ON UPDATE CASCADE ON DELETE CASCADE",
            "FOREIGN KEY(simkl_show_id) REFERENCES shows(simkl_id) ON UPDATE CASCADE ON DELETE CASCADE",
        ],
        "indices": [
            ("idx_episodes_showid", ["simkl_show_id"]),
            ("idx_episodes_seasonid", ["simkl_season_id"]),
            ("idx_episodes_showid_season_number_lastwatched", ["simkl_show_id", "season", "number", "last_watched_at"]),
            ("idx_episodes_season_number", ["season", "number"]),
        ],
        "default_seed": [],
    },
    "movies": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "PRIMARY KEY", "NOT NULL"]),
                ("tmdb_id", ["INTEGER", "NULL"]),
                ("tvdb_id", ["INTEGER", "NULL"]),
                ("imdb_id", ["INTEGER", "NULL"]),
                ("info", ["PICKLE", "NULL"]),
                ("cast", ["PICKLE", "NULL"]),
                ("art", ["PICKLE", "NULL"]),
                ("meta_hash", ["TEXT", "NULL"]),
                ("last_updated", ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"]),
                ("watched", ["INTEGER", "NOT NULL", "DEFAULT 0"]),
                ("args", ["TEXT", "NOT NULL"]),
                ("air_date", ["TEXT"]),
                ("last_watched_at", ["TEXT"]),
                ("user_rating", ["INTEGER", "NULL"]),
                ("needs_update", ["BOOLEAN", "NOT NULL", "DEFAULT 1"]),
                ("simkl_status", ["TEXT", "NULL"]),
            ]
        ),
        "table_constraints": [],
        "indices": [
            ("idx_movies_watched_lastwatched", ["watched", "last_watched_at"]),
            ("idx_movies_simkl_status", ["simkl_status"]),
        ],
        "default_seed": [],
    },
    "hidden": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "NOT NULL"]),
                ("mediatype", ["TEXT", "NOT NULL"]),
                ("section", ["TEXT", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["PRIMARY KEY(simkl_id, simkl_id, mediatype, section)"],
        "indices": [("idx_hidden_section_mediatype", ["section", "mediatype"])],
        "default_seed": [],
    },
    "activities": {
        "columns": collections.OrderedDict(
            [
                ("sync_id", ["INTEGER", "PRIMARY KEY"]),
                (
                    "all_activities",
                    ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"],
                ),
                (
                    "movies_bookmarked",
                    ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"],
                ),
                (
                    "episodes_bookmarked",
                    ["TEXT", "NOT NULL", "DEFAULT '1970-01-01T00:00:00'"],
                ),
                ("simkl_username", ["TEXT", "NULL"]),
                ("last_activities_call", ["INTEGER", "NOT NULL", "DEFAULT 1"]),
                ("last_changes_poll", ["INTEGER", "NOT NULL", "DEFAULT 0"]),
            ]
        ),
        "table_constraints": ["UNIQUE(sync_id)"],
        "default_seed": [],
    },
    "bookmarks": {
        "columns": collections.OrderedDict(
            [
                ("simkl_id", ["INTEGER", "PRIMARY KEY", "NOT NULL"]),
                ("resume_time", ["TEXT", "NOT NULL"]),
                ("percent_played", ["TEXT", "NOT NULL"]),
                ("type", ["TEXT", "NOT NULL"]),
                ("paused_at", ["TEXT", "NOT NULL"]),
                ("catalog", ["TEXT"]),
            ]
        ),
        "table_constraints": [],
        "indices": [("idx_bookmarks_paused", ["paused_at"])],
        "default_seed": [],
    },
}


class SimklSyncDatabase(Database):
    def __init__(
        self,
        db_path=None,
    ):
        super().__init__(db_path or g.SIMKL_SYNC_DB_PATH, schema)

        self._ensure_auxiliary_tables()
        self.activities = {}
        self.item_list = []
        self.base_date = "1970-01-01T00:00:00"
        self.task_queue = ThreadPool()
        self.mill_task_queue = ThreadPool()
        self.refresh_activities()
        if not g.get_bool_runtime_setting("simkl_sync.migrations.done"):
            self._migrate_library_status_columns()
            self._migrate_movies_tvdb_id_column()
            self._migrate_library_cache_tables()
            self._migrate_bookmarks_catalog_column()
            self._migrate_last_changes_poll_column()
            g.set_runtime_setting("simkl_sync.migrations.done", True)

        self._migrate_provider_cast_art_only_policy()
        self._migrate_episode_watch_state_repair()
        self._migrate_activities_slim_schema()

        if self.activities is None:
            self.clear_all_meta(False)
            self.set_base_activities()

        self.notification_prefix = f"{g.ADDON_NAME}: Simkl"
        self.hide_unaired = g.get_bool_setting("general.hideUnAired")
        self.hide_specials = g.get_bool_setting("general.hideSpecials")
        self.hide_watched = g.get_bool_setting("general.hideWatched")
        self.page_limit = g.get_int_setting("item.limit")
        self._pending_list_enrichment = None

    def _ensure_auxiliary_tables(self) -> None:
        """Tables created outside the declarative schema (discover catalog, library cache)."""
        from resources.lib.discover.catalog_store import ensure_catalog_tables
        from resources.lib.simkl.library_cache import ensure_library_cache_tables

        ensure_catalog_tables(self)
        ensure_library_cache_tables(self)

    def consume_list_enrichment_refs(self) -> tuple[list[dict], str | None]:
        """Return and clear the first refs batch queued during the last paint-first list load."""
        batches = self.consume_list_enrichment_batches()
        if not batches:
            return [], None
        return batches[0]

    def consume_list_enrichment_batches(self) -> list[tuple[list[dict], str]]:
        """Return and clear all refs batches queued during the last paint-first list load."""
        pending = self._pending_list_enrichment
        self._pending_list_enrichment = None
        if not pending:
            return []
        if isinstance(pending, list):
            batches: list[tuple[list[dict], str]] = []
            for batch in pending:
                refs = batch.get("refs") or []
                media_type = batch.get("media_type")
                if refs and media_type:
                    batches.append((refs, media_type))
            return batches
        refs = pending.get("refs") or []
        media_type = pending.get("media_type")
        if refs and media_type:
            return [(refs, media_type)]
        return []

    def set_list_enrichment_refs(self, refs: list[dict], media_type: str) -> None:
        if not refs:
            self._pending_list_enrichment = None
            return
        batch = {"refs": refs, "media_type": media_type}
        pending = self._pending_list_enrichment
        if pending is None:
            self._pending_list_enrichment = [batch]
        elif isinstance(pending, list):
            pending.append(batch)
        else:
            self._pending_list_enrichment = [pending, batch]

    @cached_property
    def metadataHandler(self):
        from resources.lib.modules.metadataHandler import MetadataHandler

        return MetadataHandler()

    def fetch_paint_overlay_fields(self, media_type: str, simkl_ids: list[int]) -> dict[int, dict]:
        """Lightweight sync fields for paint-cache rows (no info/art pickles)."""
        if not simkl_ids:
            return {}
        ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
        if media_type == "movie":
            query = f"""
                SELECT m.simkl_id, m.args, m.watched AS play_count, m.user_rating,
                       m.tmdb_id, m.tvdb_id, m.imdb_id, m.air_date, m.[cast],
                       b.resume_time, b.percent_played
                FROM movies AS m
                LEFT JOIN bookmarks AS b ON b.simkl_id = m.simkl_id
                WHERE m.simkl_id IN ({ids_sql})
            """
        else:
            query = f"""
                SELECT s.simkl_id, s.args, s.watched_episodes, s.episode_count, s.user_rating,
                       s.tmdb_id, s.tvdb_id, s.imdb_id, s.air_date, s.is_airing, s.[cast],
                       b.resume_time, b.percent_played
                FROM shows AS s
                LEFT JOIN bookmarks AS b ON b.simkl_id = s.simkl_id
                WHERE s.simkl_id IN ({ids_sql})
            """
        rows = self.fetchall(query) or []
        return {
            int(row["simkl_id"]): dict(row)
            for row in rows
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }

    def fetch_paint_rows_batch(self, media_type: str, simkl_ids: list[int]) -> dict[int, dict]:
        """Load denormalized info/art/cast rows for list paint (Seren-style)."""
        if not simkl_ids:
            return {}
        ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
        if media_type == "movie":
            query = f"""
                SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id, air_date
                FROM movies
                WHERE simkl_id IN ({ids_sql})
            """
        else:
            query = f"""
                SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id, air_date, is_airing
                FROM shows
                WHERE simkl_id IN ({ids_sql})
            """
        rows = self.fetchall(query) or []
        return {
            int(row["simkl_id"]): dict(row)
            for row in rows
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }

    def upsert_paint_rows_batch(self, media_type: str, rows: list[dict]) -> int:
        """Persist paint-complete info/art/cast into movies/shows without full metadata update."""
        if not rows:
            return 0
        from resources.lib.meta.storage import slim_art_dict, slim_info_dict
        from resources.lib.database.sync_meta_cache import SyncMetaCache
        from resources.lib.simkl.ids import serialize_action_args

        table = "movies" if media_type == "movie" else "shows"
        cache_media = "movie" if media_type == "movie" else "show"
        payload_rows: list[tuple] = []
        cache_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("simkl_id") is None:
                continue
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            art = row.get("art") if isinstance(row.get("art"), dict) else {}
            cast = row.get("cast") if isinstance(row.get("cast"), list) else None
            if not info and not art and not cast:
                continue
            slim_info = slim_info_dict(info) or None
            slim_art = slim_art_dict(art, cache_media) or None
            simkl_id = int(row["simkl_id"])
            args_value = row.get("args")
            if not args_value:
                args_value = serialize_action_args(
                    {
                        "simkl_id": simkl_id,
                        "catalog": row.get("catalog") or info.get("catalog"),
                        "info": slim_info or info,
                        "simkl_object": {"info": slim_info or info, "art": slim_art or art},
                    }
                )
            payload_rows.append(
                (
                    simkl_id,
                    slim_info,
                    slim_art,
                    cast,
                    row.get("tmdb_id") or info.get("tmdb_id"),
                    row.get("tvdb_id") or info.get("tvdb_id"),
                    row.get("imdb_id") or info.get("imdb_id"),
                    args_value,
                )
            )
            cache_rows.append(
                {
                    "simkl_id": simkl_id,
                    "info": slim_info or info,
                    "art": slim_art or art,
                    "cast": cast or [],
                    "tmdb_id": row.get("tmdb_id") or info.get("tmdb_id"),
                    "tvdb_id": row.get("tvdb_id") or info.get("tvdb_id"),
                    "imdb_id": row.get("imdb_id") or info.get("imdb_id"),
                    "air_date": row.get("air_date") or info.get("premiered") or info.get("released"),
                }
            )
        if not payload_rows:
            return 0
        if table == "movies":
            query = """
                INSERT INTO movies(simkl_id, info, art, cast, tmdb_id, tvdb_id, imdb_id, args, watched, needs_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
                ON CONFLICT(simkl_id) DO UPDATE SET
                    info = coalesce(excluded.info, movies.info),
                    art = coalesce(excluded.art, movies.art),
                    cast = coalesce(excluded.cast, movies.cast),
                    tmdb_id = coalesce(excluded.tmdb_id, movies.tmdb_id),
                    tvdb_id = coalesce(excluded.tvdb_id, movies.tvdb_id),
                    imdb_id = coalesce(excluded.imdb_id, movies.imdb_id),
                    args = coalesce(excluded.args, movies.args)
            """
        else:
            query = """
                INSERT INTO shows(simkl_id, info, art, cast, tmdb_id, tvdb_id, imdb_id, args, needs_update, needs_milling)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                ON CONFLICT(simkl_id) DO UPDATE SET
                    info = coalesce(excluded.info, shows.info),
                    art = coalesce(excluded.art, shows.art),
                    cast = coalesce(excluded.cast, shows.cast),
                    tmdb_id = coalesce(excluded.tmdb_id, shows.tmdb_id),
                    tvdb_id = coalesce(excluded.tvdb_id, shows.tvdb_id),
                    imdb_id = coalesce(excluded.imdb_id, shows.imdb_id),
                    args = coalesce(excluded.args, shows.args)
            """
        self.execute_sql(query, payload_rows)
        SyncMetaCache().set_many_rows(cache_media, cache_rows)
        return len(payload_rows)

    @cached_property
    def simkl_api(self):
        from resources.lib.indexers.simkl import SimklAPI

        return SimklAPI()

    def clear_specific_item_meta(self, simkl_id, media_type):
        if media_type in ["tvshow", "show"]:
            media_type = "shows"
        elif media_type == "movie":
            media_type = "movies"
        elif media_type == "episode":
            media_type = "episodes"
        elif media_type == "season":
            media_type = "seasons"

        if media_type not in ["shows", "movies", "seasons", "episodes"]:
            raise InvalidMediaTypeException(media_type)

        self.execute_sql(f"DELETE from {media_type}_meta where id=?", (simkl_id,))
        self.execute_sql(
            f"UPDATE {media_type} SET info=null, art=null, cast=null, meta_hash=null where simkl_id=?",
            (simkl_id,),
        )

    def _purge_meta_table_ids(self, meta_table: str, ids: list[int] | set[int]) -> None:
        unique = sorted({int(i) for i in ids if i is not None})
        if not unique:
            return
        placeholders = ",".join("?" * len(unique))
        self.execute_sql(
            f"DELETE FROM {meta_table} WHERE id IN ({placeholders})",
            tuple(unique),
        )

    def _invalidate_sync_row_meta(
        self,
        table: str,
        meta_table: str,
        simkl_id: int,
        *,
        include_provider_ids: bool = True,
    ) -> None:
        """Clear formatted fields, provider meta blobs, and flag row for re-enrichment."""
        row = self.fetchone(
            f"SELECT simkl_id, tmdb_id, tvdb_id FROM {table} WHERE simkl_id = ?",
            (int(simkl_id),),
        )
        if not row:
            return
        meta_ids = [int(simkl_id)]
        if include_provider_ids:
            if row.get("tmdb_id"):
                meta_ids.append(int(row["tmdb_id"]))
            if row.get("tvdb_id"):
                meta_ids.append(int(row["tvdb_id"]))
        self._purge_meta_table_ids(meta_table, meta_ids)
        self.execute_sql(
            f"UPDATE {table} SET info=null, art=null, cast=null, meta_hash=null, needs_update=1 WHERE simkl_id=?",
            (int(simkl_id),),
        )

    def _update_last_activities_call(self):
        self.execute_sql("UPDATE activities SET last_activities_call=? WHERE sync_id=1", (int(time.time()),))
        self.refresh_activities()

    def _insert_last_activities_column(self):
        self.execute_sql("ALTER TABLE activities ADD last_activities_call INTEGER NOT NULL DEFAULT 1")

    def get_library_status(self, simkl_id: int, catalog: str, info: dict | None = None) -> str | None:
        if isinstance(info, dict) and info.get("simkl_status"):
            return info.get("simkl_status")
        table = "movies" if catalog == "movie" else "shows"
        row = self.fetchone(f"SELECT simkl_status FROM {table} WHERE simkl_id=?", (int(simkl_id),))
        return row.get("simkl_status") if row else None

    def _migrate_library_status_columns(self):
        """Add simkl_status column for local watchlist membership (survives metadata refresh)."""
        for table in ("movies", "shows"):
            columns = {row["name"] for row in self.fetchall(f"PRAGMA table_info({table})")}
            if "simkl_status" not in columns:
                self.execute_sql(f"ALTER TABLE {table} ADD COLUMN simkl_status TEXT")
                g.log(f"SimklSync: migrated {table}.simkl_status column", "info")
            self.execute_sql(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_simkl_status ON {table}(simkl_status)"
            )

        for table in ("movies", "shows"):
            rows = self.fetchall(f"SELECT simkl_id, info, simkl_status FROM {table}")
            for row in rows:
                if row.get("simkl_status"):
                    continue
                info = row.get("info")
                if not isinstance(info, dict):
                    continue
                status = info.get("simkl_status")
                if status:
                    self.execute_sql(
                        f"UPDATE {table} SET simkl_status=? WHERE simkl_id=?",
                        (status, row["simkl_id"]),
                    )

    def _migrate_movies_tvdb_id_column(self):
        columns = {row["name"] for row in self.fetchall("PRAGMA table_info(movies)")}
        if "tvdb_id" not in columns:
            self.execute_sql("ALTER TABLE movies ADD COLUMN tvdb_id INTEGER")
            g.log("SimklSync: migrated movies.tvdb_id column", "info")
            self.execute_sql("CREATE INDEX IF NOT EXISTS idx_movies_tvdb_id ON movies(tvdb_id)")

        rows = self.fetchall("SELECT simkl_id, info, tvdb_id FROM movies WHERE tvdb_id IS NULL")
        for row in rows:
            info = row.get("info")
            if not isinstance(info, dict):
                continue
            tvdb_id = info.get("tvdb_id")
            if tvdb_id is None and isinstance(info.get("ids"), dict):
                tvdb_id = info["ids"].get("tvdb")
            if tvdb_id is not None:
                self.execute_sql(
                    "UPDATE movies SET tvdb_id=? WHERE simkl_id=?",
                    (int(tvdb_id), row["simkl_id"]),
                )

    def _migrate_library_cache_tables(self):
        from resources.lib.simkl.library_cache import ensure_library_cache_tables

        ensure_library_cache_tables(self)

    def _migrate_bookmarks_catalog_column(self):
        columns = {row["name"] for row in self.fetchall("PRAGMA table_info(bookmarks)")}
        if "catalog" not in columns:
            self.execute_sql("ALTER TABLE bookmarks ADD COLUMN catalog TEXT")
            g.log("SimklSync: migrated bookmarks.catalog column", "info")
            self.execute_sql(
                """
                UPDATE bookmarks
                SET catalog = CASE
                    WHEN type = 'movie' THEN 'movie'
                    ELSE 'tv'
                END
                WHERE catalog IS NULL
                """
            )

    def _migrate_last_changes_poll_column(self):
        columns = {row["name"] for row in self.fetchall("PRAGMA table_info(activities)")}
        if "last_changes_poll" not in columns:
            self.execute_sql("ALTER TABLE activities ADD last_changes_poll INTEGER NOT NULL DEFAULT 0")
            g.log("SimklSync: migrated activities.last_changes_poll column", "info")

    def _migrate_activities_slim_schema(self) -> None:
        """Drop legacy per-section activity watermarks superseded by all_activities."""
        flag = "activities.slim_schema_v1"
        if g.get_bool_runtime_setting(flag):
            return

        dead_columns = (
            "shows_watched",
            "movies_watched",
            "shows_rated",
            "movies_rated",
            "hidden_sync",
            "shows_meta_update",
            "movies_meta_update",
            "lists_sync",
        )
        existing = {row["name"] for row in self.fetchall("PRAGMA table_info(activities)")}
        removed = 0
        for column in dead_columns:
            if column not in existing:
                continue
            try:
                self.execute_sql(f"ALTER TABLE activities DROP COLUMN {column}")
                removed += 1
            except Exception:
                g.log(f"SimklSync: could not drop activities.{column}", "debug")

        if removed:
            g.log(f"SimklSync: dropped {removed} legacy activities column(s)", "info")
        g.set_runtime_setting(flag, True)

    @staticmethod
    def _strip_provider_text_from_info(info: dict, simkl_info: dict) -> dict:
        """Remove TMDB/TVDB descriptive fields unless Simkl owns them."""
        if not isinstance(info, dict):
            return info
        simkl_info = simkl_info if isinstance(simkl_info, dict) else {}
        cleaned = dict(info)
        text_fields = (
            "plot",
            "overview",
            "plotoutline",
            "tagline",
            "mpaa",
            "certification",
            "studio",
            "country",
            "status",
            "duration",
            "runtime",
        )
        for key in text_fields:
            if key in cleaned and not simkl_info.get(key) and not (
                key == "plot" and simkl_info.get("overview")
            ):
                cleaned.pop(key, None)
        for key in list(cleaned.keys()):
            if str(key).startswith("rating.") and not simkl_info.get(key):
                cleaned.pop(key, None)
        for key in text_fields:
            if simkl_info.get(key):
                cleaned[key] = simkl_info[key]
        if simkl_info.get("overview") and not cleaned.get("plot"):
            cleaned["plot"] = simkl_info["overview"]
        for key, value in simkl_info.items():
            if str(key).startswith("rating."):
                cleaned[key] = value
        return cleaned

    def _migrate_provider_cast_art_only_policy(self) -> None:
        """One-time cleanup after enforcing TMDB/TVDB cast+art-only metadata policy."""
        flag = "meta.provider_cast_art_only_v1"
        if g.get_bool_runtime_setting(flag):
            return

        try:
            from resources.lib.meta.display_store import get_display_meta_store

            get_display_meta_store().clear_all()
        except Exception:
            g.log_stacktrace()

        updated = 0
        for table, meta_table in (("movies", "movies_meta"), ("shows", "shows_meta")):
            rows = self.fetchall(
                f"""
                SELECT m.simkl_id, m.info, mm.value AS simkl_object
                FROM {table} AS m
                         LEFT JOIN {meta_table} AS mm
                                   ON mm.id = m.simkl_id AND mm.type = 'simkl'
                WHERE m.info IS NOT NULL
                """
            ) or []
            for row in rows:
                info = row.get("info")
                if not isinstance(info, dict):
                    continue
                simkl_obj = row.get("simkl_object") or {}
                simkl_info = simkl_obj.get("info") if isinstance(simkl_obj, dict) else {}
                cleaned = self._strip_provider_text_from_info(info, simkl_info or {})
                if cleaned != info:
                    self.execute_sql(
                        f"UPDATE {table} SET info=? WHERE simkl_id=?",
                        (cleaned, int(row["simkl_id"])),
                    )
                    updated += 1

        g.set_runtime_setting(flag, True)
        g.log(
            f"SimklSync: provider cast+art-only policy migration complete "
            f"(display_meta cleared, {updated} info rows scrubbed)",
            "info",
        )

    def _migrate_episode_watch_state_repair(self) -> None:
        """Backfill episode watched flags cleared by episode-catalog warm upserts."""
        flag = "episode_watch_state_repair_v1"
        if g.get_bool_runtime_setting(flag):
            return

        before = self.fetchone(
            "SELECT COUNT(*) AS c FROM episodes WHERE COALESCE(watched, 0) > 0"
        )
        before_count = int((before or {}).get("c") or 0)

        self.execute_sql(
            """
            UPDATE episodes
            SET watched = 1,
                last_watched_at = COALESCE(
                    episodes.last_watched_at,
                    (SELECT s.last_watched_at FROM shows AS s WHERE s.simkl_id = episodes.simkl_show_id)
                )
            WHERE season != 0
              AND COALESCE(watched, 0) = 0
              AND simkl_show_id IN (
                SELECT simkl_id FROM shows WHERE COALESCE(simkl_status, '') = 'completed'
            )
            """
        )

        self._update_all_shows_statisics()

        after = self.fetchone(
            "SELECT COUNT(*) AS c FROM episodes WHERE COALESCE(watched, 0) > 0"
        )
        after_count = int((after or {}).get("c") or 0)

        g.set_runtime_setting(flag, True)
        g.log(
            f"SimklSync: episode watch-state repair complete "
            f"({before_count} -> {after_count} watched episode rows)",
            "info",
        )

    def sync_show_watch_state_from_entries(self, entries, shows) -> None:
        """Apply Simkl per-episode flags, progress fallback, counters, and completed overrides."""
        episode_entries = [
            entry
            for entry in (entries or [])
            if isinstance(entry, dict) and entry.get("status") in ("watching", "completed", "dropped", "hold")
        ]
        if not episode_entries or not shows:
            return
        self._apply_entry_watch_state(episode_entries, shows)
        for entry in episode_entries:
            if (
                not self._entry_has_full_episode_watch_detail(entry)
                and not self._entry_has_per_episode_watch_rows(entry)
            ):
                self.apply_watched_progress_from_entry(entry)
        self.apply_show_watch_counters(episode_entries)
        self.apply_completed_show_watch_flags(episode_entries)

    def reapply_episode_watch_state_from_entries(self, entries, shows) -> None:
        """Re-apply Simkl per-episode watch flags after episode catalog warm."""
        self.sync_show_watch_state_from_entries(entries, shows)

    def refresh_show_episode_watch_state(self, simkl_show_id: int, *, force: bool = False) -> bool:
        """Pull per-episode watch flags from Simkl when local stubs lack watched state."""
        from resources.lib.simkl.all_items_sync import refresh_show_episode_watch_state

        return refresh_show_episode_watch_state(self, int(simkl_show_id), force=force)

    def replace_playback_bookmarks(self, bookmark_type: str, rows: list[tuple]) -> None:
        """Replace synced Simkl playback sessions for movie or episode type."""
        self.execute_sql("DELETE FROM bookmarks WHERE type=?", (bookmark_type,))
        if not rows:
            return
        self.execute_sql(
            "INSERT INTO bookmarks (simkl_id, resume_time, percent_played, type, paused_at, catalog) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            rows,
        )

    def get_continue_watching(self, catalog: str, hidden_show_ids: set[int] | None = None) -> list[dict]:
        hidden_show_ids = hidden_show_ids or set()
        if catalog == "movie":
            return self._continue_watching_movies(hidden_show_ids)
        return self._continue_watching_episodes(catalog, hidden_show_ids)

    def _continue_watching_movies(self, hidden_ids: set[int]) -> list[dict]:
        rows = self.fetchall(
            """
            SELECT bm.simkl_id,
                   bm.resume_time AS progress,
                   bm.percent_played,
                   mm.value         AS simkl_object
            FROM bookmarks AS bm
                     LEFT JOIN movies_meta AS mm ON bm.simkl_id = mm.id AND mm.type = 'simkl'
            WHERE bm.type = 'movie'
              AND bm.catalog = 'movie'
            ORDER BY Datetime(bm.paused_at) DESC
            """
        )
        return self.wrap_in_simkl_object(
            [row for row in rows if row.get("simkl_id") not in hidden_ids]
        )

    def _continue_watching_episodes(self, catalog: str, hidden_show_ids: set[int]) -> list[dict]:
        rows = self.fetchall(
            """
            SELECT ep.simkl_show_id   AS simkl_show_id,
                   ep.simkl_id        AS simkl_id,
                   ep.simkl_season_id AS simkl_season_id,
                   ep.season          AS season_x,
                   ep.number          AS episode_x,
                   bm.resume_time     AS progress,
                   bm.percent_played,
                   em.value           AS episode,
                   sm.value           AS show
            FROM bookmarks AS bm
                     INNER JOIN episodes AS ep ON bm.simkl_id = ep.simkl_id
                     LEFT JOIN episodes_meta AS em
                               ON ep.simkl_id = em.id AND em.type = 'simkl'
                     LEFT JOIN shows_meta AS sm
                               ON ep.simkl_show_id = sm.id AND sm.type = 'simkl'
            WHERE bm.type = 'episode'
              AND bm.catalog = ?
            ORDER BY Datetime(bm.paused_at) DESC
            """,
            (catalog,),
        )
        return self.wrap_in_simkl_object(
            [row for row in rows if row.get("simkl_show_id") not in hidden_show_ids]
        )

    @staticmethod
    def _library_status_from_item(item) -> str | None:
        from resources.lib.modules.metadataHandler import MetadataHandler

        if not isinstance(item, dict):
            return None
        status = item.get("simkl_status")
        if status:
            return status
        info = MetadataHandler.simkl_info(item)
        if isinstance(info, dict) and info.get("simkl_status"):
            return info.get("simkl_status")
        return None

    def _preserve_library_status_on_items(self, items, table: str) -> None:
        from resources.lib.modules.metadataHandler import MetadataHandler

        for item in items:
            if not isinstance(item, dict) or not item.get("simkl_id"):
                continue
            if self._library_status_from_item(item):
                continue
            row = self.fetchone(
                f"SELECT simkl_status, info FROM {table} WHERE simkl_id=?",
                (int(item["simkl_id"]),),
            )
            if not row:
                continue
            existing = row.get("simkl_status")
            if not existing and isinstance(row.get("info"), dict):
                existing = row["info"].get("simkl_status")
            if not existing:
                continue
            info = MetadataHandler.simkl_info(item)
            if isinstance(info, dict):
                info["simkl_status"] = existing
            item["simkl_status"] = existing

    @staticmethod
    def _get_datetime_now():
        return g.datetime_to_string(datetime.datetime.utcnow())

    @staticmethod
    def _get_aired_cutoff():
        from resources.lib.modules.air_date_delay import aired_cutoff_datetime_string

        return aired_cutoff_datetime_string()

    def refresh_activities(self):
        self.activities = self.fetchone("SELECT * FROM activities WHERE sync_id=1")

    def set_base_activities(self):
        username = g.get_setting("simkl.username")
        self.execute_sql(
            "REPLACE INTO activities(sync_id, simkl_username) VALUES(1, ?)",
            (username,),
        )
        self.activities = self.fetchone("SELECT * FROM activities WHERE sync_id=1")

    def flush_activities(self, clear_meta=False):
        if clear_meta:
            self.clear_all_meta(notify=False)
        from resources.lib.modules.cache_maintenance import invalidate_all_menu_caches

        invalidate_all_menu_caches(include_api_cache=False)
        self.execute_sql("DELETE FROM activities")
        self.set_base_activities()

    def clear_user_information(self, notify=True):
        username = self.activities["simkl_username"]
        self.execute_sql(
            [
                "UPDATE episodes SET watched=?",
                "UPDATE movies SET watched=?",
                "UPDATE shows SET unwatched_episodes=?",
                "UPDATE shows SET watched_episodes=?",
                "UPDATE seasons SET unwatched_episodes=?",
                "UPDATE seasons SET watched_episodes=?",
            ],
            (0,),
        )
        self.execute_sql(
            [
                "UPDATE episodes SET last_watched_at=?",
                "UPDATE shows SET last_watched_at=?",
                "UPDATE seasons SET last_watched_at=?",
                "UPDATE movies SET last_watched_at=?",
            ],
            (None,),
        )
        self.execute_sql(
            [
                "DELETE from bookmarks WHERE TRUE",
                "DELETE from hidden WHERE TRUE",
            ]
        )
        self.execute_sql(
            [
                "UPDATE episodes SET user_rating=?",
                "UPDATE shows SET user_rating=?",
                "UPDATE seasons SET user_rating=?",
                "UPDATE movies SET user_rating=?",
            ],
            (None,),
        )
        self.set_simkl_user("")
        self.set_base_activities()
        if notify:
            g.notification(self.notification_prefix, g.get_language_string(30270), time=5000)

    def set_simkl_user(self, simkl_username):
        g.log(f"Setting Simkl username: {simkl_username}")
        self.execute_sql("UPDATE activities SET simkl_username=?", (simkl_username,))

    def clear_all_meta(self, notify=True):
        if notify:
            confirm = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30179))
            if confirm == 0:
                return

        self.execute_sql(
            [
                "UPDATE shows SET info=?, cast=?, art=?, meta_hash=?",
                "UPDATE seasons SET info=?, cast=?, art=?, meta_hash=?",
                "UPDATE episodes SET info=?, cast=?, art=?, meta_hash=?",
                "UPDATE movies SET info=?, cast=?, art=?, meta_hash=?",
            ],
            (None, None, None, None),
        )

        self.execute_sql(
            [
                "DELETE FROM movies_meta",
                "DELETE FROM shows_meta",
                "DELETE FROM seasons_meta",
                "DELETE FROM episodes_meta",
            ]
        )
        if notify:
            g.notification(self.notification_prefix, g.get_language_string(30271), time=5000)

    def re_build_database(self, silent=False):
        if not silent:
            confirm = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30179))
            if confirm == 0:
                return

        from resources.lib.database.session import reset_sync_database
        from resources.lib.modules.cache_maintenance import invalidate_all_menu_caches

        self.rebuild_database()
        invalidate_all_menu_caches(include_api_cache=False)
        # Rebuild recreates tables from declarative schema; clear the session migration
        # gate so ALTER migrations re-run on the fresh singleton (tvdb_id, bookmarks, etc.).
        g.clear_runtime_setting("simkl_sync.migrations.done")
        reset_sync_database()
        self.set_base_activities()
        self.refresh_activities()

        from resources.lib.database.session import get_sync_database

        if sync_errors := get_sync_database().sync_activities(silent):
            g.notification(self.notification_prefix, g.get_language_string(30332), time=5000)
        elif sync_errors is None:
            self.refresh_activities()
        else:
            g.notification(self.notification_prefix, g.get_language_string(30272), time=5000)

    def save_to_meta_table(self, items, meta_type, provider_type, id_column, *, include_children: bool | None = None):
        if items is None:
            return
        from resources.lib.meta.profiles import include_provider_children, persist_provider_blobs

        if provider_type != "simkl" and not persist_provider_blobs():
            return

        if include_children is None:
            include_children = include_provider_children()
        sql_statement = f"""
            INSERT INTO {meta_type}_meta (id, type, meta_hash, value) VALUES (?, ?, ?, ?)
            ON CONFLICT(id, type) DO UPDATE
                SET (meta_hash, value) = (excluded.meta_hash, excluded.value)
            """
        obj = None
        meta_hash = None
        if provider_type == "simkl":
            obj = MetadataHandler.simkl_object
            meta_hash = self.simkl_api.meta_hash
        elif provider_type == "tmdb":
            obj = MetadataHandler.tmdb_object
            meta_hash = self.metadataHandler.tmdb_api.meta_hash
        elif provider_type == "tvdb":
            obj = MetadataHandler.tvdb_object
            meta_hash = self.metadataHandler.tvdb_api.meta_hash
        elif provider_type == "fanart":
            obj = MetadataHandler.fanart_object
            meta_hash = self.metadataHandler.fanarttv_api.meta_hash
        elif provider_type == "imdb":
            obj = MetadataHandler.imdb_object
            meta_hash = "imdb_v1"

        if obj is None or meta_hash is None:
            raise UnsupportedProviderType(provider_type)

        meta_ok = MetadataHandler.simkl_meta_savable if provider_type == "simkl" else MetadataHandler.full_meta_up_to_par

        singular = meta_type.rstrip("s") if meta_type.endswith("s") else meta_type
        media_hint = "movie" if singular == "movie" else "episode" if singular == "episode" else "season" if singular == "season" else "tvshow"

        meta_rows: list[tuple] = []
        for i in items:
            if not (i and obj(i) and i.get(id_column) and meta_ok(meta_type, obj(i))):
                continue
            lookup_id = (
                self._meta_table_lookup_id(provider_type, i.get(id_column))
                if provider_type == "imdb"
                else i.get(id_column)
            )
            cleaned = self.clean_meta(obj(i), provider_type=provider_type, media_type=media_hint)
            if cleaned is None:
                continue
            meta_rows.append((lookup_id, provider_type, meta_hash, cleaned))
        if meta_rows:
            self.execute_sql(sql_statement, meta_rows)

        if include_children:
            for i in items:
                if i and obj(i):
                    if obj(i).get("seasons"):
                        self.save_to_meta_table(
                            i.get("seasons"),
                            "season",
                            provider_type,
                            id_column,
                            include_children=include_children,
                        )
                    if obj(i).get("episodes"):
                        self.save_to_meta_table(
                            i.get("episodes"),
                            "episode",
                            provider_type,
                            id_column,
                            include_children=include_children,
                        )

    @staticmethod
    def _meta_table_lookup_id(meta_type: str, ext_id) -> int | str | None:
        """Normalize an external id for *_meta table lookups."""
        if ext_id is None:
            return None
        if meta_type == "imdb":
            from resources.lib.simkl.field_map import _normalize_imdb_id

            return _normalize_imdb_id(ext_id)
        return _int_or_none(ext_id)

    @staticmethod
    def _provider_meta_from_blob_map(
        media_table: str,
        simkl_id: int,
        info: dict,
        blob_map: dict[tuple[str, str], object],
    ) -> dict:
        """Map batched *_meta rows onto a single row's provider object dict."""
        result: dict = {}
        simkl_key = (str(int(simkl_id)), "simkl")
        if simkl_key in blob_map:
            result["simkl_object"] = blob_map[simkl_key]

        for meta_type, id_key in (("tmdb", "tmdb_id"), ("tvdb", "tvdb_id"), ("imdb", "imdb_id")):
            lookup_id = SimklSyncDatabase._meta_table_lookup_id(meta_type, info.get(id_key))
            if lookup_id is None:
                continue
            meta_key = (str(lookup_id), meta_type)
            if meta_key in blob_map:
                result[f"{meta_type}_object"] = blob_map[meta_key]

        fanart_id = info.get("tvdb_id") if media_table == "shows" else info.get("tmdb_id")
        fanart_lookup_id = _int_or_none(fanart_id)
        if fanart_lookup_id is not None:
            fanart_key = (str(fanart_lookup_id), "fanart")
            if fanart_key in blob_map:
                result["fanart_object"] = blob_map[fanart_key]
        return result

    def load_cached_provider_meta(self, media_table: str, simkl_id: int, info: dict) -> dict:
        """Load pickled provider blobs from *_meta for offline art merge."""
        return self.load_cached_provider_meta_batch(media_table, [{"simkl_id": simkl_id, "info": info}]).get(
            int(simkl_id), {}
        )

    _PROVIDER_META_BATCH_SIZE = 40

    def load_cached_provider_meta_batch(self, media_table: str, rows: list[dict]) -> dict[int, dict]:
        """Batch-load provider meta blobs for list rows; keyed by simkl_id."""
        if not rows:
            return {}
        if len(rows) <= self._PROVIDER_META_BATCH_SIZE:
            return self._load_cached_provider_meta_batch_chunk(media_table, rows)

        result: dict[int, dict] = {}
        for start in range(0, len(rows), self._PROVIDER_META_BATCH_SIZE):
            chunk = rows[start : start + self._PROVIDER_META_BATCH_SIZE]
            result.update(self._load_cached_provider_meta_batch_chunk(media_table, chunk))
        return result

    def _load_cached_provider_meta_batch_chunk(self, media_table: str, rows: list[dict]) -> dict[int, dict]:
        if not rows:
            return {}

        lookup_ids: set[str | int] = set()
        row_by_simkl: dict[int, dict] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            simkl_id = row.get("simkl_id") or info.get("simkl_id")
            if simkl_id is None:
                continue
            sid = int(simkl_id)
            row_by_simkl[sid] = row
            lookup_ids.add(sid)
            for meta_type, id_key in (("tmdb", "tmdb_id"), ("tvdb", "tvdb_id"), ("imdb", "imdb_id")):
                lookup_id = self._meta_table_lookup_id(meta_type, info.get(id_key))
                if lookup_id is not None:
                    lookup_ids.add(lookup_id)
            fanart_id = info.get("tvdb_id") if media_table == "shows" else info.get("tmdb_id")
            fanart_lookup_id = _int_or_none(fanart_id)
            if fanart_lookup_id is not None:
                lookup_ids.add(fanart_lookup_id)

        if not lookup_ids:
            return {}

        placeholders = ",".join("?" * len(lookup_ids))
        meta_rows = self.fetchall(
            f"""
            SELECT id, type, value
            FROM {media_table}_meta
            WHERE id IN ({placeholders})
              AND type IN ('simkl', 'tmdb', 'tvdb', 'imdb', 'fanart')
            """,
            tuple(lookup_ids),
        )
        blob_map: dict[tuple[str, str], object] = {}
        for meta_row in meta_rows or []:
            if not isinstance(meta_row, dict):
                continue
            if meta_row.get("value") is None:
                continue
            blob_map[(str(meta_row["id"]), str(meta_row["type"]))] = meta_row["value"]

        result: dict[int, dict] = {}
        for sid, row in row_by_simkl.items():
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            provider_meta = self._provider_meta_from_blob_map(media_table, sid, info, blob_map)
            if provider_meta:
                result[sid] = provider_meta
        return result

    @staticmethod
    def clean_meta(item, *, provider_type=None, media_type=None):
        if not item:
            return None

        from resources.lib.meta.storage import slim_provider_blob

        if provider_type in ("tmdb", "tvdb", "fanart", "imdb"):
            result = {
                "art": item.get("art"),
                "cast": item.get("cast"),
            }
        else:
            result = {
                "info": {key: value for key, value in item.get("info", {}).items() if key not in ["seasons", "episodes"]},
                "art": item.get("art"),
                "cast": item.get("cast"),
            }

        if result.get("info") or result.get("art") or result.get("cast"):
            return slim_provider_blob(result, provider_type=provider_type, media_type=media_type)
        g.log(
            f"Bad Item meta discovered when cleaning - item: {item}",
            "error",
        )
        return None

    @staticmethod
    def _apply_request_force_update(db_rows, request_refs) -> None:
        """Honor gap-fill requests that need a provider refresh even when DB says meta is current."""
        force_ids = {
            int(ref["simkl_id"])
            for ref in request_refs or []
            if ref.get("simkl_id") is not None
            and ref.get("needs_update") in (True, "true", "True", 1, "1")
        }
        if not force_ids:
            return
        for row in db_rows or []:
            simkl_id = row.get("simkl_id")
            if simkl_id is not None and int(simkl_id) in force_ids:
                row["needs_update"] = True

    def _set_needs_update(self, items, media_type):
        update_list = self.fetchall(f"SELECT simkl_id from {media_type} WHERE needs_update")
        if not update_list:
            return
        update_set = {tid.get('simkl_id') for tid in update_list}
        for i in items:
            i["needs_update"] = i.get("simkl_id") in update_set

    def _prepare_sync_inserts(self, items: list[dict]) -> None:
        from resources.lib.simkl.ids import sync_sql_columns_from_info

        for item in items:
            if isinstance(item, dict):
                sync_sql_columns_from_info(item)

    def ensure_catalog_refs_seeded(self, refs: list[dict], catalog: str, media_type: str) -> None:
        """Seed movies/shows from catalog_items so provider enrichment can resolve TMDB/TVDB IDs."""
        if not refs or catalog not in ("movie", "tv", "anime"):
            return
        from resources.lib.discover.catalog_store import sync_items_for_refs

        page_refs: list[dict] = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("simkl_id") is None:
                continue
            page_refs.append(
                {"simkl_id": int(ref["simkl_id"]), "catalog": ref.get("catalog") or catalog}
            )
        if not page_refs:
            return

        items = sync_items_for_refs(catalog, page_refs)
        if not items:
            return

        simkl_ids = [int(item["simkl_id"]) for item in items if item.get("simkl_id") is not None]
        if simkl_ids:
            table = "movies" if media_type == "movie" or catalog == "movie" else "shows"
            ids_sql = ",".join(str(sid) for sid in simkl_ids)
            existing = self.fetchall(
                f"SELECT simkl_id, tmdb_id, tvdb_id, imdb_id FROM {table} WHERE simkl_id IN ({ids_sql})"
            ) or []
            seeded_ids = {
                int(row["simkl_id"])
                for row in existing
                if isinstance(row, dict)
                and row.get("simkl_id") is not None
                and any(row.get(key) for key in ("tmdb_id", "tvdb_id", "imdb_id"))
            }
        else:
            seeded_ids = set()

        sync_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or item.get("simkl_id") is None:
                continue
            sid = int(item["simkl_id"])
            if sid in seeded_ids:
                continue
            if any(item.get(key) for key in ("tmdb_id", "tvdb_id", "imdb_id")):
                continue
            simkl_obj = item.get("simkl_object")
            if not isinstance(simkl_obj, dict):
                simkl_obj = {"info": item.get("info") or {}, "art": item.get("art") or {}}
            sync_items.append(
                {
                    "simkl_id": int(item["simkl_id"]),
                    "catalog": item.get("catalog") or catalog,
                    "simkl_object": simkl_obj,
                    "tmdb_id": item.get("tmdb_id"),
                    "tvdb_id": item.get("tvdb_id"),
                    "imdb_id": item.get("imdb_id"),
                    "needs_update": True,
                }
            )
        if not sync_items:
            return

        if media_type == "movie" or catalog == "movie":
            self.insert_simkl_movies(sync_items, force_meta=True)
        else:
            self.insert_simkl_shows(sync_items, force_meta=True)

    def insert_simkl_movies(self, movies, force_meta=False):
        if not movies:
            return

        if force_meta:
            to_insert = [i for i in movies if isinstance(i, dict) and i.get("simkl_id")]
        else:
            to_insert = self._filter_media_items_that_needs_updating(movies, "movies")

        if not to_insert:
            return

        self._prepare_sync_inserts(to_insert)
        self._preserve_library_status_on_items(to_insert, "movies")
        g.log(f"Inserting Movies into sync database: {len(to_insert)}")
        from resources.lib.meta.storage import slim_art_dict, slim_info_dict

        get = MetadataHandler.get_simkl_info
        simkl_obj = MetadataHandler.simkl_object
        self.execute_sql(
            self.upsert_movie_query,
            (
                (
                    i.get("simkl_id"),
                    slim_info_dict(MetadataHandler.simkl_info(i) or {}, simkl=True) or None,
                    slim_art_dict(MetadataHandler.art(simkl_obj(i)) or {}, "movie") or None,
                    None,
                    get(i, "watched"),
                    g.validate_date(get(i, "aired")),
                    g.validate_date(get(i, "dateadded")),
                    get(i, "tmdb_id"),
                    get(i, "tvdb_id"),
                    get(i, "imdb_id"),
                    None,
                    self._create_args(i),
                    g.validate_date(get(i, "last_watched_at")),
                    get(i, "user_rating"),
                    self._library_status_from_item(i),
                )
                for i in to_insert
            ),
        )
        self.save_to_meta_table(to_insert, "movies", "simkl", "simkl_id")
        self._set_needs_update(movies, "movies")
        from resources.lib.database.sync_meta_cache import SyncMetaCache

        cache = SyncMetaCache()
        for item in to_insert:
            cache.set_row(
                "movie",
                {
                    "simkl_id": item.get("simkl_id"),
                    "info": slim_info_dict(MetadataHandler.simkl_info(item) or {}, simkl=True),
                    "art": slim_art_dict(MetadataHandler.art(simkl_obj(item)) or {}, "movie"),
                    "tmdb_id": get(item, "tmdb_id"),
                    "tvdb_id": get(item, "tvdb_id"),
                    "imdb_id": get(item, "imdb_id"),
                    "air_date": g.validate_date(get(item, "aired")),
                },
            )

    def insert_simkl_shows(self, shows, force_meta=False):
        if not shows:
            return

        if force_meta:
            to_insert = [i for i in shows if isinstance(i, dict) and i.get("simkl_id")]
        else:
            to_insert = self._filter_media_items_that_needs_updating(shows, "shows")

        if not to_insert:
            return

        self._prepare_sync_inserts(to_insert)
        self._preserve_library_status_on_items(to_insert, "shows")
        g.log(f"Inserting Shows into sync database: {len(to_insert)}")
        from resources.lib.meta.storage import slim_art_dict, slim_info_dict

        get = MetadataHandler.get_simkl_info
        simkl_obj = MetadataHandler.simkl_object
        self.execute_sql(
            self.upsert_show_query,
            (
                (
                    i.get("simkl_id"),
                    slim_info_dict(MetadataHandler.simkl_info(i) or {}, simkl=True) or None,
                    slim_art_dict(
                        MetadataHandler.art(simkl_obj(i)) or {},
                        "anime"
                        if (MetadataHandler.simkl_info(i) or {}).get("catalog") == "anime"
                        else "tvshow",
                    )
                    or None,
                    None,
                    g.validate_date(get(i, "aired")),
                    g.validate_date(get(i, "dateadded")),
                    get(i, "tmdb_id"),
                    get(i, "tvdb_id"),
                    get(i, "imdb_id"),
                    self.simkl_api.meta_hash,
                    get(i, "season_count"),
                    get(i, "episode_count"),
                    self._create_args(i),
                    get(i, "is_airing"),
                    g.validate_date(get(i, "last_watched_at")),
                    get(i, "user_rating"),
                    self._library_status_from_item(i),
                )
                for i in to_insert
            ),
        )
        self.save_to_meta_table(to_insert, "shows", "simkl", "simkl_id")
        self._set_needs_update(shows, "shows")
        from resources.lib.database.sync_meta_cache import SyncMetaCache

        cache = SyncMetaCache()
        for item in to_insert:
            info = MetadataHandler.simkl_info(item) or {}
            cache.set_row(
                "show",
                {
                    "simkl_id": item.get("simkl_id"),
                    "info": slim_info_dict(info, simkl=True),
                    "art": slim_art_dict(
                        MetadataHandler.art(simkl_obj(item)) or {},
                        "anime" if info.get("catalog") == "anime" else "tvshow",
                    ),
                    "tmdb_id": get(item, "tmdb_id"),
                    "tvdb_id": get(item, "tvdb_id"),
                    "imdb_id": get(item, "imdb_id"),
                    "air_date": g.validate_date(get(item, "aired")),
                    "is_airing": get(item, "is_airing"),
                },
            )

    def insert_simkl_episodes(self, episodes):
        if not episodes:
            return

        to_insert = self._filter_media_items_that_needs_updating(episodes, "episodes")

        if not to_insert:
            return
        self._prepare_sync_inserts(to_insert)
        g.log(f"Inserting episodes into sync database: {len(to_insert)}")
        get = MetadataHandler.get_simkl_info

        if missing_season_ids := [i for i in to_insert if not i.get("simkl_season_id")]:
            predicate = " OR ".join(
                [f"(simkl_show_id={get(i, 'simkl_show_id')} AND season={get(i, 'season')})" for i in missing_season_ids]
            )
            season_ids = self.fetchall(f"SELECT simkl_show_id, simkl_id, season FROM seasons WHERE {predicate}")
            season_ids = {f"{i['simkl_show_id']}-{i['season']}": i["simkl_id"] for i in season_ids}
            for i in to_insert:
                i["simkl_season_id"] = season_ids.get(f"{get(i, 'simkl_show_id')}-{get(i, 'season')}")

        for item in to_insert:
            show_id = item.get("simkl_show_id")
            season = get(item, "season")
            ep_num = get(item, "episode")
            new_id = item.get("simkl_id")
            if show_id is None or season is None or ep_num is None or new_id is None:
                continue
            old_row = self.fetchone(
                """
                SELECT simkl_id
                FROM episodes
                WHERE simkl_show_id = ?
                  AND season = ?
                  AND number = ?
                """,
                (int(show_id), int(season), int(ep_num)),
            )
            if not old_row:
                continue
            old_id = int(old_row["simkl_id"])
            if old_id != int(new_id):
                self.execute_sql(
                    "UPDATE bookmarks SET simkl_id = ? WHERE simkl_id = ? AND type = 'episode'",
                    (int(new_id), old_id),
                )

        self.execute_sql(
            self.upsert_episode_query,
            (
                (
                    i.get("simkl_id"),
                    i.get("simkl_show_id"),
                    i.get("simkl_season_id"),
                    get(i, "playcount"),
                    g.validate_date(get(i, "aired")),
                    g.validate_date(get(i, "dateadded")),
                    get(i, "season"),
                    get(i, "episode"),
                    get(i, "tmdb_id"),
                    get(i, "tvdb_id"),
                    get(i, "imdb_id"),
                    None,
                    None,
                    None,
                    self._create_args(i),
                    g.validate_date(get(i, "last_watched_at")),
                    get(i, "user_rating"),
                    self.simkl_api.meta_hash,
                )
                for i in to_insert
            ),
        )
        self.save_to_meta_table(to_insert, "episodes", "simkl", "simkl_id")
        self._set_needs_update(episodes, "episodes")

    def insert_simkl_seasons(self, seasons):
        if not seasons:
            return

        to_insert = self._filter_media_items_that_needs_updating(seasons, "seasons")

        if not to_insert:
            return

        self._prepare_sync_inserts(to_insert)
        g.log(f"Inserting seasons into sync database: {len(to_insert)}")
        get = MetadataHandler.get_simkl_info
        for i in to_insert:
            g.log(
                f"[season trace] insert_simkl_seasons show={i.get('simkl_show_id')} "
                f"season={get(i, 'season')} row_id={i.get('simkl_id')} title={get(i, 'title')}",
                "debug",
            )
        self.execute_sql(
            self.upsert_season_query,
            (
                (
                    i.get("simkl_show_id"),
                    i.get("simkl_id"),
                    None,
                    None,
                    None,
                    g.validate_date(get(i, "aired")),
                    g.validate_date(get(i, "dateadded")),
                    get(i, "tmdb_id"),
                    get(i, "tvdb_id"),
                    self.simkl_api.meta_hash,
                    None,
                    get(i, "season"),
                    self._create_args(i),
                    g.validate_date(get(i, "last_watched_at")),
                    get(i, "user_rating"),
                )
                for i in to_insert
            ),
        )
        self.save_to_meta_table(to_insert, "seasons", "simkl", "simkl_id")
        self._set_needs_update(seasons, "seasons")

    def _mill_if_needed(self, list_to_update, queue_wrapper=None, mill_episodes=True):
        if queue_wrapper is None:
            queue_wrapper = self._queue_mill_tasks

        ids_to_mill_check = ",".join(str(i.get("simkl_show_id", i.get("simkl_id"))) for i in list_to_update)
        now = self._get_aired_cutoff()

        query = f"""
            SELECT s.simkl_id, s.needs_milling, s.season_count, agg.meta_count, agg.tot_season_count, agg.tot_meta_count
            FROM shows AS s
                     LEFT JOIN(SELECT s.simkl_id,
                                      sum(CASE
                                              WHEN se.simkl_id IS NOT NULL
                                                  AND se.season != 0 AND Datetime(se.air_date) < Datetime('{now}')
                                                  THEN 1
                                              ELSE 0
                                          END)           AS season_count,
                                      sum(CASE
                                              WHEN sm.id IS NOT NULL
                                                  AND se.season != 0
                                                  AND Datetime(se.air_date) < Datetime('{now}')
                                                  THEN 1
                                              ELSE 0
                                          END)           AS meta_count,
                                      count(se.simkl_id) AS tot_season_count,
                                      count(sm.id)       AS tot_meta_count
                               FROM shows AS s
                                        INNER JOIN seasons AS se
                                                   ON s.simkl_id = se.simkl_show_id
                                        LEFT JOIN seasons_meta AS sm
                                                  ON sm.id = se.simkl_id
                                                      AND sm.type = 'simkl'
                                                      AND sm.meta_hash = '{self.simkl_api.meta_hash}'
                               WHERE s.simkl_id IN ({ids_to_mill_check})
                               GROUP BY s.simkl_id) AS agg
                              ON s.simkl_id = agg.simkl_id
            WHERE s.simkl_id IN ({ids_to_mill_check})
              AND (s.needs_milling
                OR (agg.season_count IS NULL OR agg.season_count != s.season_count)
                OR (agg.meta_count = 0 OR agg.meta_count != s.season_count)
                OR agg.tot_season_count != agg.tot_meta_count)
            """
        needs_milling = self.fetchall(query)
        if needs_milling is not None:
            needs_milling = {x.get('simkl_id') for x in needs_milling}
        else:
            needs_milling = set()

        if mill_episodes:
            query = f"""
                SELECT s.simkl_id,
                       s.episode_count,
                       agg.episode_count,
                       agg.meta_count,
                       agg.tot_episode_count,
                       agg.tot_meta_count
                FROM shows AS s
                         LEFT JOIN(SELECT s.simkl_id,
                                          sum(CASE
                                                  WHEN e.simkl_id IS NOT NULL
                                                      AND e.season != 0
                                                      AND Datetime(e.air_date) < Datetime('{now}')
                                                      THEN 1
                                              END)          AS episode_count,
                                          sum(CASE
                                                  WHEN em.id IS NOT NULL
                                                      AND e.season != 0
                                                      AND Datetime(e.air_date) < Datetime('{now}')
                                                      THEN 1
                                              END)          AS meta_count,
                                          count(e.simkl_id) AS tot_episode_count,
                                          count(em.id)      AS tot_meta_count
                                   FROM shows
                                            AS s
                                            INNER JOIn episodes AS e
                                                       ON s.simkl_id = e.simkl_show_id
                                            LEFT JOIN episodes_meta AS em
                                                      ON em.id = e.simkl_id
                                                          AND em.type = 'simkl'
                                                          AND em.meta_hash = '{self.simkl_api.meta_hash}'
                                   WHERE s.simkl_id IN ({ids_to_mill_check})
                                   GROUP BY s.simkl_id) AS agg ON s.simkl_id = agg.simkl_id
                WHERE s.simkl_id IN ({ids_to_mill_check})
                  AND ((agg.episode_count IS NULL OR agg.episode_count != s.episode_count)
                    OR (agg.meta_count = 0 OR agg.meta_count != s.episode_count)
                    OR agg.tot_episode_count != agg.tot_meta_count)
                """
            episodes_needs_milling = self.fetchall(query)
            if episodes_needs_milling is not None:
                needs_milling.update({x.get('simkl_id') for x in episodes_needs_milling})

            missing_episodes = self.fetchall(
                f"""
                SELECT s.simkl_id
                FROM shows AS s
                WHERE s.simkl_id IN ({ids_to_mill_check})
                  AND EXISTS (SELECT 1 FROM seasons AS se WHERE se.simkl_show_id = s.simkl_id)
                  AND NOT EXISTS (SELECT 1 FROM episodes AS e WHERE e.simkl_show_id = s.simkl_id)
                """
            )
            if missing_episodes:
                needs_milling.update({x.get("simkl_id") for x in missing_episodes})

            missing_specials = self.fetchall(
                f"""
                SELECT s.simkl_id
                FROM shows AS s
                WHERE s.simkl_id IN ({ids_to_mill_check})
                  AND EXISTS (SELECT 1 FROM seasons AS se WHERE se.simkl_show_id = s.simkl_id)
                  AND NOT EXISTS (SELECT 1 FROM seasons AS se WHERE se.simkl_show_id = s.simkl_id AND se.season = 0)
                """
            )
            if missing_specials:
                from resources.lib.database.simkl_sync.milling import count_special_episodes

                for row in missing_specials:
                    show_id = row.get("simkl_id")
                    if show_id:
                        catalog = self._infer_show_catalog(show_id)
                        slug = self._meta_slug(show_id, "shows")
                        if count_special_episodes(int(show_id), catalog, slug=slug) > 0:
                            needs_milling.add(show_id)

        show_milling_count = len(needs_milling)
        if show_milling_count > 0:
            g.log(f"{show_milling_count} items require season milling", "debug")
        else:
            return

        self.mill_seasons(
            [i for i in list_to_update if i.get("simkl_show_id", i.get("simkl_id")) in needs_milling],
            queue_wrapper,
            mill_episodes,
        )

    def force_mill_shows(
        self,
        shows,
        mill_episodes=True,
        *,
        skip_provider_episode_updates=False,
        defer_episode_format: bool = False,
        mill_season_filter: set[int] | frozenset[int] | None = None,
    ):
        """Parallel Simkl catalogue pull — skips needs_milling gate and show meta enrichment."""
        if not shows:
            return
        if isinstance(shows, dict):
            shows = [shows]
        show_ids = sorted(
            {
                int(show_id)
                for show in shows
                if (show_id := self._ref_show_id(show) or show.get("simkl_id")) is not None
            }
        )
        if not show_ids:
            return
        predicate = ",".join(str(show_id) for show_id in show_ids)
        rows = self.fetchall(
            f"""
            SELECT s.simkl_id, m.value AS simkl_object, s.tmdb_id, s.tvdb_id
            FROM shows AS s
                     LEFT JOIN shows_meta AS m ON m.id = s.simkl_id AND m.type = 'simkl'
            WHERE s.simkl_id IN ({predicate})
            """
        )
        if not rows:
            rows = [{"simkl_id": show_id} for show_id in show_ids]
        g.log(f"Parallel force-mill: {len(rows)} show(s)", "debug")
        self.insert_simkl_shows(rows)
        self.mill_seasons(
            rows,
            self._queue_mill_tasks,
            mill_episodes=mill_episodes,
            skip_provider_episode_updates=skip_provider_episode_updates,
            defer_episode_format=defer_episode_format,
            mill_season_filter=mill_season_filter,
        )

    def mill_seasons(
        self,
        show_collection,
        queue_wrapper,
        mill_episodes=False,
        skip_provider_episode_updates=False,
        defer_episode_format: bool = False,
        mill_season_filter: set[int] | frozenset[int] | None = None,
    ):
        with SyncLock(
            f"mill_seasons_episodes_{mill_episodes}",
            {show.get("simkl_show_id", show.get("simkl_id")) for show in show_collection},
        ) as sync_lock:
            # Everything we are milling may already be being milled in another process/thread
            # we need to check if there are any running IDs first.  The sync_lock wont exit until
            # the other process/thread is done with its milling giving good results.
            if len(sync_lock.running_ids) > 0:
                get = MetadataHandler.get_simkl_info
                simkl_info = MetadataHandler.simkl_info

                queue_wrapper(
                    self._pull_show_seasons,
                    [(i, mill_episodes, mill_season_filter) for i in sync_lock.running_ids],
                )
                results = self.mill_task_queue.wait_completion()

                seasons = []
                episodes = []

                season_ids = {}
                episode_ids = {}

                for show in show_collection:
                    show_id = self._ref_show_id(show)
                    if show_id is None:
                        continue
                    self._ensure_show_ref_keys(show, show_id)
                    show_info = simkl_info(show)
                    show_catalog = (
                        show.get("catalog")
                        or show_info.get("catalog")
                        or self._infer_show_catalog(show_id)
                    )
                    extended_seasons = {get(x, "season"): x for x in get(show, "seasons", [])}
                    # We make a dict here to ensure that the season numbers are unique due to a few bad simkl_meta records.
                    milled_seasons = results.get(show.get("simkl_id"), [])
                    g.log(
                        f"[season trace] mill_seasons show={get(show, 'simkl_id')} "
                        f"milled_keys={[s.get('season') for s in milled_seasons]} "
                        f"extended_keys={list(extended_seasons.keys())}",
                        "debug",
                    )
                    for s_num, season in {
                        season.get("season", get(season, "season")): season for season in milled_seasons
                    }.items():
                        simkl_info(season).update({"simkl_show_id": get(show, "simkl_id")})
                        simkl_info(season).update({"tmdb_show_id": get(show, "tmdb_id")})
                        simkl_info(season).update({"tvdb_show_id": get(show, "tvdb_id")})
                        if show_catalog in ("movie", "tv", "anime"):
                            simkl_info(season).setdefault("catalog", show_catalog)

                        season.update({"simkl_show_id": show.get("simkl_id")})
                        season.update({"tmdb_show_id": show.get("tmdb_id")})
                        season.update({"tvdb_show_id": show.get("tvdb_id")})
                        if show_catalog in ("movie", "tv", "anime"):
                            season.setdefault("catalog", show_catalog)

                        simkl_info(season).update({"dateadded": get(show, "dateadded")})
                        simkl_info(season).update({"tvshowtitle": get(show, "title")})

                        if s_num > 0:
                            show.update(
                                {
                                    "season_count": show.get("season_count", 0)
                                    + (1 if get(season, "aired_episodes", 0) > 0 else 0)
                                }
                            )
                            show.update(
                                {'episode_count': show.get("episode_count", 0) + get(season, "aired_episodes", 0)}
                            )

                        extended_season = extended_seasons.get(s_num)
                        if extended_season:
                            tools.smart_merge_dictionary(season, extended_season, keep_original=True)

                        g.log(
                            f"[season trace] mill_seasons -> insert season={s_num} "
                            f"row_id={get(season, 'simkl_id')} episodes={len(season.get('episodes') or [])}",
                            "debug",
                        )
                        seasons.append(season)
                        if get(show, "simkl_id") not in season_ids:
                            season_ids[get(show, "simkl_id")] = []

                        season_ids[get(show, "simkl_id")].append(get(season, "simkl_id"))

                        extended_episodes = {get(x, "episode"): x for x in get(extended_season, "episodes", [])}
                        season_episodes = season.get("episodes") or get(season, "episodes") or []
                        if (
                            mill_season_filter is not None
                            and s_num is not None
                            and int(s_num) not in mill_season_filter
                        ):
                            season_episodes = []
                        for episode in season_episodes:
                            e_num = episode.get("episode", get(episode, "episode"))
                            simkl_info(episode).update({"simkl_show_id": get(show, "simkl_id")})
                            simkl_info(episode).update({"tmdb_show_id": get(show, "tmdb_id")})
                            simkl_info(episode).update({"tvdb_show_id": get(show, "tvdb_id")})
                            simkl_info(episode).update({"simkl_season_id": get(season, "simkl_id")})
                            from resources.lib.simkl.field_map import inherit_show_fields

                            inherit_show_fields(simkl_info(episode), show_info)

                            episode.update({"simkl_show_id": show.get("simkl_id")})
                            episode.update({"tmdb_show_id": show.get("tmdb_id")})
                            episode.update({"tvdb_show_id": show.get("tvdb_id")})
                            episode.update({"simkl_season_id": season.get("simkl_id")})
                            if show_catalog in ("movie", "tv", "anime"):
                                episode.setdefault("catalog", show_catalog)

                            simkl_info(episode).update({"tvshowtitle": get(show, "title")})

                            if extended_episode := extended_episodes.get(e_num):
                                tools.smart_merge_dictionary(episode, extended_episode, keep_original=True)

                            episodes.append(episode)
                            if get(show, "simkl_id") not in episode_ids:
                                episode_ids[get(show, "simkl_id")] = []

                            episode_ids[get(show, "simkl_id")].append(get(episode, "simkl_id"))

                self.insert_simkl_seasons(seasons)
                self.insert_simkl_episodes(episodes)
                if mill_episodes and episodes and not defer_episode_format:
                    episode_refs = [{"simkl_id": get(ep, "simkl_id")} for ep in episodes if get(ep, "simkl_id")]
                    if skip_provider_episode_updates:
                        self._format_episodes(episode_refs)
                    else:
                        self._update_episodes(episode_refs)
                        self._format_episodes(episode_refs)

                if mill_episodes and mill_season_filter is None:
                    self.execute_sql(
                        [
                            f"""
                            DELETE FROM episodes
                            WHERE simkl_show_id = {simkl_id} AND simkl_id NOT IN ({','.join(map(str, episode))})
                            """
                            for simkl_id, episode in episode_ids.items()
                            if episode
                        ]
                    )

                self.execute_sql(
                    [
                        f"""
                        DELETE FROM seasons
                        WHERE simkl_show_id = {simkl_id} AND simkl_id NOT IN ({','.join(map(str, season))})
                        """
                        for simkl_id, season in season_ids.items()
                    ]
                )

                self.execute_sql(
                    "UPDATE shows SET episode_count=?, season_count=? WHERE simkl_id=? ",
                    (
                        (i.get("episode_count", 0), i.get("season_count", 0), i["simkl_id"])
                        for i in show_collection
                        if i["simkl_id"] in sync_lock.running_ids
                    ),
                )

                self.update_shows_statistics({"simkl_id": i} for i in sync_lock.running_ids)

                if mill_episodes:
                    self.update_season_statistics({"simkl_id": i['simkl_id']} for i in seasons)

                self.execute_sql(
                    f"UPDATE shows SET needs_milling=0 WHERE simkl_id IN ({','.join(map(str, sync_lock.running_ids))})"
                )

    def _resolve_sync_simkl_id(self, item: dict):
        simkl_id = item.get("simkl_id")
        if simkl_id is not None:
            return int(simkl_id)
        ids = item.get("ids") or {}
        simkl_id = ids.get("simkl") or ids.get("simkl_id")
        return int(simkl_id) if simkl_id is not None else None

    def _filter_media_items_that_needs_updating(self, requested, media_type):
        if not requested:
            return requested

        requested = [i for i in requested if isinstance(i, dict)]
        if not requested:
            return []

        get = MetadataHandler.get_simkl_info

        resolved = []
        for item in requested:
            simkl_id = self._resolve_sync_simkl_id(item)
            if simkl_id is not None:
                resolved.append((item, simkl_id))

        if not resolved:
            return []

        query_predicate = [
            f"({simkl_id}, '{self.simkl_api.meta_hash}', '{get(item, 'dateadded')}')"
            for item, simkl_id in resolved
        ]

        query = f"""
            WITH requested(simkl_id, meta_hash, updated_at) AS (VALUES {','.join(query_predicate)})
            SELECT r.simkl_id AS simkl_id
            FROM requested AS r
            LEFT JOIN {media_type} AS db
                      ON r.simkl_id == db.simkl_id
            LEFT JOIN {media_type}_meta AS m
                      ON db.simkl_id == id AND type = 'simkl'
            WHERE db.simkl_id IS NULL
                  OR m.value IS NULL
                  OR m.meta_hash != r.meta_hash
                  OR Datetime(db.last_updated) < Datetime(r.updated_at)
            """

        result = {r["simkl_id"] for r in self.fetchall(query)}

        nested_key = media_type.rstrip("s")

        def _requested_record(item):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                return nested
            return item

        return [_requested_record(item) for item, simkl_id in resolved if simkl_id in result]

    def _pull_show_seasons(self, show_id, mill_episodes, mill_season_filter=None):
        from resources.lib.database.simkl_sync.milling import pull_show_seasons
        from resources.lib.simkl.catalog import is_anime_movie_info

        row = self.fetchone("SELECT info FROM shows WHERE simkl_id = ?", (int(show_id),))
        if row and is_anime_movie_info(row.get("info")):
            g.log(f"Skipping season mill for anime movie show_id={show_id}", "debug")
            return {show_id: []}

        catalog = self._infer_show_catalog(show_id)
        slug = self._meta_slug(show_id, "shows")
        g.log(f"[season trace] _pull_show_seasons show={show_id} inferred_catalog={catalog} slug={slug}", "debug")
        return {
            show_id: pull_show_seasons(
                int(show_id),
                catalog,
                mill_episodes,
                slug=slug,
                mill_season_filter=mill_season_filter,
            )
        }

    def _catalog_from_show_row(self, row) -> str:
        if not row:
            return "tv"
        for info in (
            (row.get("simkl_object") or {}).get("info") or {},
            row.get("show_info") or {},
        ):
            if not isinstance(info, dict):
                continue
            ids = info.get("ids") or {}
            if ids.get("mal") or info.get("mal_id"):
                return "anime"
            if info.get("catalog") == "anime" or info.get("type") == "anime":
                return "anime"
        return "tv"

    def _infer_show_catalog(self, show_id):
        if show_id is None:
            return "tv"
        row = self.fetchone(
            """
            SELECT m.value AS simkl_object, s.info AS show_info
            FROM shows AS s
            LEFT JOIN shows_meta AS m ON m.id = s.simkl_id AND m.type = 'simkl'
            WHERE s.simkl_id = ?
            """,
            (int(show_id),),
        )
        return self._catalog_from_show_row(row)

    def _show_ids_for_catalog(self, catalog: str | None) -> list[int] | None:
        """Return show simkl_ids for a catalog (tv/anime), cached on the session DB singleton."""
        if not catalog:
            return None
        cache = getattr(self, "_show_ids_by_catalog", None)
        if isinstance(cache, dict) and catalog in cache:
            return cache[catalog]
        rows = self.fetchall(
            """
            SELECT s.simkl_id, m.value AS simkl_object, s.info AS show_info
            FROM shows AS s
            LEFT JOIN shows_meta AS m ON m.id = s.simkl_id AND m.type = 'simkl'
            """
        )
        grouped: dict[str, list[int]] = {"tv": [], "anime": []}
        for row in rows or []:
            if row.get("simkl_id") is None:
                continue
            cat = self._catalog_from_show_row(row)
            grouped.setdefault(cat, []).append(int(row["simkl_id"]))
        self._show_ids_by_catalog = grouped
        return grouped.get(catalog, [])

    @staticmethod
    def _create_args(item):
        from resources.lib.simkl.ids import serialize_action_args

        return serialize_action_args(item)

    def _queue_mill_tasks(self, func, args):
        for arg in args:
            self.mill_task_queue.put(func, *arg)

    @staticmethod
    def requires_update(new_date, old_date):
        parsed_new = tools.parse_datetime(new_date, False) if new_date else None
        parsed_old = tools.parse_datetime(old_date, False) if old_date else None
        if parsed_new is None:
            return False
        if parsed_old is None:
            return True
        return parsed_new > parsed_old

    @staticmethod
    def wrap_in_simkl_object(items):
        for item in items:
            if item.get("show") is not None:
                info = item["show"].pop("info")
                item["show"].update({"simkl_id": info.get("simkl_id")})
                item["show"].update({"simkl_object": {"info": info}})
            if item.get("episode") is not None:
                info = item["episode"].pop("info")
                item["episode"].update({"simkl_id": info.get("simkl_id")})
                item["episode"].update({"tvdb_id": info.get("tvdb_id")})
                item["episode"].update({"tmdb_id": info.get("tmdb_id")})
                item["episode"].update({"simkl_object": {"info": info}})
        return items

    def _meta_slug(self, simkl_id, media_type):
        from resources.lib.simkl.ids import slug_from_info

        row = self.fetchone(
            f"""
            SELECT value FROM {media_type}_meta
            WHERE id = ? AND type = 'simkl'
            """,
            (int(simkl_id),),
        )
        if row and row.get("value"):
            obj = row["value"]
            if isinstance(obj, dict):
                slug = slug_from_info(obj.get("info") or obj)
                if slug:
                    return slug
        info_row = self.fetchone(f"SELECT info FROM {media_type} WHERE simkl_id = ?", (int(simkl_id),))
        if info_row and info_row.get("info"):
            return slug_from_info(info_row["info"])
        return None

    def _get_single_meta(self, simkl_id, media_type):
        from resources.lib.simkl.ids import movie_api_path, show_api_path

        if media_type == "shows":
            api_url = show_api_path(int(simkl_id))
        elif media_type == "movies":
            api_url = movie_api_path(int(simkl_id))
        else:
            api_url = f"/{media_type}/{simkl_id}"
        return self._update_single_meta(
            api_url,
            self.fetchone(
                f"""
                SELECT id AS simkl_id, value AS simkl_object
                FROM {media_type}_meta
                WHERE id = ? AND type = 'simkl'
                """,
                (int(simkl_id),),
            ),
            media_type,
        )

    def _update_single_meta(self, api_url, item, media_type):
        from resources.lib.simkl.api_normalize import api_detail_to_sync_dict

        simkl_object = MetadataHandler.simkl_object
        if item is None:
            item = {}
        if simkl_object(item) is None or simkl_object(item) == {}:
            catalog = {"shows": "tv", "movies": "movie"}.get(media_type, media_type)
            new_object = self.simkl_api.get_json(
                api_url,
                authorized=False,
                client_id=self.simkl_api.client_id,
            )
            if not new_object:
                g.log(f"Simkl meta fetch failed: {api_url}", "warning")
                return item

            sync_item = api_detail_to_sync_dict(new_object, catalog)
            if not sync_item:
                g.log(f"Simkl meta could not be normalized: {api_url}", "warning")
                return item

            if media_type == "movies":
                self.insert_simkl_movies([sync_item])
            elif media_type == "shows":
                self.insert_simkl_shows([sync_item])
            elif media_type == "seasons":
                self.insert_simkl_seasons([sync_item])
            elif media_type == "episodes":
                self.insert_simkl_episodes([sync_item])

            item["simkl_id"] = sync_item.get("simkl_id")
            item["simkl_object"] = sync_item.get("simkl_object", {})
        return item

    def _extract_browse_page(self, url, media_type, **params):
        result = []

        hide_watched = params.get("hide_watched", self.hide_watched)
        hide_unaired = params.get("hide_unaired", self.hide_unaired)
        get = MetadataHandler.get_simkl_info
        page_number = params.pop("page", 1)
        params.pop("pull_all", None)
        params.pop("ignore_cache", None)
        no_paging = params.pop("no_paging", False)

        if url.startswith("search/"):
            query = params.get("query") or params.get("q")
            if not query:
                g.log(f"Simkl search missing query for {url}", "warning")
                return []
            params.pop("fields", None)
            params.pop("field", None)
            params.pop("extended", None)
            from resources.lib.simkl.search import search_page

            return search_page(
                url,
                media_type,
                page_number,
                self.page_limit,
                query,
            )

        g.log(f"Unsupported legacy browse URL (use Discover menus): {url}", "warning")
        return []

    @staticmethod
    def _ref_show_id(item: dict) -> int | None:
        """Resolve Simkl show id from sync rows, bare refs, or nested simkl_object."""
        if not isinstance(item, dict):
            return None
        for key in ("simkl_show_id", "simkl_id"):
            value = item.get(key)
            if value is not None:
                return int(value)
        value = MetadataHandler.get_simkl_info(item, "simkl_id")
        return int(value) if value is not None else None

    @staticmethod
    def _ensure_show_ref_keys(show: dict, show_id: int) -> None:
        show.setdefault("simkl_id", show_id)
        show.setdefault("simkl_show_id", show_id)
        simkl_object = show.setdefault("simkl_object", {})
        info = simkl_object.setdefault("info", {})
        info.setdefault("simkl_id", show_id)

    @staticmethod
    def _entry_show_simkl_id(entry: dict) -> int | None:
        if not isinstance(entry, dict):
            return None
        for key in ("show", "anime"):
            blob = entry.get(key)
            if isinstance(blob, dict):
                simkl_id = (blob.get("ids") or {}).get("simkl")
                if simkl_id is not None:
                    return int(simkl_id)
        if entry.get("ids", {}).get("simkl") is not None:
            return int(entry["ids"]["simkl"])
        return None

    @staticmethod
    def _episode_marked_watched(episode: dict, entry: dict | None = None) -> bool:
        if not isinstance(episode, dict):
            return False
        if episode.get("watched") in (True, 1, "true", "True"):
            return True
        if episode.get("watched_at") or episode.get("last_watched_at"):
            return True
        return False

    def _entry_has_per_episode_watch_rows(self, entry: dict) -> bool:
        """True when Simkl returned explicit per-episode watch rows (not count-only)."""
        if not isinstance(entry, dict):
            return False
        for season in entry.get("seasons") or []:
            if not isinstance(season, dict):
                continue
            for episode in season.get("episodes") or []:
                if self._episode_marked_watched(episode, entry):
                    return True
        return False

    def _entry_has_full_episode_watch_detail(self, entry: dict) -> bool:
        """True when Simkl returned episode rows suitable for watched/unwatched reconciliation."""
        if not isinstance(entry, dict):
            return False
        for season in entry.get("seasons") or []:
            if isinstance(season, dict) and season.get("episodes"):
                return True
        return False

    def apply_episode_watch_state_from_entries(self, entries, shows) -> bool:
        """Apply Simkl per-episode watched and unwatched flags when episode rows are present."""
        show_ids = {int(show["simkl_id"]): show for show in shows if show.get("simkl_id")}
        rows = []

        for entry in entries or []:
            if not self._entry_has_full_episode_watch_detail(entry):
                continue
            show_id = self._entry_show_simkl_id(entry)
            if show_id is None or int(show_id) not in show_ids:
                continue
            show_id = int(show_id)

            for season in entry.get("seasons") or []:
                if not isinstance(season, dict):
                    continue
                season_num = season.get("number") if season.get("number") is not None else season.get("season")
                if season_num is None:
                    continue
                for episode in season.get("episodes") or []:
                    if not isinstance(episode, dict):
                        continue
                    episode_num = (
                        episode.get("number") if episode.get("number") is not None else episode.get("episode")
                    )
                    if episode_num is None:
                        continue
                    watched = 1 if self._episode_marked_watched(episode, entry) else 0
                    last_watched_at = None
                    if watched:
                        last_watched_at = (
                            episode.get("watched_at")
                            or episode.get("last_watched_at")
                            or entry.get("last_watched_at")
                        )
                    rows.append(
                        {
                            "simkl_show_id": show_id,
                            "season": int(season_num),
                            "episode": int(episode_num),
                            "watched": watched,
                            "last_watched_at": last_watched_at,
                        }
                    )

        if not rows:
            return False

        show_id_list = sorted({row["simkl_show_id"] for row in rows})
        with self.create_temp_table(
            "_episodes_watch_state",
            ["simkl_show_id", "season", "episode", "watched", "last_watched_at"],
            primary_key="simkl_show_id, season, episode",
        ) as temp_table:
            temp_table.insert_data(rows)
            placeholders = ",".join(str(sid) for sid in show_id_list)
            self.execute_sql(
                f"""
                UPDATE episodes
                SET watched = (
                        SELECT watched
                        FROM _episodes_watch_state
                        WHERE _episodes_watch_state.simkl_show_id = episodes.simkl_show_id
                          AND _episodes_watch_state.season = episodes.season
                          AND _episodes_watch_state.episode = episodes.number
                    ),
                    last_watched_at = (
                        SELECT CASE
                            WHEN _episodes_watch_state.watched > 0
                                THEN COALESCE(
                                    _episodes_watch_state.last_watched_at,
                                    episodes.last_watched_at
                                )
                            ELSE NULL
                        END
                        FROM _episodes_watch_state
                        WHERE _episodes_watch_state.simkl_show_id = episodes.simkl_show_id
                          AND _episodes_watch_state.season = episodes.season
                          AND _episodes_watch_state.episode = episodes.number
                    )
                WHERE simkl_show_id IN ({placeholders})
                  AND EXISTS (
                    SELECT 1
                    FROM _episodes_watch_state
                    WHERE _episodes_watch_state.simkl_show_id = episodes.simkl_show_id
                      AND _episodes_watch_state.season = episodes.season
                      AND _episodes_watch_state.episode = episodes.number
                )
                """
            )

        for show_id in show_id_list:
            self._refresh_show_and_season_statistics(int(show_id))
        self.apply_show_watch_counters(entries)
        return True

    def _apply_entry_watch_state(self, entries, shows) -> None:
        """Apply per-episode watch flags, using bidirectional sync when Simkl returned episode rows."""
        full_entries = [entry for entry in (entries or []) if self._entry_has_full_episode_watch_detail(entry)]
        partial_entries = [entry for entry in (entries or []) if entry not in full_entries]
        if full_entries:
            self.apply_episode_watch_state_from_entries(full_entries, shows)
        if partial_entries:
            self.apply_watched_episodes_from_entries(partial_entries, shows)

    def apply_show_watch_counters(self, entries):
        """Apply Simkl summary progress (watched/total episode counts) to show rows for list indicators."""
        rows = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            simkl_id = self._entry_show_simkl_id(entry)
            watched = entry.get("watched_episodes_count")
            total = entry.get("total_episodes_count")
            if simkl_id is None or watched is None or total is None:
                continue
            watched = int(watched)
            total = int(total)
            rows.append((watched, total, max(0, total - watched), simkl_id))

        if not rows:
            return

        self.execute_sql(
            """
            UPDATE shows
            SET watched_episodes = MAX(watched_episodes, ?),
                episode_count = MAX(episode_count, ?),
                unwatched_episodes = ?
            WHERE simkl_id = ?
            """,
            rows,
        )

    def apply_sync_episode_stubs_from_entries(self, entries, shows, catalog: str | None = None, *, selective: bool = False):
        """Create lightweight season/episode rows from sync payload (no per-show milling)."""
        show_ids = {int(show["simkl_id"]): show for show in shows if show.get("simkl_id")}
        if not show_ids:
            return

        from resources.lib.simkl.field_map import anime_menu_episode_number
        from resources.lib.simkl.ids import attach_tv_context, season_key, synthetic_episode_id

        season_items: dict[tuple[int, int], dict] = {}
        episode_items: dict[tuple[int, int, int], dict] = {}

        for entry in entries or []:
            show_id = self._entry_show_simkl_id(entry)
            if show_id is None or show_id not in show_ids:
                continue
            show_id = int(show_id)

            entry_catalog = catalog
            if entry_catalog is None:
                blob = entry.get("anime") or entry.get("show") or entry
                entry_catalog = "anime" if (blob.get("ids") or {}).get("mal") else "tv"

            for season in entry.get("seasons") or []:
                season_num = season.get("number") if season.get("number") is not None else season.get("season")
                if season_num is None:
                    continue
                season_num = int(season_num)
                season_row_id = season_key(show_id, season_num)
                episodes = season.get("episodes") or []

                if (show_id, season_num) not in season_items:
                    season_info = {
                        "simkl_id": season_row_id,
                        "mediatype": "season",
                        "catalog": entry_catalog,
                        "season": season_num,
                        "simkl_show_id": show_id,
                        "episode_count": len(episodes),
                    }
                    attach_tv_context(
                        season_info,
                        show_id,
                        season_num=season_num,
                        season_row_id=season_row_id,
                    )
                    season_items[(show_id, season_num)] = {
                        "simkl_id": season_row_id,
                        "simkl_show_id": show_id,
                        "simkl_object": {"info": season_info},
                    }
                else:
                    existing_info = season_items[(show_id, season_num)]["simkl_object"]["info"]
                    if len(episodes) > int(existing_info.get("episode_count") or 0):
                        existing_info["episode_count"] = len(episodes)

                for episode in episodes:
                    ep_num = episode.get("number") if episode.get("number") is not None else episode.get("episode")
                    if ep_num is None and entry_catalog == "anime":
                        ep_num = anime_menu_episode_number(episode, season_num, None)
                    if ep_num is None:
                        continue
                    ep_num = int(ep_num)

                    if selective and not self._episode_marked_watched(episode, entry):
                        continue

                    ep_key = (show_id, season_num, ep_num)
                    if ep_key in episode_items:
                        continue

                    ep_ids = episode.get("ids") or {}
                    ep_simkl_id = ep_ids.get("simkl_id") or ep_ids.get("simkl")
                    if ep_simkl_id is None:
                        ep_simkl_id = synthetic_episode_id(show_id, season_num, ep_num)
                    else:
                        ep_simkl_id = int(ep_simkl_id)

                    tvdb_id = ep_ids.get("tvdb_id")
                    watched = 1 if self._episode_marked_watched(episode, entry) else 0
                    ep_info = {
                        "simkl_id": ep_simkl_id,
                        "mediatype": "episode",
                        "catalog": entry_catalog,
                        "season": season_num,
                        "episode": ep_num,
                        "number": ep_num,
                        "playcount": watched,
                    }
                    if tvdb_id is not None:
                        ep_info["tvdb_id"] = int(tvdb_id)
                    attach_tv_context(
                        ep_info,
                        show_id,
                        season_num=season_num,
                        season_row_id=season_row_id,
                    )
                    episode_items[ep_key] = {
                        "simkl_id": ep_simkl_id,
                        "simkl_show_id": show_id,
                        "simkl_season_id": season_row_id,
                        "simkl_object": {"info": ep_info},
                    }

        seasons = list(season_items.values())
        episodes = list(episode_items.values())
        if seasons:
            self.insert_simkl_seasons(seasons)
        if episodes:
            self.insert_simkl_episodes(episodes)
        for (show_id, season_num), item in season_items.items():
            info = (item.get("simkl_object") or {}).get("info") or {}
            ep_count = int(info.get("episode_count") or 0)
            if ep_count > 0:
                self.execute_sql(
                    """
                    UPDATE seasons
                    SET episode_count = MAX(COALESCE(episode_count, 0), ?)
                    WHERE simkl_show_id = ? AND season = ?
                    """,
                    (ep_count, int(show_id), int(season_num)),
                )
        if seasons or episodes:
            g.log(
                f"Simkl sync stubs: {len(seasons)} season(s), {len(episodes)} episode(s)",
                "debug",
            )


    @staticmethod
    def _milled_catalog_season_guard_sql(episode_alias: str = "e") -> str:
        """SQL guard: keep full drilldown-milled season catalogs out of stub prune."""
        return f"""
              AND NOT EXISTS (
                SELECT 1
                FROM seasons AS se
                WHERE se.simkl_show_id = {episode_alias}.simkl_show_id
                  AND se.season = {episode_alias}.season
                  AND se.season > 0
                  AND (
                    SELECT COUNT(*)
                    FROM episodes AS ec
                    WHERE ec.simkl_show_id = se.simkl_show_id
                      AND ec.season = se.season
                  ) >= CASE
                    WHEN COALESCE(se.episode_count, 0) > 1 THEN se.episode_count
                    ELSE 2
                  END
              )
        """

    def prune_library_episodes(self) -> None:
        """Drop episode rows not needed for Continue Watching / Next Up / Watched Episodes."""
        bookmarked = self.fetchall("SELECT simkl_id FROM bookmarks WHERE type='episode'")
        bookmark_ids = {int(row["simkl_id"]) for row in bookmarked if row.get("simkl_id") is not None}
        bookmark_clause = ""
        bookmark_params: tuple = ()
        if bookmark_ids:
            placeholders = ",".join("?" * len(bookmark_ids))
            bookmark_clause = f" AND e.simkl_id NOT IN ({placeholders})"
            bookmark_params = tuple(bookmark_ids)
        milled_guard = self._milled_catalog_season_guard_sql("e")

        self.execute_sql(
            f"""
            DELETE FROM episodes AS e
            WHERE e.simkl_show_id IN (
                SELECT s.simkl_id FROM shows AS s
                WHERE COALESCE(s.simkl_status, '') NOT IN ('watching', 'completed')
            ){bookmark_clause}
            """,
            bookmark_params or None,
        )

        self.execute_sql(
            f"""
            DELETE FROM episodes AS e
            WHERE COALESCE(e.watched, 0) = 0
              AND e.simkl_show_id IN (
                SELECT s.simkl_id FROM shows AS s
                WHERE s.simkl_status = 'completed'
            ){bookmark_clause}
            """,
            bookmark_params or None,
        )

        self.execute_sql(
            f"""
            DELETE FROM episodes AS e
            WHERE COALESCE(e.watched, 0) = 0
              AND e.simkl_show_id IN (
                SELECT s.simkl_id FROM shows AS s
                WHERE s.simkl_status = 'watching'
            )
              AND e.simkl_id NOT IN (
                SELECT e2.simkl_id
                FROM episodes AS e2
                WHERE e2.simkl_show_id = e.simkl_show_id
                  AND COALESCE(e2.watched, 0) = 0
                  AND e2.season > 0
                ORDER BY e2.season ASC, e2.number ASC
                LIMIT 1
            ){milled_guard}{bookmark_clause}
            """,
            bookmark_params or None,
        )

        self.execute_sql(
            """
            DELETE FROM seasons
            WHERE simkl_show_id NOT IN (
                SELECT DISTINCT simkl_show_id FROM episodes
            )
            """
        )

    def ensure_playback_episode_row(self, show: dict, episode: dict, catalog: str) -> int | None:
        """Create a minimal season/episode row for a paused playback entry (no full mill)."""
        show_id = (show.get("ids") or {}).get("simkl")
        if not show_id:
            return None
        show_id = int(show_id)

        from resources.lib.simkl.field_map import anime_menu_episode_number, anime_menu_season
        from resources.lib.simkl.ids import attach_tv_context, season_key, synthetic_episode_id

        ep_ids = episode.get("ids") or {}
        ep_simkl_id = ep_ids.get("simkl_id") or ep_ids.get("simkl")

        season_num = episode.get("season")
        if season_num is None:
            season_num = anime_menu_season(episode)
        if season_num is None:
            return None
        season_num = int(season_num)

        ep_num = episode.get("number") or episode.get("episode")
        ep_num = anime_menu_episode_number(episode, season_num, ep_num)
        if ep_num is None:
            return None
        ep_num = int(ep_num)

        if ep_simkl_id is not None:
            ep_simkl_id = int(ep_simkl_id)
        else:
            row = self.fetchone(
                """
                SELECT simkl_id FROM episodes
                WHERE simkl_show_id = ? AND season = ? AND number = ?
                """,
                (show_id, season_num, ep_num),
            )
            if row:
                return int(row["simkl_id"])
            ep_simkl_id = synthetic_episode_id(show_id, season_num, ep_num)

        if self.fetchone("SELECT 1 AS ok FROM episodes WHERE simkl_id = ?", (ep_simkl_id,)):
            return ep_simkl_id

        season_row_id = season_key(show_id, season_num)
        if not self.fetchone("SELECT 1 AS ok FROM seasons WHERE simkl_id = ?", (season_row_id,)):
            season_info = {
                "simkl_id": season_row_id,
                "mediatype": "season",
                "catalog": catalog,
                "season": season_num,
                "simkl_show_id": show_id,
            }
            attach_tv_context(
                season_info,
                show_id,
                season_num=season_num,
                season_row_id=season_row_id,
            )
            self.insert_simkl_seasons(
                [
                    {
                        "simkl_id": season_row_id,
                        "simkl_show_id": show_id,
                        "simkl_object": {"info": season_info},
                    }
                ]
            )

        tvdb_id = ep_ids.get("tvdb_id")
        from resources.lib.simkl.field_map import _unescape

        ep_info = {
            "simkl_id": ep_simkl_id,
            "mediatype": "episode",
            "catalog": catalog,
            "season": season_num,
            "episode": ep_num,
            "number": ep_num,
            "playcount": 0,
        }
        ep_title = _unescape(episode.get("title"))
        if ep_title:
            ep_info["title"] = ep_title
            ep_info.setdefault("sorttitle", ep_title)
        if tvdb_id is not None:
            ep_info["tvdb_id"] = int(tvdb_id)
        attach_tv_context(
            ep_info,
            show_id,
            season_num=season_num,
            season_row_id=season_row_id,
        )
        self.insert_simkl_episodes(
            [
                {
                    "simkl_id": ep_simkl_id,
                    "simkl_show_id": show_id,
                    "simkl_season_id": season_row_id,
                    "simkl_object": {"info": ep_info},
                }
            ]
        )
        return ep_simkl_id

    @staticmethod
    def _parse_next_to_watch_string(value: str | None) -> tuple[int, int] | None:
        if not value or not isinstance(value, str):
            return None
        import re

        match = re.match(r"[Ss](\d+)[Ee](\d+)", value.strip())
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.match(r"[Ee](\d+)$", value.strip())
        if match:
            return 1, int(match.group(1))
        return None

    def apply_next_watch_stubs_from_entries(self, entries, shows, catalog: str | None = None):
        """Seed the next unwatched episode per watching show from Simkl next_watch_info (no milling)."""
        show_ids = {int(show["simkl_id"]): show for show in shows if show.get("simkl_id")}
        if not show_ids:
            return

        from resources.lib.simkl.ids import attach_tv_context, season_key, synthetic_episode_id

        season_items: dict[tuple[int, int], dict] = {}
        episode_items: dict[tuple[int, int, int], dict] = {}
        count = 0

        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("status") != "watching":
                continue
            show_id = self._entry_show_simkl_id(entry)
            if show_id is None or show_id not in show_ids:
                continue
            show_id = int(show_id)

            entry_catalog = catalog
            if entry_catalog is None:
                blob = entry.get("anime") or entry.get("show") or entry
                entry_catalog = "anime" if (blob.get("ids") or {}).get("mal") else "tv"

            next_info = entry.get("next_to_watch_info")
            season_num = episode_num = None
            air_date = None
            title = None
            if isinstance(next_info, dict):
                season_num = next_info.get("season")
                episode_num = next_info.get("episode")
                if episode_num is None:
                    episode_num = next_info.get("number")
                air_date = next_info.get("date")
                title = next_info.get("title")
            if season_num is None or episode_num is None:
                parsed = self._parse_next_to_watch_string(entry.get("next_to_watch"))
                if parsed:
                    season_num, episode_num = parsed
            if season_num is None or episode_num is None:
                continue
            season_num = int(season_num)
            episode_num = int(episode_num)

            season_row_id = season_key(show_id, season_num)
            ep_key = (show_id, season_num, episode_num)
            if ep_key in episode_items:
                continue

            if (show_id, season_num) not in season_items:
                season_info = {
                    "simkl_id": season_row_id,
                    "mediatype": "season",
                    "catalog": entry_catalog,
                    "season": season_num,
                    "simkl_show_id": show_id,
                }
                attach_tv_context(
                    season_info,
                    show_id,
                    season_num=season_num,
                    season_row_id=season_row_id,
                )
                season_items[(show_id, season_num)] = {
                    "simkl_id": season_row_id,
                    "simkl_show_id": show_id,
                    "simkl_object": {"info": season_info},
                }

            ep_simkl_id = synthetic_episode_id(show_id, season_num, episode_num)
            ep_info = {
                "simkl_id": ep_simkl_id,
                "mediatype": "episode",
                "catalog": entry_catalog,
                "season": season_num,
                "episode": episode_num,
                "number": episode_num,
                "playcount": 0,
                "title": title,
                "aired": air_date,
                "first_aired": air_date,
            }
            attach_tv_context(
                ep_info,
                show_id,
                season_num=season_num,
                season_row_id=season_row_id,
            )
            episode_items[ep_key] = {
                "simkl_id": ep_simkl_id,
                "simkl_show_id": show_id,
                "simkl_season_id": season_row_id,
                "simkl_object": {"info": ep_info},
            }
            count += 1

        seasons = list(season_items.values())
        episodes = list(episode_items.values())
        if seasons:
            self.insert_simkl_seasons(seasons)
        if episodes:
            self.insert_simkl_episodes(episodes)
        if count:
            g.log(f"Simkl next-watch stubs: {count} episode(s)", "debug")

    def apply_watched_episodes_from_entries(self, entries, shows):
        show_ids = {show["simkl_id"]: show for show in shows if show.get("simkl_id")}
        rows = []

        for entry in entries or []:
            show_id = self._entry_show_simkl_id(entry)
            if show_id is None or show_id not in show_ids:
                continue

            for season in entry.get("seasons") or []:
                season_num = season.get("number") if season.get("number") is not None else season.get("season")
                if season_num is None:
                    continue
                for episode in season.get("episodes") or []:
                    episode_num = episode.get("number") if episode.get("number") is not None else episode.get("episode")
                    if episode_num is None:
                        continue
                    if not self._episode_marked_watched(episode, entry):
                        continue
                    rows.append(
                        {
                            "simkl_show_id": show_id,
                            "season": int(season_num),
                            "episode": int(episode_num),
                            "last_watched_at": episode.get("watched_at")
                            or episode.get("last_watched_at")
                            or entry.get("last_watched_at"),
                            "watched": 1,
                        }
                    )

        if not rows:
            self.apply_show_watch_counters(entries)
            return

        show_id_list = ",".join(str(i) for i in show_ids.keys())
        with self.create_temp_table(
            "_episodes_watched",
            ["simkl_show_id", "season", "episode", "last_watched_at", "watched"],
            primary_key="simkl_show_id, season, episode",
        ) as temp_table:
            temp_table.insert_data(rows)
            self.execute_sql(
                """
                UPDATE episodes
                SET (watched, last_watched_at) = (
                    SELECT watched, last_watched_at
                    FROM _episodes_watched
                    WHERE _episodes_watched.simkl_show_id = episodes.simkl_show_id
                        AND _episodes_watched.season = episodes.season
                        AND _episodes_watched.episode = episodes.number
                )
                WHERE simkl_show_id IN ({show_ids})
                  AND EXISTS (
                    SELECT 1
                    FROM _episodes_watched
                    WHERE _episodes_watched.simkl_show_id = episodes.simkl_show_id
                        AND _episodes_watched.season = episodes.season
                        AND _episodes_watched.episode = episodes.number
                )
                """.format(show_ids=show_id_list),
            )

        for show_id in show_ids.keys():
            self._refresh_show_and_season_statistics(int(show_id))
        self.apply_show_watch_counters(entries)

    def _refresh_show_and_season_statistics(self, simkl_show_id: int) -> None:
        """Recompute show + season counters from episode watch rows."""
        show_id = int(simkl_show_id)
        self.update_shows_statistics([{"simkl_id": show_id}])
        seasons = self.fetchall("SELECT simkl_id FROM seasons WHERE simkl_show_id=?", (show_id,))
        if seasons:
            self.update_season_statistics(seasons)
            self.execute_sql(
                """
                UPDATE seasons
                SET unwatched_episodes = MAX(
                    0,
                    COALESCE(episode_count, 0) - COALESCE(watched_episodes, 0)
                )
                WHERE simkl_show_id = ?
                """,
                (show_id,),
            )

    def apply_watched_progress_from_entry(self, entry: dict) -> int:
        """Mark episodes watched up to Simkl last_watched when seasons[] is missing or sparse."""
        show_id = self._entry_show_simkl_id(entry)
        if show_id is None:
            return 0
        show_id = int(show_id)

        watched_count = int(entry.get("watched_episodes_count") or 0)
        if watched_count <= 0:
            return 0

        last = self._parse_next_to_watch_string(entry.get("last_watched"))
        last_watched_at = entry.get("last_watched_at")

        if last is not None:
            last_season, last_episode = last
            up_to_last = self.fetchall(
                """
                SELECT season, number
                FROM episodes
                WHERE simkl_show_id=? AND season > 0
                  AND (season < ? OR (season = ? AND number <= ?))
                ORDER BY season, number
                """,
                (show_id, last_season, last_season, last_episode),
            )
            if len(up_to_last) > watched_count:
                rows = up_to_last[-watched_count:]
            else:
                rows = up_to_last
        else:
            rows = self.fetchall(
                """
                SELECT season, number
                FROM episodes
                WHERE simkl_show_id=? AND season > 0
                ORDER BY season, number
                LIMIT ?
                """,
                (show_id, watched_count),
            )

        if not rows:
            return 0

        stamp = last_watched_at or self._get_aired_cutoff()
        for row in rows:
            self.execute_sql(
                (
                    "UPDATE episodes SET watched=1, "
                    "last_watched_at=COALESCE(last_watched_at, ?) "
                    "WHERE simkl_show_id=? AND season=? AND number=? "
                    "AND COALESCE(watched, 0) = 0"
                ),
                (stamp, show_id, int(row["season"]), int(row["number"])),
            )

        self._refresh_show_and_season_statistics(show_id)
        return len(rows)

    def apply_completed_show_watch_flags(self, entries):
        """Completed Simkl list entries should appear fully watched in Kodi lists."""
        for entry in entries or []:
            if not isinstance(entry, dict) or entry.get("status") != "completed":
                continue
            show_id = self._entry_show_simkl_id(entry)
            if show_id is None:
                continue
            show_id = int(show_id)

            watched = entry.get("watched_episodes_count")
            total = entry.get("total_episodes_count")
            if total is not None:
                try:
                    total_int = int(total)
                except (TypeError, ValueError):
                    total_int = 0
                if total_int > 0:
                    watched_int = int(watched) if watched is not None else total_int
                    self.execute_sql(
                        """
                        UPDATE shows
                        SET watched_episodes=MAX(watched_episodes, ?),
                            episode_count=MAX(episode_count, ?),
                            unwatched_episodes=0
                        WHERE simkl_id=?
                        """,
                        (max(watched_int, total_int), total_int, show_id),
                    )

            # Do not bulk-mark every episode row watched from show-level counters alone.
            # Per-episode Simkl flags (apply_watched_episodes_from_entries) are authoritative;
            # list UI uses _apply_completed_watched_display for completed-show indicators.

    def apply_movie_watch_flags(self, entries):
        """Align movies.watched with Simkl list status, not prior watch history alone."""
        watched_ids: list[int] = []
        unwatched_ids: list[int] = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            blob = entry.get("movie") or entry
            if not isinstance(blob, dict):
                continue
            simkl_id = (blob.get("ids") or {}).get("simkl")
            if simkl_id is None:
                continue
            simkl_id = int(simkl_id)
            status = entry.get("status")
            if status == "completed":
                watched_ids.append(simkl_id)
            elif status in ("plantowatch", "dropped", "hold", "watching"):
                unwatched_ids.append(simkl_id)
            elif entry.get("last_watched_at"):
                watched_ids.append(simkl_id)

        if unwatched_ids:
            placeholders = ",".join("?" * len(unwatched_ids))
            self.execute_sql(
                f"UPDATE movies SET watched=0 WHERE simkl_id IN ({placeholders})",
                tuple(unwatched_ids),
            )
        if not watched_ids:
            return

        placeholders = ",".join("?" * len(watched_ids))
        self.execute_sql(
            f"UPDATE movies SET watched=1 WHERE simkl_id IN ({placeholders})",
            tuple(watched_ids),
        )

    def apply_movie_library_status(self, entries):
        """Merge Simkl list status into movies.info for My Library menus."""
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if not status:
                continue
            blob = entry.get("movie") or entry
            if not isinstance(blob, dict):
                continue
            simkl_id = (blob.get("ids") or {}).get("simkl")
            if simkl_id is None:
                continue
            self.set_simkl_status(int(simkl_id), "movie", status)

    def set_simkl_status(self, simkl_id: int, catalog: str, status: str | None) -> None:
        """Persist Simkl list status on a movie or show row for My Library menus."""
        table = "movies" if catalog == "movie" else "shows"
        row = self.fetchone(f"SELECT info FROM {table} WHERE simkl_id=?", (int(simkl_id),))
        if not row:
            return
        info = row.get("info")
        if not isinstance(info, dict):
            info = {}
        if status:
            info["simkl_status"] = status
        else:
            info.pop("simkl_status", None)
        self.execute_sql(
            f"UPDATE {table} SET info=?, simkl_status=? WHERE simkl_id=?",
            (info, status, int(simkl_id)),
        )

    def set_user_rating(self, simkl_id: int, catalog: str, rating: int | None) -> None:
        """Persist Simkl user rating (1-10) on a movie or show row."""
        table = "movies" if catalog == "movie" else "shows"
        self.execute_sql(
            f"UPDATE {table} SET user_rating=? WHERE simkl_id=?",
            (rating, int(simkl_id)),
        )
        row = self.fetchone(f"SELECT info FROM {table} WHERE simkl_id=?", (int(simkl_id),))
        if not row:
            return
        info = row.get("info")
        if not isinstance(info, dict):
            return
        if rating is not None:
            info["user_rating"] = rating
        else:
            info.pop("user_rating", None)
        self.execute_sql(f"UPDATE {table} SET info=? WHERE simkl_id=?", (info, int(simkl_id)))

    def apply_show_library_status(self, entries):
        """Merge Simkl list status into shows.info for My Library menus."""
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if not status:
                continue
            blob = entry.get("show") or entry.get("anime") or entry
            if not isinstance(blob, dict):
                continue
            simkl_id = (blob.get("ids") or {}).get("simkl")
            if simkl_id is None:
                continue
            self.set_simkl_status(int(simkl_id), "show", status)

    def apply_library_watch_state(self, catalog: str, entries, sync_items):
        if catalog == "movie":
            self.apply_movie_watch_flags(entries)
            self.apply_movie_library_status(entries)
            return
        self.apply_show_library_status(entries)
        self.apply_sync_episode_stubs_from_entries(entries, sync_items, catalog)
        self.apply_next_watch_stubs_from_entries(entries, sync_items, catalog)
        self.sync_show_watch_state_from_entries(entries, sync_items)

    def update_shows_statistics(self, media_list):
        self.__update_shows_statisics(media_list)

    def _update_all_shows_statisics(self):
        self.__update_shows_statisics()

    def __update_shows_statisics(self, media_list=None):
        now = self._get_aired_cutoff()
        if media_list:
            where_restriction_clause = f"WHERE simkl_id in ({','.join(str(i.get('simkl_id')) for i in media_list)})"
        else:
            where_restriction_clause = ""
        self.execute_sql(
            f"""
            UPDATE shows
            SET (
                    air_date, is_airing,
                    season_count, episode_count, watched_episodes, unwatched_episodes,
                    last_watched_at
                    ) = (SELECT coalesce(CASE
                                             WHEN min(coalesce(e.air_date, datetime('9999-12-31T00:00:00'))
                                                      ) <> datetime('9999-12-31T00:00:00')
                                                 THEN min(e.air_date)
                                             END,
                                         s.air_date)               AS air_date,
                                coalesce(CASE
                                             WHEN max(e.simkl_id) IS NOT NULL
                                                 THEN CASE
                                                          WHEN e.season > 0 AND max(e.air_date) > datetime('{now}')
                                                              THEN 1
                                                          ELSE 0
                                                 END
                                             END, s.is_airing)     AS is_airing,
                                coalesce(CASE
                                             WHEN count(DISTINCT CASE
                                                                     WHEN e.season > 0
                                                                         AND Datetime(e.air_date) < Datetime('{now}')
                                                                         THEN season END) > 0
                                                 THEN count(DISTINCT CASE
                                                                         WHEN e.season > 0
                                                                             AND Datetime(e.air_date) < Datetime('{now}')
                                                                             THEN season END)
                                             END, s.season_count)  AS season_count,
                                coalesce(CASE
                                             WHEN max(e.simkl_id) IS NOT NULL
                                                 THEN sum(
                                                     CASE
                                                         WHEN e.season > 0
                                                             AND datetime(e.air_date) < datetime('{now}')
                                                             THEN 1
                                                         ELSE 0
                                                         END
                                                 )
                                             END, s.episode_count) AS episode_count,
                                coalesce(CASE
                                             WHEN max(e.simkl_id) IS NOT NULL
                                                 THEN sum(
                                                     CASE
                                                         WHEN e.season > 0 AND e.watched > 0
                                                             AND (e.air_date IS NULL OR datetime(e.air_date) < datetime('{now}'))
                                                             THEN 1
                                                         ELSE 0
                                                         END
                                                 )
                                             END,
                                         s.watched_episodes)       AS watched_episodes,
                                coalesce(CASE
                                             WHEN sum(CASE
                                                          WHEN e.season > 0
                                                              AND Datetime(e.air_date) < Datetime('{now}')
                                                              THEN 1 END) > s.episode_count
                                                 THEN sum(CASE
                                                              WHEN e.season > 0
                                                                  AND Datetime(e.air_date) < Datetime('{now}')
                                                                  THEN 1 END)
                                             ELSE s.episode_count
                                             END - sum(CASE
                                                           WHEN e.season > 0 AND e.watched > 0
                                                               AND (e.air_date IS NULL OR Datetime(e.air_date) < Datetime('{now}'))
                                                               THEN 1
                                                           ELSE 0
                                    END), s.unwatched_episodes)    AS unwatched_episodes,
                                CASE
                                    WHEN max(e.simkl_id) IS NOT NULL
                                        THEN max(e.last_watched_at)
                                    ELSE s.last_watched_at
                                    END                            AS last_watched_at
                         FROM shows AS s
                                  LEFT JOIN episodes AS e
                                            ON e.simkl_show_id = s.simkl_id
                         WHERE s.simkl_id = shows.simkl_id
                         GROUP BY e.simkl_show_id)
            {where_restriction_clause}
            """
        )

    def update_season_statistics(self, media_list):
        self.__update_season_statistics(media_list)

    def _update_all_season_statistics(self):
        self.__update_season_statistics()

    def __update_season_statistics(self, media_list=None):
        now = self._get_aired_cutoff()
        if media_list:
            where_restriction_clause = f"AND simkl_id in ({','.join(str(i.get('simkl_id')) for i in media_list)})"
        else:
            where_restriction_clause = ""
        self.execute_sql(
            f"""
            UPDATE seasons
            SET (
                    air_date, is_airing,
                    episode_count, watched_episodes, unwatched_episodes,
                    last_watched_at
                    ) = (SELECT coalesce(
                                        CASE
                                            WHEN min(coalesce(e.air_date, datetime('9999-12-31T00:00:00'))
                                                     ) <> datetime('9999-12-31T00:00:00')
                                                THEN min(e.air_date)
                                            END,
                                        seasons.air_date
                                    )   as air_date,
                                CASE
                                    WHEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN CASE
                                                             WHEN max(e.air_date) > datetime('{now}')
                                                                 THEN 1
                                                             ELSE 0
                                                    END
                                                END,
                                            seasons.is_airing
                                        )
                                        THEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN CASE
                                                             WHEN max(e.air_date) > datetime('{now}')
                                                                 THEN 1
                                                             ELSE 0
                                                    END
                                                END,
                                            seasons.is_airing
                                        )
                                    ELSE 0
                                    END as is_airing,
                                CASE
                                    WHEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) is not null
                                                    THEN sum(
                                                        CASE
                                                            WHEN e.air_date IS NULL
                                                                OR datetime(e.air_date) < datetime('{now}')
                                                                THEN 1
                                                            ELSE 0
                                                            END
                                                    )
                                                END,
                                            seasons.episode_count
                                        ) IS NOT NULL
                                        THEN MAX(
                                            COALESCE(seasons.episode_count, 0),
                                            COALESCE(
                                                CASE
                                                    WHEN max(e.simkl_id) is not null
                                                        THEN sum(
                                                            CASE
                                                                WHEN e.air_date IS NULL
                                                                    OR datetime(e.air_date) < datetime('{now}')
                                                                    THEN 1
                                                                ELSE 0
                                                                END
                                                        )
                                                    END,
                                                0
                                            )
                                        )
                                    ELSE COALESCE(seasons.episode_count, 0)
                                    END AS episode_count,
                                CASE
                                    WHEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN sum(
                                                        CASE
                                                            WHEN e.watched > 0
                                                                THEN 1
                                                            ELSE 0
                                                            END
                                                    )
                                                END,
                                            seasons.watched_episodes
                                        ) IS NOT NULL
                                        THEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN sum(
                                                        CASE
                                                            WHEN e.watched > 0
                                                                THEN 1
                                                            ELSE 0
                                                            END
                                                    )
                                                END,
                                            seasons.watched_episodes
                                        )
                                    ELSE 0
                                    END AS watched_episodes,
                                CASE
                                    WHEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN sum(
                                                        CASE
                                                            WHEN e.watched == 0
                                                                    AND (
                                                                        e.air_date IS NULL
                                                                        OR datetime(e.air_date) < datetime('{now}')
                                                                    )
                                                                THEN 1
                                                            ELSE 0
                                                            END
                                                    )
                                                END,
                                            seasons.unwatched_episodes
                                        ) IS NOT NULL
                                        THEN coalesce(
                                            CASE
                                                WHEN max(e.simkl_id) IS NOT NULL
                                                    THEN sum(
                                                        CASE
                                                            WHEN e.watched == 0
                                                                    AND (
                                                                        e.air_date IS NULL
                                                                        OR datetime(e.air_date) < datetime('{now}')
                                                                    )
                                                                THEN 1
                                                            ELSE 0
                                                            END
                                                    )
                                                END,
                                            seasons.unwatched_episodes
                                        )
                                    ELSE 0
                                    END AS unwatched_episodes,
                                CASE
                                    WHEN max(e.simkl_id) IS NOT NULL
                                        THEN max(e.last_watched_at)
                                    ELSE seasons.last_watched_at
                                    END AS last_watched_at
                         FROM episodes AS e
                         WHERE e.simkl_show_id = seasons.simkl_show_id
                           AND e.season = seasons.season)
            WHERE EXISTS(
                SELECT 1
                FROM episodes AS ep
                WHERE ep.simkl_show_id = seasons.simkl_show_id
                  AND ep.season = seasons.season
            )
            {where_restriction_clause}
            """
        )

    def clean_orphaned_metadata(self):
        media_meta_types = {
            "movies": ["simkl", "tmdb", "tvdb", "imdb", "fanart"],
            "episodes": ["simkl", "tmdb", "tvdb", "imdb", "fanart"],
            "seasons": ["simkl", "tmdb", "tvdb", "fanart"],
            "shows": ["simkl", "tmdb", "tvdb", "imdb", "fanart"],
        }
        for media_type in media_meta_types:
            for meta_type in media_meta_types[media_type]:
                if meta_type == "fanart":
                    self.execute_sql(self._clean_orphaned_fanart_metadata_query(media_type))
                else:
                    self.execute_sql(self._clean_orphaned_metadata_query(media_type, meta_type))

    def _clean_orphaned_metadata_query(self, media_type, meta_type):
        return f'''
            DELETE
            FROM {media_type}_meta
            WHERE type = '{meta_type}'
              AND id IN (SELECT id
                         FROM {media_type}_meta AS meta
                LEFT JOIN {media_type} AS media
                         ON media.{meta_type}_id = meta.id
                         WHERE meta.type = '{meta_type}'
                           AND media.{meta_type}_id IS NULL)
        '''

    def _clean_orphaned_fanart_metadata_query(self, media_type):
        if media_type == "movies":
            return f'''
                DELETE
                FROM {media_type}_meta
                WHERE type = 'fanart'
                  AND id IN (SELECT id
                             FROM {media_type}_meta AS meta
                                      LEFT JOIN {media_type} AS media ON media.imdb_id = meta.id OR media.tmdb_id = meta.id
                             WHERE meta.type = 'fanart'
                               AND media.imdb_id IS NULL
                               AND media.tmdb_id IS NULL)
            '''

        return f'''
            DELETE
            FROM {media_type}_meta
            WHERE type = 'fanart'
              AND id IN (SELECT id
                         FROM {media_type}_meta AS meta
                                  LEFT JOIN {media_type} AS media ON media.tvdb_id = meta.id
                         WHERE meta.type = 'fanart'
                           AND media.tvdb_id IS NULL)
        '''

    @property
    def upsert_movie_query(self):
        return """
                WITH new(simkl_id, info, art, cast, watched, air_date,
                         last_updated, tmdb_id, tvdb_id, imdb_id, meta_hash, args,
                         last_watched_at, user_rating, simkl_status,
                         needs_update
                    ) AS (values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE))
                INSERT
                INTO movies(simkl_id, info, art, cast, watched, air_date,
                            last_updated, tmdb_id, tvdb_id, imdb_id, meta_hash, args,
                            last_watched_at, user_rating, simkl_status,
                            needs_update)
                SELECT simkl_id,
                       info,
                       art,
                       [cast],
                       coalesce(watched, 0),
                       air_date,
                       coalesce(last_updated, '1970-01-01T00:00:00'),
                       tmdb_id,
                       tvdb_id,
                       imdb_id,
                       meta_hash,
                       coalesce(args, FALSE),
                       last_watched_at,
                       user_rating,
                       simkl_status,
                       needs_update
                FROM new
                WHERE TRUE
                ON CONFLICT(simkl_id) DO UPDATE
                    SET (info, art, cast, watched, air_date,
                            last_updated, tmdb_id, tvdb_id, imdb_id, meta_hash,
                            args, last_watched_at, user_rating, simkl_status,
                            needs_update) = (SELECT coalesce(new.info, old.info),
                                                    coalesce(new.art, old.art),
                                                    coalesce(new.cast, old.cast),
                                                    coalesce(new.watched, old.watched),
                                                    coalesce(new.air_date, old.air_date),
                                                    coalesce(new.last_updated, old.last_updated),
                                                    coalesce(new.tmdb_id, old.tmdb_id),
                                                    coalesce(new.tvdb_id, old.tvdb_id),
                                                    coalesce(new.imdb_id, old.imdb_id),
                                                    coalesce(new.meta_hash, old.meta_hash),
                                                    coalesce(new.args, old.args),
                                                    coalesce(new.last_watched_at, old.last_watched_at),
                                                    coalesce(new.user_rating, old.user_rating),
                                                    coalesce(new.simkl_status, old.simkl_status),
                                                    CASE
                                                        WHEN old.needs_update
                                                            THEN CASE
                                                                     WHEN new.info <> old.info
                                                                         OR new.art <> old.art
                                                                         OR new.cast <> old.cast
                                                                         THEN TRUE
                                                                     ELSE old.needs_update END
                                                        ELSE CASE
                                                                 WHEN Datetime(coalesce(old.last_updated, 0))
                                                                     < Datetime(new.last_updated)
                                                                     THEN TRUE
                                                                 ELSE FALSE END
                                                        END AS needs_update
                                             FROM new
                                                      LEFT JOIN movies AS old
                                                                ON old.simkl_id = new.simkl_id)
                """

    @property
    def upsert_show_query(self):
        return """
            WITH new(simkl_id, info, art, cast, air_date, last_updated,
                     tmdb_id, tvdb_id, imdb_id, meta_hash,
                     season_count, episode_count,
                     args, is_airing,
                     last_watched_at, user_rating, simkl_status,
                     needs_update, needs_milling)
                     AS (VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, TRUE))
            INSERT
            INTO shows(simkl_id, info, art, cast, air_date, last_updated,
                       tmdb_id, tvdb_id, imdb_id, meta_hash,
                       season_count, episode_count,
                       args, is_airing,
                       last_watched_at, user_rating, simkl_status,
                       needs_update, needs_milling)
            SELECT simkl_id,
                   info,
                   art,
                   [cast],
                   air_date,
                   coalesce(last_updated, '1970-01-01T00:00:00'),
                   tmdb_id,
                   tvdb_id,
                   imdb_id,
                   meta_hash,
                   coalesce(season_count, 0),
                   coalesce(episode_count, 0),
                   coalesce(args, FALSE),
                   is_airing,
                   last_watched_at,
                   user_rating,
                   simkl_status,
                   needs_update,
                   needs_milling
            FROM new
            WHERE TRUE
            ON CONFLICT(simkl_id) DO UPDATE
                SET (info, art, cast, air_date, last_updated,
                        tmdb_id, tvdb_id, imdb_id, meta_hash,
                        season_count, episode_count, watched_episodes, unwatched_episodes,
                        args, is_airing,
                        last_watched_at, user_rating, simkl_status,
                        needs_update,
                        needs_milling) = (SELECT coalesce(new.info, old.info),
                                                 coalesce(new.art, old.art),
                                                 coalesce(new.cast, old.cast),
                                                 coalesce(new.air_date, old.air_date),
                                                 coalesce(new.last_updated, old.last_updated),
                                                 coalesce(new.tmdb_id, old.tmdb_id),
                                                 coalesce(new.tvdb_id, old.tvdb_id),
                                                 coalesce(new.imdb_id, old.imdb_id),
                                                 coalesce(new.meta_hash, old.meta_hash),
                                                 coalesce(new.season_count, old.season_count),
                                                 coalesce(new.episode_count, old.episode_count),
                                                 coalesce(old.watched_episodes, 0),
                                                 coalesce(old.unwatched_episodes, 0),
                                                 coalesce(new.args, old.args),
                                                 coalesce(new.is_airing, old.is_airing),
                                                 coalesce(new.last_watched_at, old.last_watched_at),
                                                 coalesce(new.user_rating, old.user_rating),
                                                 coalesce(new.simkl_status, old.simkl_status),
                                                 CASE
                                                     WHEN old.needs_update
                                                         THEN CASE
                                                                  WHEN new.info <> old.info
                                                                      OR new.art <> old.art
                                                                      OR new.cast <> old.cast
                                                                      THEN FALSE
                                                                  ELSE old.needs_update END
                                                     ELSE CASE
                                                              WHEN Datetime(coalesce(old.last_updated, 0))
                                                                  < Datetime(new.last_updated)
                                                                  THEN TRUE
                                                              ELSE FALSE END
                                                     END AS needs_update,
                                                 CASE
                                                     WHEN old.needs_milling THEN old.needs_milling
                                                     ELSE CASE
                                                              WHEN Datetime(coalesce(old.last_updated, 0))
                                                                  < Datetime(new.last_updated)
                                                                  THEN TRUE
                                                              ELSE FALSE END
                                                     END AS needs_milling
                                          FROM new
                                                   LEFT JOIN shows AS old
                                                             on old.simkl_id = new.simkl_id)
            """

    @property
    def upsert_season_query(self):
        return """
            WITH new(simkl_show_id, simkl_id, info, art, cast,
                     air_date, last_updated,
                     tmdb_id, tvdb_id, meta_hash, episode_count,
                     season, args,
                     last_watched_at, user_rating,
                     needs_update) AS (VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE))
            INSERT
            INTO seasons(simkl_show_id, simkl_id, info, art, cast,
                         air_date, last_updated,
                         tmdb_id, tvdb_id, meta_hash, episode_count,
                         season, args,
                         last_watched_at, user_rating,
                         needs_update)
            SELECT simkl_show_id,
                   simkl_id,
                   info,
                   art,
                   [cast],
                   air_date,
                   coalesce(last_updated, '1970-01-01T00:00:00'),
                   tmdb_id,
                   tvdb_id,
                   meta_hash,
                   coalesce(episode_count, 0),
                   season,
                   coalesce(args, FALSE),
                   last_watched_at,
                   user_rating,
                   needs_update
            FROM new
            WHERE TRUE
            ON CONFLICT(simkl_show_id, season) DO UPDATE
                SET (simkl_id, info, art, cast,
                        air_date, last_updated,
                        tmdb_id, tvdb_id, meta_hash, episode_count,
                        args,
                        last_watched_at, user_rating,
                        needs_update) = (SELECT new.simkl_id,
                                                coalesce(new.info, old.info),
                                                coalesce(new.art, old.art),
                                                coalesce(new.cast, old.cast),
                                                coalesce(new.air_date, old.air_date),
                                                coalesce(new.last_updated, old.last_updated),
                                                coalesce(new.tmdb_id, old.tmdb_id),
                                                coalesce(new.tvdb_id, old.tvdb_id),
                                                coalesce(new.meta_hash, old.meta_hash),
                                                coalesce(new.episode_count, old.episode_count),
                                                coalesce(new.args, old.args),
                                                coalesce(new.last_watched_at, old.last_watched_at),
                                                coalesce(new.user_rating, old.user_rating),
                                                CASE
                                                    WHEN old.needs_update
                                                        THEN CASE
                                                                 WHEN new.info != old.info
                                                                     OR new.art != old.art
                                                                     OR new.cast != old.cast
                                                                     THEN FALSE
                                                                 ELSE old.needs_update END
                                                    ELSE CASE
                                                             WHEN Datetime(coalesce(old.last_updated, 0))
                                                                 < Datetime(new.last_updated)
                                                                 THEN TRUE
                                                             ELSE FALSE END
                                                    END AS needs_update
                                         FROM new
                                                  LEFT JOIN seasons AS old
                                                            ON old.simkl_show_id = new.simkl_show_id
                                                                AND old.season = new.season)
            """

    @property
    def upsert_episode_query(self):
        return """
            WITH new(simkl_id, simkl_show_id, simkl_season_id,
                     watched,
                     air_date, last_updated,
                     season, number,
                     tmdb_id, tvdb_id, imdb_id,
                     info, art, cast,
                     args, last_watched_at,
                     user_rating, meta_hash,
                     needs_update) AS (VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE))
            INSERT
            INTO episodes(simkl_id, simkl_show_id, simkl_season_id,
                          watched,
                          air_date, last_updated,
                          season, number,
                          tmdb_id, tvdb_id, imdb_id,
                          info, art, cast,
                          args, last_watched_at,
                          user_rating, meta_hash, needs_update)
            SELECT simkl_id,
                   simkl_show_id,
                   simkl_season_id,
                   coalesce(watched, 0),
                   air_date,
                   coalesce(last_updated, '1970-01-01T00:00:00'),
                   season,
                   number,
                   tmdb_id,
                   tvdb_id,
                   imdb_id,
                   info,
                   art,
                   [cast],
                   coalesce(args, FALSE),
                   last_watched_at,
                   user_rating,
                   meta_hash,
                   needs_update
            FROM new
            WHERE TRUE
            ON CONFLICT(simkl_show_id, season, number) DO UPDATE
                SET (simkl_id, simkl_season_id,
                        watched,
                        air_date, last_updated,
                        tmdb_id, tvdb_id, imdb_id,
                        info, art, cast,
                        args, last_watched_at,
                        user_rating, meta_hash,
                        needs_update) = (SELECT new.simkl_id,
                                                coalesce(new.simkl_season_id, old.simkl_season_id),
                                                MAX(coalesce(new.watched, 0), coalesce(old.watched, 0)),
                                                coalesce(new.air_date, old.air_date),
                                                coalesce(new.last_updated, old.last_updated),
                                                coalesce(new.tmdb_id, old.tmdb_id),
                                                coalesce(new.tvdb_id, old.tvdb_id),
                                                coalesce(new.imdb_id, old.imdb_id),
                                                coalesce(new.info, old.info),
                                                coalesce(new.art, old.art),
                                                coalesce(new.cast, old.cast),
                                                coalesce(new.args, old.args),
                                                coalesce(new.last_watched_at, old.last_watched_at),
                                                coalesce(new.user_rating, old.user_rating),
                                                coalesce(new.meta_hash, old.meta_hash),
                                                CASE
                                                    WHEN old.needs_update
                                                        THEN CASE
                                                                 WHEN new.info <> old.info
                                                                     OR new.art <> old.art
                                                                     OR new.cast <> old.cast
                                                                     THEN FALSE
                                                                 ELSE old.needs_update END
                                                    ELSE CASE
                                                             WHEN
                                                                     Datetime(coalesce(old.last_updated, 0))
                                                                     < Datetime(new.last_updated)
                                                                 THEN TRUE
                                                             ELSE FALSE END
                                                    END AS needs_update
                                         FROM new
                                                  LEFT JOIN episodes AS old
                                                            ON old.simkl_show_id = new.simkl_show_id
                                                                AND old.season = new.season
                                                                AND old.number = new.number)
            """
