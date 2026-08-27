from __future__ import annotations

import copy
import random
import sys
import contextlib
from functools import cached_property
from urllib import parse

import xbmc
import xbmcgui

from resources.lib.common import tools
from resources.lib.database.skinManager import SkinManager
from resources.lib.gui.windows.persistent_background import PersistentBackground
from resources.lib.indexers.simkl import SimklAPI
from resources.lib.modules.globals import g
from resources.lib.modules.list_builder import ListBuilder
from resources.lib.modules.metadataHandler import MetadataHandler
from resources.lib.simkl.ids import (
    attach_tv_show_id,
    encode_action_args,
    show_id_from_item,
    show_id_from_playlist_action_args,
    slug_from_item,
)


class SmartPlay:
    """
    Provides smart operations for playback
    """

    def __init__(self, item_information):
        self.list_builder = ListBuilder()
        if not isinstance(item_information, dict):
            item_information = tools.get_item_information(item_information)
        elif "info" not in item_information:
            lookup_args = item_information.get("action_args") or item_information
            item_information = tools.get_item_information(lookup_args)
        self.item_information = item_information

        if not isinstance(self.item_information, dict):
            raise TypeError("Item Information is not a dictionary")
        if "info" not in self.item_information:
            action_args = (self.item_information.get("action_args") or {}) if isinstance(self.item_information, dict) else {}
            episode_id = action_args.get("simkl_id")
            raise ValueError(f"Unable to load metadata for episode {episode_id}")

        self.show_simkl_id = show_id_from_item(self.item_information)
        if not self.show_simkl_id and "action_args" in self.item_information:
            self.show_simkl_id = self._extract_show_id_from_args(self.item_information["action_args"])

        self.show_slug = slug_from_item(self.item_information)

        self.display_style = g.get_int_setting("smartplay.displaystyle")
        self.simkl_api = SimklAPI()

    @staticmethod
    def _extract_show_id_from_args(action_args):
        from resources.lib.simkl.ids import (
            normalize_action_args,
            show_id_for_episode_action,
            show_id_from_args,
        )

        action_args = normalize_action_args(action_args)
        if action_args["mediatype"] in ["tvshow", "movie"]:
            return action_args["simkl_id"]
        if action_args["mediatype"] == "season":
            return show_id_from_args(action_args)
        if action_args["mediatype"] == "episode":
            return show_id_for_episode_action(action_args)
        return None

    @cached_property
    def seasons_info(self):
        """
        Fetches all season information for current show from database
        :return:
        :rtype:
        """
        from resources.lib.database.session import get_sync_database

        seasons = {}
        for item in get_sync_database().get_season_list(
            self.show_simkl_id,
            skip_watch_refresh=True,
        ):
            if not isinstance(item, dict):
                continue
            info = MetadataHandler.info(item)
            season_num = info.get("season")
            if season_num is None:
                season_num = item.get("season")
            if season_num is None:
                continue
            info["episode_count"] = (
                item.get("episode_count") or info.get("episode_count") or info.get("aired_episodes")
            )
            seasons[season_num] = info
        return seasons

    def resume_show(self):
        """
        Identifies resume point for a show and plays from there
        :return:
        :rtype:
        """
        g.cancel_playback()
        g.close_all_dialogs()
        g.PLAYLIST.clear()

        if not self.show_simkl_id:
            g.log("Quick Resume: missing show id", "warning")
            return

        if not self.seasons_info:
            g.log("Quick Resume: no season data — open the show once so episodes can load", "warning")
            return

        window = None
        try:
            window = self._get_window()

            window.set_text(g.get_language_string(30060))
            window.show()

            window.set_text(g.get_language_string(30061))

            season_id, episode = self.get_resume_episode()

            window.set_text(g.get_language_string(30062))

            window.set_text(g.get_language_string(30063))

            self.build_playlist(season_id, episode)

            if g.PLAYLIST.size() == 0:
                g.log("Quick Resume: no episodes available to play", "warning")
                return

            window.set_text(g.get_language_string(30311))

            g.log(
                f"Beginning play from Season {season_id} Episode {episode}",
                "info",
            )

            window.close()
            xbmc.Player().play(g.PLAYLIST)
        finally:
            if window is not None:
                with contextlib.suppress(Exception):
                    window.close()
                del window

    def build_playlist(self, season_num=None, minimum_episode=None):
        """
        Uses available information to add relevant episodes to the current playlist
        :param season_num: Season number to build from
        :param minimum_episode: Minimum episodes to add from
        """
        action_args = self.item_information.get("action_args") or {}
        if action_args.get("external_play"):
            return self._build_external_playlist(season_num, minimum_episode)

        if season_num is None:
            season_num = self.item_information["info"]["season"]

        if minimum_episode is None:
            minimum_episode = int(self.item_information["info"]["episode"]) + 1

        try:
            for i in self.list_builder.episode_list_builder(
                self.show_simkl_id,
                season=season_num,
                minimum_episode=minimum_episode,
                smart_play=True,
                hide_unaired=True,
            ):
                g.PLAYLIST.add(url=i[0], listitem=i[1])
        except TypeError:
            g.log(
                "Unable to add more episodes to the playlist, they may not be available for the requested season",
                "warning",
            )
            return

    def prepare_external_playlist(self, url_extras: dict[str, str] | None = None) -> None:
        """Seed SmartPlay playlist for TMDb Helper external episode playback."""
        self._stored_external_url_extras = dict(url_extras or {})
        g.PLAYLIST.clear()
        self._add_playlist_episode(self.item_information, url_extras=self._stored_external_url_extras)

    def _external_url_extras(self) -> dict[str, str]:
        stored = getattr(self, "_stored_external_url_extras", None)
        if stored:
            return dict(stored)
        extras = self.item_information.get("external_play_url_extras")
        if isinstance(extras, dict):
            return {str(key): str(value) for key, value in extras.items()}
        return {}

    def _resolve_tmdb_show_id(self) -> int | None:
        info = self.item_information.get("info") or {}
        action_args = self.item_information.get("action_args") or {}
        for source in (action_args, info):
            for key in ("tmdb_id", "tmdb_show_id"):
                value = source.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue

        if not self.show_simkl_id:
            return None

        from resources.lib.database.session import get_sync_database

        show_row = get_sync_database().get_show(self.show_simkl_id)
        if not isinstance(show_row, dict):
            return None
        if show_row.get("tmdb_id") is not None:
            return int(show_row["tmdb_id"])
        show_info = show_row.get("info")
        if isinstance(show_info, dict) and show_info.get("tmdb_id") is not None:
            return int(show_info["tmdb_id"])
        return None

    def _external_season_episode_count(self, season_num: int) -> int | None:
        tmdb_show_id = self._resolve_tmdb_show_id()
        if tmdb_show_id is not None:
            from resources.lib.modules.tmdb_helper import fetch_tmdb_season_episodes

            episodes = fetch_tmdb_season_episodes(tmdb_show_id, int(season_num))
            if episodes:
                return len(episodes)

        season = self.seasons_info.get(season_num, {})
        episode_count = season.get("episode_count") or season.get("aired_episodes")
        if episode_count is not None:
            return int(episode_count)
        return None

    def _build_external_playlist(self, season_num=None, minimum_episode=None) -> None:
        if season_num is None:
            season_num = self.item_information["info"]["season"]
        if minimum_episode is None:
            minimum_episode = int(self.item_information["info"]["episode"]) + 1

        action_args = self.item_information.get("action_args") or {}
        catalog = action_args.get("catalog") or self.item_information.get("catalog") or "tv"
        url_extras = self._external_url_extras()
        tmdb_show_id = self._resolve_tmdb_show_id()
        if tmdb_show_id is None:
            g.log("External SmartPlay: missing TMDb show id for playlist build", "warning")
            return

        from resources.lib.modules.tmdb_helper import (
            build_external_episode_menu_item,
            fetch_tmdb_season_episodes,
        )

        added = 0
        for tmdb_episode in fetch_tmdb_season_episodes(tmdb_show_id, int(season_num)):
            ep_num = int(tmdb_episode.get("episode_number") or 0)
            if ep_num < int(minimum_episode):
                continue
            menu_item = build_external_episode_menu_item(
                int(self.show_simkl_id),
                int(season_num),
                ep_num,
                catalog,
                tmdb_show_id=tmdb_show_id,
                tmdb_episode=tmdb_episode,
            )
            self._add_playlist_episode(menu_item, url_extras=url_extras)
            added += 1

        if not added:
            g.log(
                f"External SmartPlay: no further TMDb episodes found for season {season_num}",
                "debug",
            )

    def _add_playlist_episode(self, menu_item: dict, url_extras: dict[str, str] | None = None) -> None:
        url_extras = dict(url_extras or {})
        info = menu_item.get("info") or {}
        name = info.get("title") or menu_item.get("name") or ""
        action_args = menu_item.get("action_args")
        if not isinstance(action_args, dict):
            action_args = encode_action_args(menu_item)

        entry = g.add_directory_item(
            name,
            action="getSources",
            menu_item=copy.deepcopy(menu_item),
            action_args=action_args,
            bulk_add=True,
            is_playable=True,
            **url_extras,
        )
        g.PLAYLIST.add(url=entry[0], listitem=entry[1])

    def get_resume_episode(self):
        """
        Identifies playback start for a show using local Continue Watching / Next Up first,
        then Simkl remote history as a fallback.
        :return: (Season, Episode) tuple
        :rtype: tuple
        """
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()

        if bookmarked := db.get_bookmarked_episode_for_show(self.show_simkl_id):
            g.log(
                f"Quick Resume: continue watching at S{bookmarked[0]:02d}E{bookmarked[1]:02d}",
                "info",
            )
            return bookmarked

        if local_next := db.get_next_episode_for_show(self.show_simkl_id):
            g.log(
                f"Quick Resume: next up at S{local_next[0]:02d}E{local_next[1]:02d}",
                "info",
            )
            season, episode = local_next
        else:
            season, episode = self._resume_episode_from_simkl_history()

        season_info = self.seasons_info.get(season)
        if season_info:
            episode_count = season_info.get("episode_count")
            if episode_count and episode > int(episode_count):
                season += 1
                episode = 1

        if self.final_episode_check(season, episode):
            season = 1
            episode = 1

        return season, episode

    def _resume_episode_from_simkl_history(self) -> tuple[int, int]:
        """Fallback: last Simkl cloud watch activity for this show."""
        get = MetadataHandler.get_simkl_info
        action = "watch"
        season = 1
        episode = 1

        history = self.simkl_api.get_json(f"sync/history/shows/{self.show_simkl_id}", limit=1)
        if isinstance(history, list) and history:
            try:
                playback_history = history[0]
                action = playback_history.get("action", "watch")
                episode_info = playback_history.get("episode") or {}
                season = get(episode_info, "season") or 1
                episode = get(episode_info, "episode") or 1
                g.log(
                    f"Quick Resume: Simkl history at S{int(season):02d}E{int(episode):02d} ({action})",
                    "info",
                )
            except (KeyError, TypeError, IndexError):
                g.log("Unable to parse Simkl resume history; defaulting to S01E01", "warning")

        if action != "watch":
            episode += 1

        return int(season), int(episode)

    def final_episode_check(self, season, episode):
        """
        True when resume coords are past the last aired regular episode (season > 0).
        """
        season = int(season)
        episode = int(episode)

        from resources.lib.database.session import get_sync_database

        row = get_sync_database().fetchone(
            """
            SELECT season, number FROM episodes
            WHERE simkl_show_id = ? AND air_date IS NOT NULL AND season > 0
            ORDER BY season DESC, number DESC
            LIMIT 1
            """,
            (self.show_simkl_id,),
        )
        if not row:
            return False

        last_season = int(row["season"])
        last_number = int(row["number"])

        if season > last_season:
            return True
        if season == last_season and episode > last_number:
            return True
        return False

    def append_next_season(self):
        """
        Checks if current episode is the last episode for the season, if true adds next seasons episodes to playlist
        :return:
        :rtype:
        """
        episode = self.item_information["info"]["episode"]
        season = self.item_information["info"]["season"]
        action_args = self.item_information.get("action_args") or {}
        if action_args.get("external_play"):
            episode_count = self._external_season_episode_count(int(season))
            if episode_count is None or int(episode) != int(episode_count):
                return
            next_season = int(season) + 1
            if not self._external_season_episode_count(next_season):
                return
            self.build_playlist(next_season, 1)
            return

        current_season_info = self.seasons_info[season]
        if episode != current_season_info["episode_count"]:
            return

        next_season = self.seasons_info.get(season + 1)
        if not next_season:
            return

        next_season_num = next_season.get("season", int(season) + 1)
        self.build_playlist(next_season_num, 1)

    @staticmethod
    def pre_scrape():
        """
        Checks whether a item exists in the current playlist after current item and then pre-fetches results
        :return:
        :rtype:
        """
        next_position = g.PLAYLIST.getposition() + 1
        if next_position >= g.PLAYLIST.size():
            return

        url = g.PLAYLIST[next_position].getPath()  # pylint: disable=unsubscriptable-object

        if not url:
            return

        url = url.replace("getSources", "preScrape")
        g.set_runtime_setting("tempSilent", True)
        g.log(f"Running Pre-Scrape: {url}")
        xbmc.executebuiltin(f'RunPlugin("{url}")')

    def shuffle_play(self):
        """
        Creates a playlist of shuffled episodes for selected show and plays it
        :return:
        :rtype:
        """

        g.PLAYLIST.clear()
        window = self._get_window()
        window.show()
        window.set_text(g.get_language_string(30062))

        from resources.lib.database.session import get_sync_database

        episode_rows = get_sync_database().get_episode_list(
            self.show_simkl_id,
            hide_unaired=False,
            hide_watched=False,
            hide_specials=True,
        )
        episode_list = [
            {
                "simkl_id": row["simkl_id"],
                "simkl_show_id": self.show_simkl_id,
                "simkl_object": {
                    "info": row.get("info") or {},
                    "art": row.get("art"),
                    "cast": row.get("cast"),
                },
            }
            for row in episode_rows
        ]
        if not episode_list:
            window.close()
            del window
            return

        window.set_text(g.get_language_string(30063))

        random.shuffle(episode_list)
        episode_list = episode_list[:40]
        attach_tv_show_id(episode_list, self.show_simkl_id, self.show_slug)

        playlist = self.list_builder.mixed_episode_builder(episode_list, smart_play=True)

        window.set_text(g.get_language_string(30064))

        for episode in playlist:
            if episode is not None:
                g.PLAYLIST.add(url=episode[0], listitem=episode[1])

        window.close()
        del window

        g.PLAYLIST.shuffle()
        xbmc.Player().play(g.PLAYLIST)

    def play_from_random_point(self):
        """
        Select a random episode for show and plays from that point onwards
        :return:
        :rtype:
        """

        import random

        g.PLAYLIST.clear()

        season_num = random.choice(list(self.seasons_info.keys()))
        playlist = self.list_builder.episode_list_builder(
            self.show_simkl_id, season=season_num, smart_play=True
        )
        random_episode = random.randint(0, len(playlist) - 1)
        playlist = playlist[random_episode]
        g.PLAYLIST.add(url=playlist[0], listitem=playlist[1])
        xbmc.Player().play(g.PLAYLIST)

    def create_single_item_playlist_from_info(self):
        g.cancel_playback()
        name = self.item_information["info"]["title"]
        entry = g.add_directory_item(
            name,
            action="getSources",
            menu_item=copy.deepcopy(self.item_information),
            action_args=encode_action_args(self.item_information),
            bulk_add=True,
            is_playable=True,
        )
        g.PLAYLIST.add(url=entry[0], listitem=entry[1])
        return g.PLAYLIST

    @staticmethod
    def clear_other_playlist_items():
        while (pos := g.PLAYLIST.getposition() + 1) < g.PLAYLIST.size():
            g.PLAYLIST.remove(g.PLAYLIST[pos].getPath())

    def playlist_present_check(self, ignore_setting=False):
        """
        Confirms if a playlist is currently present. If not or playlist is for a different item, clear current list
        and build a new one
        :param ignore_setting: Force playlist building if setting is disabled
        :type ignore_setting: bool
        :return: Playlist if playlist is present else False
        :rtype: any
        """
        if not (g.get_bool_setting("smartplay.playlistcreate") or ignore_setting):
            return

        if self.item_information["info"]["mediatype"] != "episode":
            g.log("Movie playback requested, clearing playlist")
            g.PLAYLIST.clear()
            return

        playlist_uris = [
            g.PLAYLIST[i].getPath() for i in range(g.PLAYLIST.size())  # pylint: disable=unsubscriptable-object
        ]

        # Check to see if we are just starting playback and kodi has created a playlist
        if len(playlist_uris) == 1 and playlist_uris[0].split('/')[-1].lstrip('?') == g.PARAM_STRING:
            return

        if g.PLAYLIST.getposition() == -1:
            return self.create_single_item_playlist_from_info()

        if any(g.ADDON_ID not in u for u in playlist_uris):
            g.log("Cleaning up other addon items from playlist", "debug")
            self.clear_other_playlist_items()
            return

        action_args = [
            g.legacy_action_args_converter(g.legacy_params_converter(dict(parse.parse_qsl(i.split("?")[-1]))))[
                "action_args"
            ]
            for i in playlist_uris
        ]

        show_ids = {
            show_id
            for i in action_args
            if (show_id := show_id_from_playlist_action_args(i)) is not None
        }

        if len(show_ids) > 1:
            g.log("Cleaning up items from other shows", "debug")
            self.clear_other_playlist_items()
            return

    def is_season_final(self):
        """
        Checks if episode in question is the final for the season
        :return: bool
        :rtype: True if last episode of season, else False
        """
        from resources.lib.simkl.ids import episode_num_from_info

        season_num = self.item_information["info"]["season"]
        action_args = self.item_information.get("action_args") or {}
        if action_args.get("external_play"):
            episode_count = self._external_season_episode_count(int(season_num))
        else:
            season = self.seasons_info.get(season_num, {})
            episode_count = season.get("episode_count") or season.get("aired_episodes")
        if episode_count is None:
            return True
        episode_num = episode_num_from_info(self.item_information["info"])
        return episode_num is not None and episode_num == episode_count

    @staticmethod
    def handle_resume_prompt(resume_switch, force_resume_off=False, force_resume_on=False, force_resume_check=False):
        """
        Handles displaying of resume prompt for item if required
        :param resume_switch: Resume param from arg string
        :type resume_switch: any
        :param force_resume_off: Disable resuming of item
        :type force_resume_off: bool
        :param force_resume_on: Force try resuming item
        :type force_resume_on: bool
        :param force_resume_check: Force a database check for item resume point
        :type force_resume_check: bool
        :return: Resume time in seconds for item
        :rtype: int
        """
        bookmark_style = g.get_int_setting("general.bookmarkstyle")

        if not resume_switch and not force_resume_off and bookmark_style != 2:
            action_args = g.REQUEST_PARAMS.get("action_args") or {}
            simkl_id = action_args.get("simkl_id")
            if simkl_id:
                from resources.lib.database.session import get_sync_database

                if bookmark := get_sync_database().get_bookmark(simkl_id):
                    resume_switch = bookmark["resume_time"]

        if g.PLAYLIST.size() <= 1 and resume_switch is not None and bookmark_style != 2 and not force_resume_off:

            if bookmark_style == 0 and not force_resume_on:
                import datetime

                selection = xbmcgui.Dialog().contextmenu(
                    [
                        f"{g.get_language_string(30059)} {datetime.timedelta(seconds=int(resume_switch))}",
                        g.get_language_string(30331),
                    ]
                )
                if selection == -1:
                    g.cancel_playback()
                    sys.exit()
                elif selection != 0:
                    resume_switch = None
        else:
            resume_switch = None

        return resume_switch

    def _get_window(self):
        if self.display_style == 0:
            # not sure about this one either
            return PersistentBackground(
                *SkinManager().confirm_skin_path("persistent_background.xml"), item_information=self.item_information
            )
        else:
            return BackgroundWindowAdapter()


class BackgroundWindowAdapter(xbmcgui.DialogProgressBG):
    """
    Ease of use adapter for handling smart play dialogs
    """

    def __init__(self):
        super().__init__()
        self.text = ""
        self.created = False

    def show(self):
        """
        Show the dialog to the user
        :return:
        :rtype:
        """
        self.create(g.ADDON_NAME, self.text)

    def set_text(self, text):
        """
        Sets the dialog text
        :param text: Text to display to user
        :type text: str
        :return:
        :rtype:
        """
        self.text = text
        if self.created:
            self.update()

    def update(self, percent=None, heading=None, message=None):
        """
        Update dialog progress
        :param percent: Percent of progress
        :type percent: int
        :param heading: Text to set as dialog heading
        :type heading: str
        :param message: Text to set as dialog message
        :type message: str
        :return:
        :rtype:
        """
        super().update(percent, heading, message)
