# Import Thread lock workaround
# noinspection PyUnresolvedReferences
import contextlib
import datetime
import html
import json
import os
import re
import traceback
import unicodedata
from functools import cached_property
from urllib import parse
from xml.etree import ElementTree

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs
from unidecode import unidecode

from resources.lib.common import tools
from resources.lib.modules.settings_cache import PersistedSettingsCache
from resources.lib.modules.settings_cache import RuntimeSettingsCache
from resources.lib.third_party import pytz

viewTypes = [
    ("Default", 50),
    ("Poster", 51),
    ("Icon Wall", 52),
    ("Shift", 53),
    ("Info Wall", 54),
    ("Wide List", 55),
    ("Wall", 500),
    ("Banner", 501),
    ("Fanart", 502),
]

colorChart = [
    "black",
    "white",
    "whitesmoke",
    "gainsboro",
    "lightgray",
    "silver",
    "darkgray",
    "gray",
    "dimgray",
    "snow",
    "floralwhite",
    "ivory",
    "beige",
    "cornsilk",
    "antiquewhite",
    "bisque",
    "blanchedalmond",
    "burlywood",
    "darkgoldenrod",
    "ghostwhite",
    "azure",
    "aliveblue",
    "lightsaltegray",
    "lightsteelblue",
    "powderblue",
    "lightblue",
    "skyblue",
    "lightskyblue",
    "deepskyblue",
    "dodgerblue",
    "royalblue",
    "blue",
    "mediumblue",
    "midnightblue",
    "navy",
    "darkblue",
    "cornflowerblue",
    "slateblue",
    "slategray",
    "yellowgreen",
    "springgreen",
    "seagreen",
    "steelblue",
    "teal",
    "fuchsia",
    "deeppink",
    "darkmagenta",
    "blueviolet",
    "darkviolet",
    "darkorchid",
    "darkslateblue",
    "darkslategray",
    "indigo",
    "cadetblue",
    "darkcyan",
    "darkturquoise",
    "turquoise",
    "cyan",
    "paleturquoise",
    "lightcyan",
    "mintcream",
    "honeydew",
    "aqua",
    "aquamarine",
    "chartreuse",
    "greenyellow",
    "palegreen",
    "lawngreen",
    "lightgreen",
    "lime",
    "mediumspringgreen",
    "mediumturquoise",
    "lightseagreen",
    "mediumaquamarine",
    "mediumseagreen",
    "limegreen",
    "darkseagreen",
    "forestgreen",
    "green",
    "darkgreen",
    "darkolivegreen",
    "olive",
    "olivedab",
    "darkkhaki",
    "khaki",
    "gold",
    "goldenrod",
    "lightyellow",
    "lightgoldenrodyellow",
    "lemonchiffon",
    "yellow",
    "seashell",
    "lavenderblush",
    "lavender",
    "lightcoral",
    "indianred",
    "darksalmon",
    "lightsalmon",
    "pink",
    "lightpink",
    "hotpink",
    "magenta",
    "plum",
    "violet",
    "orchid",
    "palevioletred",
    "mediumvioletred",
    "purple",
    "marron",
    "mediumorchid",
    "mediumpurple",
    "mediumslateblue",
    "thistle",
    "linen",
    "mistyrose",
    "palegoldenrod",
    "oldlace",
    "papayawhip",
    "moccasin",
    "navajowhite",
    "peachpuff",
    "sandybrown",
    "peru",
    "chocolate",
    "orange",
    "darkorange",
    "tomato",
    "orangered",
    "red",
    "crimson",
    "salmon",
    "coral",
    "firebrick",
    "brown",
    "darkred",
    "tan",
    "rosybrown",
    "sienna",
    "saddlebrown",
]

info_labels = {
    "genre",
    "country",
    "year",
    "episode",
    "season",
    "sortepisode",
    "sortseason",
    "episodeguide",
    "showlink",
    "top250",
    "setid",
    "tracknumber",
    "rating",
    "userrating",
    "watched",
    "playcount",
    "overlay",
    "castandrole",
    "director",
    "mpaa",
    "plot",
    "plotoutline",
    "title",
    "originaltitle",
    "sorttitle",
    "duration",
    "studio",
    "tagline",
    "writer",
    "tvshowtitle",
    "premiered",
    "status",
    "set",
    "setoverview",
    "tag",
    "imdbnumber",
    "code",
    "aired",
    "credits",
    "lastplayed",
    "album",
    "artist",
    "votes",
    "path",
    "trailer",
    "dateadded",
    "mediatype",
    "dbid",
}

info_dates = {
    "premiered",
    "aired",
    "lastplayed",
    "dateadded",
}


def normalize_cast_to_actors(cast_list):
    """
    Convert cast dictionaries to xbmc.Actor objects for InfoTagVideo.setCast().
    
    :param cast_list: List of cast dictionaries with 'name', 'role', 'order', 'thumbnail' keys
    :return: List of xbmc.Actor objects
    """
    if not cast_list or not isinstance(cast_list, (list, set)):
        return []
    
    actors = []
    for idx, cast_member in enumerate(cast_list):
        if not isinstance(cast_member, dict):
            continue
        name = cast_member.get("name", "")
        if not name:
            continue
        role = cast_member.get("role", cast_member.get("character", ""))
        order = cast_member.get("order", idx)
        thumbnail = cast_member.get("thumbnail") or cast_member.get("thumb") or cast_member.get("profile_path") or ""
        try:
            actors.append(xbmc.Actor(name, role, order, thumbnail or ""))
        except Exception:
            # Fallback if xbmc.Actor fails
            pass
    return actors


def set_video_info_tag(item, info, cast=None, unique_ids=None):
    """
    Set video metadata using InfoTagVideo API (Kodi 21+).
    
    :param item: xbmcgui.ListItem
    :param info: Dictionary of info labels
    :param cast: List of cast dictionaries (optional)
    :param unique_ids: Dictionary of unique IDs (optional)
    """
    info_tag = item.getVideoInfoTag()
    
    # Basic info
    if info.get("title"):
        info_tag.setTitle(str(info["title"]))
    if info.get("originaltitle"):
        info_tag.setOriginalTitle(str(info["originaltitle"]))
    if info.get("sorttitle"):
        info_tag.setSortTitle(str(info["sorttitle"]))
    if info.get("plot"):
        info_tag.setPlot(str(info["plot"]))
    elif info.get("overview"):
        info_tag.setPlot(str(info["overview"]))
    if info.get("plotoutline"):
        info_tag.setPlotOutline(str(info["plotoutline"]))
    if info.get("tagline"):
        info_tag.setTagLine(str(info["tagline"]))
    if info.get("mediatype"):
        info_tag.setMediaType(str(info["mediatype"]))
    
    # Numeric fields
    if info.get("year"):
        try:
            info_tag.setYear(int(info["year"]))
        except (ValueError, TypeError):
            pass
    if info.get("episode") is not None:
        try:
            info_tag.setEpisode(int(info["episode"]))
        except (ValueError, TypeError):
            pass
    if info.get("season") is not None:
        try:
            info_tag.setSeason(int(info["season"]))
        except (ValueError, TypeError):
            pass
    if info.get("sortepisode") is not None:
        try:
            info_tag.setSortEpisode(int(info["sortepisode"]))
        except (ValueError, TypeError):
            pass
    if info.get("sortseason") is not None:
        try:
            info_tag.setSortSeason(int(info["sortseason"]))
        except (ValueError, TypeError):
            pass
    if info.get("duration"):
        try:
            info_tag.setDuration(int(info["duration"]))
        except (ValueError, TypeError):
            pass
    elif info.get("runtime"):
        try:
            info_tag.setDuration(int(float(info["runtime"]) * 60))
        except (ValueError, TypeError):
            pass
    if info.get("playcount") is not None:
        try:
            info_tag.setPlaycount(int(info["playcount"]))
        except (ValueError, TypeError):
            pass
    if info.get("userrating") is not None:
        try:
            info_tag.setUserRating(int(info["userrating"]))
        except (ValueError, TypeError):
            pass
    if info.get("top250"):
        try:
            info_tag.setTop250(int(info["top250"]))
        except (ValueError, TypeError):
            pass
    if info.get("dbid"):
        try:
            info_tag.setDbId(int(info["dbid"]))
        except (ValueError, TypeError):
            pass
    
    # Rating with votes
    rating = info.get("rating")
    votes = info.get("votes", 0)
    if rating is not None:
        try:
            info_tag.setRating(float(rating), int(votes) if votes else 0, "default", True)
        except (ValueError, TypeError):
            pass
    
    # String fields
    if info.get("mpaa"):
        info_tag.setMpaa(str(info["mpaa"]))
    if info.get("tvshowtitle"):
        info_tag.setTvShowTitle(str(info["tvshowtitle"]))
    if info.get("imdbnumber"):
        info_tag.setIMDBNumber(str(info["imdbnumber"]))
    if info.get("trailer"):
        info_tag.setTrailer(str(info["trailer"]))
    if info.get("code"):
        info_tag.setProductionCode(str(info["code"]))
    if info.get("status") and str(info.get("mediatype") or "").lower() in ("tvshow", "season"):
        info_tag.setTvShowStatus(str(info["status"]))
    if info.get("path"):
        info_tag.setPath(str(info["path"]))
    if info.get("set"):
        info_tag.setSet(str(info["set"]))
    if info.get("setoverview"):
        info_tag.setSetOverview(str(info["setoverview"]))
    if info.get("album"):
        info_tag.setAlbum(str(info["album"]))
    
    # Date fields
    if info.get("premiered"):
        try:
            info_tag.setPremiered(str(info["premiered"]))
        except Exception:
            pass
    if info.get("aired"):
        try:
            info_tag.setFirstAired(str(info["aired"]))
        except Exception:
            pass
    if info.get("dateadded"):
        try:
            info_tag.setDateAdded(str(info["dateadded"]))
        except Exception:
            pass
    if info.get("lastplayed"):
        try:
            info_tag.setLastPlayed(str(info["lastplayed"]))
        except Exception:
            pass
    
    # List fields (need to be lists)
    if info.get("genre"):
        genres = info["genre"]
        if isinstance(genres, str):
            genres = [x.strip() for x in genres.split(",") if x.strip()]
        if isinstance(genres, (list, set)):
            info_tag.setGenres(list(genres))
    if info.get("country"):
        countries = info["country"]
        if isinstance(countries, str):
            countries = [x.strip() for x in countries.split(",") if x.strip()]
        if isinstance(countries, (list, set)):
            info_tag.setCountries(list(countries))
    if info.get("director"):
        directors = info["director"]
        if isinstance(directors, str):
            directors = [x.strip() for x in directors.split(",") if x.strip()]
        if isinstance(directors, (list, set)):
            info_tag.setDirectors(list(directors))
    if info.get("writer"):
        writers = info["writer"]
        if isinstance(writers, str):
            writers = [x.strip() for x in writers.split(",") if x.strip()]
        if isinstance(writers, (list, set)):
            info_tag.setWriters(list(writers))
    if info.get("studio"):
        studios = info["studio"]
        if isinstance(studios, str):
            studios = [s.strip() for s in studios.split(",") if s.strip()]
        if isinstance(studios, (list, set)):
            info_tag.setStudios(list(studios))
    if info.get("tag"):
        tags = info["tag"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, (list, set)):
            info_tag.setTags(list(tags))
    if info.get("credits"):
        credits = info["credits"]
        if isinstance(credits, str):
            credits = [c.strip() for c in credits.split(",") if c.strip()]
        if isinstance(credits, (list, set)):
            info_tag.setWriters(list(credits))  # credits maps to writers
    if info.get("artist"):
        artists = info["artist"]
        if isinstance(artists, str):
            artists = [a.strip() for a in artists.split(",") if a.strip()]
        if isinstance(artists, (list, set)):
            info_tag.setArtists(list(artists))
    
    # Cast (needs xbmc.Actor objects)
    if cast:
        actors = normalize_cast_to_actors(cast)
        if actors:
            info_tag.setCast(actors)
    
    # Unique IDs
    if unique_ids and isinstance(unique_ids, dict):
        # Filter out None/empty values and ensure all values are strings
        clean_ids = {k: str(v) for k, v in unique_ids.items() if v}
        if clean_ids:
            info_tag.setUniqueIDs(clean_ids)
    
    # Handle named ratings (rating.tmdb, rating.imdb, rating.simkl, rating.tvdb, etc.)
    for key in info:
        if key.startswith("rating.") and isinstance(info[key], dict):
            rating_name = key.split(".")[1]  # e.g., "tmdb" from "rating.tmdb"
            try:
                rating_value = float(info[key].get("rating", 0.0))
                votes_value = int(info[key].get("votes", 0))
                info_tag.setRating(rating_value, votes_value, rating_name, False)
            except (ValueError, TypeError):
                pass


