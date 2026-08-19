from __future__ import annotations

from resources.lib.database.simkl_sync import database
from resources.lib.modules.globals import g
from resources.lib.modules.guard_decorators import guard_against_none
from resources.lib.modules.guard_decorators import guard_against_none_or_empty
from resources.lib.modules.metadataHandler import MetadataHandler


class SimklSyncDatabase(database.SimklSyncDatabase):
    def extract_browse_page(self, url, **params):
        return super()._extract_browse_page(url, "movies", **params)

    @guard_against_none(list)
    def get_movie_list(self, media_list, **params):
        skip_update = params.pop("skip_update", False)
        paint_only = params.pop("paint_only", False)
        sync_path = params.pop("sync_path", False)
        if skip_update and paint_only and not sync_path:
            from resources.lib.meta.paint_cache import try_fast_paint_list

            fast_rows = try_fast_paint_list(media_list, "movie", self, **params)
            if fast_rows is not None:
                return fast_rows

        if not skip_update:
            self._update_movies(media_list)

        from resources.lib.database.sync_meta_cache import SyncMetaCache

        meta_cache = SyncMetaCache()

        query = f"""
            SELECT m.simkl_id,
                   m.info,
                   m.art,
                   m.[cast],
                   m.args,
                   m.last_updated,
                   m.tmdb_id,
                   m.tvdb_id,
                   m.imdb_id,
                   b.resume_time,
                   b.percent_played,
                   m.watched AS play_count,
                   m.user_rating
            FROM movies AS m
                     LEFT JOIN bookmarks AS b
                               ON m.simkl_id = b.simkl_id
            WHERE m.simkl_id IN ({','.join(str(i.get('simkl_id')) for i in media_list)})
            """

        if params.pop("hide_unaired", self.hide_unaired):
            query += (
                f" AND (m.air_date IS NULL OR Datetime(m.air_date) < Datetime('{self._get_aired_cutoff()}'))"
            )
        if params.pop("hide_watched", self.hide_watched):
            query += " AND watched = 0"

        rows = self.fetchall(query) or []
        meta_cache.set_many_rows("movie", rows or [])

        if not sync_path:
            from resources.lib.meta.display_store import get_display_meta_store

            rows = get_display_meta_store().overlay_rows(rows, "movie")

        if skip_update and not sync_path:
            from resources.lib.meta.paint_cache import paint_sync_list_rows

            rows = paint_sync_list_rows(rows, media_list, "movie", self, **params)
        else:
            self.set_list_enrichment_refs([], "movie")
        rows = MetadataHandler.sort_list_items(rows, media_list)
        return rows

    @guard_against_none(list)
    def get_watched_movies(self, page):
        return self.fetchall(
            f"""
            SELECT m.simkl_id,
                   meta.value AS simkl_object,
                   m.info,
                   m.art,
                   m.tmdb_id,
                   m.tvdb_id,
                   m.imdb_id
            FROM movies AS m
                     LEFT JOIN movies_meta AS meta
                               ON m.simkl_id = meta.id AND meta.type = 'simkl'
            WHERE watched = 1
            ORDER BY last_watched_at DESC
            LIMIT {self.page_limit} OFFSET {self.page_limit * (page - 1)}
            """
        )

    @guard_against_none(list)
    def get_movies_by_simkl_status(self, status: str) -> list[dict]:
        rows = self.fetchall(
            "SELECT simkl_id FROM movies WHERE simkl_status = ?",
            (status,),
        )
        if not rows:
            rows = self.fetchall("SELECT simkl_id, info FROM movies")
            rows = [
                {"simkl_id": row["simkl_id"]}
                for row in rows
                if isinstance(row.get("info"), dict) and row["info"].get("simkl_status") == status
            ]
        refs = [{"simkl_id": row["simkl_id"]} for row in rows]
        if not refs:
            return refs
        from resources.lib.simkl.library_sort import sort_library_refs

        return sort_library_refs(refs, "movie", status)

    @guard_against_none()
    def mark_movie_watched(self, simkl_id):
        play_count = self.fetchone("SELECT watched FROM movies WHERE simkl_id=?", (simkl_id,))["watched"]
        self._mark_movie_record("watched", play_count + 1, simkl_id)

    @guard_against_none()
    def mark_movie_unwatched(self, simkl_id):
        self._mark_movie_record("watched", 0, simkl_id)

    def refresh_movie_watch_state(self, simkl_id: int) -> bool:
        """Pull movie watched/list status from Simkl for one library row."""
        from resources.lib.simkl.remote_state import fetch_remote_item_state, reconcile_local_item_state

        row = self.fetchone("SELECT simkl_id, info FROM movies WHERE simkl_id=?", (int(simkl_id),))
        if not row:
            return False
        info = {
            "simkl_id": int(simkl_id),
            "mediatype": "movie",
            "info": row.get("info") if isinstance(row.get("info"), dict) else {},
        }
        remote = fetch_remote_item_state(info)
        if remote is None:
            return False
        reconcile_local_item_state(info, remote)
        return remote.matched

    @guard_against_none()
    def _mark_movie_record(self, column, value, simkl_id):
        if column != "watched":
            raise TypeError("NoneType Error: Date Time Column")
        self.execute_sql(
            "UPDATE movies SET watched=?, last_watched_at=? WHERE simkl_id=?",
            (value, self._get_datetime_now() if value > 0 else None, simkl_id),
        )

    def _fetch_movie_summary(self, simkl_id):
        from resources.lib.simkl.ids import movie_api_path

        return self.simkl_api.get_json_cached(
            movie_api_path(int(simkl_id)),
            authorized=False,
            client_id=self.simkl_api.client_id,
        )

    @guard_against_none(list)
    def get_movie(self, simkl_id):
        return self.get_movie_list([self._get_single_movie_meta(simkl_id)], hide_unaired=False, hide_watched=False)[0]

    @guard_against_none()
    def _get_single_movie_meta(self, simkl_id):
        return self._get_single_meta(simkl_id, "movies")

    @guard_against_none_or_empty()
    def _update_movies(self, list_to_update):
        get = MetadataHandler.get_simkl_info

        sql_statement = f"""
            WITH requested(simkl_id, last_updated) AS (VALUES
                    {','.join(f"({i.get('simkl_id')},'{get(i, 'dateadded')}')" for i in list_to_update)})
            SELECT r.simkl_id,
                   simkl_meta.value      AS simkl_object,
                   simkl_meta.meta_hash  AS simkl_meta_hash,
                   tmdb_id,
                   tmdb.value       AS tmdb_object,
                   tmdb.meta_hash   AS tmdb_meta_hash,
                   tvdb_id,
                   tvdb.value       AS tvdb_object,
                   tvdb.meta_hash   AS tvdb_meta_hash,
                   fanart.value     AS fanart_object,
                   fanart.meta_hash AS fanart_meta_hash,
                   m.needs_update
            FROM requested as r
                     LEFT JOIN movies AS m
                               ON r.simkl_id = m.simkl_id
                     LEFT JOIN movies_meta AS simkl_meta
                               ON simkl_meta.id = m.simkl_id AND simkl_meta.type = 'simkl'
                     LEFT JOIN movies_meta AS tmdb
                               ON tmdb.id = m.tmdb_id AND tmdb.type = 'tmdb'
                     LEFT JOIN movies_meta AS tvdb
                               ON tvdb.id = m.tvdb_id AND tvdb.type = 'tvdb'
                     LEFT JOIN movies_meta AS fanart
                               ON fanart.id = m.tmdb_id AND fanart.type = 'fanart'
            """

        db_list_to_update = self.fetchall(sql_statement)
        self._apply_request_force_update(db_list_to_update, list_to_update)

        for movie in db_list_to_update:
            movie["_entity"] = "movie"
            self.task_queue.put(self.metadataHandler.update, movie)
        updated_items = self.task_queue.wait_completion()

        if not updated_items:
            return

        self.task_queue.put(
            self.save_to_meta_table,
            (i for i in updated_items if "tmdb_object" in i),
            "movies",
            "tmdb",
            "tmdb_id",
        )
        self.task_queue.put(
            self.save_to_meta_table,
            (i for i in updated_items if "tvdb_object" in i),
            "movies",
            "tvdb",
            "tvdb_id",
        )
        self.task_queue.put(
            self.save_to_meta_table,
            (i for i in updated_items if "fanart_object" in i),
            "movies",
            "fanart",
            "tmdb_id",
        )
        self.task_queue.wait_completion()

        formatted_items = self.metadataHandler.format_db_object(updated_items)

        movie_rows = [
            (
                i["info"]["simkl_id"],
                i["info"],
                i.get("art"),
                i.get("cast"),
                i["info"].get("playcount") or 0,
                i["info"].get("aired"),
                i["info"].get("dateadded"),
                i["info"].get("tmdb_id"),
                i["info"].get("tvdb_id"),
                i["info"].get("imdb_id"),
                self.metadataHandler.meta_hash,
                self._create_args(i),
                i["info"].get("last_watched_at"),
                i["info"].get("user_rating"),
                self.get_library_status(i["info"]["simkl_id"], "movie", i["info"]),
            )
            for i in formatted_items
        ]
        self.execute_sql(
            self.upsert_movie_query,
            movie_rows,
        )
