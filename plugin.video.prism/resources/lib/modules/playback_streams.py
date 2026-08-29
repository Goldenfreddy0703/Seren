"""Per-stream subtitle selection during playback (anime track name filter)."""
from __future__ import annotations

import time
from typing import Any

import xbmc
import xbmcgui

from resources.lib.modules import locale_playback
from resources.lib.modules.globals import g

KEYWORD_OFF = "off"
KEYWORD_DIALOG = "dialog"
KEYWORD_SIGNS_SONGS = "signs_songs"
KEYWORD_CUSTOM = "custom"

KEYWORD_MODES = (KEYWORD_OFF, KEYWORD_DIALOG, KEYWORD_SIGNS_SONGS, KEYWORD_CUSTOM)

KEYWORD_SETTING = "playback.anime.subtitlekeyword"
CUSTOM_KEYWORD_SETTING = "playback.anime.subtitlekeyword.custom"
LEGACY_SIGNS_SONGS_SETTING = "playback.anime.signsandsongs"

DIALOG_KEYWORDS = (
    "dialogue",
    "dialog",
    "full subtitle",
    "full subs",
)

SIGNS_SONGS_KEYWORDS = (
    "signs & songs",
    "signs and songs",
    "sign & song",
    "sign and song",
    "signs/songs",
    "signs",
    "songs",
    "s&s",
    "snh",
)

KEYWORD_LABEL_IDS = {
    KEYWORD_OFF: 31061,
    KEYWORD_DIALOG: 31062,
    KEYWORD_SIGNS_SONGS: 31057,
    KEYWORD_CUSTOM: 31064,
}


def _ensure_keyword_migration() -> None:
    if g.get_setting(KEYWORD_SETTING) is not None:
        return
    if g.get_bool_setting(LEGACY_SIGNS_SONGS_SETTING, False):
        g.set_setting(KEYWORD_SETTING, KEYWORD_SIGNS_SONGS)
    else:
        g.set_setting(KEYWORD_SETTING, KEYWORD_OFF)


def get_subtitle_keyword_mode() -> str:
    _ensure_keyword_migration()
    mode = (g.get_setting(KEYWORD_SETTING, KEYWORD_OFF) or KEYWORD_OFF).strip().lower()
    if mode in KEYWORD_MODES:
        return mode
    if mode in ("signs", "signsandsongs", "signs_and_songs"):
        return KEYWORD_SIGNS_SONGS
    return KEYWORD_OFF


def set_subtitle_keyword_mode(mode: str) -> None:
    normalized = (mode or KEYWORD_OFF).strip().lower()
    if normalized not in KEYWORD_MODES:
        normalized = KEYWORD_OFF
    g.set_setting(KEYWORD_SETTING, normalized)


def cycle_subtitle_keyword_mode() -> str:
    current = get_subtitle_keyword_mode()
    index = KEYWORD_MODES.index(current)
    next_mode = KEYWORD_MODES[(index + 1) % len(KEYWORD_MODES)]
    set_subtitle_keyword_mode(next_mode)
    return next_mode


def get_custom_subtitle_keyword() -> str:
    return (g.get_setting(CUSTOM_KEYWORD_SETTING, "") or "").strip()


def set_custom_subtitle_keyword(value: str) -> None:
    g.set_setting(CUSTOM_KEYWORD_SETTING, (value or "").strip())


def subtitle_keyword_label(mode: str | None = None) -> str:
    mode = mode or get_subtitle_keyword_mode()
    if mode == KEYWORD_CUSTOM:
        custom = get_custom_subtitle_keyword()
        if custom:
            return custom
    message_id = KEYWORD_LABEL_IDS.get(mode, KEYWORD_LABEL_IDS[KEYWORD_OFF])
    return g.get_language_string(message_id)


def keyword_mode_active(mode: str | None = None) -> bool:
    return (mode or get_subtitle_keyword_mode()) != KEYWORD_OFF


def keyword_filter_options() -> tuple[tuple[str, str], ...]:
    """Return (label, mode) pairs for the locale picker (excludes Off)."""
    return tuple(
        (subtitle_keyword_label(mode), mode)
        for mode in KEYWORD_MODES
        if mode != KEYWORD_OFF
    )


def reset_anime_stream_settings() -> None:
    set_subtitle_keyword_mode(KEYWORD_OFF)
    set_custom_subtitle_keyword("")


def _keywords_for_mode(mode: str) -> tuple[str, ...]:
    if mode == KEYWORD_DIALOG:
        return DIALOG_KEYWORDS
    if mode == KEYWORD_SIGNS_SONGS:
        return SIGNS_SONGS_KEYWORDS
    if mode == KEYWORD_CUSTOM:
        custom = get_custom_subtitle_keyword()
        return (custom.lower(),) if custom else ()
    return ()


def _subtitle_name_matches_keywords(name: str, keywords: tuple[str, ...]) -> bool:
    if not keywords:
        return False
    lower = (name or "").lower()
    return any(keyword in lower for keyword in keywords if keyword)


def _active_video_player_id() -> int:
    players = g.json_rpc("Player.GetActivePlayers")
    if isinstance(players, list):
        for entry in players:
            if isinstance(entry, dict) and entry.get("type") == "video":
                return int(entry["playerid"])
    return 1