listitem_properties = [
    (("awards",), "Awards"),
    (("oscar_wins",), "Oscar_Wins"),
    (("oscar_nominations",), "Oscar_Nominations"),
    (("award_wins",), "Award_Wins"),
    (("award_nominations",), "Award_Nominations"),
    (("metacritic_rating",), "Metacritic_Rating"),
    (("rating.tmdb", "rating"), "TMDb_Rating"),
    (("rating.tmdb", "votes"), "TMDb_Votes"),
    (("rating.tvdb", "rating"), "Tvdb_Rating"),
    (("rating.tvdb", "votes"), "Tvdb_Votes"),
    (("rating.imdb", "rating"), "IMDb_Rating"),
    (("rating.imdb", "votes"), "IMDb_Votes"),
    (("rating.simkl", "rating"), "Simkl_Rating"),
    (("rating.simkl", "votes"), "Simkl_Votes"),
    (("rating.mal", "rating"), "MAL_Rating"),
    (("rating.mal", "votes"), "MAL_Votes"),
    (("rottentomatoes_rating",), "RottenTomatoes_Rating"),
    (("rottentomatoes_image",), "RottenTomatoes_Image"),
    (("rottentomatoes_reviewstotal",), "RottenTomatoes_ReviewsTotal"),
    (("rottentomatoes_reviewsfresh",), "RottenTomatoes_ReviewsFresh"),
    (("rottentomatoes_reviewsrotten",), "RottenTomatoes_ReviewsRotten"),
    (("rottentomatoes_consensus",), "RottenTomatoes_Consensus"),
    (("rottentomatoes_usermeter",), "RottenTomatoes_UserMeter"),
    (("rottentomatoes_userreviews",), "RottenTomatoes_UserReviews"),
]


