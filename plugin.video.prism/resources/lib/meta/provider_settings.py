"""Per-provider enable toggles for runtime metadata APIs."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resources.lib.discover.definitions import DiscoverList

_SETTING_BY_PROVIDER = {
    "tmdb": "metadata.tmdb.enabled",
    "tvdb": "metadata.tvdb.enabled",
    "fanart": "metadata.fanart.enabled",
    "mdblist": "metadata.mdblist.enabled",
    "imdb": "metadata.imdb.enabled",
    "anilist": "metadata.anilist.enabled",
}

_API_NAME_BY_PROVIDER = {
    "tmdb": "TMDB",
    "tvdb": "TVDB",
    "fanart": "Fanart.tv",
    "mdblist": "MDBList",
    "imdb": "IMDb",
    "anilist": "AniList",
}

# Extended artwork types supplied primarily by Fanart.tv (not TMDB/TVDB).
_FANART_EXCLUSIVE_SETTINGS = frozenset(
    {
        "movies.discart",
        "movies.landscape",
        "movies.characterart_limit",
        "tvshows.landscape",
        "tvshows.characterart_limit",
        "season.landscape",
        "anime.discart",
        "anime.landscape",
        "anime.characterart_limit",
        "anime.season.landscape",
    }
)

# Discover DB queries gated by provider toggles (none currently).

def provider_enabled(provider: str) -> bool:
    setting_id = _SETTING_BY_PROVIDER.get(provider)
    if not setting_id:
        return True
    from resources.lib.modules.globals import g

    return g.get_bool_setting(setting_id, True)


def gapfill_providers_enabled() -> bool:
    return any(provider_enabled(name) for name in ("tmdb", "tvdb"))


def external_ids_from_row(row: dict) -> dict[str, object | None]:
    """Resolve external ids from row columns, flat info fields, and nested info.ids."""
    if not isinstance(row, dict):
        return {
            "tmdb_id": None,
            "tvdb_id": None,
            "imdb_id": None,
            "mal_id": None,
            "anilist_id": None,
        }

    info = dict(row.get("info") or {})
    from resources.lib.simkl.ids import sync_flat_ids_from_ids, sync_ids_from_flat

    sync_ids_from_flat(info)
    sync_flat_ids_from_ids(info)

    ids_block = info.get("ids") if isinstance(info.get("ids"), dict) else {}

    return {
        "tmdb_id": info.get("tmdb_id") or row.get("tmdb_id"),
        "tvdb_id": info.get("tvdb_id") or row.get("tvdb_id"),
        "imdb_id": info.get("imdb_id") or row.get("imdb_id"),
        "mal_id": info.get("mal_id") or row.get("mal_id") or ids_block.get("mal"),
        "anilist_id": info.get("anilist_id") or row.get("anilist_id") or ids_block.get("anilist"),
    }


def gapfill_provider_available_for_row(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

    art_profile = artwork_profile_for_row(row)
    media_type = provider_media_type(art_profile)
    if preferred_art_source_for_row(row, media_type) == ART_SIMKL:
        return False
    ids = external_ids_from_row(row)
    if provider_enabled("tmdb") and ids.get("tmdb_id"):
        return True
    if provider_enabled("tvdb") and ids.get("tvdb_id"):
        return True
    if provider_enabled("imdb") and ids.get("imdb_id"):
        return True
    from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES

    if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES) and provider_enabled("anilist"):
        if ids.get("mal_id") or ids.get("anilist_id"):
            return True
    if fanart_art_usable() and (ids.get("tmdb_id") or ids.get("tvdb_id") or ids.get("imdb_id")):
        return True
    return False


def preferred_art_source_for_row(row: dict, media_type: str) -> int:
    """Resolve effective artwork preference (Fanart/TMDb/TVDB/Simkl-only) for a list row."""
    from resources.lib.meta.artwork import (
        PROFILE_ANIME_MOVIE,
        PROFILE_ANIME_SERIES,
        artwork_profile_for_row,
    )
    from resources.lib.modules.globals import g

    art_profile = artwork_profile_for_row(row, default_media_type=media_type)
    if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES):
        raw = g.get_int_setting("anime.preferedsource", 1)
    elif media_type == "movie":
        raw = g.get_int_setting("movies.preferedsource", 1)
    else:
        raw = g.get_int_setting("tvshows.preferedsource", 1)
    return effective_preferred_art_source(raw)


def cast_gapfill_available(row: dict, media_type: str) -> bool:
    if not isinstance(row, dict):
        return False
    preferred = preferred_art_source_for_row(row, media_type)
    if preferred == ART_SIMKL:
        return False
    ids = external_ids_from_row(row)
    if provider_enabled("imdb") and ids.get("imdb_id"):
        return True
    from resources.lib.meta.artwork import PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES, artwork_profile_for_row

    art_profile = artwork_profile_for_row(row, default_media_type=media_type)
    if art_profile in (PROFILE_ANIME_MOVIE, PROFILE_ANIME_SERIES) and provider_enabled("anilist"):
        if ids.get("mal_id") or ids.get("anilist_id"):
            return True
    if media_type == "movie":
        if provider_enabled("tmdb") and ids.get("tmdb_id"):
            return True
        if provider_enabled("tvdb") and ids.get("tvdb_id"):
            return True
        return False
    if preferred == ART_TVDB and provider_enabled("tvdb") and ids.get("tvdb_id"):
        return True
    if preferred == ART_TMDB and provider_enabled("tmdb") and ids.get("tmdb_id"):
        return True
    if preferred == ART_FANART:
        if provider_enabled("tmdb") and ids.get("tmdb_id"):
            return True
        if provider_enabled("tvdb") and ids.get("tvdb_id"):
            return True
    return False


def art_gapfill_available(row: dict, media_type: str = "tvshow") -> bool:
    if not isinstance(row, dict):
        return False
    preferred = preferred_art_source_for_row(row, media_type)
    if preferred == ART_SIMKL:
        return False
    ids = external_ids_from_row(row)
    if preferred == ART_FANART:
        return fanart_art_usable() and bool(
            ids.get("tmdb_id") or ids.get("tvdb_id") or ids.get("imdb_id")
        )
    if preferred == ART_TMDB:
        return provider_enabled("tmdb") and bool(ids.get("tmdb_id"))
    if preferred == ART_TVDB:
        return provider_enabled("tvdb") and bool(ids.get("tvdb_id"))
    return False


def advanced_artwork_enabled(media_type: str) -> bool:
    from resources.lib.modules.globals import g

    if media_type in ("anime", "anime_movie", "anime_series"):
        setting_id = "anime.artwork.advanced"
    elif media_type == "movie":
        setting_id = "movie.artwork.advanced"
    else:
        setting_id = "tv.artwork.advanced"
    return g.get_bool_setting(setting_id, False)


def art_option_enabled(setting_id: str, media_type: str, default: bool = True) -> bool:
    """Advanced artwork toggles default on when the advanced panel is hidden."""
    if setting_id in _FANART_EXCLUSIVE_SETTINGS and not fanart_art_usable():
        return False
    scope = media_type
    if media_type in ("anime_movie", "anime_series"):
        scope = "anime"
    if not advanced_artwork_enabled(scope):
        return default
    from resources.lib.modules.globals import g

    return g.get_bool_setting(setting_id, default)


_ART_LIMIT_DEFAULTS = {
    "poster_limit": 1,
    "fanart_limit": 1,
    "keyart_limit": 0,
    "characterart_limit": 0,
}


def art_limit(setting_id: str, scope: str) -> int:
    """Read a poster/fanart/keyart/characterart limit; use defaults when advanced panel is hidden."""
    from resources.lib.modules.globals import g

    fallback_key = setting_id.rsplit(".", 1)[-1]
    default = _ART_LIMIT_DEFAULTS.get(fallback_key, 1)
    if setting_id in _FANART_EXCLUSIVE_SETTINGS and not fanart_art_usable():
        return 0
    if not advanced_artwork_enabled(scope):
        return default
    return g.get_int_setting(setting_id, default)


def mdblist_calendar_enabled() -> bool:
    """True when the weekly airing calendar may call the MDBList API."""
    return provider_enabled("mdblist")


def mdblist_runtime_enabled() -> bool:
    """Alias for mdblist_calendar_enabled (calendar-only MDBList usage)."""
    return mdblist_calendar_enabled()


def discover_db_query_allowed(query_name: str | None) -> bool:
    return True


def discover_list_visible(item: "DiscoverList") -> bool:
    return True


def filter_discover_lists(lists: tuple["DiscoverList", ...]) -> tuple["DiscoverList", ...]:
    return tuple(item for item in lists if discover_list_visible(item))


def notify_tmdb_required() -> None:
    import xbmcgui

    from resources.lib.modules.globals import g

    xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30959))


def notify_imdb_required() -> None:
    import xbmcgui

    from resources.lib.modules.globals import g

    xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30974))


def imdb_metadata_enabled() -> bool:
    return provider_enabled("imdb")


def anilist_metadata_enabled() -> bool:
    return provider_enabled("anilist")


# Match MetadataHandler ART_* spinner values (Fanart.TV / TMDb / TVDB).
ART_FANART = 0
ART_TMDB = 1
ART_TVDB = 2
ART_SIMKL = -1

_ART_PROVIDER = {
    ART_FANART: "fanart",
    ART_TMDB: "tmdb",
    ART_TVDB: "tvdb",
}


def fanart_enabled() -> bool:
    from resources.lib.modules.globals import g

    return g.get_bool_setting("metadata.fanart.enabled", True)


def fanart_art_usable() -> bool:
    """True when Fanart.tv is enabled and an API key is available (user setting or context.prism info.db)."""
    if not fanart_enabled():
        return False
    from resources.lib.database.keys import get_api_key

    return bool(get_api_key("Fanart-TV"))


def art_provider_usable(provider: str) -> bool:
    if provider == "fanart":
        return fanart_art_usable()
    return provider_enabled(provider)


def effective_preferred_art_source(raw: int) -> int:
    """Use the stored preference when its provider is enabled; else fall back, then Simkl-only."""
    if raw == ART_FANART:
        order = (ART_FANART, ART_TMDB, ART_TVDB)
    elif raw == ART_TMDB:
        order = (ART_TMDB, ART_TVDB, ART_FANART)
    else:
        order = (ART_TVDB, ART_TMDB, ART_FANART)

    for choice in order:
        provider = _ART_PROVIDER.get(choice)
        if provider and art_provider_usable(provider):
            return choice
    return ART_SIMKL
