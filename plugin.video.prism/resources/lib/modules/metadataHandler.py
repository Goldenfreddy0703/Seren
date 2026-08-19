from __future__ import annotations

import datetime
from collections import OrderedDict
from concurrent.futures import as_completed
from functools import cached_property
from typing import Any

from resources.lib.common import tools
from resources.lib.modules.globals import g
from resources.lib.modules.language_lookup import get_country_set_for_language

ART_FANART = 0
ART_TMDB = 1
ART_TVDB = 2

# Simkl metadata is authoritative. TMDB, TVDB, MDBList, and Fanart only gap-fill empty fields.
# All merges into info/art use keep_original=True so Simkl values are never overwritten.


class MetadataHandler:
    def __init__(self):
        self.lang_code = g.get_language_code()
        self.lang_full_code = g.get_language_code(True)
        self.lang_region_code = self.lang_full_code.split("-")[-1]
        self.lang_based_movie_releases = g.get_bool_setting("movies.language_based_releases", True)
        self.allowed_artwork_languages = {None, "en", self.lang_code}
        self.preferred_artwork_size = g.get_int_setting("artwork.preferredsize", 0)

        self.genres = {
            "action": g.get_language_string(30491),
            "adventure": g.get_language_string(30492),
            "animation": g.get_language_string(30493),
            "anime": g.get_language_string(30494),
            "biography": g.get_language_string(30495),
            "children": g.get_language_string(30496),
            "comedy": g.get_language_string(30497),
            "crime": g.get_language_string(30498),
            "documentary": g.get_language_string(30499),
            "drama": g.get_language_string(30500),
            "family": g.get_language_string(30501),
            "fantasy": g.get_language_string(30502),
            "game-show": g.get_language_string(30503),
            "history": g.get_language_string(30504),
            "holiday": g.get_language_string(30505),
            "home-and-garden": g.get_language_string(30506),
            "horror": g.get_language_string(30507),
            "mini-series": g.get_language_string(30508),
            "music": g.get_language_string(30509),
            "musical": g.get_language_string(30510),
            "mystery": g.get_language_string(30511),
            "news": g.get_language_string(30512),
            "none": g.get_language_string(30513),
            "reality": g.get_language_string(30514),
            "romance": g.get_language_string(30515),
            "science-fiction": g.get_language_string(30516),
            "sci-fi": g.get_language_string(30516),
            "short": g.get_language_string(30517),
            "soap": g.get_language_string(30518),
            "special-interest": g.get_language_string(30519),
            "sporting-event": g.get_language_string(30520),
            "superhero": g.get_language_string(30521),
            "suspense": g.get_language_string(30522),
            "talk-show": g.get_language_string(30523),
            "talkshow": g.get_language_string(30523),
            "thriller": g.get_language_string(30524),
            "tv-movie": g.get_language_string(30525),
            "war": g.get_language_string(30526),
            "western": g.get_language_string(30527),
        }

    @property
    def meta_hash(self):
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        return tools.md5_hash(
            [
                self.lang_code,
                art_limit("movies.poster_limit", "movie"),
                art_limit("movies.fanart_limit", "movie"),
                art_limit("movies.keyart_limit", "movie"),
                art_limit("movies.characterart_limit", "movie"),
                art_option_enabled("movies.banner", "movie"),
                art_option_enabled("movies.clearlogo", "movie"),
                art_option_enabled("movies.landscape", "movie"),
                art_option_enabled("movies.clearart", "movie"),
                art_option_enabled("movies.discart", "movie"),
                art_limit("anime.poster_limit", "anime"),
                art_limit("anime.fanart_limit", "anime"),
                art_limit("anime.keyart_limit", "anime"),
                art_limit("anime.characterart_limit", "anime"),
                art_option_enabled("anime.banner", "anime_series"),
                art_option_enabled("anime.clearlogo", "anime_series"),
                art_option_enabled("anime.landscape", "anime_series"),
                art_option_enabled("anime.clearart", "anime_series"),
                art_option_enabled("anime.discart", "anime_movie"),
                art_option_enabled("anime.season.poster", "anime_series"),
                art_option_enabled("anime.season.banner", "anime_series"),
                art_option_enabled("anime.season.landscape", "anime_series"),
                art_option_enabled("anime.season.fanart", "anime_series"),
                art_option_enabled("anime.episode.fanart", "anime_series"),
                art_limit("tvshows.poster_limit", "tvshow"),
                art_limit("tvshows.fanart_limit", "tvshow"),
                art_limit("tvshows.keyart_limit", "tvshow"),
                art_limit("tvshows.characterart_limit", "tvshow"),
                art_option_enabled("tvshows.banner", "tvshow"),
                art_option_enabled("tvshows.clearlogo", "tvshow"),
                art_option_enabled("tvshows.landscape", "tvshow"),
                art_option_enabled("tvshows.clearart", "tvshow"),
                g.get_bool_setting("season.poster", True),
                g.get_bool_setting("season.banner", True),
                g.get_bool_setting("season.landscape", True),
                g.get_bool_setting("season.fanart", True),
                g.get_bool_setting("episode.fanart", True),
                g.get_int_setting("tvshows.preferedsource", 1),
                g.get_int_setting("movies.preferedsource", 1),
                g.get_int_setting("anime.preferedsource", 1),
                self._effective_preferred_art_source("movie"),
                self._effective_preferred_art_source("tvshow"),
                self._effective_preferred_art_source("tvshow", "anime_series"),
                g.get_int_setting("artwork.preferredsize", 0),
                self.tmdb_api.meta_hash,
                self.tvdb_api.meta_hash,
                self.simkl_api.meta_hash,
                self.fanarttv_api.meta_hash,
                self.fanarttv_api.fanart_support,
                self._provider_enabled("tmdb"),
                self._provider_enabled("tvdb"),
                self._fanart_art_usable(),
            ]
        )

    @cached_property
    def simkl_api(self):
        from resources.lib.indexers.simkl import SimklAPI

        return SimklAPI()

    @cached_property
    def tmdb_api(self):
        from resources.lib.indexers.tmdb import TMDBAPI

        return TMDBAPI()

    @cached_property
    def tvdb_api(self):
        from resources.lib.indexers.tvdb import TVDBAPI

        return TVDBAPI()

    @cached_property
    def fanarttv_api(self):
        from resources.lib.indexers.fanarttv import FanartTv

        return FanartTv()

    # region format art
    def format_db_object(self, db_object):
        return [self.format_meta(i) for i in db_object]

    def format_meta(self, db_object):
        simkl_data = self._coalesce_simkl_object(db_object, db_object.get("simkl_object"))
        tmdb_object = db_object.get("tmdb_object")
        tvdb_object = db_object.get("tvdb_object")
        fanart_object = db_object.get("fanart_object")
        show_info = db_object.get("show_info")
        season_info = db_object.get("season_info")
        show_art = db_object.get("show_art")
        season_art = db_object.get("season_art")
        show_cast = db_object.get("show_cast")
        season_cast = db_object.get("season_cast")

        result = {"info": {}, "art": {}, "cast": []}

        from resources.lib.meta.artwork import artwork_profile_for_row

        simkl_info = tools.safe_dict_get(simkl_data, "info") or {}
        default_media = simkl_info.get("mediatype") or "tvshow"
        profile_info = dict(simkl_info)
        for parent in (show_info, season_info):
            if isinstance(parent, dict) and parent.get("catalog") and not profile_info.get("catalog"):
                profile_info["catalog"] = parent["catalog"]
        art_profile = db_object.get("_art_profile") or artwork_profile_for_row(
            {"info": profile_info, "simkl_id": db_object.get("simkl_id")},
            default_media,
        )

        result.update(
            self._apply_best_fit_meta_data(
                simkl_data,
                tmdb_object,
                tvdb_object,
                fanart_object,
                art_profile=art_profile,
                imdb_data=db_object.get("imdb_object"),
                anilist_data=db_object.get("anilist_object"),
            )
        )

        self._show_season_art_fallback(result, season_art, show_art)
        self._add_season_show_info(result, season_info, show_info)
        self._add_season_show_art(result, season_art, show_art)
        self._add_season_show_cast(result, season_cast, show_cast)
        if result["info"].get("mediatype") == "tvshow" and result["info"].get("simkl_id"):
            from resources.lib.simkl.ids import attach_show_identity, slug_from_info

            attach_show_identity(result["info"], int(result["info"]["simkl_id"]), slug_from_info(result["info"]))
        self._apply_simkl_episode_thumb(result, simkl_data)
        self._restore_simkl_child_info(result, simkl_data)
        self._restore_simkl_primary_art(result, simkl_data)
        from resources.lib.simkl.images import rescale_simkl_art

        result["art"] = rescale_simkl_art(result.get("art"))
        if result["info"].get("thumb"):
            from resources.lib.simkl.images import episode_thumb_url

            thumb = episode_thumb_url(
                result["info"].get("simkl_img") or result["info"].get("thumb"),
            )
            if thumb:
                result["info"]["thumb"] = thumb
                result.setdefault("art", {})["thumb"] = thumb
        from resources.lib.simkl.field_map import finalize_playback_info
        from resources.lib.simkl.ids import canonicalize_info_identity

        canonicalize_info_identity(result["info"])
        catalog = profile_info.get("catalog") or result["info"].get("catalog")
        if catalog == "anime":
            from resources.lib.simkl.field_map import ensure_anime_title_slots, merge_anime_title_slots

            if isinstance(simkl_info, dict):
                merge_anime_title_slots(result["info"], simkl_info)
            ensure_anime_title_slots(result["info"])
        finalize_playback_info(result["info"])
        if result["info"].get("mediatype") == "season":
            MetadataHandler._title_fallback(result)
        from resources.lib.meta.storage import slim_formatted_item

        return slim_formatted_item(result)

    @staticmethod
    def _restore_simkl_primary_art(result, simkl_data):
        """Simkl poster/fanart/thumb wins over TMDB/TVDB/Fanart gap-fill."""
        simkl_art = tools.safe_dict_get(simkl_data, "art") or {}
        if not simkl_art:
            return
        art = result.setdefault("art", {})
        for key in ("poster", "fanart", "thumb", "icon"):
            if simkl_art.get(key):
                art[key] = simkl_art[key]

    @staticmethod
    def _restore_simkl_child_info(result, simkl_data):
        """Simkl is authoritative for season/episode metadata (structure + text fields)."""
        simkl_info = tools.safe_dict_get(simkl_data, "info")
        if not simkl_info:
            return
        mediatype = result["info"].get("mediatype")
        if mediatype not in ("episode", "season"):
            return

        result["info"] = dict(simkl_info)

        if mediatype == "season":
            from resources.lib.simkl.field_map import ensure_season_title

            ensure_season_title(result["info"])

        simkl_art = tools.safe_dict_get(simkl_data, "art") or {}
        if simkl_art:
            art = result.setdefault("art", {})
            for key, value in simkl_art.items():
                if value and (key not in art or key in ("thumb", "icon", "poster")):
                    art[key] = value

    @staticmethod
    def _simkl_episode_lookup(db_object):
        from resources.lib.simkl.ids import episode_num_from_info

        info = tools.safe_dict_get(db_object, "simkl_object", "info") or {}
        return info.get("season"), episode_num_from_info(info)

    @staticmethod
    def _apply_simkl_episode_thumb(result, simkl_data):
        """Simkl GET /tv/episodes/{id} `img` → episode list thumb (Otaku / apib wsrv pattern)."""
        if tools.safe_dict_get(result, "info", "mediatype") != "episode":
            return
        from resources.lib.simkl.images import episode_thumb_url

        thumb = tools.safe_dict_get(simkl_data, "art", "thumb")
        if not thumb:
            thumb = tools.safe_dict_get(result, "info", "thumb")
        if not thumb:
            img = tools.safe_dict_get(simkl_data, "info", "simkl_img") or tools.safe_dict_get(
                simkl_data, "info", "img"
            )
            thumb = episode_thumb_url(img)
        if not thumb:
            return
        result.setdefault("art", {})["thumb"] = thumb
        result["info"]["thumb"] = thumb

    @staticmethod
    def _add_season_show_info(result, season_info, show_info):
        from resources.lib.simkl.ids import attach_tv_context, slug_from_info

        if season_info:
            result["info"]["simkl_season_id"] = season_info["simkl_id"]
            if not result["info"].get("mpaa") and (mpaa := season_info.get("mpaa")):
                result["info"]["mpaa"] = mpaa
        if show_info:
            show_id = show_info.get("simkl_id")
            if show_id is not None:
                attach_tv_context(
                    result["info"],
                    int(show_id),
                    season_row_id=season_info.get("simkl_id") if season_info else None,
                    show_info=show_info,
                    slug=slug_from_info(show_info),
                )
            if not result["info"].get("tvshowtitle"):
                result["info"]["tvshowtitle"] = show_info.get("title")
            if not result["info"].get("tmdb_show_id"):
                result["info"]["tmdb_show_id"] = show_info.get("tmdb_id")
            if not result["info"].get("tvdb_show_id"):
                result["info"]["tvdb_show_id"] = show_info.get("tvdb_id")
            if not result["info"].get("year"):
                result["info"]["year"] = show_info.get("year")
            if not result["info"].get("tvshow.year"):
                result["info"]["tvshow.year"] = show_info.get("year")
            if not result["info"].get("studio"):
                result["info"]["studio"] = show_info.get("studio")
            if not result["info"].get("country_origin"):
                result["info"]["country_origin"] = show_info.get("country_origin")
            if not result["info"].get("aliases") and show_info.get("aliases"):
                result["info"]["aliases"] = show_info.get("aliases")
            if not result["info"].get("mpaa") and (mpaa := show_info.get("mpaa")):
                result["info"]["mpaa"] = mpaa
            if not result["info"].get("runtime") and show_info.get("runtime") is not None:
                result["info"]["runtime"] = show_info.get("runtime")
            from resources.lib.simkl.field_map import inherit_show_fields

            inherit_show_fields(result["info"], show_info)
            result["info"].update({f"tvshow.{key}": value for key, value in show_info.items() if key.endswith("_id")})

    @staticmethod
    def _add_season_show_cast(result, season_cast, show_cast):
        if season_cast and len(result.get("cast", [])) == 0:
            result["cast"] = season_cast
        if show_cast and len(result.get("cast", [])) == 0:
            result["cast"] = show_cast

    @staticmethod
    def _add_season_show_art(result, season_art, show_art):
        if show_art:
            result["art"].update({f"tvshow.{key}": value for key, value in show_art.items()})
        if season_art:
            result["art"].update(
                {f"season.{key}": value for key, value in season_art.items() if not key.startswith("tvshow.")}
            )

    @staticmethod
    def _show_season_art_fallback(data, season_art, show_art):
        show_season_art_mixin = {}

        if season_art:
            show_season_art_mixin = tools.smart_merge_dictionary(
                show_season_art_mixin,
                tools.filter_dictionary(season_art, "poster", "fanart", "clearlogo"),
                True,
            )

        if show_art:
            show_season_art_mixin = tools.smart_merge_dictionary(
                show_season_art_mixin,
                tools.filter_dictionary(show_art, "poster", "fanart", "clearlogo"),
                True,
            )

        data["art"] = tools.smart_merge_dictionary(data["art"], show_season_art_mixin, True)

    @staticmethod
    def _coalesce_simkl_object(db_object, simkl_data):
        """Ensure format/update paths always have a Simkl object (sync can mill before meta is cached)."""
        info = tools.safe_dict_get(simkl_data, "info") or {}
        simkl_id = info.get("simkl_id") or db_object.get("simkl_id")
        if simkl_id is None:
            return simkl_data or {}

        info.setdefault("simkl_id", simkl_id)
        if db_object.get("simkl_show_id") is not None:
            info.setdefault("simkl_show_id", db_object["simkl_show_id"])
        if db_object.get("simkl_season_id") is not None:
            info.setdefault("simkl_season_id", db_object["simkl_season_id"])
        if db_object.get("season") is not None:
            info.setdefault("season", db_object["season"])
        if db_object.get("episode") is not None:
            info.setdefault("episode", db_object["episode"])
            info.setdefault("number", db_object["episode"])
        if not info.get("mediatype"):
            if db_object.get("_entity") == "movie":
                info["mediatype"] = "movie"
            elif db_object.get("episode") is not None:
                info["mediatype"] = "episode"
            elif db_object.get("season") is not None or db_object.get("simkl_season_id") is not None:
                info["mediatype"] = "season"
            else:
                info["mediatype"] = "tvshow"

        coalesced = {
            "info": info,
            "art": tools.safe_dict_get(simkl_data, "art") or {},
            "cast": tools.safe_dict_get(simkl_data, "cast") or [],
        }
        return coalesced

    def _apply_best_fit_meta_data(self, simkl_data, tmdb_data, tvdb_data, fanart_object, art_profile=None, imdb_data=None, anilist_data=None):
        simkl_data = simkl_data or {}
        simkl_info = tools.safe_dict_get(simkl_data, "info") or {}
        media_type = simkl_info.get("mediatype") or "episode"
        if art_profile is None:
            from resources.lib.meta.artwork import artwork_profile_for_row

            art_profile = artwork_profile_for_row(
                {"info": simkl_info, "simkl_id": simkl_info.get("simkl_id")},
                media_type if media_type in ("movie", "tvshow") else "tvshow",
            )
        result = {}

        self._apply_best_fit_info(result, simkl_data, tmdb_data, tvdb_data)
        self._apply_best_fit_cast(
            result,
            tmdb_data,
            tvdb_data,
            imdb_data=imdb_data,
            anilist_data=anilist_data,
        )
        if anilist_data and anilist_data.get("studio"):
            info = result.setdefault("info", {})
            if isinstance(info, dict) and not info.get("studio"):
                info["studio"] = anilist_data.get("studio")
        result["art"] = dict(tools.safe_dict_get(simkl_data, "art") or {})
        self._apply_best_fit_art(result, tmdb_data, tvdb_data, fanart_object, media_type, art_profile=art_profile)

        return result

    def _apply_best_fit_art(self, result, tmdb_object, tvdb_object, fanart_object, media_type, art_profile=None):
        """Simkl art is seeded on result before this runs; external sources gap-fill only."""
        if tmdb_object:
            result["art"] = tools.smart_merge_dictionary(
                result.get("art", {}),
                tmdb_object.get("art", {}),
                keep_original=not self._is_tmdb_artwork_selected(media_type, art_profile=art_profile),
                extend_array=False,
            )

        if tvdb_object:
            result["art"] = tools.smart_merge_dictionary(
                result.get("art", {}),
                tvdb_object.get("art", {}),
                keep_original=not self._is_tvdb_artwork_selected(media_type, art_profile=art_profile),
                extend_array=False,
            )

        if fanart_object:
            result["art"] = tools.smart_merge_dictionary(
                result.get("art", {}),
                fanart_object.get("art", {}),
                keep_original=not self._is_fanart_artwork_selected(media_type, art_profile=art_profile),
                extend_array=False,
            )

        result["art"] = self._handle_art(media_type, result.get("art", {}), art_profile=art_profile)

    def _apply_best_fit_info(
        self,
        result,
        simkl_data,
        tmdb_data,
        tvdb_data,
    ):
        # Simkl owns identity, status, and ratings; providers gap-fill descriptive fields.
        result.update({"info": tools.safe_dict_get(simkl_data, "info") or {}})
        mediatype = result["info"].get("mediatype")

        self._apply_best_fit_release(result)
        self._use_simkl_air_date(simkl_data, result)
        self._normalize_genres(result)
        if mediatype not in ("episode", "season") and not result["info"].get("plot") and result["info"].get("overview"):
            result["info"]["plot"] = result["info"]["overview"]
        self._title_fallback(result)

    def _apply_best_fit_release(self, result):
        releases = tools.safe_dict_get(result, "info", "releases")
        if not releases:
            return

        us_release = self._get_best_release(releases.get("US"))
        country_release = self._get_best_release(releases.get(self.lang_region_code))

        if (
            self.lang_based_movie_releases
            and tools.parse_datetime(country_release.get("release_date", "9999-12-31T00:00:00"), date_only=False)
            > datetime.datetime.utcnow()
        ):
            lang_releases = [
                self._get_best_release(releases[c])
                for c in set(releases.keys())
                & get_country_set_for_language(self.lang_code) - {"US", self.lang_region_code}
            ]
            lang_releases.append(country_release)
            release = self._get_best_release(lang_releases, convert_to_utc=False)
        else:
            release = country_release

        if release_date := release.get("release_date", us_release.get("release_date")):
            result['info']["premiered"] = release_date
            result['info']["aired"] = release_date
        if mpaa := country_release.get("mpaa", us_release.get("mpaa")):
            result['info']['mpaa'] = mpaa

    @staticmethod
    def _get_best_release(releases, convert_to_utc=True):
        best_release = {}
        if releases:
            for release in releases:
                if (
                    release
                    and release.get("release_type", "unknown") not in {"premiere", "limited"}
                    and (
                        (not best_release and release.get("release_date"))
                        or release.get("release_date")
                        and release['release_date'] < best_release['release_date']
                    )
                ):
                    best_release = release
            if convert_to_utc and best_release:
                best_release['release_date'] = g.local_to_utc_by_country(
                    best_release['release_date'], best_release['country']
                )
        return best_release

    @staticmethod
    def _use_simkl_air_date(simkl_data, result):
        if result['info']['mediatype'] == g.MEDIA_MOVIE:
            return
        if simkl_premiered_date := tools.safe_dict_get(simkl_data, "info", "premiered"):
            result['info']['premiered'] = simkl_premiered_date

        if simkl_aired_date := tools.safe_dict_get(simkl_data, "info", "aired"):
            result['info']['aired'] = simkl_aired_date

    def _normalize_genres(self, meta):
        meta["info"]["genre"] = sorted(
            OrderedDict.fromkeys(
                [self.genres.get(i.lower().replace(" ", "-"), i) for i in meta["info"].get("genre", [])]
            )
        )

    @staticmethod
    def _title_fallback(meta):
        if not meta["info"].get('title'):
            media_type = meta["info"]["mediatype"]
            title = None
            if media_type == "episode":
                title = g.get_language_string(30529).format(meta["info"]["episode"])
            elif media_type == "season":
                if meta["info"]["season"] == 0:
                    title = "Specials"
                else:
                    title = g.get_language_string(30528).format(meta["info"]["season"])
            if title:
                meta["info"]["sorttitle"] = title
                meta["info"]["title"] = title

    def _apply_best_fit_cast(self, result, tmdb_data, tvdb_data, imdb_data=None, anilist_data=None):
        if tools.safe_dict_get(result, "info", "mediatype") in ("episode", "season"):
            return
        if imdb_data is not None and imdb_data.get("cast"):
            result["cast"] = imdb_data.get("cast", [])
            return
        if anilist_data is not None and anilist_data.get("cast"):
            result["cast"] = anilist_data.get("cast", [])
            return
        if result.get("cast"):
            return
        from resources.lib.meta.artwork import artwork_profile_for_row

        media_type = tools.safe_dict_get(result, "info", "mediatype") or "tvshow"
        art_profile = artwork_profile_for_row(result, default_media_type=media_type)
        preferred = self._effective_preferred_art_source(
            "movie" if media_type == "movie" else "tvshow",
            art_profile=art_profile,
        )
        from resources.lib.meta.provider_settings import ART_TMDB, ART_TVDB

        if preferred == ART_TVDB:
            if tvdb_data is not None and tvdb_data.get("cast", []):
                result["cast"] = tvdb_data.get("cast", [])
            elif tmdb_data is not None and tmdb_data.get("cast", []):
                result["cast"] = tmdb_data.get("cast", [])
            return
        if preferred == ART_TMDB:
            if tmdb_data is not None and tmdb_data.get("cast", []):
                result["cast"] = tmdb_data.get("cast", [])
            elif tvdb_data is not None and tvdb_data.get("cast", []):
                result["cast"] = tvdb_data.get("cast", [])
            return
        if tmdb_data is not None and tmdb_data.get("cast", []):
            result["cast"] = tmdb_data.get("cast", [])
        elif tvdb_data is not None and tvdb_data.get("cast", []):
            result["cast"] = tvdb_data.get("cast", [])

    def _effective_preferred_art_source(self, media_type: str, art_profile: str | None = None) -> int:
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES
        from resources.lib.meta.provider_settings import effective_preferred_art_source

        if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES):
            raw = g.get_int_setting("anime.preferedsource", 1)
        elif media_type == "movie":
            raw = g.get_int_setting("movies.preferedsource", 1)
        else:
            raw = g.get_int_setting("tvshows.preferedsource", 1)
        return effective_preferred_art_source(raw)

    def _is_fanart_artwork_selected(self, media_type, art_profile=None):
        from resources.lib.meta.provider_settings import ART_FANART

        return self._effective_preferred_art_source(media_type, art_profile=art_profile) == ART_FANART

    def _is_tmdb_artwork_selected(self, media_type, art_profile=None):
        from resources.lib.meta.provider_settings import ART_TMDB

        return self._effective_preferred_art_source(media_type, art_profile=art_profile) == ART_TMDB

    def _is_tvdb_artwork_selected(self, media_type, art_profile=None):
        from resources.lib.meta.provider_settings import ART_TVDB

        return self._effective_preferred_art_source(media_type, art_profile=art_profile) == ART_TVDB

    def _child_art_profile_for_db_object(self, db_object):
        from resources.lib.meta.artwork import artwork_profile_for_row

        info = tools.safe_dict_get(db_object, "simkl_object", "info")
        if not isinstance(info, dict):
            info = db_object.get("info") if isinstance(db_object.get("info"), dict) else {}
        return artwork_profile_for_row(
            {"info": info, "simkl_id": db_object.get("simkl_id")},
            default_media_type="tvshow",
        )

    def _is_tmdb_art_preferred_for_child(self, db_object):
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE

        profile = self._child_art_profile_for_db_object(db_object)
        media_type = "movie" if profile == PROFILE_ANIME_MOVIE else "tvshow"
        return self._is_tmdb_artwork_selected(media_type, art_profile=profile)

    def _is_tvdb_art_preferred_for_child(self, db_object):
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE

        profile = self._child_art_profile_for_db_object(db_object)
        media_type = "movie" if profile == PROFILE_ANIME_MOVIE else "tvshow"
        return self._is_tvdb_artwork_selected(media_type, art_profile=profile)

    def _art_profile_for_db_object(self, db_object):
        profile = db_object.get("_art_profile")
        if profile:
            return profile
        return self._child_art_profile_for_db_object(db_object)

    @staticmethod
    def _media_type_for_art_profile(art_profile: str) -> str:
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_MOVIE

        if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_MOVIE):
            return "movie"
        return "tvshow"

    def _preferred_art_source_for_db_object(self, db_object):
        art_profile = self._art_profile_for_db_object(db_object)
        return self._effective_preferred_art_source(
            self._media_type_for_art_profile(art_profile),
            art_profile=art_profile,
        )

    def _fetch_movie_fanart_patch(self, db_object):
        g.ensure_addon()
        if not self._fanart_art_usable() or not self.fanarttv_api.fanart_support:
            return None
        if not (self._fanart_needs_update(db_object) or self._force_update(db_object)):
            return None
        patch = {}
        if self._tmdb_id_valid(db_object):
            tools.smart_merge_dictionary(patch, self.fanarttv_api.get_movie(db_object.get("tmdb_id")))
        if self._imdb_id_valid(db_object) and self._fanart_needs_update(db_object):
            tools.smart_merge_dictionary(patch, self.fanarttv_api.get_movie(db_object.get("imdb_id")))
        return patch or None

    def _fetch_tvshow_fanart_patch(self, db_object):
        g.ensure_addon()
        if not self._fanart_art_usable() or not self.fanarttv_api.fanart_support:
            return None
        if not (self._fanart_needs_update(db_object) or self._force_update(db_object)):
            return None
        if not self._tvdb_id_valid(db_object):
            return None
        return self.fanarttv_api.get_show(db_object.get("tvdb_id"))

    def _merge_supplementary_fanart_movie_art(self, db_object):
        """Fanart.tv supplies discart/clearart/banner etc. when TMDB/TVDB is preferred (Seren parity)."""
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred in (ART_SIMKL, ART_FANART):
            return
        patch = self._fetch_movie_fanart_patch(db_object)
        if patch:
            tools.smart_merge_dictionary(db_object, patch)

    def _merge_supplementary_fanart_tvshow_art(self, db_object):
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred in (ART_SIMKL, ART_FANART):
            return
        patch = self._fetch_tvshow_fanart_patch(db_object)
        if patch:
            tools.smart_merge_dictionary(db_object, patch)

    def _merge_tmdb_movie_art(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return
        if self._tmdb_art_meta_up_to_par("movie", db_object) and not self._force_update(db_object):
            return
        tools.smart_merge_dictionary(db_object, self.tmdb_api.get_movie_art(db_object["tmdb_id"]))

    def _merge_tvdb_movie_art(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return
        if self._tvdb_art_meta_up_to_par("movie", db_object) and not self._force_update(db_object):
            return
        tools.smart_merge_dictionary(db_object, self.tvdb_api.get_movie_art(db_object["tvdb_id"]))

    def _merge_tmdb_tvshow_art(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return
        if self._tmdb_art_meta_up_to_par("tvshow", db_object) and not self._force_update(db_object):
            return
        tools.smart_merge_dictionary(db_object, self.tmdb_api.get_show_art(db_object["tmdb_id"]))

    def _merge_tvdb_tvshow_art(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return
        if self._tvdb_art_meta_up_to_par("tvshow", db_object) and not self._force_update(db_object):
            return
        tools.smart_merge_dictionary(db_object, self.tvdb_api.get_show_art(db_object["tvdb_id"]))

    def _update_movie_art_fallback(self, db_object):
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        if preferred == ART_FANART and not self._fanart_art_meta_up_to_par("movie", db_object):
            if self._provider_enabled("tmdb") and self._tmdb_id_valid(db_object):
                self._merge_tmdb_movie_art(db_object)
            elif self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
                self._merge_tvdb_movie_art(db_object)
            return
        if preferred == ART_TMDB and not self._tmdb_art_meta_up_to_par("movie", db_object):
            if self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
                self._merge_tvdb_movie_art(db_object)
            elif self._fanart_art_usable() and self.fanarttv_api.fanart_support:
                tools.smart_merge_dictionary(db_object, self._fetch_movie_fanart_patch(db_object) or {})
            return
        if preferred == ART_TVDB and not self._tvdb_art_meta_up_to_par("movie", db_object):
            if self._provider_enabled("tmdb") and self._tmdb_id_valid(db_object):
                self._merge_tmdb_movie_art(db_object)
            elif self._fanart_art_usable() and self.fanarttv_api.fanart_support:
                tools.smart_merge_dictionary(db_object, self._fetch_movie_fanart_patch(db_object) or {})

    def _update_tvshow_art_fallback(self, db_object):
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        if preferred == ART_FANART and not self._fanart_art_meta_up_to_par("tvshow", db_object):
            if self._provider_enabled("tmdb") and self._tmdb_id_valid(db_object):
                self._merge_tmdb_tvshow_art(db_object)
            elif self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
                self._merge_tvdb_tvshow_art(db_object)
            return
        if preferred == ART_TMDB and not self._tmdb_art_meta_up_to_par("tvshow", db_object):
            if self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
                self._merge_tvdb_tvshow_art(db_object)
            elif self._fanart_art_usable() and self._tvdb_id_valid(db_object):
                tools.smart_merge_dictionary(db_object, self._fetch_tvshow_fanart_patch(db_object) or {})
            return
        if preferred == ART_TVDB and not self._tvdb_art_meta_up_to_par("tvshow", db_object):
            if self._provider_enabled("tmdb") and self._tmdb_id_valid(db_object):
                self._merge_tmdb_tvshow_art(db_object)
            elif self._fanart_art_usable() and self._tvdb_id_valid(db_object):
                tools.smart_merge_dictionary(db_object, self._fetch_tvshow_fanart_patch(db_object) or {})

    def _handle_art(self, media_type, art_data, art_profile=None):
        if art_data is None:
            return {}
        [
            art_data.update({k: self._sort_art(self._filter_art(v))})
            for k, v in art_data.items()
            if isinstance(v, (list, set))
        ]

        self._fallback_art_before_handling(art_data)

        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES

        profile = art_profile or media_type
        if profile == PROFILE_ANIME_MOVIE:
            return self._handle_anime_movie_art(art_data)
        if profile == PROFILE_ANIME_SERIES:
            return self._handle_anime_series_art(art_data)
        if media_type == "movie":
            return self._handle_movie_art(art_data)
        elif media_type == "tvshow":
            return self._handle_show_art(art_data)
        elif media_type == "season":
            return self._handle_season_art(art_data, art_profile=art_profile)
        elif media_type == "episode":
            return self._handle_episode_art(art_data, art_profile=art_profile)

    @staticmethod
    def _sort_art(art):
        art.sort(key=lambda i: i.get("url", ""))
        art.sort(key=lambda i: i.get("rating", 0), reverse=True)
        art.sort(key=lambda i: i.get("size", 0), reverse=True)
        return art

    def _filter_art(self, art):
        return [
            i
            for i in art
            if isinstance(i, dict) and i.get("language") in self.allowed_artwork_languages
        ]

    @staticmethod
    def _fallback_art_before_handling(art):
        if len(art.get("poster", [])) == 0 and len(art.get("keyart", [])) > 0:
            art.update({"poster": art.pop("keyart")})

    @staticmethod
    def _handle_artwork_multis(limit, art_type, art_data):
        if limit <= 0:
            return {}
        data = {}
        raw = art_data.get(art_type)
        if raw is None:
            return data
        if isinstance(raw, list):
            images = raw
        elif isinstance(raw, dict):
            images = [raw]
        elif isinstance(raw, str):
            images = [raw]
        else:
            return data
        for idx in range(limit):
            name = art_type if idx == 0 else f"{art_type}{idx}"
            if idx >= len(images):
                break
            image = images[idx]
            if isinstance(image, dict):
                data[name] = image["url"]
            else:
                data[name] = image
        return data

    def _handle_show_art(self, data):
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        result = {}

        result.update(self._handle_artwork_multis(art_limit("tvshows.poster_limit", "tvshow"), "poster", data))
        result.update(self._handle_artwork_multis(art_limit("tvshows.fanart_limit", "tvshow"), "fanart", data))
        result.update(self._handle_artwork_multis(art_limit("tvshows.characterart_limit", "tvshow"), "characterart", data))
        result.update(self._handle_artwork_multis(art_limit("tvshows.keyart_limit", "tvshow"), "keyart", data))
        if art_option_enabled("tvshows.clearlogo", "tvshow"):
            result.update(self._handle_artwork_multis(1, "clearlogo", data))
        result.update(self._handle_artwork_multis(1, "thumb", data))
        result.update(self._handle_artwork_multis(1, "icon", data))

        if art_option_enabled("tvshows.banner", "tvshow"):
            result.update(self._handle_artwork_multis(1, "banner", data))
        if art_option_enabled("tvshows.landscape", "tvshow"):
            result.update(self._handle_artwork_multis(1, "landscape", data))
        if art_option_enabled("tvshows.clearart", "tvshow"):
            result.update(self._handle_artwork_multis(1, "clearart", data))

        return result

    def _handle_movie_art(self, data):
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        result = {}

        result.update(self._handle_artwork_multis(art_limit("movies.poster_limit", "movie"), "poster", data))
        result.update(self._handle_artwork_multis(art_limit("movies.fanart_limit", "movie"), "fanart", data))
        result.update(self._handle_artwork_multis(art_limit("movies.characterart_limit", "movie"), "characterart", data))
        result.update(self._handle_artwork_multis(art_limit("movies.keyart_limit", "movie"), "keyart", data))
        if art_option_enabled("movies.clearlogo", "movie"):
            result.update(self._handle_artwork_multis(1, "clearlogo", data))
        result.update(self._handle_artwork_multis(1, "thumb", data))
        result.update(self._handle_artwork_multis(1, "icon", data))

        if art_option_enabled("movies.banner", "movie"):
            result.update(self._handle_artwork_multis(1, "banner", data))
        if art_option_enabled("movies.landscape", "movie"):
            result.update(self._handle_artwork_multis(1, "landscape", data))
        if art_option_enabled("movies.discart", "movie"):
            result.update(self._handle_artwork_multis(1, "discart", data))
        if art_option_enabled("movies.clearart", "movie"):
            result.update(self._handle_artwork_multis(1, "clearart", data))

        return result

    def _handle_anime_series_art(self, data):
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        result = {}

        result.update(self._handle_artwork_multis(art_limit("anime.poster_limit", "anime"), "poster", data))
        result.update(self._handle_artwork_multis(art_limit("anime.fanart_limit", "anime"), "fanart", data))
        result.update(self._handle_artwork_multis(art_limit("anime.characterart_limit", "anime"), "characterart", data))
        result.update(self._handle_artwork_multis(art_limit("anime.keyart_limit", "anime"), "keyart", data))
        if art_option_enabled("anime.clearlogo", "anime_series"):
            result.update(self._handle_artwork_multis(1, "clearlogo", data))
        result.update(self._handle_artwork_multis(1, "thumb", data))
        result.update(self._handle_artwork_multis(1, "icon", data))

        if art_option_enabled("anime.banner", "anime_series"):
            result.update(self._handle_artwork_multis(1, "banner", data))
        if art_option_enabled("anime.landscape", "anime_series"):
            result.update(self._handle_artwork_multis(1, "landscape", data))
        if art_option_enabled("anime.clearart", "anime_series"):
            result.update(self._handle_artwork_multis(1, "clearart", data))

        return result

    def _handle_anime_movie_art(self, data):
        from resources.lib.meta.provider_settings import art_option_enabled

        result = self._handle_anime_series_art(data)
        if art_option_enabled("anime.discart", "anime_movie"):
            result.update(self._handle_artwork_multis(1, "discart", data))
        return result

    def _handle_season_art(self, data, art_profile=None):
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        is_anime = art_profile in (PROFILE_ANIME_SERIES, PROFILE_ANIME_MOVIE)
        result = {}
        result.update(self._handle_artwork_multis(1, "thumb", data))
        result.update(self._handle_artwork_multis(1, "icon", data))
        if is_anime:
            if art_option_enabled("anime.season.poster", "anime_series"):
                result.update(self._handle_artwork_multis(art_limit("anime.poster_limit", "anime"), "poster", data))
            if art_option_enabled("anime.season.fanart", "anime_series"):
                result.update(self._handle_artwork_multis(art_limit("anime.fanart_limit", "anime"), "fanart", data))
            if art_option_enabled("anime.season.banner", "anime_series"):
                result.update(self._handle_artwork_multis(1, "banner", data))
            if art_option_enabled("anime.season.landscape", "anime_series"):
                result.update(self._handle_artwork_multis(1, "landscape", data))
        else:
            if g.get_bool_setting("season.poster", True):
                result.update(self._handle_artwork_multis(art_limit("tvshows.poster_limit", "tvshow"), "poster", data))
            if g.get_bool_setting("season.fanart", True):
                result.update(self._handle_artwork_multis(art_limit("tvshows.fanart_limit", "tvshow"), "fanart", data))
            if g.get_bool_setting("season.banner", True):
                result.update(self._handle_artwork_multis(1, "banner", data))
            if g.get_bool_setting("season.landscape", True):
                result.update(self._handle_artwork_multis(1, "landscape", data))
        return result

    def _handle_episode_art(self, data, art_profile=None):
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES
        from resources.lib.meta.provider_settings import art_limit, art_option_enabled

        is_anime = art_profile in (PROFILE_ANIME_SERIES, PROFILE_ANIME_MOVIE)
        result = {}
        result.update(self._handle_artwork_multis(1, "thumb", data))
        if is_anime:
            if art_option_enabled("anime.episode.fanart", "anime_series"):
                result.update(self._handle_artwork_multis(art_limit("anime.fanart_limit", "anime"), "fanart", data))
        elif g.get_bool_setting("episode.fanart", True):
            result.update(self._handle_artwork_multis(art_limit("tvshows.fanart_limit", "tvshow"), "fanart", data))
        return result

    # endregion

    # region update meta
    def _parallel_provider_patches(self, fetchers: list) -> list:
        """Run independent provider fetches concurrently; return non-empty patches."""
        tasks = [fetcher for fetcher in fetchers if callable(fetcher)]
        if not tasks:
            return []
        if len(tasks) == 1:
            patch = tasks[0]()
            return [patch] if patch else []

        from resources.lib.common.thread_pool import get_provider_executor

        executor = get_provider_executor()
        futures = [executor.submit(fetcher) for fetcher in tasks]
        patches = []
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                raise exc
            patch = future.result()
            if patch:
                patches.append(patch)
        return patches

    def _merge_provider_patches(self, db_object, patches: list) -> None:
        for patch in patches:
            if patch:
                tools.smart_merge_dictionary(db_object, patch)

    def update(self, db_object):
        """Checks and updates the requested db_object with the full set of meta data.

        :param db_object:dictionary with the ids and meta from the db.
        :type db_object:dict
        :return:list with the updated db_object
        :rtype:list[dict]
        """
        media_type = MetadataHandler.get_simkl_info(db_object, "mediatype")

        if media_type in ("movie", "tvshow") and self._sync_update_noop(db_object, media_type):
            g.log(
                f"sync_meta_update provider_fetches=0 simkl_id={db_object.get('simkl_id')} media={media_type}",
                "debug",
            )
            self._write_log(db_object, media_type)
            return [db_object]

        if media_type == "movie":
            self._update_movie(db_object)
        if media_type == "tvshow":
            self._update_tvshow(db_object)
        if media_type == "season":
            self._update_season(db_object)
        if media_type == "episode":
            self._update_episode(db_object)

        self._write_log(db_object, media_type)

        return [db_object]

    def _write_log(self, db_object, media_type):
        if (media_type == "movie" and not db_object.get("tmdb_object") and not db_object.get("tvdb_object")) or (
            media_type in ["tvshow", "season", "episode"]
            and not db_object.get("tmdb_object")
            and not db_object.get("tvdb_object")
        ):
            g.log(f"Unable to lookup some meta for {db_object.get('simkl_id')}", "debug")
        if self.fanarttv_api.fanart_support and media_type != "episode" and not db_object.get("fanart_object"):
            g.log(f"Unable to lookup fanart meta for {db_object.get('simkl_id')}", "debug")

    def _provider_cast_missing(self, db_object, provider_key):
        return not tools.safe_dict_get(db_object, f"{provider_key}_object", "cast")

    @staticmethod
    def _imdb_id_valid(db_object) -> bool:
        from resources.lib.meta.provider_settings import external_ids_from_row

        return bool(external_ids_from_row(db_object).get("imdb_id"))

    def _merge_imdb_cast(self, db_object, cast: list[dict]) -> None:
        if not cast:
            return
        tools.smart_merge_dictionary(db_object, {"cast": cast, "imdb_object": {"cast": cast}})

    @staticmethod
    def _coerce_int_id(value) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _merge_anilist_cast(self, db_object: dict, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not payload:
            return
        cast = payload.get("cast")
        studio = payload.get("studio")
        merge: dict[str, Any] = {}
        anilist_object: dict[str, Any] = {}
        if cast:
            merge["cast"] = cast
            anilist_object["cast"] = cast
        if studio:
            anilist_object["studio"] = studio
            info = db_object.get("info")
            if not isinstance(info, dict):
                info = {}
            if not info.get("studio"):
                info = dict(info)
                info["studio"] = studio
                merge["info"] = info
        if anilist_object:
            merge["anilist_object"] = anilist_object
        if merge:
            tools.smart_merge_dictionary(db_object, merge)

    def _fill_anilist_cast_batch(
        self,
        fallback_pending: list[tuple[int, dict]],
    ) -> tuple[list[tuple[int, dict]], dict[int, list], dict[int, str], list[dict]]:
        from resources.lib.indexers.anilist import get_cast_batch_for_pending
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES
        from resources.lib.meta.provider_settings import external_ids_from_row

        if not self._provider_enabled("anilist"):
            return fallback_pending, {}, {}, []

        anilist_queue: list[tuple[int, dict, int | None, int | None]] = []
        remaining: list[tuple[int, dict]] = []
        for simkl_id, db_object in fallback_pending:
            profile = db_object.get("_art_profile")
            if profile not in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES):
                remaining.append((simkl_id, db_object))
                continue
            ids = external_ids_from_row(db_object)
            mal_id = self._coerce_int_id(ids.get("mal_id"))
            anilist_id = self._coerce_int_id(ids.get("anilist_id"))
            if mal_id or anilist_id:
                anilist_queue.append((simkl_id, db_object, mal_id, anilist_id))
            else:
                remaining.append((simkl_id, db_object))

        if not anilist_queue:
            return fallback_pending, {}, {}, []

        resolved = get_cast_batch_for_pending(anilist_queue)
        cast_map: dict[int, list] = {}
        studio_map: dict[int, str] = {}
        blobs: list[dict] = []
        still_missing: list[tuple[int, dict]] = list(remaining)

        for simkl_id, db_object, _mal_id, _anilist_id in anilist_queue:
            payload = resolved.get(int(simkl_id))
            if not payload:
                still_missing.append((simkl_id, db_object))
                continue
            self._merge_anilist_cast(db_object, payload)
            cast = payload.get("cast")
            if cast:
                cast_map[int(simkl_id)] = cast
            studio = payload.get("studio")
            if studio:
                studio_map[int(simkl_id)] = str(studio)
            blobs.append(db_object)

        return still_missing, cast_map, studio_map, blobs

    def _fetch_anilist_cast_for_object(self, db_object) -> None:
        if not self._provider_enabled("anilist"):
            return
        from resources.lib.indexers.anilist import get_cast_batch_for_pending
        from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES, artwork_profile_for_row
        from resources.lib.meta.provider_settings import external_ids_from_row

        profile = db_object.get("_art_profile")
        if not profile:
            info = db_object.get("info") if isinstance(db_object.get("info"), dict) else {}
            profile = artwork_profile_for_row(
                {"info": info, "simkl_id": db_object.get("simkl_id")},
                info.get("mediatype") or "tvshow",
            )
        if profile not in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES):
            return
        if not self._provider_cast_missing(db_object, "anilist") and db_object.get("cast"):
            return

        simkl_id = self._coerce_int_id(db_object.get("simkl_id") or (db_object.get("info") or {}).get("simkl_id"))
        if simkl_id is None:
            return

        ids = external_ids_from_row(db_object)
        mal_id = self._coerce_int_id(ids.get("mal_id"))
        anilist_id = self._coerce_int_id(ids.get("anilist_id"))
        if not mal_id and not anilist_id:
            return

        payload = get_cast_batch_for_pending([(simkl_id, db_object, mal_id, anilist_id)]).get(simkl_id)
        if payload:
            self._merge_anilist_cast(db_object, payload)

    @staticmethod
    def _merge_cast_studio_into_rows(
        rows: list,
        cast_by_id: dict[int, list],
        studio_by_id: dict[int, str] | None = None,
    ) -> list:
        if not cast_by_id and not studio_by_id:
            return rows
        merged_rows: list = []
        for row in rows:
            if not isinstance(row, dict):
                merged_rows.append(row)
                continue
            simkl_id = row.get("simkl_id") or (row.get("info") or {}).get("simkl_id")
            if simkl_id is None:
                merged_rows.append(row)
                continue
            key = int(simkl_id)
            if key not in cast_by_id and key not in (studio_by_id or {}):
                merged_rows.append(row)
                continue
            updated = dict(row)
            if key in cast_by_id:
                updated["cast"] = cast_by_id[key]
            if studio_by_id and key in studio_by_id:
                info = dict(updated.get("info") or {})
                if not info.get("studio"):
                    info["studio"] = studio_by_id[key]
                updated["info"] = info
            merged_rows.append(updated)
        return merged_rows

    def _fetch_imdb_cast_for_object(self, db_object) -> None:
        if not self._provider_enabled("imdb") or not self._imdb_id_valid(db_object):
            return
        if not self._provider_cast_missing(db_object, "imdb") and db_object.get("cast"):
            return
        from resources.lib.meta.provider_settings import external_ids_from_row
        from resources.lib.indexers.imdb import thread_imdb_api
        from resources.lib.simkl.field_map import _normalize_imdb_id

        imdb_id = _normalize_imdb_id(external_ids_from_row(db_object).get("imdb_id"))
        if not imdb_id:
            return
        cast_map = thread_imdb_api().get_cast_batch([imdb_id])
        cast = cast_map.get(imdb_id)
        if cast:
            self._merge_imdb_cast(db_object, cast)

    def _movie_needs_tmdb_bundle(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return False
        return (
            not self._tmdb_art_meta_up_to_par("movie", db_object)
            or self._provider_cast_missing(db_object, "tmdb")
            or self._force_update(db_object)
        )

    def _merge_tmdb_movie_bundle(self, db_object):
        if not self._movie_needs_tmdb_bundle(db_object):
            return
        if self._provider_cast_missing(db_object, "tmdb"):
            tools.smart_merge_dictionary(db_object, self.tmdb_api.get_movie_cast(db_object["tmdb_id"]))
        self._merge_tmdb_movie_art(db_object)

    def _movie_needs_tvdb_bundle(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return False
        return (
            not self._tvdb_art_meta_up_to_par("movie", db_object)
            or self._provider_cast_missing(db_object, "tvdb")
            or self._force_update(db_object)
        )

    def _merge_tvdb_movie_bundle(self, db_object):
        if not self._movie_needs_tvdb_bundle(db_object):
            return
        if self._provider_cast_missing(db_object, "tvdb"):
            tools.smart_merge_dictionary(db_object, self.tvdb_api.get_movie(db_object["tvdb_id"]))
        self._merge_tvdb_movie_art(db_object)

    def _tvshow_needs_tmdb_bundle(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return False
        return (
            not self._tmdb_art_meta_up_to_par("tvshow", db_object)
            or self._provider_cast_missing(db_object, "tmdb")
            or self._force_update(db_object)
        )

    def _merge_tmdb_tvshow_bundle(self, db_object):
        if not self._tvshow_needs_tmdb_bundle(db_object):
            return
        if self._provider_cast_missing(db_object, "tmdb"):
            tools.smart_merge_dictionary(db_object, self.tmdb_api.get_show_cast(db_object["tmdb_id"]))
        self._merge_tmdb_tvshow_art(db_object)

    def _tvshow_needs_tvdb_bundle(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return False
        needs_cast = self._provider_cast_missing(db_object, "tvdb") and self._provider_cast_missing(db_object, "tmdb")
        return (
            not self._tvdb_art_meta_up_to_par("tvshow", db_object)
            or needs_cast
            or self._force_update(db_object)
        )

    def _merge_tvdb_tvshow_bundle(self, db_object):
        if not self._tvshow_needs_tvdb_bundle(db_object):
            return
        if self._provider_cast_missing(db_object, "tvdb"):
            tools.smart_merge_dictionary(db_object, self.tvdb_api.get_show_cast(db_object["tvdb_id"]))
        self._merge_tvdb_tvshow_art(db_object)

    # region movie
    def _update_movie(self, db_object):
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        if preferred == ART_FANART:
            tools.smart_merge_dictionary(db_object, self._fetch_movie_fanart_patch(db_object) or {})
        elif preferred == ART_TMDB:
            self._merge_tmdb_movie_bundle(db_object)
        elif preferred == ART_TVDB:
            self._merge_tvdb_movie_bundle(db_object)
        self._update_movie_art_fallback(db_object)
        self._merge_supplementary_fanart_movie_art(db_object)
        self._update_movie_cast(db_object)

    def _update_movie_tmdb(self, db_object):
        self._merge_tmdb_movie_bundle(db_object)

    def _update_movie_tvdb(self, db_object):
        self._merge_tvdb_movie_bundle(db_object)

    def _update_movie_fanart(self, db_object):
        if not self._fanart_art_usable():
            return
        tools.smart_merge_dictionary(db_object, self._fetch_movie_fanart_patch(db_object) or {})

    def _update_movie_cast(self, db_object):
        self._fetch_imdb_cast_for_object(db_object)
        if db_object.get("cast"):
            return
        self._fetch_anilist_cast_for_object(db_object)
        if db_object.get("cast"):
            return
        from resources.lib.meta.provider_settings import ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_TVDB and self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
            if self._provider_cast_missing(db_object, "tvdb"):
                tools.smart_merge_dictionary(db_object, self.tvdb_api.get_movie(db_object["tvdb_id"]))
            return
        if self._provider_enabled("tmdb") and self._tmdb_id_valid(db_object):
            if self._provider_cast_missing(db_object, "tmdb"):
                tools.smart_merge_dictionary(db_object, self.tmdb_api.get_movie_cast(db_object["tmdb_id"]))
            return
        if self._provider_enabled("tvdb") and self._tvdb_id_valid(db_object):
            if self._provider_cast_missing(db_object, "tvdb"):
                tools.smart_merge_dictionary(db_object, self.tvdb_api.get_movie(db_object["tvdb_id"]))

    def _list_extended_art_enabled(self, db_object: dict) -> bool:
        """True when any extended list art type (clearlogo, discart, etc.) is enabled."""
        from resources.lib.meta.artwork import (
            PROFILE_ANIME_MOVIE,
            PROFILE_ANIME_SERIES,
            PROFILE_MOVIE,
            artwork_profile_for_row,
        )
        from resources.lib.meta.provider_settings import art_option_enabled

        profile = db_object.get("_art_profile")
        if not profile:
            entity = db_object.get("_entity", "movie")
            default = "movie" if entity == "movie" else "tvshow"
            profile = artwork_profile_for_row(db_object, default_media_type=default)
        if profile == PROFILE_ANIME_MOVIE:
            keys = (
                ("anime.clearlogo", "anime_movie"),
                ("anime.clearart", "anime_movie"),
                ("anime.discart", "anime_movie"),
                ("anime.banner", "anime_movie"),
                ("anime.landscape", "anime_movie"),
            )
        elif profile == PROFILE_ANIME_SERIES:
            keys = (
                ("anime.clearlogo", "anime_series"),
                ("anime.clearart", "anime_series"),
                ("anime.banner", "anime_series"),
                ("anime.landscape", "anime_series"),
            )
        elif profile == PROFILE_MOVIE:
            keys = (
                ("movies.clearlogo", "movie"),
                ("movies.clearart", "movie"),
                ("movies.discart", "movie"),
                ("movies.banner", "movie"),
                ("movies.landscape", "movie"),
            )
        else:
            keys = (
                ("tvshows.clearlogo", "tvshow"),
                ("tvshows.clearart", "tvshow"),
                ("tvshows.banner", "tvshow"),
                ("tvshows.landscape", "tvshow"),
            )
        return any(art_option_enabled(setting_id, media_key) for setting_id, media_key in keys)

    def _fetch_tmdb_movie_art_patch(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return None
        if self._tmdb_art_meta_up_to_par("movie", db_object) and not self._force_update(db_object):
            return None
        return self.tmdb_api.get_movie_art(db_object["tmdb_id"])

    def _fetch_tvdb_movie_art_patch(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return None
        if self._tvdb_art_meta_up_to_par("movie", db_object) and not self._force_update(db_object):
            return None
        return self.tvdb_api.get_movie_art(db_object["tvdb_id"])

    def _fetch_tmdb_tvshow_art_patch(self, db_object):
        if not self._provider_enabled("tmdb") or not self._tmdb_id_valid(db_object):
            return None
        if self._tmdb_art_meta_up_to_par("tvshow", db_object) and not self._force_update(db_object):
            return None
        return self.tmdb_api.get_show_art(db_object["tmdb_id"])

    def _fetch_tvdb_tvshow_art_patch(self, db_object):
        if not self._provider_enabled("tvdb") or not self._tvdb_id_valid(db_object):
            return None
        if self._tvdb_art_meta_up_to_par("tvshow", db_object) and not self._force_update(db_object):
            return None
        return self.tvdb_api.get_show_art(db_object["tvdb_id"])

    def _merge_cast_art_paint_rows(
        self,
        base_rows: list[dict],
        cast_rows: list[dict],
        art_rows: list[dict],
    ) -> list[dict]:
        """Merge parallel cast and art waves by simkl_id, preserving base order."""
        by_id: dict[int, dict] = {
            int(row["simkl_id"]): dict(row)
            for row in base_rows
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }
        for row in cast_rows:
            if not isinstance(row, dict) or row.get("simkl_id") is None:
                continue
            sid = int(row["simkl_id"])
            merged = dict(by_id.get(sid, row))
            if row.get("cast"):
                merged["cast"] = row["cast"]
            cast_info = row.get("info") if isinstance(row.get("info"), dict) else {}
            info = merged.get("info") if isinstance(merged.get("info"), dict) else {}
            if cast_info.get("studio") and not info.get("studio"):
                info = dict(info)
                info["studio"] = cast_info["studio"]
                merged["info"] = info
            by_id[sid] = merged
        for row in art_rows:
            if not isinstance(row, dict) or row.get("simkl_id") is None:
                continue
            sid = int(row["simkl_id"])
            merged = dict(by_id.get(sid, row))
            merged["art"] = tools.smart_merge_dictionary(
                dict(merged.get("art") or {}),
                dict(row.get("art") or {}),
                keep_original=True,
                extend_array=False,
            )
            by_id[sid] = merged
        merged_rows: list[dict] = []
        for row in base_rows:
            if not isinstance(row, dict):
                merged_rows.append(row)
                continue
            sid = row.get("simkl_id")
            if sid is not None and int(sid) in by_id:
                merged_rows.append(by_id[int(sid)])
            else:
                merged_rows.append(row)
        return merged_rows

    def _update_movie_list_art(self, db_object):
        """List paint: extended art from the preferred provider only, then fallback."""
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        wave1: list = []
        if preferred == ART_FANART:
            wave1.append(lambda o=db_object: self._fetch_movie_fanart_patch(o))
        elif preferred == ART_TMDB:
            wave1.append(lambda o=db_object: self._fetch_tmdb_movie_art_patch(o))
        elif preferred == ART_TVDB:
            wave1.append(lambda o=db_object: self._fetch_tvdb_movie_art_patch(o))
        if (
            preferred in (ART_TMDB, ART_TVDB)
            and self._list_extended_art_enabled(db_object)
            and self._fanart_art_usable()
        ):
            wave1.append(lambda o=db_object: self._fetch_movie_fanart_patch(o))
        if wave1:
            self._merge_provider_patches(db_object, self._parallel_provider_patches(wave1))
        self._update_movie_art_fallback(db_object)

    # endregion

    # region tvshow
    def _update_tvshow(self, db_object):
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        if preferred == ART_FANART:
            tools.smart_merge_dictionary(db_object, self._fetch_tvshow_fanart_patch(db_object) or {})
        elif preferred == ART_TMDB:
            self._merge_tmdb_tvshow_bundle(db_object)
        elif preferred == ART_TVDB:
            self._merge_tvdb_tvshow_bundle(db_object)
        self._update_tvshow_art_fallback(db_object)
        self._merge_supplementary_fanart_tvshow_art(db_object)
        self._update_tvshow_cast(db_object)

    def _update_tvshow_tmdb(self, db_object):
        self._merge_tmdb_tvshow_bundle(db_object)

    def _update_tvshow_tvdb(self, db_object):
        self._merge_tvdb_tvshow_bundle(db_object)

    def _update_tvshow_fanart(self, db_object):
        if not self._fanart_art_usable():
            return
        tools.smart_merge_dictionary(db_object, self._fetch_tvshow_fanart_patch(db_object) or {})

    def _update_tvshow_cast(self, db_object):
        self._fetch_imdb_cast_for_object(db_object)
        if db_object.get("cast"):
            return
        self._fetch_anilist_cast_for_object(db_object)
        if db_object.get("cast"):
            return
        from resources.lib.meta.provider_settings import ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_TVDB:
            if (
                self._provider_enabled("tvdb")
                and self._provider_cast_missing(db_object, "tvdb")
                and self._tvdb_id_valid(db_object)
            ):
                tools.smart_merge_dictionary(db_object, self.tvdb_api.get_show_cast(db_object["tvdb_id"]))
            return
        if preferred == ART_TMDB:
            if (
                self._provider_enabled("tmdb")
                and self._provider_cast_missing(db_object, "tmdb")
                and self._tmdb_id_valid(db_object)
            ):
                tools.smart_merge_dictionary(db_object, self.tmdb_api.get_show_cast(db_object["tmdb_id"]))
            return
        if (
            self._provider_enabled("tmdb")
            and self._provider_cast_missing(db_object, "tmdb")
            and not tools.safe_dict_get(db_object, "tvdb_object", "cast")
            and self._tmdb_id_valid(db_object)
        ):
            tools.smart_merge_dictionary(db_object, self.tmdb_api.get_show_cast(db_object["tmdb_id"]))
        elif (
            self._provider_enabled("tvdb")
            and self._provider_cast_missing(db_object, "tvdb")
            and not tools.safe_dict_get(db_object, "tmdb_object", "cast")
            and self._tvdb_id_valid(db_object)
        ):
            tools.smart_merge_dictionary(db_object, self.tvdb_api.get_show_cast(db_object["tvdb_id"]))

    def _update_tvshow_list_art(self, db_object):
        """List paint: extended art from the preferred provider only, then fallback."""
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return
        wave1: list = []
        if preferred == ART_FANART:
            wave1.append(lambda o=db_object: self._fetch_tvshow_fanart_patch(o))
        elif preferred == ART_TMDB:
            wave1.append(lambda o=db_object: self._fetch_tmdb_tvshow_art_patch(o))
        elif preferred == ART_TVDB:
            wave1.append(lambda o=db_object: self._fetch_tvdb_tvshow_art_patch(o))
        if (
            preferred in (ART_TMDB, ART_TVDB)
            and self._list_extended_art_enabled(db_object)
            and self._fanart_art_usable()
        ):
            wave1.append(lambda o=db_object: self._fetch_tvshow_fanart_patch(o))
        if wave1:
            self._merge_provider_patches(db_object, self._parallel_provider_patches(wave1))
        self._update_tvshow_art_fallback(db_object)

    # endregion

    # region season
    def _merge_season_external(self, db_object, external):
        from resources.lib.simkl.field_map import season_external_patch

        patch = season_external_patch(external)
        if patch:
            tools.smart_merge_dictionary(db_object, patch)

    def _simkl_season_lookup(self, db_object):
        return tools.safe_dict_get(db_object, "simkl_object", "info", "season")

    def _update_season(self, db_object):
        """Simkl owns season metadata — external APIs only supply artwork."""
        from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

        preferred = self._preferred_art_source_for_db_object(db_object)
        if preferred == ART_SIMKL:
            return

        def _fetch_tmdb():
            g.ensure_addon()
            if preferred != ART_TMDB or not self._provider_enabled("tmdb"):
                return None
            if not self._tmdb_show_id_valid(db_object):
                return None
            season_num = self._simkl_season_lookup(db_object)
            if season_num is None:
                return None
            needs_refresh = self._tmdb_needs_update(db_object) or self._force_update(db_object)
            if needs_refresh or not self._tmdb_art_meta_up_to_par("season", db_object):
                return self.tmdb_api.get_season_art(db_object["tmdb_show_id"], season_num)
            return None

        def _fetch_tvdb():
            g.ensure_addon()
            if preferred != ART_TVDB or not self._provider_enabled("tvdb"):
                return None
            if not self._tvdb_show_id_valid(db_object):
                return None
            season_num = self._simkl_season_lookup(db_object)
            if season_num is None:
                return None
            needs_refresh = self._tvdb_needs_update(db_object) or self._force_update(db_object)
            if needs_refresh or not self._tvdb_art_meta_up_to_par("season", db_object):
                return self.tvdb_api.get_season_art(db_object["tvdb_show_id"], season_num)
            return None

        def _fetch_fanart():
            g.ensure_addon()
            if preferred != ART_FANART:
                return None
            if not self._fanart_art_usable() or not self.fanarttv_api.fanart_support:
                return None
            if not (self._fanart_needs_update(db_object) or self._force_update(db_object)):
                return None
            if not self._tvdb_show_id_valid(db_object):
                return None
            season_num = self._simkl_season_lookup(db_object)
            if season_num is None:
                return None
            return self.fanarttv_api.get_season(
                db_object.get("tvdb_show_id"),
                season_num,
            )

        for patch in self._parallel_provider_patches([_fetch_tmdb, _fetch_tvdb, _fetch_fanart]):
            if patch:
                self._merge_season_external(db_object, patch)
        self._update_season_fallback(db_object)

    def _update_season_tmdb(self, db_object):
        if not self._provider_enabled("tmdb"):
            return
        if not self._tmdb_show_id_valid(db_object):
            return
        season_num = self._simkl_season_lookup(db_object)
        if season_num is None:
            return
        needs_refresh = self._tmdb_needs_update(db_object) or self._force_update(db_object)
        if needs_refresh or not self._tmdb_art_meta_up_to_par("season", db_object):
            self._merge_season_external(
                db_object,
                self.tmdb_api.get_season_art(db_object["tmdb_show_id"], season_num),
            )

    def _update_season_tvdb(self, db_object):
        if not self._provider_enabled("tvdb"):
            return
        if not self._tvdb_show_id_valid(db_object):
            return
        season_num = self._simkl_season_lookup(db_object)
        if season_num is None:
            return
        needs_refresh = self._tvdb_needs_update(db_object) or self._force_update(db_object)
        if needs_refresh or not self._tvdb_art_meta_up_to_par("season", db_object):
            self._merge_season_external(
                db_object,
                self.tvdb_api.get_season_art(db_object["tvdb_show_id"], season_num),
            )

    def _update_season_fanart(self, db_object):
        if not self._fanart_art_usable():
            return
        if (
            self.fanarttv_api.fanart_support
            and (self._fanart_needs_update(db_object) or self._force_update(db_object))
            and self._tvdb_show_id_valid(db_object)
        ):
            tools.smart_merge_dictionary(
                db_object,
                self.fanarttv_api.get_season(
                    db_object.get("tvdb_show_id"),
                    tools.safe_dict_get(db_object, "simkl_object", "info", "season"),
                ),
            )

    def _update_season_fallback(self, db_object):
        season_num = self._simkl_season_lookup(db_object)
        if season_num is None:
            return
        if (
            self._provider_enabled("tmdb")
            and self._tmdb_show_id_valid(db_object)
            and not self._tmdb_art_meta_up_to_par("season", db_object)
            and not self._is_tmdb_art_preferred_for_child(db_object)
        ):
            self._merge_season_external(
                db_object,
                self.tmdb_api.get_season_art(db_object["tmdb_show_id"], season_num),
            )
        if (
            self._provider_enabled("tvdb")
            and self._tvdb_show_id_valid(db_object)
            and not self._tvdb_art_meta_up_to_par("season", db_object)
            and not self._is_tvdb_art_preferred_for_child(db_object)
        ):
            self._merge_season_external(
                db_object,
                self.tvdb_api.get_season_art(db_object["tvdb_show_id"], season_num),
            )

    # endregion

    # region episode
    def _merge_episode_external(self, db_object, external):
        from resources.lib.simkl.field_map import episode_external_patch

        patch = episode_external_patch(external)
        if patch:
            tools.smart_merge_dictionary(db_object, patch)

    def _update_episode(self, db_object):
        """Simkl owns episode metadata — external APIs only supply artwork."""
        from resources.lib.meta.provider_settings import ART_SIMKL

        if self._preferred_art_source_for_db_object(db_object) == ART_SIMKL:
            return
        self._update_episode_tmdb(db_object)
        self._update_episode_tvdb(db_object)
        self._update_episode_fallback(db_object)

    def _update_episode_tmdb(self, db_object):
        from resources.lib.meta.provider_settings import ART_TMDB

        if self._preferred_art_source_for_db_object(db_object) not in (ART_TMDB,):
            return
        if not self._provider_enabled("tmdb"):
            return
        if not self._tmdb_show_id_valid(db_object):
            return
        season_num, episode_num = self._simkl_episode_lookup(db_object)
        if episode_num is None:
            return
        if not self._tmdb_art_meta_up_to_par("episode", db_object):
            self._merge_episode_external(
                db_object,
                self.tmdb_api.get_episode_art(db_object["tmdb_show_id"], season_num, episode_num),
            )

    def _update_episode_tvdb(self, db_object):
        from resources.lib.meta.provider_settings import ART_TVDB

        if self._preferred_art_source_for_db_object(db_object) not in (ART_TVDB,):
            return
        if not self._provider_enabled("tvdb"):
            return
        if not self._tvdb_show_id_valid(db_object):
            return
        season_num, episode_num = self._simkl_episode_lookup(db_object)
        if episode_num is None:
            return
        if not self._tvdb_art_meta_up_to_par("episode", db_object):
            self._merge_episode_external(
                db_object,
                self.tvdb_api.get_episode(db_object["tvdb_show_id"], season_num, episode_num),
            )

    def _update_episode_fallback(self, db_object):
        season_num, episode_num = self._simkl_episode_lookup(db_object)
        if episode_num is None:
            return
        if (
            self._provider_enabled("tmdb")
            and self._tmdb_show_id_valid(db_object)
            and not self._tmdb_art_meta_up_to_par("episode", db_object)
            and not self._is_tmdb_art_preferred_for_child(db_object)
        ):
            self._merge_episode_external(
                db_object,
                self.tmdb_api.get_episode_art(db_object["tmdb_show_id"], season_num, episode_num),
            )
        if (
            self._provider_enabled("tvdb")
            and self._tvdb_show_id_valid(db_object)
            and not self._tvdb_art_meta_up_to_par("episode", db_object)
            and not self._is_tvdb_art_preferred_for_child(db_object)
        ):
            self._merge_episode_external(
                db_object,
                self.tvdb_api.get_episode(db_object["tvdb_show_id"], season_num, episode_num),
            )

    # endregion

    # endregion

    # region needs_update
    def _tmdb_needs_update(self, db_object):
        return not db_object.get("tmdb_object") or (
            db_object.get("tmdb_meta_hash") and db_object.get("tmdb_meta_hash") != self.tmdb_api.meta_hash
        )

    def _tvdb_needs_update(self, db_object):
        return not db_object.get("tvdb_object") or (
            db_object.get("tvdb_meta_hash") and db_object.get("tvdb_meta_hash") != self.tvdb_api.meta_hash
        )

    def _fanart_needs_update(self, db_object):
        return not db_object.get("fanart_object") or (
            db_object.get("fanart_meta_hash") and db_object.get("fanart_meta_hash") != self.fanarttv_api.meta_hash
        )

    # endregion

    # region is_valid

    @staticmethod
    def _tvdb_id_valid(db_object):
        return db_object.get("tvdb_id") is not None

    def _tvdb_show_id_valid(self, db_object):
        return db_object.get("tvdb_show_id") is not None and self._tvdb_id_valid(db_object)

    @staticmethod
    def _tmdb_id_valid(db_object):
        return db_object.get("tmdb_id") is not None

    def _tmdb_show_id_valid(self, db_object):
        return db_object.get("tmdb_show_id") is not None and self._tmdb_id_valid(db_object)

    @staticmethod
    def _imdb_id_valid(db_object):
        return db_object.get("imdb_id") is not None

    # region fast-menu meta merge (cache-first, online gap-fill)
    @staticmethod
    def _art_key_present(art: dict, art_key: str) -> bool:
        if art.get(art_key):
            return True
        prefix = f"{art_key}"
        return any(key.startswith(prefix) and art.get(key) for key in art)

    def _db_object_for_row(self, row: dict, media_type: str) -> dict:
        from resources.lib.meta.provider_settings import external_ids_from_row
        from resources.lib.simkl.ids import canonicalize_info_identity

        info = dict(row.get("info") or {})
        canonicalize_info_identity(info)
        ids = external_ids_from_row(row)
        for key, value in ids.items():
            if value is not None and info.get(key) is None:
                info[key] = value
        simkl_id = row.get("simkl_id") or info.get("simkl_id")
        db_object = {
            "simkl_id": simkl_id,
            "info": info,
            "tmdb_id": ids.get("tmdb_id"),
            "tvdb_id": ids.get("tvdb_id"),
            "imdb_id": ids.get("imdb_id"),
            "cast": row.get("cast") or [],
            "simkl_object": {
                "info": info,
                "art": dict(row.get("art") or {}),
                "cast": row.get("cast") or [],
            },
        }
        if media_type == "movie":
            info.setdefault("mediatype", "movie")
        else:
            info.setdefault("mediatype", "tvshow")
        return db_object

    @staticmethod
    def _can_fetch_provider_meta(row: dict) -> bool:
        from resources.lib.meta.provider_settings import gapfill_provider_available_for_row

        return gapfill_provider_available_for_row(row)

    @staticmethod
    def _provider_enabled(provider: str) -> bool:
        from resources.lib.meta.provider_settings import provider_enabled

        return provider_enabled(provider)

    @staticmethod
    def _fanart_art_usable() -> bool:
        from resources.lib.meta.provider_settings import fanart_art_usable

        return fanart_art_usable()

    @staticmethod
    def _cast_has_photos(cast: list) -> bool:
        for member in cast:
            if not isinstance(member, dict):
                continue
            if (
                member.get("thumbnail")
                or member.get("thumb")
                or member.get("profile")
                or member.get("profile_path")
            ):
                return True
        return False

    @staticmethod
    def _row_needs_refresh(row: dict, media_type: str) -> bool:
        from resources.lib.database.sync_meta_cache import row_needs_refresh

        normalized = "movie" if media_type == "movie" else "show"
        return row_needs_refresh(normalized, row)

    def _row_meta_gaps(
        self,
        row: dict,
        media_type: str,
        art_profile: str | None = None,
        *,
        scope: str = "full",
    ) -> list[str]:
        from resources.lib.meta.artwork import (
            PROFILE_ANIME_MOVIE,
            PROFILE_ANIME_SERIES,
            PROFILE_MOVIE,
            artwork_profile_for_row,
        )
        from resources.lib.meta.provider_settings import art_gapfill_available, art_option_enabled, cast_gapfill_available, fanart_art_usable

        if art_profile is None:
            art_profile = artwork_profile_for_row(row, default_media_type=media_type)
        provider_type = "movie" if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_MOVIE) else "tvshow"
        blocking_scope = str(scope).lower() == "blocking"

        gaps: list[str] = []
        cast = row.get("cast")
        if cast_gapfill_available(row, provider_type) and (
            not cast
            or not isinstance(cast, list)
            or len(cast) == 0
            or not self._cast_has_photos(cast)
        ):
            gaps.append("cast")
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        online_art_keys = []
        if not blocking_scope:
            if art_profile == PROFILE_ANIME_MOVIE:
                if art_option_enabled("anime.clearlogo", "anime_movie"):
                    online_art_keys.append("clearlogo")
                if art_option_enabled("anime.clearart", "anime_movie"):
                    online_art_keys.append("clearart")
                if art_option_enabled("anime.discart", "anime_movie"):
                    online_art_keys.append("discart")
                if art_option_enabled("anime.banner", "anime_movie"):
                    online_art_keys.append("banner")
                if art_option_enabled("anime.landscape", "anime_movie"):
                    online_art_keys.append("landscape")
            elif art_profile == PROFILE_ANIME_SERIES:
                if art_option_enabled("anime.clearlogo", "anime_series"):
                    online_art_keys.append("clearlogo")
                if art_option_enabled("anime.clearart", "anime_series"):
                    online_art_keys.append("clearart")
                if art_option_enabled("anime.banner", "anime_series"):
                    online_art_keys.append("banner")
                if art_option_enabled("anime.landscape", "anime_series"):
                    online_art_keys.append("landscape")
            elif media_type == "movie":
                if art_option_enabled("movies.clearlogo", "movie"):
                    online_art_keys.append("clearlogo")
                if art_option_enabled("movies.clearart", "movie"):
                    online_art_keys.append("clearart")
                if art_option_enabled("movies.discart", "movie"):
                    online_art_keys.append("discart")
                if art_option_enabled("movies.banner", "movie"):
                    online_art_keys.append("banner")
                if art_option_enabled("movies.landscape", "movie"):
                    online_art_keys.append("landscape")
            elif media_type in ("tvshow", "show"):
                if art_option_enabled("tvshows.clearlogo", "tvshow"):
                    online_art_keys.append("clearlogo")
                if art_option_enabled("tvshows.clearart", "tvshow"):
                    online_art_keys.append("clearart")
                if art_option_enabled("tvshows.banner", "tvshow"):
                    online_art_keys.append("banner")
                if art_option_enabled("tvshows.landscape", "tvshow"):
                    online_art_keys.append("landscape")
            for art_key in online_art_keys:
                if art_gapfill_available(row, provider_type) and not self._art_key_present(art, art_key):
                    gaps.append(art_key)
        if (
            fanart_art_usable()
            and art_gapfill_available(row, provider_type)
            and not self._art_key_present(art, "fanart")
        ):
            gaps.append("fanart")
        return gaps

    def merge_row_from_cache(
        self,
        row: dict,
        media_type: str,
        *,
        db=None,
        art_profile: str | None = None,
        provider_cache: dict | None = None,
    ) -> dict:
        """Merge cached provider art and cast only — Simkl owns descriptive metadata."""
        if not isinstance(row, dict):
            return row

        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type
        from resources.lib.simkl.ids import canonicalize_info_identity, entity_simkl_id

        info = row.get("info") or {}
        if isinstance(info, dict):
            canonicalize_info_identity(info)

        if art_profile is None:
            art_profile = artwork_profile_for_row(row, default_media_type=media_type)
        provider_type = provider_media_type(art_profile)

        simkl_id = entity_simkl_id({"info": info, "simkl_id": row.get("simkl_id")}) or row.get("simkl_id") or info.get("simkl_id")
        if not simkl_id:
            return row

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        table = "movies" if provider_type == "movie" else "shows"
        db_object = self._db_object_for_row(row, provider_type)
        db_object["_art_profile"] = art_profile
        cached_meta = (provider_cache or {}).get(int(simkl_id))
        if cached_meta is None:
            cached_meta = db.load_cached_provider_meta(table, int(simkl_id), info)
        db_object.update(cached_meta)
        if not any(
            db_object.get(f"{provider}_object")
            for provider in ("simkl", "tmdb", "tvdb", "imdb", "fanart")
        ):
            return row

        formatted = self.format_meta(db_object)
        merged = dict(row)
        merged["art"] = tools.smart_merge_dictionary(
            dict(row.get("art") or {}),
            formatted.get("art") or {},
            keep_original=True,
            extend_array=False,
        )
        if formatted.get("cast"):
            merged["cast"] = formatted["cast"]
        return merged

    def _collect_enrichment_ref(
        self,
        row: dict,
        media_type: str,
        *,
        meta_cache,
        art_profile: str | None = None,
    ) -> dict | None:
        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type
        from resources.lib.simkl.ids import entity_simkl_id

        if art_profile is None:
            art_profile = artwork_profile_for_row(row, default_media_type=media_type)
        provider_type = provider_media_type(art_profile)
        cache_media_type = "movie" if provider_type == "movie" else "show"
        gaps = self._row_meta_gaps(row, provider_type, art_profile=art_profile, scope="full")
        stale = self._row_needs_refresh(row, provider_type)
        simkl_id = entity_simkl_id(row) or row.get("simkl_id")
        if simkl_id is None:
            return None
        if stale:
            meta_cache.delete_row(cache_media_type, int(simkl_id))
        actionable_gaps = [
            gap
            for gap in gaps
            if not meta_cache.is_gap_miss(cache_media_type, int(simkl_id), gap)
        ]
        if not ((actionable_gaps or stale) and self._can_fetch_provider_meta(row)):
            return None
        return {
            "simkl_id": int(simkl_id),
            "needs_update": True,
            "_gapfill_gaps": list(actionable_gaps),
            "_provider_type": provider_type,
        }

    def merge_list_meta_local(self, rows, media_type: str, *, db=None) -> tuple[list, list[dict]]:
        """Merge cached provider meta for list paint — no HTTP, no DB writes."""
        import time

        if not rows:
            return rows, []

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        start = time.time()
        merged: list = []
        enrichment_refs: list[dict] = []
        from resources.lib.database.sync_meta_cache import SyncMetaCache
        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

        meta_cache = SyncMetaCache()
        from resources.lib.meta.paint_stamp import row_has_trusted_paint_stamp

        movie_rows: list[dict] = []
        show_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            profile = artwork_profile_for_row(row, default_media_type=media_type)
            if provider_media_type(profile) == "movie":
                movie_rows.append(row)
            else:
                show_rows.append(row)

        provider_cache: dict[int, dict] = {}
        if movie_rows:
            provider_cache.update(db.load_cached_provider_meta_batch("movies", movie_rows))
        if show_rows:
            provider_cache.update(db.load_cached_provider_meta_batch("shows", show_rows))

        for row in rows:
            if not isinstance(row, dict):
                merged.append(row)
                continue
            if row_has_trusted_paint_stamp(row):
                merged.append(row)
                continue
            info = row.get("info")
            if isinstance(info, dict):
                from resources.lib.simkl.ids import canonicalize_info_identity

                canonicalize_info_identity(info)
            profile = artwork_profile_for_row(row, default_media_type=media_type)
            provider_type = provider_media_type(profile)
            updated = self.merge_row_from_cache(
                row,
                provider_type,
                db=db,
                art_profile=profile,
                provider_cache=provider_cache,
            )
            ref = self._collect_enrichment_ref(updated, provider_type, meta_cache=meta_cache, art_profile=profile)
            if ref:
                enrichment_refs.append(ref)
            merged.append(updated)

        g.log(
            f"local_merge_ms={(time.time() - start) * 1000:.0f} rows={len(rows)} enrich_refs={len(enrichment_refs)}",
            "debug",
        )
        return merged, enrichment_refs

    def _fetch_cast_patch(self, db_object: dict) -> dict:
        """Fetch TMDB/TVDB cast credits for a single list row db_object."""
        entity = db_object.get("_entity", "movie")
        if entity == "movie":
            self._update_movie_cast(db_object)
        else:
            self._update_tvshow_cast(db_object)
        return db_object

    _LIST_ART_GAP_KEYS = frozenset({"clearlogo", "clearart", "discart", "banner", "landscape", "fanart"})

    def _fetch_art_patch(self, db_object: dict) -> dict:
        """Fetch Fanart/TMDB/TVDB extended art for a single list row db_object."""
        from resources.lib.meta.profiles import MetaProfile, profile_scope

        entity = db_object.get("_entity", "movie")
        with profile_scope(MetaProfile.LIST):
            if entity == "movie":
                self._update_movie_list_art(db_object)
            else:
                self._update_tvshow_list_art(db_object)
        return db_object

    def _persist_list_provider_blobs(self, db_object: dict, db, provider_type: str) -> None:
        """Cache provider blobs fetched during list paint for subsequent offline merges."""
        from resources.lib.meta.profiles import MetaProfile, profile_scope

        table = "movies" if provider_type == "movie" else "shows"
        fanart_id_col = "tmdb_id" if provider_type == "movie" else "tvdb_id"
        with profile_scope(MetaProfile.FULL):
            if db_object.get("tmdb_object"):
                db.save_to_meta_table([db_object], table, "tmdb", "tmdb_id")
            if db_object.get("tvdb_object"):
                db.save_to_meta_table([db_object], table, "tvdb", "tvdb_id")
            imdb_obj = db_object.get("imdb_object")
            if isinstance(imdb_obj, dict) and imdb_obj and set(imdb_obj.keys()) != {"cast"}:
                db.save_to_meta_table([db_object], table, "imdb", "imdb_id")
            if db_object.get("fanart_object"):
                db.save_to_meta_table([db_object], table, "fanart", fanart_id_col)

    def fill_list_art_online(
        self,
        rows,
        media_type: str,
        *,
        db=None,
        gap_scope: str = "full",
    ) -> list:
        """Deduped extended art lookup via ArtBatchCoordinator."""
        if not rows:
            return rows

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

        provider_cache: dict[int, dict] = {}
        movie_rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and provider_media_type(artwork_profile_for_row(row, default_media_type=media_type)) == "movie"
        ]
        show_rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and provider_media_type(artwork_profile_for_row(row, default_media_type=media_type)) != "movie"
        ]
        if movie_rows:
            provider_cache.update(db.load_cached_provider_meta_batch("movies", movie_rows))
        if show_rows:
            provider_cache.update(db.load_cached_provider_meta_batch("shows", show_rows))

        merged_rows, _stats = self._apply_art_gaps_to_rows(
            rows,
            media_type,
            db=db,
            provider_cache=provider_cache,
            gap_scope=gap_scope,
        )
        try:
            from resources.lib.meta.display_store import get_display_meta_store

            paint_type = "movie" if str(media_type).lower() in ("movie", "movies") else "tvshow"
            to_persist = [
                row
                for row in merged_rows
                if isinstance(row, dict) and row.get("simkl_id") is not None and row.get("art")
            ]
            if to_persist:
                get_display_meta_store().merge_art_cast_rows_batch(paint_type, to_persist)
        except Exception:
            g.log_stacktrace()
        return merged_rows

    def fill_list_cast_online(self, rows, media_type: str, *, db=None) -> list:
        """POV-style per-item cast lookup for visible list rows missing cached cast."""
        if not rows:
            return rows

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

        pending: list[tuple[int, dict]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            profile = artwork_profile_for_row(row, default_media_type=media_type)
            provider_type = provider_media_type(profile)
            gaps = self._row_meta_gaps(row, provider_type, art_profile=profile)
            if "cast" not in gaps or not self._can_fetch_provider_meta(row):
                continue
            simkl_id = row.get("simkl_id") or (row.get("info") or {}).get("simkl_id")
            if simkl_id is None:
                continue
            db_object = self._db_object_for_row(row, provider_type)
            db_object["_entity"] = "movie" if provider_type == "movie" else "tvshow"
            db_object["_art_profile"] = profile
            pending.append((int(simkl_id), db_object))

        if not pending:
            return rows

        provider_type = "movie" if str(media_type).lower() in ("movie", "movies") else "tvshow"

        imdb_pending: list[tuple[int, dict, str]] = []
        fallback_pending: list[tuple[int, dict]] = []
        if self._provider_enabled("imdb"):
            from resources.lib.meta.provider_settings import external_ids_from_row
            from resources.lib.simkl.field_map import _normalize_imdb_id

            for simkl_id, db_object in pending:
                imdb_id = _normalize_imdb_id(external_ids_from_row(db_object).get("imdb_id"))
                if imdb_id:
                    imdb_pending.append((simkl_id, db_object, imdb_id))
                else:
                    fallback_pending.append((simkl_id, db_object))
        else:
            fallback_pending = list(pending)

        cast_by_id: dict[int, list] = {}
        if imdb_pending:
            from resources.lib.indexers.imdb import thread_imdb_api

            imdb_ids = [item[2] for item in imdb_pending]
            cast_map = thread_imdb_api().get_cast_batch(imdb_ids)
            for simkl_id, db_object, imdb_id in imdb_pending:
                cast = cast_map.get(imdb_id)
                if cast:
                    self._merge_imdb_cast(db_object, cast)
                    cast_by_id[simkl_id] = cast
                else:
                    fallback_pending.append((simkl_id, db_object))
            try:
                for simkl_id, db_object, _ in imdb_pending:
                    if db_object.get("imdb_object"):
                        self._persist_list_provider_blobs(db_object, db, provider_type)
            except Exception:
                g.log_stacktrace()

        studio_by_id: dict[int, str] = {}
        fallback_pending, anilist_cast_map, anilist_studio_map, _anilist_blobs = self._fill_anilist_cast_batch(fallback_pending)
        cast_by_id.update(anilist_cast_map)
        studio_by_id.update(anilist_studio_map)

        from resources.lib.common.thread_pool import ThreadPool

        pool = ThreadPool()
        for _, db_object in fallback_pending:
            pool.put(self._fetch_cast_patch, db_object)

        if pool.tasks:
            import concurrent.futures

            for task in concurrent.futures.as_completed(pool.tasks):
                try:
                    db_object = task.result()
                except Exception:
                    g.log_stacktrace()
                    continue
                if not isinstance(db_object, dict):
                    continue
                sid = db_object.get("simkl_id") or (db_object.get("info") or {}).get("simkl_id")
                if sid is None:
                    continue
                formatted = self.format_meta(db_object)
                cast = formatted.get("cast")
                if cast:
                    cast_by_id[int(sid)] = cast
                if db_object.get("imdb_object"):
                    try:
                        self._persist_list_provider_blobs(db_object, db, provider_type)
                    except Exception:
                        g.log_stacktrace()
            pool.tasks.clear()

        if not cast_by_id and not studio_by_id:
            return rows

        merged_rows = self._merge_cast_studio_into_rows(rows, cast_by_id, studio_by_id)

        try:
            from resources.lib.meta.display_store import get_display_meta_store

            paint_type = "movie" if str(media_type).lower() in ("movie", "movies") else "tvshow"
            to_persist = [
                row
                for row in merged_rows
                if isinstance(row, dict) and row.get("simkl_id") is not None and row.get("cast")
            ]
            if to_persist:
                store = get_display_meta_store()
                for paint_row in to_persist:
                    store.merge_art_cast_row(paint_type, paint_row)
        except Exception:
            g.log_stacktrace()

        return merged_rows

    def _apply_cast_gaps_to_rows(
        self,
        rows: list[dict],
        media_type: str,
        *,
        db,
    ) -> tuple[list[dict], int]:
        """IMDb batch + fallback cast gap-fill for the given row subset."""
        if not rows:
            return rows, 0

        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

        pending: list[tuple[int, dict]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            profile = artwork_profile_for_row(row, default_media_type=media_type)
            provider_type = provider_media_type(profile)
            gaps = self._row_meta_gaps(row, provider_type, art_profile=profile)
            if "cast" not in gaps or not self._can_fetch_provider_meta(row):
                continue
            simkl_id = row.get("simkl_id") or (row.get("info") or {}).get("simkl_id")
            if simkl_id is None:
                continue
            db_object = self._db_object_for_row(row, provider_type)
            db_object["_entity"] = "movie" if provider_type == "movie" else "tvshow"
            db_object["_art_profile"] = profile
            pending.append((int(simkl_id), db_object))

        if not pending:
            return rows, 0

        provider_type = "movie" if str(media_type).lower() in ("movie", "movies") else "tvshow"
        cast_batch_count = 0
        imdb_pending: list[tuple[int, dict, str]] = []
        fallback_pending: list[tuple[int, dict]] = []
        if self._provider_enabled("imdb"):
            from resources.lib.meta.provider_settings import external_ids_from_row
            from resources.lib.simkl.field_map import _normalize_imdb_id

            for simkl_id, db_object in pending:
                imdb_id = _normalize_imdb_id(external_ids_from_row(db_object).get("imdb_id"))
                if imdb_id:
                    imdb_pending.append((simkl_id, db_object, imdb_id))
                else:
                    fallback_pending.append((simkl_id, db_object))
        else:
            fallback_pending = list(pending)

        cast_by_id: dict[int, list] = {}
        blobs_to_persist: list[dict] = []
        if imdb_pending:
            from resources.lib.indexers.imdb import thread_imdb_api

            imdb_ids = [item[2] for item in imdb_pending]
            cast_map = thread_imdb_api().get_cast_batch(imdb_ids)
            cast_batch_count = 1
            for simkl_id, db_object, imdb_id in imdb_pending:
                cast = cast_map.get(imdb_id)
                if cast:
                    self._merge_imdb_cast(db_object, cast)
                    cast_by_id[simkl_id] = cast
                    blobs_to_persist.append(db_object)
                else:
                    fallback_pending.append((simkl_id, db_object))

        studio_by_id: dict[int, str] = {}
        fallback_pending, anilist_cast_map, anilist_studio_map, anilist_blobs = self._fill_anilist_cast_batch(fallback_pending)
        cast_by_id.update(anilist_cast_map)
        studio_by_id.update(anilist_studio_map)
        if anilist_blobs:
            cast_batch_count += 1
            blobs_to_persist.extend(anilist_blobs)

        from resources.lib.common.thread_pool import ThreadPool

        pool = ThreadPool()
        for _, db_object in fallback_pending:
            pool.put(self._fetch_cast_patch, db_object)

        if pool.tasks:
            import concurrent.futures

            for task in concurrent.futures.as_completed(pool.tasks):
                try:
                    db_object = task.result()
                except Exception:
                    g.log_stacktrace()
                    continue
                if not isinstance(db_object, dict):
                    continue
                sid = db_object.get("simkl_id") or (db_object.get("info") or {}).get("simkl_id")
                if sid is None:
                    continue
                formatted = self.format_meta(db_object)
                cast = formatted.get("cast")
                if cast:
                    cast_by_id[int(sid)] = cast
                if db_object.get("imdb_object"):
                    blobs_to_persist.append(db_object)
            pool.tasks.clear()

        for db_object in blobs_to_persist:
            try:
                self._persist_list_provider_blobs(db_object, db, provider_type)
            except Exception:
                g.log_stacktrace()

        if not cast_by_id and not studio_by_id:
            return rows, cast_batch_count

        merged_rows = self._merge_cast_studio_into_rows(rows, cast_by_id, studio_by_id)
        return merged_rows, cast_batch_count

    def _apply_art_gaps_to_rows(
        self,
        rows: list[dict],
        media_type: str,
        *,
        db,
        provider_cache: dict[int, dict] | None = None,
        gap_scope: str = "full",
    ) -> tuple[list[dict], dict[str, int]]:
        """Coordinated art gap-fill for the given row subset."""
        if not rows:
            return rows, {"art_fetch": 0, "art_deduped": 0}

        from resources.lib.meta.art_batch import ArtBatchCoordinator
        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type
        from resources.lib.meta.paint_complete import art_gap_keys_for_scope

        allowed_art_keys = art_gap_keys_for_scope(gap_scope)
        pending: list[tuple[int, dict, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            profile = artwork_profile_for_row(row, default_media_type=media_type)
            provider_type = provider_media_type(profile)
            gaps = self._row_meta_gaps(
                row, provider_type, art_profile=profile, scope=gap_scope
            )
            art_gaps = [gap for gap in gaps if gap in allowed_art_keys]
            if not art_gaps or not self._can_fetch_provider_meta(row):
                continue
            simkl_id = row.get("simkl_id") or (row.get("info") or {}).get("simkl_id")
            if simkl_id is None:
                continue
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            table = "movies" if provider_type == "movie" else "shows"
            db_object = self._db_object_for_row(row, provider_type)
            cached = (provider_cache or {}).get(int(simkl_id))
            if cached:
                db_object.update(cached)
            else:
                db_object.update(db.load_cached_provider_meta(table, int(simkl_id), info))
            db_object["_entity"] = "movie" if provider_type == "movie" else "tvshow"
            db_object["_art_profile"] = profile
            pending.append((int(simkl_id), db_object, provider_type))

        if not pending:
            return rows, {"art_fetch": 0, "art_deduped": 0}

        coordinator = ArtBatchCoordinator(self)
        art_by_id = coordinator.apply_art_gaps(pending, db=db, provider_cache=provider_cache)
        if not art_by_id:
            return rows, coordinator.stats

        merged_rows: list = []
        for row in rows:
            if not isinstance(row, dict):
                merged_rows.append(row)
                continue
            simkl_id = row.get("simkl_id") or (row.get("info") or {}).get("simkl_id")
            if simkl_id is not None and int(simkl_id) in art_by_id:
                updated = dict(row)
                updated["art"] = tools.smart_merge_dictionary(
                    dict(row.get("art") or {}),
                    art_by_id[int(simkl_id)],
                    keep_original=True,
                    extend_array=False,
                )
                merged_rows.append(updated)
            else:
                merged_rows.append(row)
        return merged_rows, coordinator.stats

    def _prepare_drilldown_rows_local(self, rows: list[dict]) -> list[dict]:
        """Simkl-only local paint for season/episode drilldown (no provider HTTP)."""
        prepared: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                prepared.append(row)
                continue
            updated = dict(row)
            info = updated.get("info") if isinstance(updated.get("info"), dict) else {}
            simkl_data = {"info": dict(info), "art": dict(updated.get("art") or {})}
            mediatype = (info.get("mediatype") or "").lower()
            if mediatype == "episode":
                self._apply_simkl_episode_thumb(updated, simkl_data)
                art = updated.setdefault("art", {})
                if not art.get("thumb") and not updated.get("info", {}).get("thumb"):
                    thumb = (
                        art.get("poster")
                        or art.get("tvshow.poster")
                        or art.get("season.poster")
                        or art.get("fanart")
                        or art.get("tvshow.fanart")
                    )
                    if thumb:
                        art["thumb"] = thumb
                        updated["info"]["thumb"] = thumb
            elif mediatype == "season":
                art = updated.setdefault("art", {})
                if not art.get("poster") and not art.get("thumb"):
                    poster = info.get("poster")
                    if poster:
                        art["poster"] = poster
                        art.setdefault("thumb", poster)
            prepared.append(updated)
        return prepared

    def prepare_list_rows_for_paint(
        self,
        rows: list[dict],
        media_type: str,
        *,
        db=None,
        profile: str = "browse",
        overlay_sync: bool = True,
    ) -> tuple[list[dict], list[dict], dict]:
        """Seren-style prepare: provider HTTP only for incomplete rows."""
        import time

        stats: dict[str, int | float] = {
            "complete": 0,
            "incomplete": 0,
            "cast_batch": 0,
            "art_fetch": 0,
            "art_deduped": 0,
            "cast_art_parallel_ms": 0.0,
            "prepare_ms": 0.0,
            "prepare_skipped": 0,
        }
        if not rows:
            return rows, [], stats

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        start = time.time()
        from resources.lib.meta.paint_complete import partition_paint_rows

        complete_rows, incomplete_rows = partition_paint_rows(rows, media_type, profile=profile, handler=self)
        stats["complete"] = len(complete_rows)
        stats["incomplete"] = len(incomplete_rows)

        enrichment_refs: list[dict] = []
        prepared_incomplete = incomplete_rows

        if incomplete_rows:
            if str(profile).lower() == "drilldown":
                prepared_incomplete = self._prepare_drilldown_rows_local(incomplete_rows)
            else:
                prepared_incomplete, enrichment_refs = self.merge_list_meta_local(
                    incomplete_rows, media_type, db=db
                )
                from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type
                from resources.lib.meta.paint_complete import gap_scope_for_profile

                gap_scope = gap_scope_for_profile(profile)
                provider_cache: dict[int, dict] = {}
                movie_rows = [
                    row
                    for row in prepared_incomplete
                    if provider_media_type(artwork_profile_for_row(row, default_media_type=media_type)) == "movie"
                ]
                show_rows = [
                    row
                    for row in prepared_incomplete
                    if provider_media_type(artwork_profile_for_row(row, default_media_type=media_type)) != "movie"
                ]
                if movie_rows:
                    provider_cache.update(db.load_cached_provider_meta_batch("movies", movie_rows))
                if show_rows:
                    provider_cache.update(db.load_cached_provider_meta_batch("shows", show_rows))

                wave_rows = [dict(row) for row in prepared_incomplete]
                cast_input = [dict(row) for row in wave_rows]
                art_input = [dict(row) for row in wave_rows]
                parallel_start = time.time()
                from resources.lib.common.thread_pool import get_provider_executor

                executor = get_provider_executor()
                cast_future = executor.submit(
                    self._apply_cast_gaps_to_rows, cast_input, media_type, db=db
                )
                art_future = executor.submit(
                    self._apply_art_gaps_to_rows,
                    art_input,
                    media_type,
                    db=db,
                    provider_cache=provider_cache,
                    gap_scope=gap_scope,
                )
                cast_rows, cast_batch_count = cast_future.result()
                art_rows, art_stats = art_future.result()
                prepared_incomplete = self._merge_cast_art_paint_rows(wave_rows, cast_rows, art_rows)
                stats["cast_batch"] = cast_batch_count
                stats["cast_art_parallel_ms"] = round((time.time() - parallel_start) * 1000, 1)
                stats["art_fetch"] = int(art_stats.get("art_fetch", 0))
                stats["art_deduped"] = int(art_stats.get("art_deduped", 0))

        if overlay_sync and prepared_incomplete and str(profile).lower() != "drilldown":
            from resources.lib.meta.paint_cache import _overlay_sync_fields

            prepared_incomplete = _overlay_sync_fields(prepared_incomplete, media_type, db)

        by_id: dict[int, dict] = {}
        for row in complete_rows + prepared_incomplete:
            if isinstance(row, dict) and row.get("simkl_id") is not None:
                by_id[int(row["simkl_id"])] = row

        merged: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                merged.append(row)
                continue
            sid = row.get("simkl_id")
            if sid is not None and int(sid) in by_id:
                merged.append(by_id[int(sid)])
            else:
                merged.append(row)

        try:
            from resources.lib.meta.display_store import get_display_meta_store
            from resources.lib.meta.paint_complete import row_paint_complete
            from resources.lib.meta.paint_library import filter_library_paint_rows

            from resources.lib.meta.paint_stamp import attach_paint_stamp, row_ready_to_stamp

            paint_type = "movie" if str(media_type).lower() in ("movie", "movies") else "tvshow"
            paint_complete_rows = [
                row
                for row in merged
                if isinstance(row, dict)
                and row.get("simkl_id") is not None
                and row_paint_complete(row, media_type, handler=self, scope="full")
            ]
            paint_complete_ids = {
                int(row["simkl_id"])
                for row in paint_complete_rows
                if row.get("simkl_id") is not None
            }
            to_stamp = [
                row
                for row in merged
                if isinstance(row, dict)
                and row.get("simkl_id") is not None
                and (
                    int(row["simkl_id"]) in paint_complete_ids
                    or (stats["incomplete"] > 0 and row_ready_to_stamp(row))
                )
            ]
            if to_stamp:
                stamped_batch = get_display_meta_store().set_stamped_rows_batch(paint_type, to_stamp)
                stamped_ids = {int(row["simkl_id"]) for row in to_stamp if row.get("simkl_id") is not None}

                merged = [
                    attach_paint_stamp(row)
                    if isinstance(row, dict)
                    and row.get("simkl_id") is not None
                    and int(row["simkl_id"]) in stamped_ids
                    else row
                    for row in merged
                ]
                _ = stamped_batch
                sync_media = "movie" if paint_type == "movie" else "show"
                library_rows = filter_library_paint_rows(paint_complete_rows, db=db)
                if library_rows:
                    try:
                        db.upsert_paint_rows_batch(sync_media, library_rows)
                    except Exception:
                        g.log_stacktrace()
        except Exception:
            g.log_stacktrace()

        stats["prepare_skipped"] = 1 if stats["incomplete"] == 0 else 0

        if not enrichment_refs:
            from resources.lib.database.sync_meta_cache import SyncMetaCache

            meta_cache = SyncMetaCache()
            from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

            for row in merged:
                if not isinstance(row, dict) or row.get("simkl_id") is None:
                    continue
                art_profile = artwork_profile_for_row(row, default_media_type=media_type)
                provider_type = provider_media_type(art_profile)
                ref = self._collect_enrichment_ref(
                    row, provider_type, meta_cache=meta_cache, art_profile=art_profile
                )
                if ref:
                    enrichment_refs.append(ref)

        from resources.lib.meta.paint_complete import row_paint_complete

        filtered_refs: list[dict] = []
        for ref in enrichment_refs:
            sid = ref.get("simkl_id")
            if sid is None:
                continue
            row = by_id.get(int(sid))
            if row and row_paint_complete(row, media_type, handler=self, scope="full"):
                art_profile = artwork_profile_for_row(row, default_media_type=media_type)
                provider_type = provider_media_type(art_profile)
                if not self._row_needs_refresh(row, provider_type):
                    continue
            filtered_refs.append(ref)
        enrichment_refs = filtered_refs

        stats["prepare_ms"] = round((time.time() - start) * 1000, 1)
        return merged, enrichment_refs, stats

    def enrich_list_meta_online(
        self,
        refs: list[dict],
        media_type: str,
        *,
        db=None,
        persist: bool = True,
        catalog: str | None = None,
    ) -> list:
        """Fetch missing provider meta online and optionally persist merged rows."""
        if not refs:
            return []

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        from resources.lib.database.sync_meta_cache import SyncMetaCache
        from resources.lib.meta.profiles import current_profile
        from resources.lib.meta.providers import MetaProviderRouter
        from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

        meta_cache = SyncMetaCache()
        need_online_refs = [dict(ref) for ref in refs if ref.get("simkl_id") is not None]
        movie_refs, tvshow_refs = MetaProviderRouter.group_refs_by_provider_type(need_online_refs)
        active_profile = current_profile()
        online_ids: set[int] = set()
        if movie_refs:
            online_ids |= self._online_update_refs(
                movie_refs, "movie", db, profile=active_profile, catalog=catalog
            )
        if tvshow_refs:
            online_ids |= self._online_update_refs(
                tvshow_refs, "tvshow", db, profile=active_profile, catalog=catalog
            )

        merged_by_id: dict[int, dict] = {}
        if online_ids:
            movie_ids = {
                int(ref["simkl_id"])
                for ref in movie_refs
                if ref.get("simkl_id") is not None and int(ref["simkl_id"]) in online_ids
            }
            tvshow_ids = {
                int(ref["simkl_id"])
                for ref in tvshow_refs
                if ref.get("simkl_id") is not None and int(ref["simkl_id"]) in online_ids
            }
            if movie_ids:
                merged_by_id.update(self._reload_rows_by_id(db, "movie", movie_ids))
            if tvshow_ids:
                merged_by_id.update(self._reload_rows_by_id(db, "tvshow", tvshow_ids))

        ref_by_id = {
            int(ref["simkl_id"]): ref
            for ref in need_online_refs
            if ref.get("simkl_id") is not None
        }
        merged = list(merged_by_id.values())
        for row in merged:
            if not isinstance(row, dict):
                continue
            simkl_id = row.get("simkl_id")
            if simkl_id is None:
                continue
            sid = int(simkl_id)
            ref = ref_by_id.get(sid)
            if not ref:
                continue
            provider_type = ref.get("_provider_type") or media_type
            cache_media_type = "movie" if provider_type == "movie" else "show"
            profile = artwork_profile_for_row(row, default_media_type=provider_type)
            remaining_gaps = self._row_meta_gaps(row, provider_type, art_profile=profile)
            if remaining_gaps and self._can_fetch_provider_meta(row):
                for gap in remaining_gaps:
                    meta_cache.mark_gap_miss(cache_media_type, sid, gap)
            else:
                meta_cache.clear_provider_miss(cache_media_type, sid)
                meta_cache.set_row(cache_media_type, row)

        if persist and merged:
            movie_rows = [
                row
                for row in merged
                if isinstance(row, dict) and provider_media_type(artwork_profile_for_row(row, media_type)) == "movie"
            ]
            tvshow_rows = [
                row
                for row in merged
                if isinstance(row, dict) and provider_media_type(artwork_profile_for_row(row, media_type)) != "movie"
            ]
            if movie_rows:
                self._persist_list_rows(movie_rows, "movie", db=db, skip_ids=online_ids)
            if tvshow_rows:
                self._persist_list_rows(tvshow_rows, "tvshow", db=db, skip_ids=online_ids)
        return merged

    def _online_update_refs(
        self,
        refs: list[dict],
        media_type: str,
        db,
        *,
        profile: str | None = None,
        catalog: str | None = None,
    ) -> set[int]:
        if not refs:
            return set()
        from resources.lib.meta.profiles import MetaProfile, current_profile, profile_scope

        active_profile = profile or current_profile() or MetaProfile.FULL
        with profile_scope(active_profile):
            if catalog and hasattr(db, "ensure_catalog_refs_seeded"):
                db.ensure_catalog_refs_seeded(refs, catalog, media_type)
            if media_type == "movie":
                updater = db if hasattr(db, "_update_movies") else None
                if updater is None:
                    from resources.lib.database.session import get_sync_database as MoviesDB

                    updater = MoviesDB()
                updater._update_movies(refs)
            else:
                updater = db if hasattr(db, "_update_mill_format_shows") else None
                if updater is None:
                    from resources.lib.database.session import get_sync_database as ShowsDB

                    updater = ShowsDB()
                updater._update_mill_format_shows(refs, False, skip_mill=True)
        return {int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None}

    @staticmethod
    def _reload_rows_by_id(db, media_type: str, simkl_ids: set[int]) -> dict[int, dict]:
        if not simkl_ids:
            return {}
        ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
        if media_type == "movie":
            query = f"""
                SELECT m.simkl_id,
                       m.info,
                       m.art,
                       m.cast,
                       m.args,
                       b.resume_time,
                       b.percent_played,
                       m.watched AS play_count,
                       m.user_rating
                FROM movies AS m
                         LEFT JOIN bookmarks AS b
                                   ON m.simkl_id = b.simkl_id
                WHERE m.simkl_id IN ({ids_sql})
            """
        else:
            query = f"""
                SELECT s.simkl_id,
                       s.info,
                       s.cast,
                       s.art,
                       s.args,
                       s.watched_episodes,
                       s.unwatched_episodes,
                       s.episode_count,
                       s.season_count,
                       s.air_date,
                       s.user_rating
                FROM shows AS s
                WHERE s.simkl_id IN ({ids_sql})
            """
        return {
            int(row["simkl_id"]): row
            for row in (db.fetchall(query) or [])
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }

    def _persist_list_rows(self, rows, media_type: str, *, db, skip_ids: set[int] | None = None) -> None:
        table = "movies" if media_type == "movie" else "shows"
        skip_ids = skip_ids or set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            simkl_id = row.get("simkl_id")
            if simkl_id is None or int(simkl_id) in skip_ids:
                continue
            info = row.get("info")
            art = row.get("art")
            cast = row.get("cast")
            if not isinstance(info, dict) or not isinstance(art, dict):
                continue
            from resources.lib.meta.profiles import current_profile
            from resources.lib.meta.storage import slim_db_row

            slim = slim_db_row({"info": info, "art": art, "cast": cast}, profile=current_profile())
            db.execute_sql(
                f"UPDATE {table} SET info=?, art=?, cast=?, meta_hash=?, last_updated=? WHERE simkl_id=?",
                (
                    slim["info"],
                    slim["art"],
                    slim.get("cast") if isinstance(slim.get("cast"), list) else [],
                    self.meta_hash,
                    str(datetime.datetime.now().isoformat()),
                    int(simkl_id),
                ),
            )
            from resources.lib.database.sync_meta_cache import SyncMetaCache

            SyncMetaCache().set_row(
                media_type,
                {
                    "simkl_id": int(simkl_id),
                    "info": slim["info"],
                    "art": slim["art"],
                    "cast": slim.get("cast") if isinstance(slim.get("cast"), list) else [],
                },
            )
            from resources.lib.meta.display_store import get_display_meta_store

            get_display_meta_store().merge_art_cast_row(media_type, slim)

    # endregion

    # endregion

    @staticmethod
    def _force_update(db_object):
        return db_object.get("needs_update", False) in ["true", "True", True, 1]

    def _sync_update_noop(self, db_object, media_type: str) -> bool:
        """True when denormalized sync row is warm and no provider refresh is required."""
        if self._force_update(db_object):
            return False
        info = db_object.get("info")
        art = db_object.get("art")
        if not isinstance(info, dict) or not isinstance(art, dict):
            result = False
        elif not info.get("title") and not info.get("originaltitle"):
            result = False
        elif not art.get("poster") and not art.get("thumb") and not info.get("poster"):
            result = False
        elif not db_object.get("cast"):
            result = False
        else:
            from resources.lib.meta.provider_settings import ART_FANART, ART_SIMKL, ART_TMDB, ART_TVDB

            preferred = self._preferred_art_source_for_db_object(db_object)
            if preferred == ART_SIMKL:
                result = True
            elif media_type == "movie":
                if preferred == ART_TMDB and not self._movie_needs_tmdb_bundle(db_object):
                    result = True
                elif preferred == ART_TVDB and not self._movie_needs_tvdb_bundle(db_object):
                    result = True
                elif preferred == ART_FANART and not self._fanart_needs_update(db_object):
                    result = True
                else:
                    result = False
            elif media_type == "tvshow":
                if preferred == ART_TMDB and not self._tvshow_needs_tmdb_bundle(db_object):
                    result = True
                elif preferred == ART_TVDB and not self._tvshow_needs_tvdb_bundle(db_object):
                    result = True
                elif preferred == ART_FANART and not self._fanart_needs_update(db_object):
                    result = True
                else:
                    result = False
            else:
                result = False
        return result

    def _tmdb_art_meta_up_to_par(self, media_type, item):
        return self.art_meta_up_to_par(media_type, MetadataHandler.tmdb_object(item))

    def _tvdb_art_meta_up_to_par(self, media_type, item):
        return self.art_meta_up_to_par(media_type, MetadataHandler.tvdb_object(item))

    def _fanart_art_meta_up_to_par(self, media_type, item):
        return self.art_meta_up_to_par(media_type, MetadataHandler.fanart_object(item))

    @staticmethod
    def art_meta_up_to_par(media_type, item):
        try:
            if not item:
                return False
            if (
                media_type in ["tvshow", "season", "movie"]
                and not tools.safe_dict_get(item, "art", "poster")
                and not tools.safe_dict_get(item, "art", "keyart")
            ):
                return False
            if media_type in ["tvshow", "movie"] and not tools.safe_dict_get(item, "art", "fanart"):
                return False
            return bool(media_type != "episode" or tools.safe_dict_get(item, "art", "thumb"))

        except KeyError:
            return False

    @staticmethod
    def _info_meta_up_to_par(item):
        return tools.safe_dict_get(item, "info", "title") and tools.safe_dict_get(item, "info", "plot")

    @staticmethod
    def full_meta_up_to_par(media_type, item):
        if not item:
            return False
        if item.get("cast"):
            return True
        if item.get("art"):
            return MetadataHandler.art_meta_up_to_par(media_type, item)
        return False

    @staticmethod
    def simkl_meta_savable(media_type, item):
        """Simkl is authoritative for sync rows — persist minimal episode/season identity without TMDB plot/art."""
        if not item:
            return False
        info = tools.safe_dict_get(item, "info") or {}
        if not info.get("simkl_id"):
            return False
        singular = media_type.rstrip("s") if media_type.endswith("s") else media_type
        if singular == "episode":
            season = info.get("season")
            episode = info.get("episode", info.get("number"))
            return season is not None and episode is not None
        if singular == "season":
            return info.get("season") is not None or info.get("mediatype") == "season"
        if singular in ("movie", "show"):
            return bool(info.get("title"))
        return MetadataHandler.full_meta_up_to_par(singular, item)

    @staticmethod
    def info(data):
        return data.get("info", {})

    @staticmethod
    def art(data):
        return data.get("art", {})

    @staticmethod
    def cast(data):
        return data.get("cast", {})

    @staticmethod
    def simkl_object(data):
        return data.get("simkl_object", {})

    @staticmethod
    def tmdb_object(data):
        return data.get("tmdb_object", {})

    @staticmethod
    def tvdb_object(data):
        return data.get("tvdb_object", {})

    @staticmethod
    def imdb_object(data):
        return data.get("imdb_object", {})

    @staticmethod
    def fanart_object(data):
        return data.get("fanart_object", {})

    @staticmethod
    def simkl_info(data):
        return MetadataHandler.info(MetadataHandler.simkl_object(data))

    @staticmethod
    def tmdb_info(data):
        return MetadataHandler.info(MetadataHandler.tmdb_object(data))

    @staticmethod
    def tvdb_info(data):
        return MetadataHandler.info(MetadataHandler.tvdb_object(data))

    @staticmethod
    def fanart_info(data):
        return MetadataHandler.info(MetadataHandler.fanart_object(data))

    @staticmethod
    def get_simkl_info(data, key, default=None):
        try:
            return MetadataHandler.simkl_info(data).get(key, default)
        except Exception:
            return default

    @staticmethod
    def get_tmdb_info(data, key, default=None):
        try:
            return MetadataHandler.tmdb_info(data).get(key, default)
        except Exception:
            return default

    @staticmethod
    def get_tvdb_info(data, key, default=None):
        try:
            return MetadataHandler.tvdb_info(data).get(key, default)
        except Exception:
            return default

    @staticmethod
    def get_fanart_info(data, key, default=None):
        try:
            return MetadataHandler.fanart_info(data).get(key, default)
        except Exception:
            return default

    @staticmethod
    def pop_simkl_info(data, key, default=None):
        try:
            return MetadataHandler.simkl_info(data).pop(key, default)
        except Exception:
            return default

    @staticmethod
    def pop_tmdb_info(data, key, default=None):
        try:
            return MetadataHandler.tmdb_info(data).pop(key, default)
        except Exception:
            return default

    @staticmethod
    def pop_tvdb_info(data, key, default=None):
        try:
            return MetadataHandler.tvdb_info(data).pop(key, default)
        except Exception:
            return default

    @staticmethod
    def pop_fanart_info(data, key, default=None):
        try:
            return MetadataHandler.fanart_info(data).pop(key, default)
        except Exception:
            return default

    @staticmethod
    def sort_list_items(db_list, media_list):
        db_list_dict = {}
        for row in db_list:
            if not isinstance(row, dict):
                continue
            keys: set[int] = set()
            top_id = row.get("simkl_id")
            if top_id is not None:
                keys.add(int(top_id))
            info_id = tools.safe_dict_get(row, "info", "simkl_id")
            if info_id is not None:
                keys.add(int(info_id))
            for key in keys:
                db_list_dict[key] = row
        return [db_list_dict.get(o.get("simkl_id")) if isinstance(o, dict) else None for o in media_list]