def _get_player_properties(player_id: int) -> dict[str, Any]:
    result = g.json_rpc(
        "Player.GetProperties",
        {
            "playerid": player_id,
            "properties": ["subtitles", "currentsubtitle"],
        },
    )
    return result if isinstance(result, dict) else {}


def _language_code(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in ("none", "forced_only", "original", "default", "mediadefault"):
        return None
    if len(normalized) == 3 and normalized.isalpha():
        return normalized
    code = xbmc.convertLanguage(value, xbmc.ISO_639_2)
    if code:
        return code.lower()
    code = xbmc.convertLanguage(value, xbmc.ISO_639_1)
    if code:
        return code.lower()
    return None


def _preferred_subtitle_code(catalog: str) -> str | None:
    if locale_playback.uses_kodi_defaults(catalog):
        value = locale_playback.get_kodi_setting_value(locale_playback.KODI_SUBTITLE_SETTING)
    else:
        value = locale_playback.get_catalog_subtitle(catalog)
    if value in ("original", "default"):
        return None
    if value == "forced_only":
        return "forced"
    return _language_code(value)


def _kodi_stream_index(stream: dict, *, fallback: int | None = None) -> int:
    """Return the index Kodi expects for setSubtitleStream."""
    index = stream.get("index")
    if index is not None:
        return int(index)
    if fallback is not None:
        return int(fallback)
    return 0


def _find_keyword_stream(subtitle_streams: list[dict], keywords: tuple[str, ...]) -> int | None:
    for position, stream in enumerate(subtitle_streams):
        if not isinstance(stream, dict):
            continue
        if _subtitle_name_matches_keywords(stream.get("name") or "", keywords):
            return _kodi_stream_index(stream, fallback=position)
    return None


def _find_language_stream(subtitle_streams: list[dict], language_code: str | None) -> int | None:
    if not language_code:
        return None
    if language_code == "forced":
        for position, stream in enumerate(subtitle_streams):
            if isinstance(stream, dict) and stream.get("isforced"):
                return _kodi_stream_index(stream, fallback=position)
        return None
    for position, stream in enumerate(subtitle_streams):
        if isinstance(stream, dict) and (stream.get("language") or "").lower() == language_code:
            return _kodi_stream_index(stream, fallback=position)
    return None


def _catalog_subtitle_setting(catalog: str) -> str:
    if locale_playback.uses_kodi_defaults(catalog):
        value = locale_playback.get_kodi_setting_value(locale_playback.KODI_SUBTITLE_SETTING)
    else:
        value = locale_playback.get_catalog_subtitle(catalog)
    return (value or "").strip().lower()


def _pick_preferred_subtitle_stream(
    subtitle_streams: list[dict],
    *,
    catalog: str,
) -> int | None:
    """Pick a stream from Preferred Subtitle (language, forced, default track)."""
    if _catalog_subtitle_setting(catalog) == "none":
        return None

    preferred = _preferred_subtitle_code(catalog)
    match = _find_language_stream(subtitle_streams, preferred)
    if match is not None:
        return match

    for position, stream in enumerate(subtitle_streams):
        if isinstance(stream, dict) and stream.get("isdefault"):
            return _kodi_stream_index(stream, fallback=position)
    if subtitle_streams and isinstance(subtitle_streams[0], dict):
        return _kodi_stream_index(subtitle_streams[0], fallback=0)
    return None


def _pick_subtitle_stream(
    subtitle_streams: list[dict],
    *,
    catalog: str,
    keyword_mode: str,
) -> int | None:
    if not subtitle_streams:
        return None

    keywords = _keywords_for_mode(keyword_mode)
    if keywords:
        match = _find_keyword_stream(subtitle_streams, keywords)
        if match is not None:
            return match

    return _pick_preferred_subtitle_stream(subtitle_streams, catalog=catalog)


def apply_anime_streams(player: xbmc.Player, *, catalog: str = "anime") -> None:
    """Select keyword-matched subtitle streams after playback starts (audio via locale JSON-RPC)."""
    from resources.lib.modules.catalog_profiles import normalize_catalog

    if normalize_catalog(catalog) != "anime":
        return

    keyword_mode = get_subtitle_keyword_mode()
    if not keyword_mode_active(keyword_mode):
        return

    subtitle_streams: list[dict] = []

    for _attempt in range(12):
        if not player.isPlayingVideo():
            return
        props = _get_player_properties(_active_video_player_id())
        subtitle_streams = [
            row for row in (props.get("subtitles") or []) if isinstance(row, dict)
        ]
        if subtitle_streams:
            break
        time.sleep(0.5)

    if not subtitle_streams:
        return

    stream_index = _pick_subtitle_stream(
        subtitle_streams,
        catalog=catalog,
        keyword_mode=keyword_mode,
    )

    try:
        player.showSubtitles(stream_index is not None)
    except RuntimeError:
        pass

    if stream_index is None:
        return

    try:
        player.setSubtitleStream(stream_index)
    except RuntimeError:
        pass


def prompt_custom_subtitle_keyword() -> bool:
    """Open keyboard to edit the custom subtitle keyword."""
    current = get_custom_subtitle_keyword()
    value = xbmcgui.Dialog().input(
        g.get_language_string(31065),
        defaultt=current,
        type=xbmcgui.INPUT_ALPHANUM,
    )
    if value is None:
        return False
    set_custom_subtitle_keyword(value.strip())
    if value.strip():
        set_subtitle_keyword_mode(KEYWORD_CUSTOM)
    return True
