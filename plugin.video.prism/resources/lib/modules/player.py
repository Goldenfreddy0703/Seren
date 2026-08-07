import copy
import importlib
import json
import sys
import threading
import time
from functools import cached_property
from urllib import parse

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib.common import tools
from resources.lib.database.simkl_sync import bookmark
from resources.lib.indexers.simkl import SimklAPI
from resources.lib.simkl.payloads import info_to_scrobble_payload
from resources.lib.modules import smartPlay
from resources.lib.modules.globals import g, normalize_cast_to_actors, set_video_info_tag
from resources.lib.modules import locale_playback


class PrismPlayer(xbmc.Player):
    """
    Class to handle playback methods and accept callbacks from Kodi player
    """

    def __init__(self):
        super().__init__()

        self.simkl_id = None
        self.mediatype = None
        self.offset = None
        self.playing_file = None
        self.scrobbling_enabled = g.get_bool_setting("simkl.scrobbling")
        self.item_information = None
        self.smart_playlists = g.get_bool_setting("smartplay.playlistcreate")
        self.smart_module = None
        self.current_time = 0
        self.total_time = 0
        self.watched_percentage = 0
        self.ignoreSecondsAtStart = g.get_int_setting("simkl.ignoreSecondsAtStart")
        self.min_time_before_scrape = 600
        self.playCountMinimumPercent = g.get_int_setting("simkl.playCountMinimumPercent")
        self.dialogs_enabled = g.get_bool_setting("smartplay.playingnextdialog") or g.get_bool_setting(
            "smartplay.stillwatching"
        )
        self.pre_scrape_enabled = g.get_bool_setting("smartPlay.preScrape")
        self.playing_next_time = g.get_int_setting("playingnext.time")
        self.simkl_enabled = bool(g.get_setting("simkl.auth", ""))
        self._running_path = None

        # IntroDB / AniSkip skip segments
        self.introdb_enabled = g.get_bool_setting("introdb.enabled")
        self.introdb_aniskip = g.get_bool_setting("introdb.aniskip")
        self.introdb_introdb = g.get_bool_setting("introdb.introdb")
        self.introdb_credits_playingnext = g.get_bool_setting("introdb.credits.playingnext")
        self.introdb_offset = g.get_int_setting("introdb.offset")
        self.introdb_segments = []
        self.introdb_fired = set()

        # Flags
        self.resumed = False
        self.playback_started = False
        self.playback_error = False
        self.playback_ended = False
        self.playback_stopped = False
        self.scrobbled = False
        self.scrobble_started = False
        self.last_attempted_scrobble_stop = 0
        self.last_attempted_scrobble_pause = 0
        self.marked_watched = False
        self.dialogs_triggered = False
        self.pre_scrape_initiated = False
        self.playback_timestamp = 0
        self._locale_backup = None

    @cached_property
    def _simkl_api(self):
        return SimklAPI()

    @cached_property
    def bookmark_sync(self):
        from resources.lib.database.session import get_sync_database

        return get_sync_database()

    def play_source(self, stream_link, item_information, resume_time=None):
        """Method for handling playing of sources.

        :param stream_link: Direct link of source to be played or dict containing more information about the stream
        to play
        :type stream_link: str|dict
        :param item_information: Information about the item to be played
        :type item_information:dict
        :param resume_time:Time to resume the source at
        :type resume_time:int
        :rtype:None
        """
        self.pre_scrape_initiated = False
        if resume_time:
            self.offset = float(resume_time)

        if not stream_link:
            g.cancel_playback()
            return

        self.playing_file = stream_link
        self.item_information = item_information
        self.smart_module = smartPlay.SmartPlay(item_information)
        self.mediatype = self.item_information["info"]["mediatype"]
        self.simkl_id = self.item_information["info"]["simkl_id"]

        if self.item_information.get("resume", "false") == "true":
            self._try_get_bookmark()

        from resources.lib.common import tools

        tools.run_threaded(g.clear_kodi_bookmarks)
        self._handle_bookmark(clear_kodi=False)
        self._add_support_for_external_simkl_scrobbling()

        self.playing_next_time = max(self.playing_next_time, self.item_information["info"].get("duration", 0) * (1 - (g.get_int_setting("playingnext.percent") / 100)))

        locale_backup = None
        try:
            locale_backup = locale_playback.apply_catalog_locale(
                locale_playback.catalog_from_item(item_information)
            )
            self._locale_backup = locale_backup

            xbmcplugin.setResolvedUrl(g.PLUGIN_HANDLE, True, self._create_list_item(stream_link))

            self._keep_alive()
        finally:
            locale_playback.restore_catalog_locale(locale_backup)
            self._locale_backup = None

    # region Kodi player overrides
    def getTotalTime(self):
        """
        Returns total time for playing file if user is playing a file
        :return: Total length of file else 0 if not playing an item
        :rtype: int
        """
        try:
            return super().getTotalTime()
        except RuntimeError:
            g.log("Trying to get player total time while not playing", "warning")
            return 0

    def getTime(self):
        """
        Gets current position in seconds from start of item
        :return: Current position or 0 if not playing a file
        :rtype: int
        """
        try:
            return current_time if (current_time := super().getTime()) > 0 else 0
        except RuntimeError:
            g.log("Trying to get player time while not playing", "warning")
            return 0

    def isPlayingVideo(self):
        """
        Returns true if currently playing item is a video file
        :return: True if playing a file and it is video else False
        :rtype: bool
        """
        return super().isPlayingVideo()

    def seekTime(self, time):
        """
        Seeks the specified amount of time as fractional seconds if playing a file. The time specified is relative to
        the beginning of the currently. playing media file.
        :param time: Time to seek as fractional seconds
        :type time: float
        :return: None
        :rtype: None
        """
        try:
            super().seekTime(time)
        except RuntimeError:
            g.log("Trying to seek player when not playing a file", "warning")

    def getSubtitles(self):
        """
        Get subtitle stream name if playing a file
        :return: Stream Name if playing a file else None
        :rtype: str, None
        """
        return subtitles if (subtitles := super().getSubtitles()) else None

    def getAvailableSubtitleStreams(self):
        """
        Get Subtitle stream names.
        :return: List of available subtitle streams
        :rtype: list, None
        """
        return super().getAvailableSubtitleStreams() if self.isPlaying() else None

    def setSubtitles(self, subtitle):
        """
        Set subtitle file and enable subtitles if currently playing an item.
        :param subtitle:  Path to file to use as source of subtitles
        :type subtitle: str
        :return: None
        :rtype: None
        """
        if self.isPlaying():
            super().setSubtitles(subtitle)
        else:
            g.log("Trying to set subtitles when not playing a file", "warning")

    def getPlayingFile(self):
        """
        Fetches the path to the playing file else returns None
        :return: Path to file
        :rtype: str/None
        """
        try:
            return super().getPlayingFile()
        except RuntimeError:
            # seems that we have a racing condition between isPlaying() and getPlayingFile()
            g.log("Trying to get playing file when not playing a file", "warning")

    # endregion

    # region Kodi player callbacks
    def onAVStarted(self):
        """
        Callback method from Kodi to advise that AV stream has started
        :return: None
        :rtype: None
        """
        self._start_playback()

    def onAVChange(self):
        """
        Callback method from Kodi to advise that AV stream has started
        This is being used as a fallback for instances where AVStarted fails
        :return: None
        :rtype: None
        """
        self._start_playback()

    def onPlayBackSeek(self, time, seekOffset):
        """
        Callback method from Kodi when a seek event has occured
        :param time: Time to seek to
        :type time: int
        :param seekOffset: Offset from previous position
        :type seekOffset: int
        :return: None
        :rtype: None
        """
        seekOffset /= 1000
        self._simkl_start_watching(offset=seekOffset, re_scrobble=True)

    def onPlayBackSeekChapter(self, chapter):
        """
        Callback method from Kodi when user performs a chapter seek.
        :param chapter: Chapter seeked to
        :type chapter: int
        :return: None
        :rtype: None
        """
        self._simkl_start_watching(re_scrobble=True)

    def onPlayBackResumed(self):
        """
        Callback method from Kodi when user resumes a paused file.
        :return: None
        :rtype: None
        """
        self._simkl_start_watching(re_scrobble=True)

    def onPlayBackEnded(self):
        """
        Callback method from Kodi when playback has finished
        :return: None
        :rtype: None
        """
        self.playback_ended = bool(self.playback_started)
        self._end_playback()
        if g.PLAYLIST.getposition() == g.PLAYLIST.size() or g.PLAYLIST.size() == 1:
            g.PLAYLIST.clear()

    def onPlayBackStopped(self):
        """
        Callback method from Kodi when user stops a file.
        :return: None
        :rtype: None
        """
        self.playback_stopped = bool(self.playback_started)
        g.PLAYLIST.clear()
        g.close_busy_dialog()
        g.close_all_dialogs()
        self._end_playback()

    def onPlayBackPaused(self):
        """
        Callback method from Kodi when user pauses a file.
        :return: None
        :rtype: None
        """
        self._handle_bookmark()
        self._simkl_stop_watching()

    def onPlayBackError(self):
        """
        Callback method from Kodi when playback stops due to an error
        :return: None
        :rtype: None
        """
        g.log("Kodi has reported an error and has stopped playback!", "warning")
        self.playback_error = True
        g.PLAYLIST.clear()
        g.close_busy_dialog()
        g.close_all_dialogs()
        self._end_playback()

    # endregion

    def _start_playback(self):
        if self.playback_started:
            return

        if g.get_bool_setting("playingnext.chapters"):
            last_chapter = self.final_chapter()
            if last_chapter is not None:
                duration = self.item_information["info"].get("duration") or 0
                if duration:
                    remaining_time = duration * (1 - last_chapter / 100)
                    if remaining_time > 5:
                        self.playing_next_time = min(self.playing_next_time, remaining_time)

        if self.offset and not self.resumed:
            self.seekTime(self.offset)
            self.resumed = True

        self.playback_started = True
        self.playback_timestamp = time.time()
        self._running_path = self.getPlayingFile()

        self._fetch_introdb_segments()

        g.close_busy_dialog()
        g.close_all_dialogs()

        if self.smart_playlists and self.mediatype == "episode":
            if g.PLAYLIST.size() == 1 and not self.smart_module.is_season_final():
                self.smart_module.build_playlist()
            elif g.PLAYLIST.size() == g.PLAYLIST.getposition() + 1:
                self.smart_module.append_next_season()

    def _end_playback(self):
        locale_playback.restore_catalog_locale(self._locale_backup)
        self._handle_bookmark()
        self._simkl_stop_watching()
        self._simkl_mark_playing_item_watched()
        if g.get_bool_setting("general.force.widget.refresh.playback"):
            g.trigger_widget_refresh()

    def _get_kodi_preferred_subtitle_language(self):
        language = g.get_kodi_preferred_subtitle_language(True)
        if language == "original":
            audio_streams = self.getAvailableAudioStreams()
            if not audio_streams or len(audio_streams) == 0:
                return None
            return audio_streams[0]
        elif language == "default":
            return xbmc.getLanguage(xbmc.ISO_639_2)
        elif language in ["none", "forced_only"]:
            return None
        else:
            return language

    def _create_list_item(self, stream_link):
        info = copy.deepcopy(self.item_information["info"])
        g.clean_info_keys(info)
        g.convert_info_dates(info)

        if isinstance(stream_link, dict) and stream_link["type"] == "adaptive":
            if g.ADDON_USERDATA_PATH not in sys.path:
                sys.path.append(g.ADDON_USERDATA_PATH)
            provider = stream_link["provider_imports"]
            provider_module = importlib.import_module(f"{provider[0]}.{provider[1]}")
            if not hasattr(provider_module, "get_listitem") and hasattr(provider_module, "sources"):
                provider_module = provider_module.sources()
            item = provider_module.get_listitem(stream_link)
            # Use InfoTagVideo API for adaptive sources
            set_video_info_tag(item, info)
        else:
            item = xbmcgui.ListItem(path=stream_link)
            info["FileNameAndPath"] = parse.unquote(self.playing_file)
            item.setProperty("IsPlayable", "true")
            
            # Build unique IDs for InfoTagVideo
            unique_ids = {i.split("_")[0]: str(info[i]) for i in info if i.endswith("id") and info[i]}
            
            # Get cast from item information
            cast = self.item_information.get("cast", [])
            if not isinstance(cast, list):
                cast = []
            
            # Use InfoTagVideo API (Kodi 21+)
            set_video_info_tag(item, info, cast=cast, unique_ids=unique_ids)

        art = self.item_information.get("art", {})
        item.setArt(art if isinstance(art, dict) else {})
        return item

    def _add_support_for_external_simkl_scrobbling(self):
        simkl_meta = {}
        keys = {
            "tmdb_id": "tmdb",
            "imdb_id": "imdb",
            "tvdb_id": "tvdb",
            "simkl_id": "simkl",
        }

        info = self.item_information.get("info", {})
        for id_key in keys:
            meta_id = info.get(f"tvshow.{id_key}" if info.get("mediatype") == "episode" else id_key)
            if meta_id:
                simkl_meta[keys[id_key]] = meta_id

        g.HOME_WINDOW.setProperty("script.simkl.ids", json.dumps(simkl_meta, sort_keys=True))

    def _update_progress(self, offset=None):
        if not self._is_file_playing():
            return

        self.current_time = self.getTime()

        if offset is not None:
            self.current_time += offset

        if self.total_time > 0:
            try:
                self.watched_percentage = tools.safe_round(float(self.current_time) / float(self.total_time) * 100, 2)
                self.watched_percentage = min(self.watched_percentage, 100)
            except TypeError:
                pass

    def _log_debug_information(self):
        g.log(f"PlaybackIdentifedAt: {self.getTime()}", "debug")
        g.log(f"IgnoringSecondsAtStart: {self.ignoreSecondsAtStart}", "debug")
        g.log(f"PreScrapeSeconds: {self.min_time_before_scrape}", "debug")
        g.log(f"PlayCountMin: {self.playCountMinimumPercent}", "debug")
        g.log(f"DialogsEnabled: {self.dialogs_enabled}", "debug")
        g.log(f"SimklEnabled: {self.simkl_enabled}", "debug")
        g.log(f"DialogSeconds: {self.playing_next_time}", "debug")
        g.log(f"TotalMediaLength: {self.getTotalTime()}", "debug")

    # region Simkl scrobble
    def _simkl_start_watching(self, offset=None, re_scrobble=False):
        if (
            not self.simkl_enabled
            or not self.scrobbling_enabled
            or (self.scrobbled and not re_scrobble)
            or (self.scrobble_started and not re_scrobble)
        ):
            return

        if self.watched_percentage >= self.playCountMinimumPercent or self.current_time < self.ignoreSecondsAtStart:
            return

        try:
            if offset:
                self._update_progress(offset)
            post_data = info_to_scrobble_payload(self.item_information["info"], self.watched_percentage)
            self._simkl_api.scrobble_start(post_data)
        except Exception:
            g.log_stacktrace()
        self.scrobble_started = True

    def _simkl_stop_watching(self):
        if (
            not self.simkl_enabled
            or not self.scrobbling_enabled
            or self.scrobbled
            or (not self.scrobble_started and self.current_time < self.ignoreSecondsAtStart)
        ):
            return

        post_data = info_to_scrobble_payload(self.item_information["info"], self.watched_percentage)

        if post_data["progress"] >= self.playCountMinimumPercent:
            if time.time() - self.last_attempted_scrobble_stop < 30 and not g.abort_requested():
                return
            post_data["progress"] = max(post_data["progress"], 80)
            try:
                scrobble_response = self._simkl_api.scrobble_stop(post_data)
            except Exception:
                g.log_stacktrace()
                return
            finally:
                self.last_attempted_scrobble_stop = time.time()
            if scrobble_response is not None and scrobble_response.status_code in (201, 409):
                self.scrobbled = True
                self._simkl_mark_playing_item_watched()
                if scrobble_response.status_code == 201:
                    try:
                        action = scrobble_response.json()["action"]
                        if action != "scrobble":
                            g.log(f"Simkl scrobble/stop returned action: {action}", "warning")
                    except Exception:
                        g.log_stacktrace()
                return
            status = scrobble_response.status_code if scrobble_response is not None else "none"
            g.log(f"Simkl scrobble/stop returned status code: {status}", "warning")
        else:
            if (pause_time := time.time() - self.last_attempted_scrobble_pause) < 5:
                g.log(f"Simkl scrobble/pause repeat called: {pause_time}s", "warning")
            try:
                scrobble_response = self._simkl_api.scrobble_pause(post_data)
                if self.current_time < self.ignoreSecondsAtStart:
                    self._remove_playback_history(self.item_information)
            except Exception:
                g.log_stacktrace()
                return
            finally:
                self.last_attempted_scrobble_pause = time.time()
            if scrobble_response is None or scrobble_response.status_code != 201:
                status = scrobble_response.status_code if scrobble_response is not None else "none"
                g.log(f"Simkl scrobble/pause returned status code: {status}", "warning")

    def _simkl_mark_playing_item_watched(self):
        if (
            self.marked_watched
            or not self.playback_started
            or not self.watched_percentage >= self.playCountMinimumPercent
        ):
            return

        self.marked_watched = True

        if self.mediatype == "episode":
            from resources.lib.database.session import get_sync_database
            from resources.lib.simkl.ids import show_id_from_info

            show_id = show_id_from_info(self.item_information["info"])
            get_sync_database().mark_episode_watched(
                show_id,
                self.item_information["info"]["season"],
                self.item_information["info"]["episode"],
            )
        if self.mediatype == "movie":
            from resources.lib.database.session import get_sync_database

            get_sync_database().mark_movie_watched(self.simkl_id)
        from resources.lib.simkl.library_status import apply_local_status_after_watch

        apply_local_status_after_watch(self.item_information)

    def _build_simkl_object(self, offset=None):
        if offset:
            self._update_progress(offset)
        return info_to_scrobble_payload(self.item_information["info"], self.watched_percentage)

    # endregion

    def _keep_alive(self):
        for _ in range(480):
            if self._is_file_playing() or self._playback_has_stopped() or g.wait_for_abort(0.25):
                break

        self.total_time = self.getTotalTime()
        self.min_time_before_scrape = max(self.total_time * 0.2, self.min_time_before_scrape)

        self._log_debug_information()

        while not g.wait_for_abort(0.5) and self._is_file_playing():  # This order is correct! Wait then check.
            self._update_progress()

            if not self.scrobble_started:
                self._simkl_start_watching()

            time_left = int(self.total_time) - int(self.current_time)

            if self.min_time_before_scrape > time_left and not self.pre_scrape_initiated:
                self._handle_pre_scrape()

            if self.watched_percentage >= self.playCountMinimumPercent and self.scrobble_started and not self.scrobbled:
                self._handle_bookmark()
                self._simkl_stop_watching()

            if self.introdb_enabled and self.introdb_segments:
                self._handle_introdb_segments()

            if self.dialogs_enabled and not self.dialogs_triggered and time_left <= self.playing_next_time:
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runPlayerDialogs")')
                self.dialogs_triggered = True

        if not self._playback_has_stopped():  # Kodi does not fire the onPlaybackStopped event if early in playback
            self._end_playback()

    def _playback_has_stopped(self):
        return self.playback_stopped or self.playback_error or self.playback_ended

    def _handle_pre_scrape(self):
        if self.pre_scrape_enabled and not self.pre_scrape_initiated:
            self.smart_module.pre_scrape()
            self.pre_scrape_initiated = True

    def _fetch_introdb_segments(self):
        # Clear any stale outro flag from a previous item before (re)loading segments.
        g.HOME_WINDOW.clearProperty("prism.outro.end")

        if not self.introdb_enabled:
            return

        def _runner():
            try:
                self.introdb_segments = self._collect_skip_segments()
                if self.introdb_segments:
                    g.log(f"Skip segments: loaded {len(self.introdb_segments)} segment(s)", "debug")
                    self._publish_outro_property()
            except Exception:
                g.log_stacktrace()

        thread = threading.Thread(target=_runner)
        thread.daemon = True
        thread.start()

    def _collect_skip_segments(self):
        """Merge skip segments from AniSkip (anime, preferred) and IntroDB (gap-fill / non-anime)."""
        info = (self.item_information or {}).get("info", {})
        is_anime = (
            info.get("catalog") == "anime" or bool(info.get("mal_id")) or bool(info.get("mal_show_id"))
        )
        by_type = {}
        aniskip_count = 0
        introdb_count = 0

        if is_anime and self.introdb_aniskip:
            try:
                from resources.lib.modules.aniskip import AniSkip

                for segment in AniSkip().get_segments(self.item_information) or []:
                    if by_type.setdefault(segment["type"], segment) is segment:
                        aniskip_count += 1
            except Exception:
                g.log_stacktrace()

        if self.introdb_introdb:
            try:
                from resources.lib.modules.introdb import IntroDB

                for segment in IntroDB().get_segments(self.item_information) or []:
                    if by_type.setdefault(segment["type"], segment) is segment:
                        introdb_count += 1
            except Exception:
                g.log_stacktrace()

        g.log(
            f"Skip segments: anime={is_anime} aniskip={aniskip_count} introdb={introdb_count}",
            "debug",
        )
        segments = list(by_type.values())
        segments.sort(key=lambda s: (s["start"] if s.get("start") is not None else 0))
        return segments

    def _publish_outro_property(self):
        """Expose the credits/outro end time so the Playing Next dialog can offer a Skip Outro button.

        Stored on the home window because the dialog runs in a separate plugin invocation.
        A value of 0 is a sentinel meaning "credits run to the end of the media".
        """
        if not (g.get_bool_setting("introdb.credits") and self.introdb_credits_playingnext):
            return
        for segment in self.introdb_segments:
            if segment.get("type") != "credits":
                continue
            end = segment.get("end")
            g.HOME_WINDOW.setProperty("prism.outro.end", str(int(end) if end else 0))
            break

    def _has_next_item(self):
        return g.PLAYLIST.size() > 0 and g.PLAYLIST.getposition() != (g.PLAYLIST.size() - 1)

    def _handle_introdb_segments(self):
        current = self.current_time
        for segment in self.introdb_segments:
            seg_type = segment["type"]
            if seg_type in self.introdb_fired:
                continue
            start = segment.get("start")
            end = segment.get("end")
            if start is None or current < start:
                continue
            self.introdb_fired.add(seg_type)
            # If we are already past the segment (e.g. user seeked), don't act.
            if end is not None and current > end:
                continue
            self._trigger_introdb_segment(seg_type, end)
            break

    def _trigger_introdb_segment(self, seg_type, end):
        if not g.get_bool_setting(f"introdb.{seg_type}"):
            return

        if (
            seg_type == "credits"
            and self.introdb_credits_playingnext
            and self.dialogs_enabled
            and self._has_next_item()
        ):
            if not self.dialogs_triggered:
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runPlayerDialogs")')
                self.dialogs_triggered = True
            return

        if g.get_bool_setting(f"introdb.{seg_type}.autoskip"):
            self._introdb_auto_skip(end)
        else:
            end_arg = "" if end is None else end
            xbmc.executebuiltin(
                f'RunPlugin("plugin://plugin.video.prism/?action=showSkipSegment&segment={seg_type}&end={end_arg}")'
            )

    def _introdb_auto_skip(self, end):
        if end is None:
            target = self.total_time - 5 if self.total_time else 0
        else:
            target = end + self.introdb_offset
        if self.total_time and target >= self.total_time:
            target = self.total_time - 5
        if target > 0:
            self.seekTime(target)

    def _try_get_bookmark(self):
        bm = self.bookmark_sync.get_bookmark(self.simkl_id)
        if not bm:
            return
        resume_time = bm.get("resume_time") or bm.get("resumeTime")
        if resume_time is not None:
            self.offset = float(resume_time)

    def _handle_bookmark(self, clear_kodi=True):
        if clear_kodi:
            try:
                g.clear_kodi_bookmarks()
            except Exception:
                g.log_stacktrace()
        if self.current_time == 0 or self.total_time == 0:
            return

        if self.watched_percentage < self.playCountMinimumPercent and self.current_time >= self.ignoreSecondsAtStart:
            info = self.item_information.get("info") or {}
            catalog = info.get("catalog") or ("movie" if self.mediatype == "movie" else "tv")
            self.bookmark_sync.set_bookmark(
                self.simkl_id,
                int(self.current_time),
                self.mediatype,
                self.watched_percentage,
                catalog=catalog,
            )
        else:
            self.bookmark_sync.remove_bookmark(self.simkl_id)

    def _is_file_playing(self):
        if not self.playback_started or self._playback_has_stopped() or self._running_path is None:
            return False

        return self.isPlayingVideo()
        
    def _remove_playback_history(self, item_information):
        self.bookmark_sync.remove_bookmark(item_information["simkl_id"])
        info = item_information["info"]
        media_type = "episode" if info["mediatype"] != "movie" else "movie"
        progress = self._simkl_api.get_playback(media_type) or []
        if isinstance(progress, dict):
            progress = progress.get("movies") or progress.get("episodes") or []
        if not progress:
            return
        target_id = info["simkl_id"]
        for entry in progress:
            if media_type == "movie":
                movie = entry.get("movie") or entry
                simkl_id = (movie.get("ids") or {}).get("simkl")
            else:
                episode = entry.get("episode") or {}
                simkl_id = (episode.get("ids") or {}).get("simkl_id") or (episode.get("ids") or {}).get("simkl")
            playback_id = entry.get("id")
            if simkl_id and int(simkl_id) == int(target_id) and playback_id:
                self._simkl_api.delete_playback(playback_id)

    def final_chapter(self):
        try:
            final_chapter = xbmc.getInfoLabel('Player.Chapters')
            if final_chapter:
                final_chapter = float(final_chapter.split(',')[-1])
                if final_chapter >= 90:
                    return final_chapter
        except: pass
        return None


