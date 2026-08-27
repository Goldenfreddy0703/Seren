"""
Per-catalog filter and sort profiles (movie / tv / anime).
"""

from resources.lib.modules.globals import g

CATALOGS = ("movie", "tv", "anime")
LAST_CATALOG_KEY = "general.filter.lastcatalog"
MIGRATION_FLAG_KEY = "general.catalogprofiles.migrated"

DEFAULT_FILTERS = "3D,AV1"

# Anime filter presets (enabled tags are excluded from scrape results).
ANIME_BASE_FILTERS = frozenset({"3D", "AV1"})
ANIME_FILTER_PRESETS = {
    "sub": ANIME_BASE_FILTERS | {"DUB"},
    "dub": ANIME_BASE_FILTERS | {"SUB", "MULTI-SUB"},
}

DEFAULT_SORTMETHOD = {1: 2, 2: 1, 3: 4, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0}
DEFAULT_SOURCETYPESORT = {1: 1, 2: 0, 3: 0, 4: 0, 5: 0}
DEFAULT_HDRSORT = {1: 2, 2: 0}
DEFAULT_DEBRIDSORT = {1: 1, 2: 0, 3: 0, 4: 0}
DEFAULT_AUDIOSORT = {1: 2, 2: 1, 3: 3, 4: 4}
DEFAULT_SUBTITLESORT = {1: 1}

# Anime sort presets — indices match sort_select.SORT_METHODS / SORT_OPTIONS.
# sortmethod: 2=Source Type, 1=Resolution, 9=Audio, 10=Subtitles, 4=Size, 0=None
# sourcetypesort: 5=Direct, 1=Cloud, 0=Other
# audiosort: 2=Dual-Audio, 1=Multi-Audio, 3=Sub, 4=Dub, 0=None
# subtitlesort: 1=Multi-Sub, 0=None
_ANIME_SORT_PRESET_SHARED = {
    "sortmethod": {1: 2, 2: 1, 3: 9, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0},
    "sourcetypesort": {1: 5, 2: 1, 3: 0, 4: 0, 5: 0},
    "hdrsort": DEFAULT_HDRSORT,
    "debridsort": DEFAULT_DEBRIDSORT,
}

ANIME_SORT_PRESETS = {
    "sub": {
        **_ANIME_SORT_PRESET_SHARED,
        "sortmethod": {1: 2, 2: 1, 3: 9, 4: 10, 5: 4, 6: 0, 7: 0, 8: 0},
        "audiosort": {1: 2, 2: 1, 3: 3, 4: 0},
        "subtitlesort": {1: 1},
    },
    "dub": {
        **_ANIME_SORT_PRESET_SHARED,
        "sortmethod": {1: 2, 2: 1, 3: 9, 4: 4, 5: 0, 6: 0, 7: 0, 8: 0},
        "audiosort": {1: 2, 2: 1, 3: 4, 4: 0},
        "subtitlesort": {1: 0},
    },
}

_SORT_PRESET_CATEGORIES = (
    ("sourcetypesort", DEFAULT_SOURCETYPESORT),
    ("hdrsort", DEFAULT_HDRSORT),
    ("debridsort", DEFAULT_DEBRIDSORT),
    ("audiosort", DEFAULT_AUDIOSORT),
    ("subtitlesort", DEFAULT_SUBTITLESORT),
)

# Anime playback locale presets (Kodi English names).
ANIME_PLAYBACK_PRESETS = {
    "sub": {
        "use_kodi": False,
        "audio": "Japanese",
        "subtitle": "English",
        "subtitlekeyword": "dialog",
    },
    "dub": {
        "use_kodi": False,
        "audio": "English",
        "subtitle": "none",
        "subtitlekeyword": "signs_songs",
    },
}


def normalize_catalog(catalog):
    if catalog in CATALOGS:
        return catalog
    return "movie"


def filters_key(catalog):
    return f"general.filters.{normalize_catalog(catalog)}"


def sortmethod_key(catalog, level, reverse=False):
    suffix = ".reverse" if reverse else ""
    return f"general.sortmethod.{normalize_catalog(catalog)}.{level}{suffix}"


def sub_sort_key(catalog, category, level):
    return f"general.{category}.{normalize_catalog(catalog)}.{level}"


