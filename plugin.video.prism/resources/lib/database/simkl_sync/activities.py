"""Simkl activities sync — library, watched episodes, and playback bookmarks."""
from __future__ import annotations

import time

import xbmc
import xbmcgui

from resources.lib.database.simkl_sync import shows
from resources.lib.modules.exceptions import ActivitySyncFailure
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g
from resources.lib.modules.timeLogger import stopwatch
from resources.lib.simkl.library import _unwrap_sync_items, simkl_entry_to_sync_dict, sync_entry_media_blob


class SimklSyncDatabase(shows.SimklSyncDatabase):
    sync_errors = False

    def __init__(self):
        super().__init__()
        self.progress_dialog = None
        self.silent = True
        self.force_sync = False
        self.current_dialog_text = None
        self._remote_activities = None
        # Playback bookmarks are cheap GETs — run before the heavy all-items + episode warm.
        self._sync_activities_list = [
            (
                "Movie bookmarks",
                ("movies", "playback"),
                "movies_bookmarked",
                self._sync_movie_bookmarks,
            ),
            (
                "Show bookmarks",
                ("tv_shows", "playback"),
                "episodes_bookmarked",
                self._sync_show_bookmarks,
            ),
            ("Simkl watchlist", None, None, self._sync_simkl_library_activity),
        ]

    def _fetch_remote_activities_payload(self, *, force: bool = False):
        from resources.lib.simkl.remote_activities import get_activities_payload

        return get_activities_payload(force=force)

    def fetch_remote_activities(self, silent=False, force=False):
        self.refresh_activities()
        if "last_activities_call" not in self.activities:
            g.log("Last activities call timestamp not present in database, migrating database change")
            self._insert_last_activities_column()
            self.refresh_activities()
            last_activities_call = 0
        else:
            last_activities_call = self.activities["last_activities_call"]

        if not force and time.time() < (last_activities_call + (5 * 60)):
            g.log("Activities endpoint called too frequently, skipping sync", "info")
            return None

        remote_activities = self._fetch_remote_activities_payload(force=force)
        self._update_last_activities_call()
        return remote_activities

    @stopwatch
    def sync_activities(self, silent=False, force=False):
        with GlobalLock("simkl.sync"):
            self.force_sync = force
            self.silent = silent
            simkl_auth = g.get_setting("simkl.auth")
            update_time = str(self._get_datetime_now())

            if not simkl_auth:
                g.log("SimklSync: No Simkl auth present, no sync will occur", "warning")
                return

            self.refresh_activities()
            remote_activities = self.fetch_remote_activities(silent, force=force)

            if remote_activities is None:
                g.log(
                    "Activities Sync Failure: Unable to connect to Simkl or activities called too often",
                    "error",
                )
                return True

            remote_all = remote_activities.get("all") or remote_activities.get("all_activities")
            library_changed = self.requires_update(remote_all, self.activities["all_activities"])
            playback_changed = self._playback_needs_sync(remote_activities)

            if library_changed or playback_changed:
                try:
                    if library_changed:
                        self._check_for_first_run(silent, simkl_auth)
                        self._do_sync_activities(remote_activities)
                    else:
                        self._do_sync_bookmark_activities(remote_activities)
                finally:
                    self._finalize_process(update_time)

            self._update_all_shows_statisics()
            self._update_all_season_statistics()

        try:
            from resources.lib.meta.enrichment import MetaEnrichmentQueue

            MetaEnrichmentQueue.schedule_needs_update()
        except Exception:
            g.log_stacktrace()

        return self.sync_errors

    def _finalize_process(self, update_time):
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            del self.progress_dialog
            self.progress_dialog = None

        if not self.sync_errors:
            self._update_activity_record("all_activities", update_time)
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=widgetRefresh&playing=False")')

    def _do_sync_activities(self, remote_activities):
        self._remote_activities = remote_activities
        if not self.silent and self.progress_dialog is None:
            self.progress_dialog = xbmcgui.DialogProgressBG()
            self.progress_dialog.create(f"{g.ADDON_NAME} Sync", "Prism: Simkl Sync")
        total_activities = len(self._sync_activities_list)
        for idx, activity in enumerate(self._sync_activities_list):
            try:
                update_time = str(self._get_datetime_now())

                if g.abort_requested():
                    return
                self.current_dialog_text = f"Syncing {activity[0]}"
                self._update_progress(int(float(idx + 1) / total_activities * 100))

                if activity[1] is not None:
                    if activity[0] == "Show bookmarks":
                        if not self._show_bookmarks_need_sync(remote_activities) and not self._playback_bookmarks_empty(
                            "episode"
                        ):
                            g.log(f"Skipping {activity[0]}, does not require update")
                            continue
                    else:
                        section, field = activity[1]
                        last_activity_update = (remote_activities.get(section) or {}).get(field)
                        if not last_activity_update and not self._playback_bookmarks_empty("movie"):
                            g.log(f"Skipping {activity[0]}, remote activity timestamp missing")
                            continue
                        if (
                            not self.force_sync
                            and last_activity_update
                            and not self.requires_update(last_activity_update, self.activities[activity[2]])
                            and not self._playback_bookmarks_empty("movie")
                        ):
                            g.log(f"Skipping {activity[0]}, does not require update")
                            continue

                g.log(f"Running Activity: {activity[0]}")
                activity[3]()
                if activity[2]:
                    self._update_activity_record(activity[2], update_time)
            except ActivitySyncFailure as exc:
                g.log(f"Failed to sync activity: {activity[0]} - {exc}")
                self.sync_errors = True
                continue

    def _playback_needs_sync(self, remote_activities) -> bool:
        movies_at = (remote_activities.get("movies") or {}).get("playback")
        if movies_at and self.requires_update(movies_at, self.activities["movies_bookmarked"]):
            return True
        return self._show_bookmarks_need_sync(remote_activities)

    def _movie_bookmarks_need_sync(self, remote_activities) -> bool:
        if self._playback_bookmarks_empty("movie"):
            return True
        movies_at = (remote_activities.get("movies") or {}).get("playback")
        return bool(movies_at and self.requires_update(movies_at, self.activities["movies_bookmarked"]))

    def _playback_bookmarks_empty(self, bookmark_type: str) -> bool:
        row = self.fetchone(
            "SELECT 1 FROM bookmarks WHERE type=? LIMIT 1",
            (bookmark_type,),
        )
        return row is None

    def _do_sync_bookmark_activities(self, remote_activities):
        """Simkl can bump playback timestamps without changing `all` — sync bookmarks only."""
        self._remote_activities = remote_activities
        bookmark_activities = [
            activity for activity in self._sync_activities_list if activity[0].endswith("bookmarks")
        ]
        total_activities = len(bookmark_activities)
        for idx, activity in enumerate(bookmark_activities):
            try:
                update_time = str(self._get_datetime_now())

                if g.abort_requested():
                    return
                self.current_dialog_text = f"Syncing {activity[0]}"
                self._update_progress(int(float(idx + 1) / max(total_activities, 1) * 100))

                if activity[0] == "Show bookmarks":
                    if not self._show_bookmarks_need_sync(remote_activities):
                        g.log(f"Skipping {activity[0]}, does not require update")
                        continue
                elif not self._movie_bookmarks_need_sync(remote_activities):
                    g.log(f"Skipping {activity[0]}, does not require update")
                    continue

                g.log(f"Running Activity: {activity[0]}")
                activity[3]()
                if activity[2]:
                    self._update_activity_record(activity[2], update_time)
            except ActivitySyncFailure as exc:
                g.log(f"Failed to sync activity: {activity[0]} - {exc}")
                self.sync_errors = True
                continue

    def _sync_simkl_library_activity(self):
        self._sync_simkl_library(self._remote_activities)

    def _sync_movie_bookmarks(self):
        try:
            from resources.lib.simkl.playback import sync_movie_playbacks

            sync_movie_playbacks(self)
        except Exception as exc:
            raise ActivitySyncFailure(exc) from exc

    def _sync_show_bookmarks(self):
        try:
            from resources.lib.simkl.playback import sync_episode_playbacks

            sync_episode_playbacks(self)
        except Exception as exc:
            raise ActivitySyncFailure(exc) from exc

    def _show_bookmarks_need_sync(self, remote_activities) -> bool:
        if self._playback_bookmarks_empty("episode"):
            return True
        local = self.activities["episodes_bookmarked"]
        for section in ("tv_shows", "anime"):
            playback_at = (remote_activities.get(section) or {}).get("playback")
            if playback_at and self.requires_update(playback_at, local):
                return True
        return False

    def _check_for_first_run(self, silent, simkl_auth):
        if not silent and str(self.activities["all_activities"]) == self.base_date and simkl_auth is not None:
            g.notification(g.ADDON_NAME, g.get_language_string(30177))
            xbmc.sleep(500)
            self.silent = False
            self.progress_dialog = xbmcgui.DialogProgressBG()
            self.progress_dialog.create(f"{g.ADDON_NAME}Sync", "Prism: Simkl Sync")

    def set_sync_progress(self, percent: int, message: str) -> None:
        self.current_dialog_text = message
        self._update_progress(percent, message)

    def _sync_simkl_library(self, remote_activities):
        from resources.lib.simkl.all_items_sync import sync_simkl_library

        sync_simkl_library(self, remote_activities, force=self.force_sync)

    def _removed_from_list_changed(self, remote_activities) -> bool:
        local_all = self.activities["all_activities"]
        for section in ("movies", "tv_shows", "anime"):
            section_data = remote_activities.get(section) or {}
            removed_at = section_data.get("removed_from_list")
            if removed_at and self.requires_update(removed_at, local_all):
                return True
        return False

    def _reconcile_removed_items(self):
        payload = self.simkl_api.get_all_items(extended="simkl_ids_only")
        if not payload:
            return

        active_movie_ids = set()
        for entry in _unwrap_sync_items(payload, "movies"):
            if not isinstance(entry, dict):
                continue
            blob = sync_entry_media_blob(entry, "movies")
            simkl_id = (blob.get("ids") or {}).get("simkl")
            if simkl_id:
                active_movie_ids.add(int(simkl_id))
        active_show_ids = set()
        for media_key in ("shows", "anime"):
            for entry in _unwrap_sync_items(payload, media_key):
                if not isinstance(entry, dict):
                    continue
                blob = sync_entry_media_blob(entry, media_key)
                simkl_id = (blob.get("ids") or {}).get("simkl")
                if simkl_id:
                    active_show_ids.add(int(simkl_id))

        local_movies = self.fetchall("SELECT simkl_id FROM movies")
        stale_movies = [row["simkl_id"] for row in local_movies if row["simkl_id"] not in active_movie_ids]
        if stale_movies:
            placeholders = ",".join("?" * len(stale_movies))
            movie_params = tuple(stale_movies)
            self.execute_sql(f"DELETE FROM movies WHERE simkl_id IN ({placeholders})", movie_params)
            self.execute_sql(f"DELETE FROM movies_meta WHERE id IN ({placeholders})", movie_params)

        local_shows = self.fetchall("SELECT simkl_id FROM shows")
        stale_shows = [row["simkl_id"] for row in local_shows if row["simkl_id"] not in active_show_ids]
        if stale_shows:
            placeholders = ",".join("?" * len(stale_shows))
            show_params = tuple(stale_shows)
            self.execute_sql(f"DELETE FROM episodes WHERE simkl_show_id IN ({placeholders})", show_params)
            self.execute_sql(f"DELETE FROM seasons WHERE simkl_show_id IN ({placeholders})", show_params)
            self.execute_sql(f"DELETE FROM shows WHERE simkl_id IN ({placeholders})", show_params)
            self.execute_sql(f"DELETE FROM shows_meta WHERE id IN ({placeholders})", show_params)

    def _process_all_items_payload(self, payload, catalog: str | None = None):
        if catalog == "movie" or (catalog is None and payload.get("movies")):
            self._process_movie_entries(_unwrap_sync_items(payload, "movies"))

        if catalog == "tv" or (catalog is None and payload.get("shows")):
            self._process_show_entries(_unwrap_sync_items(payload, "shows"), "tv")

        if catalog == "anime" or (catalog is None and payload.get("anime")):
            self._process_show_entries(_unwrap_sync_items(payload, "anime"), "anime")

    def _process_movie_entries(self, entries):
        movies = []
        watched_ids = []
        for entry in entries:
            normalized = simkl_entry_to_sync_dict(entry, "movie")
            if not normalized:
                continue
            info = normalized["simkl_object"]["info"]
            if entry.get("status"):
                info["simkl_status"] = entry.get("status")
            status = entry.get("status")
            if status == "completed":
                info["last_watched_at"] = entry.get("last_watched_at") or entry.get("added_to_watchlist_at")
                info["watched"] = 1
                watched_ids.append(normalized["simkl_id"])
            elif status in ("plantowatch", "dropped", "hold", "watching"):
                info["watched"] = 0
            elif entry.get("last_watched_at"):
                info["last_watched_at"] = entry.get("last_watched_at")
                info["watched"] = 1
                watched_ids.append(normalized["simkl_id"])
            movies.append(normalized)

        if not movies:
            return

        self.insert_simkl_movies(movies)
        if watched_ids:
            placeholders = ",".join(str(i) for i in watched_ids)
            if str(self.activities["all_activities"]) == self.base_date:
                self.execute_sql("UPDATE movies SET watched=0")
            self.execute_sql(f"UPDATE movies SET watched=1 WHERE simkl_id IN ({placeholders})")

    def _process_show_entries(self, entries, catalog: str):
        shows = []
        for entry in entries:
            normalized = simkl_entry_to_sync_dict(entry, catalog)
            if not normalized:
                continue
            info = normalized["simkl_object"]["info"]
            info["catalog"] = catalog
            if entry.get("status"):
                info["simkl_status"] = entry.get("status")
            if entry.get("last_watched_at"):
                info["last_watched_at"] = entry.get("last_watched_at")
            if entry.get("watched_episodes_count") is not None:
                info["watched_episodes_count"] = entry.get("watched_episodes_count")
            if entry.get("total_episodes_count") is not None:
                info["total_episodes_count"] = entry.get("total_episodes_count")
            shows.append(normalized)

        if not shows:
            return

        self.insert_simkl_shows(shows)
        episode_entries = [
            entry
            for entry in (entries or [])
            if isinstance(entry, dict) and entry.get("status") in ("watching", "completed")
        ]
        if episode_entries:
            self.apply_sync_episode_stubs_from_entries(
                episode_entries, shows, catalog, selective=True
            )
            self.apply_next_watch_stubs_from_entries(episode_entries, shows, catalog)
            self.sync_show_watch_state_from_entries(episode_entries, shows)

    def _resolve_episode_simkl_id(self, show, episode):
        simkl_id = (episode.get("ids") or {}).get("simkl_id") or (episode.get("ids") or {}).get("simkl")
        if simkl_id:
            return int(simkl_id)

        show_id = (show.get("ids") or {}).get("simkl")
        if not show_id:
            return None

        from resources.lib.simkl.field_map import anime_menu_episode_number, anime_menu_season, tvdb_from_episode

        menu_season = anime_menu_season(episode)
        menu_episode = anime_menu_episode_number(
            episode,
            menu_season,
            episode.get("number") or episode.get("episode"),
        )
        if menu_episode is not None:
            row = self.fetchone(
                """
                SELECT simkl_id FROM episodes
                WHERE simkl_show_id = ? AND season = ? AND number = ?
                LIMIT 1
                """,
                (int(show_id), int(menu_season), int(menu_episode)),
            )
            if row:
                return int(row["simkl_id"])

        season = episode.get("season")
        number = episode.get("number")
        if number is None:
            number = episode.get("episode")
        if season is not None and number is not None:
            row = self.fetchone(
                """
                SELECT simkl_id FROM episodes
                WHERE simkl_show_id = ? AND season = ? AND number = ?
                LIMIT 1
                """,
                (int(show_id), int(season), int(number)),
            )
            if row:
                return int(row["simkl_id"])

        tvdb_season, tvdb_number = tvdb_from_episode(episode)
        if tvdb_season is not None and tvdb_number is not None:
            row = self.fetchone(
                """
                SELECT simkl_id FROM episodes
                WHERE simkl_show_id = ? AND season = ? AND number = ?
                LIMIT 1
                """,
                (int(show_id), int(tvdb_season), int(tvdb_number)),
            )
            if row:
                return int(row["simkl_id"])

        return None

    def _episode_duration_seconds(self, simkl_id, episode, show):
        row = self.fetchone("SELECT info FROM episodes WHERE simkl_id=?", (simkl_id,))
        if row and row.get("info"):
            info = row["info"]
            if info.get("duration"):
                try:
                    return int(float(info["duration"]))
                except (TypeError, ValueError):
                    pass
            if info.get("runtime"):
                try:
                    return int(float(info["runtime"]) * 60)
                except (TypeError, ValueError):
                    pass
        runtime = episode.get("runtime") or show.get("runtime") or 25
        try:
            return int(float(runtime) * 60)
        except (TypeError, ValueError):
            return 25 * 60

    def _movie_duration_seconds(self, simkl_id, movie):
        row = self.fetchone("SELECT info FROM movies WHERE simkl_id=?", (simkl_id,))
        if row and row.get("info"):
            info = row["info"]
            if info.get("duration"):
                try:
                    return int(float(info["duration"]))
                except (TypeError, ValueError):
                    pass
            if info.get("runtime"):
                try:
                    return int(float(info["runtime"]) * 60)
                except (TypeError, ValueError):
                    pass
        runtime = movie.get("runtime") or 90
        try:
            return int(float(runtime) * 60)
        except (TypeError, ValueError):
            return 90 * 60

    def _update_progress(self, progress, text=None):
        if not self.silent:
            if text:
                self.progress_dialog.update(progress, text)
            else:
                self.progress_dialog.update(progress, self.current_dialog_text)

    def _queue_with_progress(self, func, args):
        for idx, arg in enumerate(args):
            self.mill_task_queue.put(func, *arg)
            progress = int(float(idx + 1) / max(len(args), 1) * 80) + 10
            self._update_progress(progress)

    def _update_activity_record(self, record, time):
        self.execute_sql(f"UPDATE activities SET {record}=? WHERE sync_id=1", (time,))

    def clean_orphaned_metadata(self):
        super().clean_orphaned_metadata()

    def flush_activities(self, clear_meta=False):
        super().flush_activities(clear_meta=clear_meta)