class PlayerDialogs(xbmc.Player):
    """
    Handles dialogs that appear over playing items
    """

    def __init__(self):
        super().__init__()
        self._min_time = g.get_int_setting("playingnext.time")
        self.playing_file = None

    def display_dialog(self):
        """
        Handles the initiating of dialogs and deciding which dialog to display if required
        :return: None
        :rtype: None
        """
        try:
            self.playing_file = self.getPlayingFile()
        except RuntimeError:
            g.log("Kodi did not return a playing file, killing playback dialogs", "error")
            return
        if g.PLAYLIST.size() > 0 and g.PLAYLIST.getposition() != (g.PLAYLIST.size() - 1):
            if g.get_bool_setting("smartplay.stillwatching") and self._still_watching_calc():
                target = self._show_still_watching
            elif g.get_bool_setting("smartplay.playingnextdialog"):
                target = self._show_skip_outro if self._outro_available() else self._show_playing_next
            else:
                return

            if self.playing_file != self.getPlayingFile():
                return

            if not self.isPlayingVideo():
                return

            if not self._is_video_window_open():
                return

            target()

    @staticmethod
    def _still_watching_calc():
        calculation = float(g.PLAYLIST.getposition() + 1) / g.get_float_setting("stillwatching.numepisodes")

        return False if calculation == 0 else calculation.is_integer()

    @staticmethod
    def _outro_available():
        return g.HOME_WINDOW.getProperty("prism.outro.end") != ""

    def _show_playing_next(self):
        from resources.lib.gui.windows.playing_next import PlayingNext
        from resources.lib.database.skinManager import SkinManager

        try:
            window = PlayingNext(
                *SkinManager().confirm_skin_path("playing_next.xml"),
                item_information=self._get_next_item_item_information(),
            )
            window.doModal()
        finally:
            del window

    def _show_skip_outro(self):
        from resources.lib.gui.windows.skip_outro import SkipOutro
        from resources.lib.database.skinManager import SkinManager

        try:
            window = SkipOutro(
                *SkinManager().confirm_skin_path("skip_outro.xml"),
                item_information=self._get_next_item_item_information(),
            )
            window.doModal()
        finally:
            del window

    def _show_still_watching(self):
        from resources.lib.gui.windows.still_watching import StillWatching
        from resources.lib.database.skinManager import SkinManager

        try:
            window = StillWatching(
                *SkinManager().confirm_skin_path("still_watching.xml"),
                item_information=self._get_next_item_item_information(),
            )
            window.doModal()
        finally:
            del window

    @staticmethod
    def _get_next_item_item_information():
        current_position = g.PLAYLIST.getposition()
        url = g.PLAYLIST[current_position + 1].getPath()  # pylint: disable=unsubscriptable-object
        params = dict(parse.parse_qsl(parse.unquote(url.split("?")[1])))
        return tools.get_item_information(tools.deconstruct_action_args(params.get("action_args")))

    @staticmethod
    def _is_video_window_open():
        return xbmcgui.getCurrentWindowId() == 12005