def resolve_catalog_from_item_information(item_information):
    """Resolve movie / tv / anime for filter-sort profiles.

    Priority: action_args (widget-safe) → sync row → info → anime ids → show DB → mediatype.
    """
    if not item_information:
        return "movie"

    action_args = item_information.get("action_args") or {}
    info = item_information.get("info") or {}

    for candidate in (
        action_args.get("catalog"),
        item_information.get("catalog"),
        info.get("catalog"),
    ):
        if candidate in CATALOGS:
            return candidate

    if info.get("mal_id") or info.get("mal_show_id"):
        return "anime"

    mediatype = info.get("mediatype") or action_args.get("mediatype")
    if mediatype == "movie":
        return "movie"

    if mediatype in ("tvshow", "season", "episode"):
        show_id = info.get("simkl_show_id")
        if show_id is None and mediatype == "tvshow":
            show_id = action_args.get("simkl_id") or info.get("simkl_id")
        if show_id is None and mediatype == "season":
            show_id = action_args.get("simkl_id")
        if show_id is None and mediatype == "episode":
            from resources.lib.simkl.ids import show_id_for_episode_action

            show_id = show_id_for_episode_action(action_args)
        if show_id is not None:
            from resources.lib.database.session import get_sync_database

            catalog = get_sync_database().show_catalog(int(show_id))
            if catalog in CATALOGS:
                return catalog

    return "tv"


def get_last_catalog():
    return normalize_catalog(g.get_setting(LAST_CATALOG_KEY, "movie"))


def set_last_catalog(catalog):
    g.set_setting(LAST_CATALOG_KEY, normalize_catalog(catalog))


def ensure_migrated():
    if g.get_bool_setting(MIGRATION_FLAG_KEY):
        return

    legacy_filters = g.get_setting("general.filters")
    if legacy_filters is None:
        legacy_filters = DEFAULT_FILTERS

    for catalog in CATALOGS:
        key = filters_key(catalog)
        if g.get_setting(key) is None:
            g.set_setting(key, legacy_filters)

    for catalog in CATALOGS:
        for level in range(1, 9):
            for reverse in (False, True):
                legacy = f"general.sortmethod.{level}" + (".reverse" if reverse else "")
                new_key = sortmethod_key(catalog, level, reverse=reverse)
                if g.get_setting(new_key) is None:
                    value = g.get_setting(legacy)
                    if value is not None:
                        g.set_setting(new_key, value)
                    elif not reverse:
                        g.set_setting(new_key, DEFAULT_SORTMETHOD[level])
                    else:
                        g.set_setting(new_key, False)

        for level, default in DEFAULT_SOURCETYPESORT.items():
            new_key = sub_sort_key(catalog, "sourcetypesort", level)
            if g.get_setting(new_key) is None:
                legacy = g.get_setting(f"general.sourcetypesort.{level}")
                g.set_setting(new_key, legacy if legacy is not None else default)

        for level, default in DEFAULT_HDRSORT.items():
            new_key = sub_sort_key(catalog, "hdrsort", level)
            if g.get_setting(new_key) is None:
                legacy = g.get_setting(f"general.hdrsort.{level}")
                g.set_setting(new_key, legacy if legacy is not None else default)

        for level, default in DEFAULT_DEBRIDSORT.items():
            new_key = sub_sort_key(catalog, "debridsort", level)
            if g.get_setting(new_key) is None:
                legacy = g.get_setting(f"general.debridsort.{level}")
                g.set_setting(new_key, legacy if legacy is not None else default)

        for level, default in DEFAULT_AUDIOSORT.items():
            new_key = sub_sort_key(catalog, "audiosort", level)
            if g.get_setting(new_key) is None:
                legacy = g.get_setting(f"general.audiosort.{level}")
                g.set_setting(new_key, legacy if legacy is not None else default)

        for level, default in DEFAULT_SUBTITLESORT.items():
            new_key = sub_sort_key(catalog, "subtitlesort", level)
            if g.get_setting(new_key) is None:
                legacy = g.get_setting(f"general.subtitlesort.{level}")
                g.set_setting(new_key, legacy if legacy is not None else default)

    g.set_setting(MIGRATION_FLAG_KEY, True)


def get_filters(catalog):
    ensure_migrated()
    filter_string = g.get_setting(filters_key(catalog), DEFAULT_FILTERS)
    return set() if not filter_string else set(filter_string.split(","))


def save_filters(catalog, filter_set):
    g.set_setting(filters_key(catalog), ",".join(sorted(filter_set)) if filter_set else "")


