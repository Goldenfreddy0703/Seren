"""Simkl context menu — watch history, list status, playback."""
from __future__ import annotations

import time
from functools import cached_property

import xbmcgui

from resources.lib.modules.globals import g
from resources.lib.database.session import get_sync_database
from resources.lib.simkl.library_status import (
    apply_local_library_status,
    apply_local_status_after_watch,
    queue_library_sync,
    _library_info,
)
from resources.lib.simkl.payloads import (
    info_to_history_payload,
    info_to_list_payload,
    info_to_ratings_payload,
    ratings_force_show,
)
from resources.lib.simkl.statuses import (
    current_simkl_status,
    in_simkl_library,
    library_catalog,
    library_row_id,
    resolved_list_status_from_response,
    resolved_watched_status_from_response,
    status_label,
    status_options_for_info,
    movie_show_mark_watched,
    effective_list_status,
    on_simkl_watchlist,
)
from resources.lib.simkl.remote_state import (
    fetch_remote_item_state,
    reconcile_local_item_state,
)


class SimklContextMenu:
    def __init__(self, item_information):
        self._action_args = item_information.get("action_args") or {}
        self._library_status = self._action_args.get("library_status")
        item_type = self._action_args.get("mediatype", "").lower()
        if item_type == "movies":
            item_type = "movie"
        simkl_id = item_information["simkl_id"]

        self._confirm_item_information(item_information)
        self._remote_state = fetch_remote_item_state(_library_info(item_information))
        reconcile_local_item_state(item_information, self._remote_state)
        self.dialog_list = []
        self._handle_watched_options(item_information, item_type)
        self._handle_library_options(item_information)
        self._handle_rating_options(item_information)

        self.dialog_list.append(g.get_language_string(30283))
        self._handle_progress_option(item_type, simkl_id)

        selection = xbmcgui.Dialog().select(
            f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
            self.dialog_list,
        )
        if selection == -1:
            return

        options = {
            g.get_language_string(30278): {
                "method": self._mark_watched,
                "info_key": "info",
            },
            g.get_language_string(30279): {
                "method": self._mark_unwatched,
                "info_key": "info",
            },
            g.get_language_string(30753): {
                "method": self._move_to_list,
                "info_key": "info",
            },
            g.get_language_string(30762): {
                "method": self._add_to_library,
                "info_key": "info",
            },
            g.get_language_string(30757): {
                "method": self._rate_item,
                "info_key": "info",
            },
            g.get_language_string(30758): {
                "method": self._clear_rating,
                "info_key": "info",
            },
            g.get_language_string(30754): {
                "method": self._remove_from_library,
                "info_key": "info",
            },
            g.get_language_string(30283): {
                "method": self._refresh_meta_information,
            },
            g.get_language_string(30284): {
                "method": self._remove_playback_history,
                "info_key": "info",
            },
        }

        selected_option = self.dialog_list[selection]
        if selected_option not in options:
            return
        selected_option = options[selected_option]
        info_key = selected_option.get("info_key")
        if info_key:
            selected_option["method"](item_information[info_key])
        else:
            selected_option["method"](item_information)

    @cached_property
    def simkl_api(self):
        from resources.lib.indexers.simkl import SimklAPI

        return SimklAPI()

    def _handle_progress_option(self, item_type, simkl_id):
        from resources.lib.database.session import get_sync_database

        if item_type not in ["show", "season"] and get_sync_database().get_bookmark(simkl_id):
            self.dialog_list.append(g.get_language_string(30284))

    def _handle_library_options(self, item_information):
        info = _library_info(item_information)
        on_list = on_simkl_watchlist(
            info, library_status=self._library_status, remote=self._remote_state
        )
        if on_list:
            self.dialog_list.append(g.get_language_string(30753))
            self.dialog_list.append(g.get_language_string(30754))
        else:
            self.dialog_list.append(g.get_language_string(30762))
            if in_simkl_library(item_information) and (
                self._remote_state is None or self._remote_state.in_library
            ):
                self.dialog_list.append(g.get_language_string(30754))

    def _handle_rating_options(self, item_information):
        self.dialog_list.append(g.get_language_string(30757))
        if self._current_user_rating(item_information) is not None:
            self.dialog_list.append(g.get_language_string(30758))

    @staticmethod
    def _current_user_rating(item_or_info) -> int | None:
        if isinstance(item_or_info, dict) and "action_args" in item_or_info:
            info = item_or_info.get("info") or {}
            rating = item_or_info.get("user_rating")
        else:
            info = item_or_info or {}
            rating = info.get("user_rating")
        if rating is None and isinstance(info, dict):
            rating = info.get("user_rating")
        if rating is not None:
            try:
                return int(rating)
            except (TypeError, ValueError):
                pass
        mediatype = (info.get("mediatype") or "").lower()
        if mediatype not in ("episode", "season"):
            return None
        show_id = SimklContextMenu._get_show_id(info)
        if not show_id:
            return None
        from resources.lib.database.session import get_sync_database

        row = get_sync_database().fetchone(
            "SELECT user_rating FROM shows WHERE simkl_id=?",
            (int(show_id),),
        )
        if not row or row.get("user_rating") is None:
            return None
        return int(row["user_rating"])

    @staticmethod
    def _persist_user_rating(item_information, rating: int | None) -> None:
        row_id = library_row_id(item_information)
        if not row_id:
            return
        from resources.lib.database.session import get_sync_database

        get_sync_database().set_user_rating(row_id, library_catalog(item_information), rating)

    @staticmethod
    def _ratings_success(response) -> bool:
        if not response:
            return False
        added = response.get("added") or {}
        if added.get("statuses"):
            return True
        for key in ("movies", "shows", "anime"):
            count = added.get(key)
            if isinstance(count, int) and count > 0:
                return True
        return False

    def _rate_item(self, item_information):
        labels = [str(score) for score in range(1, 11)]
        current = self._current_user_rating(item_information)
        heading = g.get_language_string(30757)
        if current is not None:
            heading = f"{heading} ({g.get_language_string(30759).format(current)})"

        selection = xbmcgui.Dialog().select(
            f"{g.ADDON_NAME}: {heading}",
            labels,
        )
        if selection == -1:
            return

        rating = selection + 1
        force_show = ratings_force_show(item_information)
        payload = info_to_ratings_payload(item_information, rating, force_show=force_show)
        response = self.simkl_api.add_ratings(payload)
        if not self._ratings_success(response):
            g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
            return

        self._persist_user_rating(item_information, rating)
        resolved = resolved_watched_status_from_response(response, item_information)
        if resolved:
            apply_local_library_status(item_information, resolved)
        queue_library_sync()
        g.notification(
            f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
            g.get_language_string(30760).format(rating),
        )
        g.container_refresh()
        g.trigger_widget_refresh()

    def _clear_rating(self, item_information):
        force_show = ratings_force_show(item_information)
        payload = info_to_history_payload(item_information, force_show=force_show)
        response = self.simkl_api.remove_ratings(payload)
        if response is None:
            g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
            return

        self._persist_user_rating(item_information, None)
        queue_library_sync()
        g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30761))
        g.container_refresh()
        g.trigger_widget_refresh()

    def _handle_watched_options(self, item_information, item_type):
        if item_type in ("movie", "movies"):
            info = _library_info(item_information)
            if movie_show_mark_watched(
                info, library_status=self._library_status, remote=self._remote_state
            ):
                self.dialog_list.append(g.get_language_string(30278))
            else:
                self.dialog_list.append(g.get_language_string(30279))
            return
        if item_type == "episode":
            if item_information["play_count"] > 0:
                self.dialog_list.append(g.get_language_string(30279))
            else:
                self.dialog_list.append(g.get_language_string(30278))
        elif self._season_or_show_mark_watched(item_information):
            self.dialog_list.append(g.get_language_string(30278))
        else:
            self.dialog_list.append(g.get_language_string(30279))

    @staticmethod
    def _season_or_show_mark_watched(item_information: dict) -> bool:
        """True when Simkl manager should offer Mark as Watched for a season or show."""
        watched = int(item_information.get("watched_episodes") or 0)
        unwatched_raw = item_information.get("unwatched_episodes")
        if unwatched_raw is not None:
            try:
                unwatched = int(unwatched_raw)
            except (TypeError, ValueError):
                unwatched = 0
            if unwatched > 0:
                return True
            if watched > 0:
                return False
            return True
        return watched <= 0

    @staticmethod
    def _confirm_item_information(item_information):
        if item_information is None:
            raise TypeError("Invalid item information passed to Simkl Manager")

    @staticmethod
    def _persist_simkl_status(item_information, status: str | None) -> None:
        apply_local_library_status(item_information, status)

    @staticmethod
    def _refresh_meta_information(item_information):
        from resources.lib.database.session import get_sync_database

        ok = get_sync_database().refresh_item_metadata(item_information)
        refreshed = g.refresh_visible_container()
        if not refreshed:
            g.container_refresh()
        g.trigger_widget_refresh(if_playing=False)
        if ok:
            g.notification(
                f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                g.get_language_string(30283),
            )
        else:
            g.notification(
                f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                g.get_language_string(30287),
            )

    @staticmethod
    def _history_success(response, key: str) -> bool:
        if not response:
            return False
        added = response.get("added") or {}
        if isinstance(added.get(key), int) and added[key] > 0:
            return True
        if isinstance(added.get(key), list) and added[key]:
            return True
        if added.get("statuses"):
            return True
        return bool(added.get("episodes") or added.get("shows") or added.get("movies"))

    @staticmethod
    def _remove_success(response, key: str) -> bool:
        if not response:
            return False
        deleted = response.get("deleted") or response.get("removed") or {}
        if isinstance(deleted.get(key), int) and deleted[key] > 0:
            return True
        return bool(deleted)

    @staticmethod
    def _get_show_id(item_information):
        from resources.lib.simkl.ids import show_id_from_info

        info = (
            item_information.get("info")
            if isinstance(item_information, dict) and "info" in item_information
            else item_information
        )
        mediatype = info.get("mediatype")
        if mediatype == "tvshow":
            return info.get("simkl_id")
        return show_id_from_info(info)

    def _mark_watched(self, item_information, silent=False):
        payload = info_to_history_payload(item_information)
        response = self.simkl_api.add_to_history(payload)

        if item_information["mediatype"] == "movie":
            if not self._history_success(response, "movies"):
                g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
                return
            get_sync_database().mark_movie_watched(item_information["simkl_id"])
        else:
            if not self._history_success(response, "shows") and not self._history_success(response, "anime"):
                g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
                return
            if item_information["mediatype"] == "episode":
                show_id = self._get_show_id(item_information)
                get_sync_database().mark_episode_watched(
                    show_id,
                    item_information["season"],
                    item_information["episode"],
                )
            elif item_information["mediatype"] == "season":
                show_id = self._get_show_id(item_information)
                get_sync_database().mark_season_watched(
                    show_id,
                    item_information["season"],
                    1,
                )
            elif item_information["mediatype"] == "tvshow":
                get_sync_database().mark_show_watched(item_information["simkl_id"], 1)

        resolved = resolved_watched_status_from_response(response, item_information)
        if resolved:
            apply_local_library_status(item_information, resolved, touch_last_watched=True)
        else:
            apply_local_status_after_watch(item_information)

        show_id = self._get_show_id(item_information)
        mediatype = item_information.get("mediatype")
        if show_id is not None and mediatype == "tvshow":
            try:
                get_sync_database().refresh_show_episode_watch_state(int(show_id), force=True)
            except Exception:
                g.log_stacktrace()

        g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30288))
        if not silent:
            if show_id is not None:
                g.set_runtime_setting(f"episode_watch_refresh_cooldown_{int(show_id)}", str(int(time.time())))
            queue_library_sync(user_initiated=False)
            refreshed = g.refresh_visible_container()
            if not refreshed:
                g.container_refresh()
            g.trigger_widget_refresh(if_playing=False)

    def _mark_unwatched(self, item_information):
        payload = info_to_history_payload(item_information)
        response = self.simkl_api.remove_from_history(payload)

        if item_information["mediatype"] == "movie":
            if not self._remove_success(response, "movies"):
                g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
                return
            get_sync_database().mark_movie_unwatched(item_information["simkl_id"])
        else:
            if not self._remove_success(response, "episodes"):
                g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
                return
            if item_information["mediatype"] == "episode":
                show_id = self._get_show_id(item_information)
                get_sync_database().mark_episode_unwatched(
                    show_id,
                    item_information["season"],
                    item_information["episode"],
                )
            elif item_information["mediatype"] == "season":
                show_id = self._get_show_id(item_information)
                get_sync_database().mark_season_watched(
                    show_id,
                    item_information["season"],
                    0,
                )
            elif item_information["mediatype"] == "tvshow":
                get_sync_database().mark_show_watched(item_information["simkl_id"], 0)

        get_sync_database().remove_bookmark(item_information["simkl_id"])
        g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30289))
        show_id = self._get_show_id(item_information)
        if show_id is not None:
            g.set_runtime_setting(f"episode_watch_refresh_cooldown_{int(show_id)}", str(int(time.time())))
        queue_library_sync(user_initiated=False)
        refreshed = g.refresh_visible_container()
        if not refreshed:
            g.container_refresh()
        g.trigger_widget_refresh(if_playing=False)

    def _add_to_library(self, item_information):
        self._change_list_status(item_information, exclude_current=False, dialog_string_id=30762)

    def _move_to_list(self, item_information):
        self._change_list_status(item_information, exclude_current=True, dialog_string_id=30753)

    def _change_list_status(self, item_information, *, exclude_current: bool, dialog_string_id: int):
        info = _library_info(item_information)
        options = status_options_for_info(
            info,
            exclude_current=exclude_current,
            library_status=self._library_status,
            remote=self._remote_state,
        )
        if not options:
            g.notification(
                f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
                g.get_language_string(30756),
            )
            return

        selection = xbmcgui.Dialog().select(
            f"{g.ADDON_NAME}: {g.get_language_string(dialog_string_id)}",
            [g.get_language_string(label_id) for _, label_id in options],
        )
        if selection == -1:
            return

        status = options[selection][0]
        payload = info_to_list_payload(item_information, status, force_show=True)
        response = self.simkl_api.add_to_list(payload)
        if response is None:
            g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
            return

        resolved = resolved_list_status_from_response(response, requested=status)
        apply_local_library_status(item_information, resolved)
        queue_library_sync()
        g.notification(
            f"{g.ADDON_NAME}: {g.get_language_string(30286)}",
            g.get_language_string(30294).format(status_label(resolved)),
        )
        g.container_refresh()
        g.trigger_widget_refresh()

    def _remove_from_library(self, item_information):
        payload = info_to_history_payload(item_information, force_show=True)
        response = self.simkl_api.remove_from_history(payload)
        if item_information["mediatype"] == "movie":
            ok = self._remove_success(response, "movies")
        else:
            ok = (
                self._remove_success(response, "shows")
                or self._remove_success(response, "anime")
                or self._remove_success(response, "episodes")
            )
        if not ok:
            g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30287))
            return

        self._persist_simkl_status(item_information, None)
        queue_library_sync()
        g.container_refresh()
        g.notification(f"{g.ADDON_NAME}: {g.get_language_string(30286)}", g.get_language_string(30755))
        g.trigger_widget_refresh()

    def _remove_playback_history(self, item_information):
        media_type = "episode" if item_information["mediatype"] != "movie" else "movie"
        progress = self.simkl_api.get_playback(media_type) or []
        if isinstance(progress, dict):
            progress = progress.get("movies") or progress.get("episodes") or []

        if not progress:
            return

        target_id = item_information["simkl_id"]
        for entry in progress:
            if media_type == "movie":
                movie = entry.get("movie") or entry
                simkl_id = (movie.get("ids") or {}).get("simkl")
                playback_id = entry.get("id")
            else:
                episode = entry.get("episode") or {}
                simkl_id = (episode.get("ids") or {}).get("simkl_id") or (episode.get("ids") or {}).get("simkl")
                playback_id = entry.get("id")
            if simkl_id and int(simkl_id) == int(target_id) and playback_id:
                self.simkl_api.delete_playback(playback_id)

        from resources.lib.database.simkl_sync.bookmark import SimklSyncDatabase

        get_sync_database().remove_bookmark(item_information["simkl_id"])
        g.container_refresh()
        g.notification(g.ADDON_NAME, g.get_language_string(30301))
        g.trigger_widget_refresh()