class GlobalVariables:
    CONTENT_MENU = ""
    CONTENT_FILES = "files"
    CONTENT_MOVIE = "movies"
    CONTENT_SHOW = "tvshows"
    CONTENT_ANIME = "anime"
    CONTENT_SEASON = "seasons"
    CONTENT_EPISODE = "episodes"
    CONTENT_GENRES = "genres"
    CONTENT_YEARS = "years"
    CONTENT_ACTORS = "actors"
    MEDIA_MENU = ""
    MEDIA_FOLDER = "file"
    MEDIA_MOVIE = "movie"
    MEDIA_SHOW = "tvshow"
    MEDIA_SEASON = "season"
    MEDIA_EPISODE = "episode"

    SEMVER_REGEX = re.compile(r"^((?:\d+\.){2}\d+)")

    def __init__(self):
        self.IS_ADDON_FIRSTRUN = None
        self.ADDON = None
        self.ADDON_DATA_PATH = None
        self.ADDON_ID = None
        self.ADDON_NAME = None
        self.VERSION = None
        self.CLEAN_VERSION = None
        self.USER_AGENT = None
        self.DEFAULT_FANART = None
        self.DEFAULT_ICON = None
        self.DEFAULT_LOGO = None
        self.DEFAULT_POSTER = None
        self.NEXT_PAGE_ICON = None
        self.ADDON_USERDATA_PATH = None
        self.SETTINGS_CACHE = None
        self.RUNTIME_SETTINGS_CACHE = None
        self.LANGUAGE_CACHE = {}
        self.PLAYLIST = None
        self.HOME_WINDOW = None
        self.KODI_DATE_LONG_FORMAT = None
        self.KODI_DATE_SHORT_FORMAT = None
        self.KODI_TIME_FORMAT = None
        self.KODI_TIME_NO_SECONDS_FORMAT = None
        self.KODI_FULL_VERSION = None
        self.KODI_VERSION = None
        self.PLATFORM = self._get_system_platform()
        self.UTC_TIMEZONE = pytz.utc
        self.LOCAL_TIMEZONE = None
        self.URL = None
        self.PLUGIN_HANDLE = 0
        self.IS_SERVICE = True
        self.BASE_URL = None
        self.PATH = None
        self.PARAM_STRING = None
        self.REQUEST_PARAMS = None
        self.FROM_WIDGET = False
        self.PAGE = 1

    def __del__(self):
        self.deinit()

    def deinit(self):
        # Keep ADDON alive while prefetch threads (and their enrich workers) are still running.
        try:
            from resources.lib.modules.page_prefetch import prefetch_threads_active

            if not prefetch_threads_active():
                self.ADDON = None
        except Exception:
            self.ADDON = None
        self.PLAYLIST = None
        self.HOME_WINDOW = None

    def ensure_addon(self):
        """Re-bind xbmcaddon when a background thread outlives the plugin request."""
        addon = getattr(self, "ADDON", None)
        if addon is not None:
            return
        self.ADDON = xbmcaddon.Addon()
        if not getattr(self, "ADDON_ID", None):
            self.ADDON_ID = self.ADDON.getAddonInfo("id")
        if not getattr(self, "ADDON_NAME", None):
            self.ADDON_NAME = self.ADDON.getAddonInfo("name")
        if not getattr(self, "VERSION", None):
            self.VERSION = self.ADDON.getAddonInfo("version")
        if not getattr(self, "SETTINGS_CACHE", None):
            self._init_settings_cache()

    def init_globals(self, argv=None, addon_id=None):
        self.IS_ADDON_FIRSTRUN = self.IS_ADDON_FIRSTRUN is None
        self.ADDON = xbmcaddon.Addon()
        self.ADDON_ID = addon_id or self.ADDON.getAddonInfo("id")
        self.ADDON_NAME = self.ADDON.getAddonInfo("name")
        self.VERSION = self.ADDON.getAddonInfo("version")
        self.CLEAN_VERSION = self.SEMVER_REGEX.findall(self.VERSION)[0]
        self.USER_AGENT = f"{self.ADDON_NAME} - {self.CLEAN_VERSION}"
        self._init_kodi()
        self._init_settings_cache()
        self._init_local_timezone()
        self._init_paths()
        from resources.lib.modules.settings_hot_cache import warm_settings_dict

        warm_settings_dict()
        self.DEFAULT_FANART = self.ADDON.getAddonInfo("fanart")
        self.DEFAULT_ICON = self.ADDON.getAddonInfo("icon")
        self.DEFAULT_LOGO = f"{self.IMAGES_PATH}logo-prism-4.png"
        self.DEFAULT_POSTER = f"{self.IMAGES_PATH}poster-prism-4.png"
        self.NEXT_PAGE_ICON = f"{self.ICONS_PATH}next.png"
        self.init_request(argv)
        self._init_cache()
        # Warm session caches from the background service only — not every RunPlugin list open.
        if self.PLUGIN_HANDLE <= 0:
            try:
                from resources.lib.modules.prism_version import do_version_change

                do_version_change()
            except Exception:
                self.log_stacktrace()
            try:
                from resources.lib.database.sync_meta_cache import maybe_prefetch_sync_meta

                maybe_prefetch_sync_meta()
            except Exception:
                self.log_stacktrace()
            try:
                from resources.lib.calendar.simkl_calendar import maybe_prefetch_calendars

                maybe_prefetch_calendars()
            except Exception:
                self.log_stacktrace()

    def _init_kodi(self):
        self.PLAYLIST = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        self.HOME_WINDOW = xbmcgui.Window(10000)
        self.KODI_DATE_LONG_FORMAT = xbmc.getRegion("datelong")
        self.KODI_DATE_SHORT_FORMAT = xbmc.getRegion("dateshort")
        self.KODI_TIME_FORMAT = xbmc.getRegion("time")
        self.KODI_TIME_NO_SECONDS_FORMAT = self.KODI_TIME_FORMAT.replace(":%S", "")
        self.KODI_FULL_VERSION = xbmc.getInfoLabel("System.BuildVersion")
        if version := re.findall(r'(?:(?:((?:\d+\.?){1,3}\S+))?\s+\(((?:\d+\.?){2,3})\))', self.KODI_FULL_VERSION):
            self.KODI_FULL_VERSION = version[0][1]
            if len(version[0][0]) > 1:
                pre_ver = version[0][0][:2]
                full_ver = version[0][1][:2]
                if pre_ver > full_ver:
                    self.KODI_VERSION = int(pre_ver[:2])
                else:
                    self.KODI_VERSION = int(full_ver[:2])
            else:
                self.KODI_VERSION = int(version[0][1][:2])
        else:
            self.KODI_FULL_VERSION = self.KODI_FULL_VERSION.split(' ')[0]
            self.KODI_VERSION = int(self.KODI_FULL_VERSION[:2])

    def _init_settings_cache(self):
        self.RUNTIME_SETTINGS_CACHE = RuntimeSettingsCache()
        self.SETTINGS_CACHE = PersistedSettingsCache()

    def _init_local_timezone(self):
        try:
            timezone_string = self.get_setting("general.localtimezone")
            if timezone_string:
                self.LOCAL_TIMEZONE = pytz.timezone(timezone_string)
        except pytz.UnknownTimeZoneError:
            self.log(f"Invalid local timezone '{timezone_string}' in settings.xml", "debug")
        except Exception as e:
            self.log(f"Error using local timezone '{timezone_string}' in settings.xml: {e}", "warning")
        finally:
            if not self.LOCAL_TIMEZONE or self.LOCAL_TIMEZONE == self.UTC_TIMEZONE:
                self.init_local_timezone()

    def init_local_timezone(self):
        """
        Attempts to detect the local timezone via a variety of approaches
        Initializes LOCAL_TIMEZONE to correct tzinfo value
        If this fails we should just use UTC as lack of any LOCAL_TIMEZONE will cause many failures
        :return: None
        """
        timezone_string = None
        try:
            cached = self.get_setting("general.localtimezone")
            if cached:
                try:
                    cached_tz = pytz.timezone(cached)
                    if cached_tz != self.UTC_TIMEZONE:
                        self.LOCAL_TIMEZONE = cached_tz
                        return
                except pytz.UnknownTimeZoneError:
                    self.log(f"Invalid cached local timezone '{cached}', re-detecting", "debug")

            response = self.json_rpc(
                "Settings.GetSettingValue",
                {"setting": "locale.timezone"},
                log_error=False,
            )
            timezone_string = response.get("value")
            if timezone_string:
                try:
                    self.LOCAL_TIMEZONE = pytz.timezone(timezone_string)
                except pytz.UnknownTimeZoneError:
                    self.log(
                        f"Kodi provided an invalid local timezone '{timezone_string}', trying a different approach",
                        "warning",
                    )
            # If Kodi detection failed, fall back on tzlocal
            try:
                if not self.LOCAL_TIMEZONE or self.LOCAL_TIMEZONE == self.UTC_TIMEZONE:
                    from resources.lib.third_party import tzlocal

                    self.LOCAL_TIMEZONE = tzlocal.get_localzone()
            except Exception as e:
                self.log(f"Error detecting local timezone with alternative approach: {e}", "warning")
            # If we still don't have a timezone, try manual setting
            try:
                if not self.LOCAL_TIMEZONE or self.LOCAL_TIMEZONE == self.UTC_TIMEZONE:
                    g.set_setting("general.manualtimezone", True)
                    timezone_string = self.get_setting("general.localtimezone")
                    if timezone_string:
                        self.LOCAL_TIMEZONE = pytz.timezone(timezone_string)
                else:
                    g.set_setting("general.manualtimezone", False)
            except pytz.UnknownTimeZoneError:
                self.log(f"Invalid local timezone '{timezone_string}' in settings.xml", "debug")
            except Exception as e:
                self.log(f"Error using local timezone '{timezone_string}' in settings.xml: {e}", "warning")
        finally:
            # If Kodi and tzocal detection fails and we don't have a valid manual setting, fallback to UTC
            if not self.LOCAL_TIMEZONE:
                self.LOCAL_TIMEZONE = self.UTC_TIMEZONE
            if self.LOCAL_TIMEZONE == self.UTC_TIMEZONE:
                self.log(
                    "Unable to detect local timezone, defaulting to UTC for displayed dates/times. "
                    "Note that this does not affect filtering or sorting, only display",
                    "debug",
                )
            self.set_setting("general.localtimezone", self.LOCAL_TIMEZONE.zone)

    @staticmethod
    def _get_system_platform():
        """
        get platform on which xbmc run
        """
        platform = "unknown"
        if xbmc.getCondVisibility("system.platform.android"):
            platform = "android"
        elif xbmc.getCondVisibility("system.platform.linux"):
            platform = "linux"
        elif xbmc.getCondVisibility("system.platform.xbox"):
            platform = "xbox"
        elif xbmc.getCondVisibility("system.platform.windows"):
            platform = "xbox" if "Users\\UserMgr" in os.environ.get("TMP") else "windows"
        elif xbmc.getCondVisibility("system.platform.osx"):
            platform = "osx"

        return platform

    def _init_cache(self):
        from resources.lib.database.cache import Cache

        self.CACHE = Cache()

    def init_request(self, argv):
        if argv is None:
            return

        self.URL = parse.urlparse(argv[0])
        try:
            self.PLUGIN_HANDLE = int(argv[1])
            self.IS_SERVICE = False
        except IndexError:
            self.PLUGIN_HANDLE = 0
            self.IS_SERVICE = True

        self.BASE_URL = f"{self.URL[0]}://{self.URL[1]}" if self.URL[1] != "" else ""
        self.PATH = parse.unquote(self.URL[2])
        try:
            self.PARAM_STRING = argv[2].lstrip('?/')
        except IndexError:
            self.PARAM_STRING = ""
        self.REQUEST_PARAMS = self.legacy_params_converter(dict(parse.parse_qsl(self.PARAM_STRING)))
        if "action_args" in self.REQUEST_PARAMS:
            self.REQUEST_PARAMS["action_args"] = tools.deconstruct_action_args(self.REQUEST_PARAMS["action_args"])
            if isinstance(self.REQUEST_PARAMS["action_args"], dict):
                self.REQUEST_PARAMS["action_args"] = self.legacy_action_args_converter(
                    self.REQUEST_PARAMS["action_args"]
                )
                from resources.lib.simkl.ids import normalize_action_args

                self.REQUEST_PARAMS["action_args"] = normalize_action_args(self.REQUEST_PARAMS["action_args"])
        self.FROM_WIDGET = not self.is_addon_visible() and self.PLUGIN_HANDLE > 0
        self.PAGE = int(g.REQUEST_PARAMS.get("page", 1))

    @staticmethod
    def legacy_action_args_converter(action_args):
        if "item_type" not in action_args:
            return action_args

        if "season" in action_args["item_type"]:
            from resources.lib.database.session import get_sync_database

            action_args.update(
                get_sync_database().get_season_action_args(action_args["simkl_id"], action_args["season"])
            )

        if "episode" in action_args["item_type"]:
            from resources.lib.database.session import get_sync_database

            action_args.update(
                get_sync_database().get_episode_action_args(
                    action_args["simkl_id"],
                    action_args["season"],
                    action_args["episode"],
                )
            )

        if "show" in action_args["item_type"]:
            action_args["item_type"] = "shows"

        action_args["mediatype"] = action_args.pop("item_type")
        return action_args

    @staticmethod
    def legacy_params_converter(params):
        if "actionArgs" in params:
            params["action_args"] = params.pop("actionArgs")
        if "action" in params:
            if params["action"] == "moviesTrending":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "trending"
                params["mediatype"] = "movies"
            if params["action"] == "moviesPopular":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "popular"
                params["mediatype"] = "movies"
            if params["action"] == "moviesWatched":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "watched"
                params["mediatype"] = "movies"
            if params["action"] == "moviesAnticipated":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "anticipated"
                params["mediatype"] = "movies"
            if params["action"] == "moviesBoxOffice":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "boxoffice"
                params["mediatype"] = "movies"
            if params["action"] == "showsTrending":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "trending"
                params["mediatype"] = "shows"
            if params["action"] == "showsPopular":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "popular"
                params["mediatype"] = "shows"
            if params["action"] == "showsWatched":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "watched"
                params["mediatype"] = "shows"
            if params["action"] == "showsAnticipated":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "anticipated"
                params["mediatype"] = "shows"
            if params["action"] == "showsBoxOffice":
                params["action"] = "genericEndpoint"
                params["endpoint"] = "boxoffice"
                params["mediatype"] = "shows"
        return params

    def _init_paths(self):
        self.ADDONS_PATH = tools.translate_path(os.path.join("special://home/", "addons/"))
        self.ADDON_PATH = tools.translate_path(os.path.join("special://home/", f"addons/{self.ADDON_ID.lower()}"))
        self.ADDON_DATA_PATH = tools.translate_path(self.ADDON.getAddonInfo("path"))  # Addon folder
        self.ADDON_USERDATA_PATH = tools.translate_path(
            f"special://profile/addon_data/{self.ADDON_ID}/"
        )  # Addon user data folder
        self.SETTINGS_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "settings.xml"))
        self.ADVANCED_SETTINGS_PATH = tools.translate_path("special://profile/advancedsettings.xml")
        self.KODI_DATABASE_PATH = tools.translate_path("special://database/")
        self.GUI_PATH = tools.translate_path(os.path.join(self.ADDON_DATA_PATH, "resources", "lib", "gui"))
        self.IMAGES_PATH = f"{self.ADDON.getAddonInfo('path')}/resources/images/"
        # Icon pack: 0 = colored (default), 1 = white. Menus/genres share the same filenames.
        self.ICON_PACK = "white" if self.get_int_setting("general.iconpack", 0) == 1 else "colored"
        self.ICONS_PATH = f"{self.IMAGES_PATH}{self.ICON_PACK}/icons/"
        self.GENRES_PATH = f"{self.IMAGES_PATH}{self.ICON_PACK}/genres/"
        self.SHARED_GENRES_PATH = f"{self.IMAGES_PATH}shared/genres/"
        self.SKINS_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "skins"))
        self.CACHE_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "cache.db"))
        self.TORRENT_CACHE = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "torrentCache.db"))
        self.TORRENT_ASSIST = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "torentAssist.db"))
        self.PROVIDER_CACHE_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "providers.db"))
        self.PREMIUMIZE_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "premiumize.db"))
        self.SIMKL_SYNC_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "simklSync.db"))
        self.PRISM_META_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "prism_meta.db"))
        self.CONTEXT_ADDON_ID = "context.prism"
        self.CONTEXT_ADDON_PATH = tools.translate_path(
            os.path.join("special://home/addons/", self.CONTEXT_ADDON_ID)
        )
        self.INFO_DB_PATH = os.path.join(self.CONTEXT_ADDON_PATH, "info.db")
        self.SEARCH_HISTORY_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "search.db"))
        self.SKINS_DB_PATH = tools.translate_path(os.path.join(self.ADDON_USERDATA_PATH, "skins.db"))

    def get_kodi_video_db_connection(self, max_lock_retries=50):
        config = self.get_kodi_video_db_config()
        if config["type"] == "sqlite3":
            from resources.lib.database import SQLiteConnection

            return SQLiteConnection(
                os.path.join(self.KODI_DATABASE_PATH, f"{config['database']}.db"),
                max_lock_retries=max_lock_retries,
            )
        elif config["type"] == "mysql":
            from resources.lib.database import MySqlConnection

            return MySqlConnection(config)

    def get_kodi_database_version(self):
        kodi_myvideos_version_map = {
            17: 107,
            18: 116,
            19: 119,
            20: 121,
            21: 131,
        }

        if (db_version := kodi_myvideos_version_map.get(self.KODI_VERSION)) is None:
            raise KeyError("Unsupported kodi version")

        return db_version

    def get_kodi_video_db_config(self):
        result = {"type": "sqlite3", "database": f"MyVideos{self.get_kodi_database_version()}"}

        if xbmcvfs.exists(self.ADVANCED_SETTINGS_PATH):
            if advanced_settings_text := g.read_all_text(self.ADVANCED_SETTINGS_PATH):
                try:
                    advanced_settings = ElementTree.fromstring(advanced_settings_text)
                    if settings := advanced_settings.find("videodatabase"):
                        for setting in settings:
                            if setting.tag == 'type':
                                result["type"] = setting.text
                            elif setting.tag == 'host':
                                result["host"] = setting.text
                            elif setting.tag == 'port':
                                result["port"] = setting.text
                            elif setting.tag == 'name':
                                result["database"] = setting.text
                            elif setting.tag == 'user':
                                result["user"] = setting.text
                            elif setting.tag == 'pass':
                                result["password"] = setting.text
                except ElementTree.ParseError as pe:
                    g.log(f"Failed to parse advanced settings.xml: {pe}", "warning")
        return result

    def clear_kodi_bookmarks(self, max_lock_retries=5):
        """Remove stale Kodi-native bookmarks for Prism plugin URLs (fast-fail by default)."""
        import sqlite3

        try:
            with self.get_kodi_video_db_connection(max_lock_retries=max_lock_retries) as video_database:
                if file_ids := [
                    str(i["idFile"])
                    for i in video_database.fetchall(
                        "SELECT * FROM files WHERE strFilename LIKE '%plugin.video.prism%'"
                    )
                ]:
                    video_database.execute_sql(
                        [
                            f"DELETE FROM {table} WHERE idFile IN ({','.join(file_ids)})"
                            for table in ["bookmark", "streamdetails", "files"]
                        ]
                    )
        except sqlite3.OperationalError:
            g.log("Skipping Kodi bookmark cleanup; video database is locked", "debug")

    # region runtime settings
    def set_runtime_setting(self, setting_id, value):
        """
        Set a runtime setting value

        Lists and Dict may only contain simple types

        :param setting_id: The name of the setting
        :type setting_id: str
        :param value: The value to store in settings
        :type value: str|float|int|bool|list|dict
        """
        self.RUNTIME_SETTINGS_CACHE.set_setting(setting_id, value)

    def clear_runtime_setting(self, setting_id):
        """
        Clear a runtime setting from the cache.

        :param setting_id: The name of the setting
        :type setting_id: str
        """
        self.RUNTIME_SETTINGS_CACHE.clear_setting(setting_id)

    def get_runtime_setting(self, setting_id, default_value=None):
        """
        Get a runtime setting value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: str|float|int|bool
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or None
        :rtype: str|float|int|bool|list|dict
        """
        return self.RUNTIME_SETTINGS_CACHE.get_setting(setting_id, default_value)

    def get_float_runtime_setting(self, setting_id, default_value=None):
        """
        Get a runtime setting as a float value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: float
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or 0.0
        :rtype: float
        """
        return self.RUNTIME_SETTINGS_CACHE.get_float_setting(setting_id, default_value)

    def get_int_runtime_setting(self, setting_id, default_value=None):
        """
        Get a runtime setting as an int value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: int
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or 0
        :rtype: int
        """
        return self.RUNTIME_SETTINGS_CACHE.get_int_setting(setting_id, default_value)

    def get_bool_runtime_setting(self, setting_id, default_value=None):
        """
        Get a runtime setting as a bool value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: bool
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or False
        :rtype: bool
        """
        return self.RUNTIME_SETTINGS_CACHE.get_bool_setting(setting_id, default_value)

    # endregion

    # region KODI setting
    def set_setting(self, setting_id, value):
        """
        Set a setting value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param value: The value to store in settings
        :type value: str|float|int|bool
        """
        self.SETTINGS_CACHE.set_setting(setting_id, value)

    def clear_setting(self, setting_id):
        """
        Clear a setting from the cache and settings.xml

        :param setting_id: The name of the setting
        :type setting_id: str
        """
        self.SETTINGS_CACHE.clear_setting(setting_id)

    def get_setting(self, setting_id, default_value=None):
        """
        Get a setting value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value:
        :type default_value: str
        :return: The value of the setting as a string
                 If the setting is not stored, the optional default_value if provided or None
        :rtype: str
        """
        return self.SETTINGS_CACHE.get_setting(setting_id, default_value)

    def get_float_setting(self, setting_id, default_value=None):
        """
        Get a setting as a float value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: float
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or 0.0
        :rtype: float
        """
        return self.SETTINGS_CACHE.get_float_setting(setting_id, default_value)

    def get_int_setting(self, setting_id, default_value=None):
        """
        Get a setting as an int value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: int
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or 0
        :rtype: int
        """
        return self.SETTINGS_CACHE.get_int_setting(setting_id, default_value)

    def get_bool_setting(self, setting_id, default_value=None):
        """
        Get a setting as a bool value

        :param setting_id: The name of the setting
        :type setting_id: str
        :param default_value: An optional default value to provide if the setting is not stored
        :type default_value: bool
        :return: The value of the setting.
                 If the setting is not stored, the optional default_value if provided or False
        :rtype: bool
        """
        return self.SETTINGS_CACHE.get_bool_setting(setting_id, default_value)

    # endregion

    def get_language_string(self, localization_id, addon=True):
        """
        Gets a localized string from cache if feasible, if not from localization files
        Will retrieve from addon localizations by default but can be requested from Kodi localizations

        :param localization_id: The id of the localization to retrieve
        :type localization_id: int
        :param addon: True to retrieve from addon, False from Kodi localizations.  Default True
        :type addon bool
        :return: The localized text matching the localization_id from the appropriate localization files.
        :rtype: str
        """
        cache_id = f"A{str(localization_id)}" if addon else f"K{str(localization_id)}"
        text = self.LANGUAGE_CACHE.get(cache_id)
        if not text:
            if addon:
                self.ensure_addon()
            text = self.ADDON.getLocalizedString(localization_id) if addon else xbmc.getLocalizedString(localization_id)
            self.LANGUAGE_CACHE.update({cache_id: text})

        return text

    def get_view_type(self, content_type):
        use_custom_ids = self.get_bool_setting("general.viewidswitch")
        preset_key, custom_key = self._view_setting_keys(content_type)
        if not preset_key:
            return 0

        if not use_custom_ids:
            preset = self.get_setting(preset_key)
            index = self._view_setting_index(preset)
            if index > 0 and index < len(viewTypes):
                return viewTypes[index][1]
            if content_type == self.CONTENT_ANIME:
                show_index = self._view_setting_index(self.get_setting("show.view"))
                if show_index > 0 and show_index < len(viewTypes):
                    return viewTypes[show_index][1]
            return 0

        custom = self._view_setting_index(self.get_setting(custom_key))
        if custom > 0:
            return custom

        if content_type == self.CONTENT_ANIME:
            show_custom = self._view_setting_index(self.get_setting("show.view.id"))
            if show_custom > 0:
                return show_custom
        return 0

    @staticmethod
    def _view_setting_index(raw) -> int:
        if raw is None:
            return 0
        if isinstance(raw, int):
            return raw
        text = str(raw).strip()
        return int(text) if text.isdigit() else 0

    @staticmethod
    def _view_setting_keys(content_type):
        mapping = {
            GlobalVariables.CONTENT_MENU: ("addon.view", "addon.view.id"),
            GlobalVariables.CONTENT_MOVIE: ("movie.view", "movie.view.id"),
            GlobalVariables.CONTENT_SHOW: ("show.view", "show.view.id"),
            GlobalVariables.CONTENT_ANIME: ("anime.view", "anime.view.id"),
            GlobalVariables.CONTENT_EPISODE: ("episode.view", "episode.view.id"),
            GlobalVariables.CONTENT_SEASON: ("season.view", "season.view.id"),
        }
        return mapping.get(content_type, (None, None))

    @staticmethod
    def kodi_content_type(content_type):
        """Map Prism list content keys to ``xbmcplugin.setContent`` values."""
        if content_type == GlobalVariables.CONTENT_ANIME:
            return GlobalVariables.CONTENT_SHOW
        return content_type

    def log(self, msg, level="info"):
        msg = msg
        msg = f"{self.ADDON_NAME.upper()} ({self.PLUGIN_HANDLE}): {msg}"
        if level == "error":
            xbmc.log(msg, level=xbmc.LOGERROR)
        elif level == "info":
            xbmc.log(msg, level=xbmc.LOGINFO)
        elif level == "debug":
            xbmc.log(msg, level=xbmc.LOGDEBUG)
        elif level == "notice":
            if self.KODI_VERSION >= 19:
                xbmc.log(msg, level=xbmc.LOGINFO)
            else:
                xbmc.log(msg, level=xbmc.LOGNOTICE)  # pylint: disable=no-member
        elif level == "warning":
            xbmc.log(msg, level=xbmc.LOGWARNING)
        else:
            xbmc.log(msg)

    def log_stacktrace(self):
        """Gets the latest traceback stacktrace and logs it."""
        self.log(traceback.format_exc(), "error")

    def color_picker(self):
        """Open the grid color picker window and persist the chosen highlight color."""
        from resources.lib.database.skinManager import SkinManager
        from resources.lib.gui.windows.color_picker import ColorPickerWindow

        window = ColorPickerWindow(*SkinManager().confirm_skin_path("color_picker.xml"))
        selection = window.doModal()
        if selection is None:
            return
        if selection in ("None", "inherit"):
            self.set_setting("general.textColor", "None")
            self.set_setting("general.displayColor", "None")
        else:
            self.set_setting("general.textColor", selection)
            self.set_setting("general.displayColor", selection)
        self.open_addon_settings(1, 2)

    @staticmethod
    def _try_get_color_from_skin():
        skin_color_lookup = {
            "skin.arctic.horizon": "ff0385b5",
            "skin.arctic.horizon.2": "ff0385b5",
            "skin.arctic.zephyr": "ff0385b5",
            "skin.arctic.zephyr.2": "ff0385b5",
            "skin.aura": "ff0385b5",
            "skin.auramod": "deepskyblue",
            "skin.confluence": "ffeb9e17",
            "skin.eminence": {
                "default": "ff33b5e5",
                "crimson": "ffdc143c",
                "emerald": "ffdc143c",
                "lilac": "ffa682a6",
                "lime": "ffb5e533",
                "magenta": "ffe533b5",
                "orange": "ffd76c38",
            },
            "skin.eminence.2": "ff287ba8",
            "skin.estouchy": "ff11e7b1",
            "skin.estuary": {
                "default": "ff11e7b1",
                "brown": "ffff4400",
                "charcoal": "ff11e7b1",
                "chartreuse": "ff24c6c9",
                "concrete": "ffff8c00",
                "gold": "fffff000",
                "green": "ff14d519",
                "maroon": "ff24c6c9",
                "midnight": "ff5be5ee",
                "orange": "fffff100",
                "pink": "ff94d800",
                "rose": "ffff0261",
                "teal": "ffc67f03",
                "violet": "ffff0054",
            },
            "skin.fuse.neue": "ffe53564",
        }

        if focus_color := xbmc.getInfoLabel("Skin.String(focuscolor.name)"):  # jurial based skins.
            return focus_color

        if skin_dir := xbmc.getSkinDir():
            if isinstance(skin_color := skin_color_lookup.get(skin_dir), dict):
                skin_color = skin_color.get(
                    xbmc.getInfoLabel("Skin.CurrentTheme").lower(), skin_color.get("default", None)
                )
            return skin_color
        return None

    def get_user_text_color(self):
        """Get the user selected color setting when nothing is selecting it returns
        the default value (deepskyblue) or the default form the installed skin.

        :return:Selected color from the user or the default from the skin or the Prism default (deepskyblue)
        :rtype:str
        """
        color = self.get_setting("general.displayColor")
        if not color or color == "None" or color == "inherit":
            color = self._try_get_color_from_skin() or "deepskyblue"

        return color

    def color_string(self, text, color=None):
        """Method that wraps the text with the supplied color, or takes the user default.

        :param text:Text that needs to be wrapped
        :type text:str|int|float
        :param color:Color name used in the Kodi color tag
        :type color:str
        :return:Text wrapped in a Kodi color tag.
        :rtype:str
        """
        if color == "default" or not color or color in ("None", "inherit"):
            color = self.get_user_text_color()

        return f"[COLOR {color}]{text}[/COLOR]"

    def clear_cache(self, silent=False):
        if not silent:
            confirm = xbmcgui.Dialog().yesno(self.ADDON_NAME, self.get_language_string(30029))
            if confirm != 1:
                return
        g.CACHE.clear_all()
        g._init_cache()
        try:
            from resources.lib.modules.cache_maintenance import invalidate_all_menu_caches

            invalidate_all_menu_caches(include_api_cache=True)
        except Exception:
            self.log_stacktrace()
        self.log(f"{self.ADDON_NAME}: Cache Cleared", "debug")
        if not silent:
            xbmcgui.Dialog().notification(self.ADDON_NAME, self.get_language_string(30052))

    def cancel_playback(self):
        self.PLAYLIST.clear()
        xbmcplugin.setResolvedUrl(self.PLUGIN_HANDLE, False, xbmcgui.ListItem(offscreen=True))
        self.close_busy_dialog()
        self.close_all_dialogs()

    def show_busy_dialog(self):
        xbmc.executebuiltin("ActivateWindow(busydialognocancel)")

    def close_all_dialogs(self):
        xbmc.executebuiltin("Dialog.Close(all,true)")

    def close_ok_dialog(self):
        xbmc.executebuiltin("Dialog.Close(okdialog, true)")

    def close_busy_dialog(self):
        xbmc.executebuiltin("Dialog.Close(busydialog)")
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    @staticmethod
    def deaccent_string(text):
        """Deaccent the provided text leaving other unicode characters intact
        Example: Mîxéd ДљфӭЖ Tëst -> Mixed ДљфэЖ Test
        :param: text: Text to deaccent
        :type text: str
        :return: Deaccented string
        :rtype:str
        """
        nfkd_form = unicodedata.normalize('NFKD', text)  # pylint: disable=c-extension-no-member
        return "".join([c for c in nfkd_form if not unicodedata.combining(c)])  # pylint: disable=c-extension-no-member

    @staticmethod
    def transliterate_string(text):
        """Transliterate the provided text into ascii equivalents
        Example: 张新成 -> Zhang Xincheng
        Note that this method will remove any extra whitespace leaving only single spaces *between* characters
        :param: text: Text to transliterate
        :type text: str
        :return: Transliterated string
        :rtype:str
        """
        transliterted_text = unidecode(text)
        # Remove extra whitespace
        transliterted_text = " ".join(transliterted_text.split())
        return transliterted_text

    def premium_check(self):
        return bool(
            self.PLAYLIST.getposition() > 0 or self.debrid_available() or self.local_playback_available()
        )

    def local_playback_available(self):
        import xbmcvfs

        if not self.get_bool_setting('local.fileInspection'):
            return False
        for setting_id in ('local.location', 'download.location'):
            folder = (self.get_setting(setting_id) or '').strip()
            if folder and folder.lower() != 'userdata' and xbmcvfs.exists(folder):
                return True
        return False

    def debrid_available(self):
        return (
            self.premiumize_enabled()
            or self.real_debrid_enabled()
            or self.all_debrid_enabled()
            or self.torbox_enabled()
            or self.offcloud_enabled()
        )

    @staticmethod
    def configured_directory_path(setting_id):
        path = (g.get_setting(setting_id) or '').strip()
        return bool(path) and path.lower() != 'userdata'

    def my_files_available(self):
        if self.debrid_available():
            return True
        return self.configured_directory_path('download.location') or self.configured_directory_path('local.location')

    def premiumize_enabled(self):
        return bool(self.get_setting("premiumize.token") != "" and self.get_bool_setting("premiumize.enabled"))

    def real_debrid_enabled(self):
        return bool(self.get_setting("rd.auth") and self.get_bool_setting("realdebrid.enabled"))

    def all_debrid_enabled(self):
        return bool(self.get_setting("alldebrid.apikey") != "" and self.get_bool_setting("alldebrid.enabled"))

    def torbox_enabled(self):
        return bool(self.get_setting("tb.token") != "" and self.get_bool_setting("torbox.enabled"))

    def offcloud_enabled(self):
        return bool(self.get_setting("oc.token") != "" and self.get_bool_setting("offcloud.enabled"))

    def container_refresh(self):
        return xbmc.executebuiltin("Container.Refresh")

    def trigger_widget_refresh(self, if_playing=True):
        """
        Trigger a widget refresh using the update library with an invalid path trick
        Widget refresh will not be attempted if playing if if_playing is False or if another process is waiting to
        refresh widgets.
        The widget refresh will not occur until the user returns to the home window to prevent updating other addon
        containers.

        Care must be taken when executing this method as it will block until the home window becomes available or
        playback is started or if the library is already being scanned
        :param if_playing: Whether to attempt to refresh widgets if playing.  Default: True
        :type if_playing: bool
        """
        player = xbmc.Player()
        if (
            self.get_bool_runtime_setting("widget_refreshing")
            or (player.isPlaying() and not if_playing)
            or xbmc.getCondVisibility(  # Don't wait if we are playing as it will refresh after
                "Library.IsScanningVideo"
            )  # Don't do library update if already scanning library
        ):
            del player
            return
        try:
            from resources.lib.modules.widget_loader import clear_widget_session_flags

            clear_widget_session_flags()
            g.set_runtime_setting("widget_refreshing", True)
            while not self.abort_requested():
                if xbmcgui.getCurrentWindowId() == 10000:
                    if not (
                        self.wait_for_abort(0.2)
                        or xbmc.getCondVisibility(  # Short wait to see if anything else kicks off a widget refresh
                            "Container().isUpdating"
                        )  # Check if widgets currently refreshing
                    ):
                        self.log("TRIGGERING WIDGET REFRESH")
                        xbmc.executebuiltin('UpdateLibrary(video,widget_refresh,true)')
                    else:
                        self.log("Aborting widget refresh as container on home screen already refreshing", "debug")
                    break
                elif (
                    self.wait_for_abort(0.3) or player.isPlaying() or xbmc.getCondVisibility("Library.IsScanningVideo")
                ):
                    self.log("Aborting queued widget refresh as another process will trigger it", "debug")
                    break
        finally:
            del player
            g.clear_runtime_setting("widget_refreshing")

    def get_language_code(self, country=None):
        lang = self.json_rpc("Application.getProperties", {"properties": ["language"]}).get("language")
        if lang is None:
            g.log("Could not determine language", "warning")
            return ""
        if country:
            if lang == "en_GB" and g.get_setting("general.localtimezone") in pytz.country_timezones["US"]:
                lang = "en_US"  # Workaround for Americans that just can't comprehend that Kodi default is en_GB
            lang = lang.split("_")
            if len(lang) > 1 and len(lang[1]) == 2:
                lang = f"{lang[0].lower()}-{lang[1].upper()}"
                return lang
            else:
                g.log("Could not determine country part of language", "warning")
                return ""
        return lang.split("_")[0].lower()

    @cached_property
    def common_video_extensions(self):
        return tuple({ext for ext in xbmc.getSupportedMedia("video").split("|") if ext not in {"", ".zip", ".rar", ".url"}})

    def _localize_anime_title(self, name, info):
        """Apply anime title language and optional (Dub) prefix — updates info.title like title language does."""
        display = name
        try:
            if info and info.get("mediatype") in ("tvshow", "movie"):
                ids = info.get("ids") if isinstance(info.get("ids"), dict) else {}
                is_anime = info.get("catalog") == "anime" or info.get("mal_id") or ids.get("mal")
                if is_anime:
                    from resources.lib.simkl.field_map import pick_anime_display_title

                    prefer_romaji = self.get_int_setting("general.anime.titlelanguage") == 1
                    preferred = pick_anime_display_title(info, prefer_romaji=prefer_romaji)
                    if preferred:
                        display = preferred
                        relation = info.get("relation_type")
                        if relation:
                            display = f"{display} ({relation})"
                        info["title"] = display
                        if info.get("mediatype") == "tvshow":
                            info["tvshowtitle"] = display
        except Exception:
            pass

        if not self.get_bool_setting("general.anime.showDubLabel", False):
            return display
        if not info or info.get("mediatype") not in ("tvshow", "movie"):
            return display
        if "(Dub)" in str(display):
            return display

        mal_id = info.get("mal_id")
        if mal_id is None and isinstance(info.get("ids"), dict):
            mal_id = info["ids"].get("mal")
        if mal_id is None:
            return display

        try:
            from resources.lib.anime.mal_dubs import has_dub

            if has_dub(mal_id):
                display = f"{self.color_string('(Dub)', 'blue')} {display}"
                info["title"] = display
                if info.get("mediatype") == "tvshow":
                    info["tvshowtitle"] = display
        except Exception:
            self.log_stacktrace()

        return display

    def add_directory_item(self, name, **params):
        menu_item = params.pop("menu_item", {})
        if not isinstance(menu_item, dict):
            menu_item = {}

        info = menu_item.pop("info", {})

        if info is None or not isinstance(info, dict):
            info = {}

        name = self._localize_anime_title(name, info)

        item = xbmcgui.ListItem(label=name, offscreen=True)
        item.setContentLookup(False)

        self._apply_listitem_properties(item, info)
        if self.studio_limit:
            self.handle_studio_icon_skin_workaround(item, info)

        if "unwatched_episodes" in menu_item:
            item.setProperty("UnWatchedEpisodes", str(menu_item["unwatched_episodes"]))
        if "watched_episodes" in menu_item:
            item.setProperty("WatchedEpisodes", str(menu_item["watched_episodes"]))

        mediatype = (info.get("mediatype") or "").lower()
        if mediatype in ("tvshow", "season") and menu_item.get("episode_count", 0) and menu_item.get("watched_episodes", 0):
            ep_count = int(menu_item["episode_count"])
            watched_eps = int(menu_item["watched_episodes"])
            if ep_count == watched_eps:
                info["playcount"] = 1
            else:
                item.setProperty(
                    "WatchedProgress",
                    str(max(1, int((float(watched_eps) / float(ep_count)) * 100))),
                )
        if (
            menu_item.get("watched_episodes", 0) == 0
            and menu_item.get("episode_count", 0)
            and menu_item.get("episode_count", 0) > 0
        ):
            item.setProperty("WatchedEpisodes", str(0))
            item.setProperty("UnWatchedEpisodes", str(menu_item.get("episode_count", 0)))
        if "episode_count" in menu_item:
            item.setProperty("TotalEpisodes", str(menu_item["episode_count"]))
        if "season_count" in menu_item:
            item.setProperty("TotalSeasons", str(menu_item["season_count"]))

        resolved_play_count = None
        if menu_item.get("play_count") is not None:
            try:
                resolved_play_count = int(menu_item["play_count"])
            except (TypeError, ValueError):
                resolved_play_count = 0
        episode_watched = resolved_play_count is not None and resolved_play_count > 0

        resume_time = menu_item.get("resume_time")
        if resume_time is None and menu_item.get("progress") is not None:
            resume_time = menu_item.get("progress")
            menu_item["resume_time"] = resume_time

        partial_percent = 0.0
        try:
            partial_percent = float(menu_item.get("percent_played") or 0)
        except (TypeError, ValueError):
            partial_percent = 0.0

        has_resume_time = False
        try:
            has_resume_time = resume_time is not None and int(resume_time) > 0
        except (TypeError, ValueError):
            has_resume_time = False

        force_resume_indicator = bool(menu_item.get("force_resume_indicator"))
        has_active_partial = (0 < partial_percent < 100) or has_resume_time
        if episode_watched and not force_resume_indicator:
            # Completed episodes show a checkmark; stale bookmarks must not keep the half-circle.
            episode_in_progress = False
            resume_time = None
            has_resume_time = False
            partial_percent = 0.0
        else:
            episode_in_progress = has_active_partial

        duration = info.get("duration")
        if not duration:
            runtime = info.get("runtime")
            try:
                duration = int(float(runtime)) * 60 if runtime else None
            except Exception:
                duration = None

        if episode_in_progress:
            if partial_percent > 0:
                item.setProperty("percentplayed", str(partial_percent))
            if has_resume_time:
                params["resume"] = str(resume_time)
                item.setProperty("resumetime", str(resume_time))
                if duration:
                    item.setProperty("totaltime", str(int(duration)))

            watched_progress_set = False
            try:
                if duration and float(duration) > 0 and has_resume_time:
                    progress = int((float(resume_time) / float(duration)) * 100)
                    progress = max(0, min(100, progress))
                    item.setProperty("WatchedProgress", str(progress))
                    watched_progress_set = True
            except Exception:
                pass
            if not watched_progress_set and 0 < partial_percent < 100:
                item.setProperty("WatchedProgress", str(int(round(partial_percent))))

            # Kodi half-watched overlay requires playcount 0 (not fully watched).
            info["playcount"] = 0
        elif resolved_play_count is not None:
            info["playcount"] = resolved_play_count if resolved_play_count > 0 else 0

        if duration and not info.get("duration"):
            info["duration"] = duration
        if "air_date" in menu_item and menu_item.get("air_date") is not None:
            # shows.air_date is often min(episode air_date) for hide-unaired SQL — not show premiere.
            if info.get("mediatype") != "tvshow" or not (info.get("premiered") or info.get("aired")):
                info["premiered"] = menu_item["air_date"]
                info["aired"] = menu_item["air_date"]
        if "description" in params:
            info["plot"] = info["overview"] = info["description"] = params.pop("description", None)
        if menu_item.get("user_rating"):
            item.setProperty("userrating", str(menu_item["user_rating"]))

        special_sort = params.pop("special_sort", None)
        if special_sort is not None:
            item.setProperty("SpecialSort", str(special_sort))
        label2 = params.pop("label2", None)
        if label2 is not None:
            item.setLabel2(label2)

        if params.pop("is_playable", False):
            item.setProperty("IsPlayable", "true")
            is_folder = params.pop("is_folder", False)
        else:
            item.setProperty("IsPlayable", "false")
            is_folder = params.pop("is_folder", True)

        cast = menu_item.get("cast", [])
        if cast is None or not isinstance(cast, (set, list)):
            cast = []

        for key, value in info.items():
            if key.endswith("_id"):
                item.setProperty(key, str(value))

        # Build unique IDs dict for InfoTagVideo
        media_type = info.get("mediatype", None)
        id_keys = {
            "tmdb_id": "tmdb",
            "imdb_id": "imdb",
            "tvdb_id": "tvdb",
        }
        unique_ids = {
            unique_id_key: info[f"tvshow.{id_key}" if media_type in ["episode", "season"] else id_key]
            for id_key, unique_id_key in id_keys.items()
            if info.get(f"tvshow.{id_key}" if media_type in ["episode", "season"] else id_key)
        }

        # Named ratings are now handled in set_video_info_tag()

        cm = params.pop("cm", [])
        if cm is None or not isinstance(cm, (set, list)):
            cm = []
        item.addContextMenuItems(cm)

        art = menu_item.pop("art", {})
        if art is None or not isinstance(art, dict):
            art = {}
        thumb_fallback = (
            art.get("thumb")
            or art.get("poster")
            or art.get("season.poster")
            or art.get("tvshow.poster")
            or art.get("landscape")
            or art.get("season.landscape")
            or art.get("tvshow.landscape")
        )
        if (
            art.get("fanart", art.get("season.fanart", art.get("tvshow.fanart", None))) is None
        ) and not self.fanart_fallback_disabled:
            art["fanart"] = self.DEFAULT_FANART
        if art.get("poster", art.get("season.poster", art.get("tvshow.poster", None))) is None:
            art["poster"] = self.DEFAULT_POSTER
        if not art.get("icon"):
            art["icon"] = thumb_fallback or self.DEFAULT_ICON
        if not art.get("thumb"):
            art["thumb"] = thumb_fallback or art.get("poster") or self.DEFAULT_POSTER
        with contextlib.suppress(Exception):
            item.setArt(art)

        # Clear out keys not relevant to Kodi info labels
        self.clean_info_keys(info)
        # Convert dates to localtime for display
        self.convert_info_dates(info)

        # Use InfoTagVideo API (Kodi 21+) instead of deprecated setInfo/setCast/setUniqueIDs
        set_video_info_tag(item, info, cast=cast, unique_ids=unique_ids)

        if episode_in_progress:
            try:
                info_tag = item.getVideoInfoTag()
                if duration and float(duration) > 0:
                    if has_resume_time:
                        info_tag.setResumePoint(float(resume_time), float(duration))
                    elif 0 < partial_percent < 100:
                        info_tag.setResumePoint(float(duration) * partial_percent / 100.0, float(duration))
                elif has_resume_time:
                    info_tag.setResumePoint(float(resume_time))
            except Exception:
                pass

        item.setLabel(name)

        bulk_add = params.pop("bulk_add", False)
        url = self.create_url(self.BASE_URL, params)
        if bulk_add:
            return url, item, is_folder
        else:
            xbmcplugin.addDirectoryItem(handle=self.PLUGIN_HANDLE, url=url, listitem=item, isFolder=is_folder)

    def add_menu_items(self, item_list):
        xbmcplugin.addDirectoryItems(self.PLUGIN_HANDLE, item_list, len(item_list))

    @staticmethod
    def clean_info_keys(info_dict):
        if not isinstance(info_dict, dict):
            return info_dict

        for key in info_dict.keys() - info_labels:
            info_dict.pop(key)

        return info_dict

    def convert_info_dates(self, info_dict):
        if not isinstance(info_dict, dict):
            return info_dict

        converted_dates = {key: self.utc_to_local(info_dict.get(key)) for key in info_dict.keys() & info_dates}
        info_dict.update(converted_dates)

        return info_dict

    def kodi_menu_caching_enabled(self) -> bool:
        """When true, Kodi may reuse cached folder listings (instant back navigation)."""
        if (self.REQUEST_PARAMS or {}).get("prism_reload") == "true":
            return False
        return self.get_bool_setting("general.menucaching", True) and not self.FROM_WIDGET

    def refresh_visible_container(self) -> bool:
        """Rebuild the active Prism folder so list art/meta repaint after background actions."""
        import time
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        import xbmc

        if not self.is_addon_visible():
            return False

        path = xbmc.getInfoLabel("Container.FolderPath") or ""

        # RunPlugin context actions (handle -1) must not rewrite folder history.
        if self.PLUGIN_HANDLE <= 0:
            return bool(self.container_refresh())

        if not path.startswith("plugin://plugin.video.prism"):
            return False

        parsed = urlparse(path)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["prism_reload"] = "true"
        query["_cb"] = str(int(time.time()))
        new_path = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        escaped = new_path.replace("\\", "\\\\").replace('"', '\\"')
        xbmc.executebuiltin(f'Container.Update("{escaped}",replace)')
        return True

    def close_directory(self, content_type, sort=False, cache=None):
        if sort == "title":
            xbmcplugin.addSortMethod(self.PLUGIN_HANDLE, xbmcplugin.SORT_METHOD_LABEL_IGNORE_THE)
        if sort == "episode":
            xbmcplugin.addSortMethod(self.PLUGIN_HANDLE, xbmcplugin.SORT_METHOD_EPISODE)
        if not sort:
            xbmcplugin.addSortMethod(self.PLUGIN_HANDLE, xbmcplugin.SORT_METHOD_NONE)
        xbmcplugin.setContent(self.PLUGIN_HANDLE, self.kodi_content_type(content_type))
        if self.FROM_WIDGET:
            menu_caching = False
        elif not self.get_bool_setting("general.menucaching", True):
            menu_caching = bool(cache) if cache is not None else False
        elif cache is False:
            menu_caching = False
        else:
            menu_caching = True
        xbmcplugin.endOfDirectory(self.PLUGIN_HANDLE, cacheToDisc=menu_caching)
        self.set_view_type(content_type)
        if content_type == self.CONTENT_EPISODE and self.get_bool_setting("general.smart.scroll.enable", True):
            tools.run_threaded(self._episode_smart_scroll)

    def _episode_smart_scroll(self) -> None:
        """Scroll episode lists to the next unwatched item (Otaku-style)."""
        import xbmc
        import xbmcgui

        if not self.is_addon_visible():
            return
        if self.get_bool_setting("general.setViews"):
            xbmc.sleep(150)
        for _ in range(56):
            if self.abort_requested():
                return
            window_id = xbmcgui.getCurrentWindowId()
            if window_id != 10025:
                xbmc.sleep(80)
                continue
            window = xbmcgui.Window(window_id)
            active_id = window.getFocusId()
            if not active_id:
                xbmc.sleep(80)
                continue
            try:
                num_watched = int(xbmc.getInfoLabel("Container.TotalWatched"))
                total_ep = int(xbmc.getInfoLabel("Container.NumItems"))
                all_items = int(xbmc.getInfoLabel("Container.NumAllItems"))
            except (TypeError, ValueError):
                xbmc.sleep(80)
                continue
            if all_items <= 0:
                xbmc.sleep(80)
                continue
            offset = 1 if all_items > total_ep else 0
            target_index = num_watched + offset
            if not (0 < target_index < all_items):
                return
            xbmc.executebuiltin("Action(firstpage)")
            xbmc.sleep(50)
            xbmc.executebuiltin(f"Control.SetFocus({active_id}, {target_index})")
            return

    def set_view_type(self, content_type):
        def _execute_set_view_mode(view):
            xbmc.sleep(200)
            xbmc.executebuiltin(f"Container.SetViewMode({view})")

        if self.get_bool_setting("general.setViews") and self.is_addon_visible():
            view_type = self.get_view_type(content_type)
            if view_type > 0:
                tools.run_threaded(_execute_set_view_mode, view_type)

    def is_addon_visible(self):
        return xbmc.getInfoLabel('Container.PluginName') == "plugin.video.prism"

    def cancel_directory(self):
        if g.FROM_WIDGET:
            g.add_directory_item(
                g.get_language_string(284, addon=False),
                menu_item=g.create_icon_dict("simkl_sync", base_path=g.ICONS_PATH),
            )
            xbmcplugin.setContent(self.PLUGIN_HANDLE, g.CONTENT_MENU)
            xbmcplugin.endOfDirectory(self.PLUGIN_HANDLE, succeeded=True, cacheToDisc=False)
        else:
            xbmcplugin.endOfDirectory(self.PLUGIN_HANDLE, succeeded=False, cacheToDisc=False)

    def read_all_text(self, file_path):
        try:
            f = xbmcvfs.File(file_path, "r")
            return f.read()
        except OSError:
            return None
        finally:
            with contextlib.suppress(Exception):
                f.close()

    def write_all_text(self, file_path, content):
        try:
            f = xbmcvfs.File(file_path, "w")
            return f.write(content)
        except OSError:
            return None
        finally:
            with contextlib.suppress(Exception):
                f.close()

    def notification(self, heading, message, time=5000, sound=True):
        if self.get_bool_setting("general.disableNotificationSound"):
            sound = False
        dialogue = xbmcgui.Dialog()
        dialogue.notification(heading, message, time=time, sound=sound)
        del dialogue

    def get_keyboard_input(self, heading=None, default=None, hidden=False):
        k = xbmc.Keyboard(default, heading, hidden)
        k.doModal()
        input_value = k.getText() if k.isConfirmed() else None
        del k
        return input_value

    def json_rpc(self, method, params=None, log_error=True):
        request_data = {
            "jsonrpc": "2.0",
            "method": method,
            "id": 1,
            "params": params or {},
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(request_data)))
        if "error" in response:
            if log_error:
                self.log(f"JsonRPC Error {response['error']['code']}: {response['error']['message']}", "debug")
        return response.get("result", {})

    def get_kodi_subtitle_languages(self, iso_format=False):
        subtitle_language = self.json_rpc("Settings.GetSettingValue", {"setting": "subtitles.languages"})
        if iso_format:
            return [self.convert_language_iso(x) for x in subtitle_language["value"]]
        else:
            return list(subtitle_language["value"])

    def get_kodi_preferred_subtitle_language(self, iso_format=False):
        subtitle_language = self.json_rpc("Settings.GetSettingValue", {"setting": "locale.subtitlelanguage"})
        if subtitle_language["value"] in ["forced_only", "original", "default", "none"]:
            return subtitle_language["value"]
        if iso_format:
            return self.convert_language_iso(subtitle_language["value"])
        else:
            return subtitle_language["value"]

    def convert_language_iso(self, from_value):
        return xbmc.convertLanguage(from_value, xbmc.ISO_639_1)

    @staticmethod
    def _apply_listitem_properties(item, info):
        for prop in listitem_properties:
            value = info
            for subkey in prop[0]:
                value = value.get(subkey, {})

            if value:
                item.setProperty(prop[1], str(value))

    @cached_property
    def fanart_fallback_disabled(self):
        return g.get_bool_setting("general.fanart.fallback", False)

    @cached_property
    def studio_limit(self):
        return g.get_bool_setting("general.meta.studiolimit", True)

    def handle_studio_icon_skin_workaround(self, item, info):
        media_type = info.get("mediatype")
        studios = info.get("studio", [])
        property_name = "studio" if media_type == g.MEDIA_MOVIE else "network"

        if isinstance(studios, list) and len(studios) >= 1:
            for studio in reversed(studios):
                if (lower_studio := studio.lower()) in self.studio_icons or (
                    lower_studio.endswith(" ltd.") and (lower_studio := lower_studio[:-5]) in self.studio_icons
                ):
                    item.setProperty(property_name, lower_studio)
                    info['studio'] = studio
                    return

            item.setProperty(property_name, studios[0].lower())
            info['studio'] = studios[0]
        elif studios and isinstance(studios, str):
            item.setProperty(property_name, studios.lower())
            info['studio'] = studios

    @cached_property
    def studio_icons(self):
        colored = {i[:-4] for i in xbmcvfs.listdir("resource://resource.images.studios.coloured")[1]}
        white = {i[:-4] for i in xbmcvfs.listdir("resource://resource.images.studios.white")[1]}

        return colored | white if (colored and white) else colored or white

    def create_url(self, base_url, params):
        if params is None:
            return base_url
        if "action_args" in params and isinstance(params["action_args"], dict):
            params["action_args"] = tools.construct_action_args(params["action_args"])
        return f"{base_url}/?{parse.urlencode(sorted(params.items()))}"

    @staticmethod
    def abort_requested():
        monitor = xbmc.Monitor()
        abort_requested = monitor.abortRequested()
        del monitor
        return abort_requested

    @staticmethod
    def wait_for_abort(timeout=1.0):
        monitor = None
        try:
            monitor = xbmc.Monitor()
            abort_requested = monitor.waitForAbort(timeout)
        finally:
            del monitor

        return abort_requested

    @staticmethod
    def reload_profile():
        xbmc.executebuiltin(f"LoadProfile({xbmc.getInfoLabel('system.profilename')})")

    @staticmethod
    def validate_date(date_string):
        """Validates the date string and returns in standard date time format, if it is invalid it just returns none.

        :param date_string:string value with a supposed date.
        :type date_string:str
        :return:formatted datetime or none
        :rtype:str
        """
        if not date_string:
            return date_string

        try:
            result = tools.parse_datetime(date_string, False)
        except ValueError:
            return None

        if result and result.year > 1900:
            return str(g.datetime_to_string(result))
        return None

    @staticmethod
    def datetime_to_string(date_time_or_date):
        """
        Converts a datetime or date object to a string in ISO format truncated to seconds and without TZ offset
        If this is a date, then the isoformat for a date without time is returned
        :param date_time_or_date: A datetime.datetime or a datetime.date object
        :return: Datetime or date string in ISO format truncated to seconds and without TZ offset.
                 Empty string if invalid object provided
        :rtype str
        """
        try:
            if hasattr(date_time_or_date, "second"):
                dt = date_time_or_date
                if getattr(dt, "tzinfo", None) is not None:
                    dt = dt.replace(tzinfo=None)
                return dt.isoformat(timespec="seconds")
            return date_time_or_date.isoformat()
        except (ValueError, AttributeError):
            return ""

    def utc_to_local(self, utc_string):
        """
        Converts a UTC style datetime string to the local timezone
        :param utc_string: UTC datetime string
        :return: localized datetime string
        """
        if not utc_string:
            return None

        if isinstance(utc_string, datetime.datetime):
            parsed = utc_string
        elif isinstance(utc_string, datetime.date):
            return g.datetime_to_string(utc_string)
        else:
            try:
                parsed = tools.parse_datetime(utc_string, False)
            except ValueError:
                return None

        if parsed is None:
            return None

        if isinstance(parsed, datetime.date) and not isinstance(parsed, datetime.datetime):
            return g.datetime_to_string(parsed)

        if parsed.tzinfo is not None:
            local_time = parsed.astimezone(self.LOCAL_TIMEZONE)
        else:
            local_time = self.UTC_TIMEZONE.localize(parsed).astimezone(self.LOCAL_TIMEZONE)

        return g.datetime_to_string(local_time)

    def local_to_utc_by_country(self, datetime_string, country_code):
        """
        Converts a local datetime string to the utc timezone based on a provided country code
        Note that some countries have moe than one timezone, we choose the first as there is no perfect solution
        :param datetime_string: Local date time string
        :type datetime_string: str
        :param country_code: The ISO-3166 2 char country code
        :return: UTC datetime string
        """
        if not datetime_string:
            return None

        try:
            local_time = tools.parse_datetime(datetime_string, False)
            tz = pytz.timezone(pytz.country_timezones[country_code][0])  # First timezone for a country
            local_time = tz.localize(local_time)
            utc_time = local_time.astimezone(self.UTC_TIMEZONE)
            return g.datetime_to_string(utc_time)
        except (KeyError, IndexError, pytz.UnknownTimeZoneError) as e:
            g.log(
                f"Unable to covert local time to utc based on country_code='{country_code}', "
                f"datetime_string='{datetime_string}'/n{e}",
                "error",
            )
            return None

    def open_addon_settings(self, section_offset, setting_offset=None):
        """
        Open prism settings at a particular section and setting
        :param section_offset: Section number starting at 0
        :type section_offset: int
        :param setting_offset: Setting number within section, starting at 0.  Note that sep / lsep are counted.
        :type setting_offset: None|int
        :return:
        """
        xbmc.executebuiltin(f"Addon.OpenSettings({self.ADDON_ID})")
        xbmc.executebuiltin(f"SetFocus({-(100 - section_offset)})")
        if setting_offset is not None:
            xbmc.executebuiltin(f"SetFocus({-(80 - setting_offset)})")

    def create_icon_dict(self, icon_slug, base_path, art_types=None):
        keys = art_types or ['icon', 'poster', 'thumb']
        return {"art": dict.fromkeys(keys, f"{base_path}{icon_slug}.png")}


g = GlobalVariables()