def load_sort_options(catalog):
    ensure_migrated()
    catalog = normalize_catalog(catalog)
    options = {
        sortmethod_key(catalog, idx): g.get_int_setting(sortmethod_key(catalog, idx), DEFAULT_SORTMETHOD[idx])
        for idx in range(1, 9)
    }
    options.update(
        {
            sortmethod_key(catalog, idx, reverse=True): g.get_bool_setting(
                sortmethod_key(catalog, idx, reverse=True), False
            )
            for idx in range(1, 9)
        }
    )
    for category, defaults in (
        ("sourcetypesort", DEFAULT_SOURCETYPESORT),
        ("hdrsort", DEFAULT_HDRSORT),
        ("debridsort", DEFAULT_DEBRIDSORT),
        ("audiosort", DEFAULT_AUDIOSORT),
        ("subtitlesort", DEFAULT_SUBTITLESORT),
    ):
        for level, default in defaults.items():
            options[sub_sort_key(catalog, category, level)] = g.get_int_setting(
                sub_sort_key(catalog, category, level), default
            )
    return options


def reset_filters(catalog):
    save_filters(catalog, set(DEFAULT_FILTERS.split(",")))


def apply_anime_filter_preset(preset: str) -> bool:
    """Apply sub/dub filter preset for the anime catalog profile."""
    key = str(preset or "").strip().lower()
    filter_set = ANIME_FILTER_PRESETS.get(key)
    if filter_set is None:
        return False
    save_filters("anime", set(filter_set))
    return True


def apply_anime_sort_preset(preset: str) -> bool:
    """Apply sub/dub sort preset for the anime catalog profile."""
    key = str(preset or "").strip().lower()
    profile = ANIME_SORT_PRESETS.get(key)
    if profile is None:
        return False

    ensure_migrated()
    catalog = "anime"
    sort_levels = profile.get("sortmethod") or {}
    for level in range(1, 9):
        g.set_setting(sortmethod_key(catalog, level), sort_levels.get(level, 0))
        g.set_setting(sortmethod_key(catalog, level, reverse=True), False)

    for category, defaults in _SORT_PRESET_CATEGORIES:
        preset_levels = profile.get(category) or {}
        for level, default in defaults.items():
            g.set_setting(
                sub_sort_key(catalog, category, level),
                preset_levels.get(level, default),
            )
    return True


def apply_anime_playback_preset(preset: str) -> bool:
    """Apply sub/dub playback locale preset for the anime catalog profile."""
    key = str(preset or "").strip().lower()
    profile = ANIME_PLAYBACK_PRESETS.get(key)
    if profile is None:
        return False

    from resources.lib.modules.locale_playback import (
        set_catalog_audio,
        set_catalog_subtitle,
        set_use_kodi_defaults,
    )
    from resources.lib.modules.playback_streams import (
        set_custom_subtitle_keyword,
        set_subtitle_keyword_mode,
    )

    set_use_kodi_defaults("anime", bool(profile.get("use_kodi", False)))
    set_catalog_audio("anime", profile["audio"])
    set_catalog_subtitle("anime", profile["subtitle"])
    set_subtitle_keyword_mode(str(profile.get("subtitlekeyword", "off")))
    return True


def apply_anime_preset(preset: str) -> bool:
    """Apply full anime sub/dub preset: filters, sort, and playback locale."""
    key = str(preset or "").strip().lower()
    if key not in ANIME_FILTER_PRESETS:
        return False
    apply_anime_filter_preset(key)
    apply_anime_sort_preset(key)
    apply_anime_playback_preset(key)
    return True


def reset_sort_profile(catalog):
    catalog = normalize_catalog(catalog)
    for level, value in DEFAULT_SORTMETHOD.items():
        g.set_setting(sortmethod_key(catalog, level), value)
        g.set_setting(sortmethod_key(catalog, level, reverse=True), False)
    for level, value in DEFAULT_SOURCETYPESORT.items():
        g.set_setting(sub_sort_key(catalog, "sourcetypesort", level), value)
    for level, value in DEFAULT_HDRSORT.items():
        g.set_setting(sub_sort_key(catalog, "hdrsort", level), value)
    for level, value in DEFAULT_DEBRIDSORT.items():
        g.set_setting(sub_sort_key(catalog, "debridsort", level), value)
    for level, value in DEFAULT_AUDIOSORT.items():
        g.set_setting(sub_sort_key(catalog, "audiosort", level), value)
    for level, value in DEFAULT_SUBTITLESORT.items():
        g.set_setting(sub_sort_key(catalog, "subtitlesort", level), value)
