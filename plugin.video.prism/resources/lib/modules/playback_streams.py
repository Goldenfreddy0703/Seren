"""Per-stream audio/subtitle selection during playback (anime keyword/dub subs)."""
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
            "properties": ["subtitles", "audiostreams", "currentsubtitle", "currentaudiostream"],
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


def _preferred_audio_code(catalog: str) -> str | None:
    if locale_playback.uses_kodi_defaults(catalog):
        value = locale_playback.get_kodi_setting_value(locale_playback.KODI_AUDIO_SETTING)
    else:
        value = locale_playback.get_catalog_audio(catalog)
    return _language_code(value)


def _current_audio_language(player: xbmc.Player, audio_streams: list[dict]) -> str | None:
    try:
        index = player.getAudioStream()
    except RuntimeError:
        index = None
    if index is None:
        props = _get_player_properties(_active_video_player_id())
        current = props.get("currentaudiostream")
        if isinstance(current, dict):
            return (current.get("language") or "").lower() or None
        return None
    if 0 <= index < len(audio_streams):
        return (audio_streams[index].get("language") or "").lower() or None
    return None


def _find_keyword_stream(subtitle_streams: list[dict], keywords: tuple[str, ...]) -> int | None:
    for index, stream in enumerate(subtitle_streams):
        if not isinstance(stream, dict):
            continue
        if _subtitle_name_matches_keywords(stream.get("name") or "", keywords):
            return index
    return None


def _find_language_stream(subtitle_streams: list[dict], language_code: str | None) -> int | None:
    if not language_code:
        return None
    if language_code == "forced":
        for index, stream in enumerate(subtitle_streams):
            if isinstance(stream, dict) and stream.get("isforced"):
                return index
        return None
    for index, stream in enumerate(subtitle_streams):
        if isinstance(stream, dict) and (stream.get("language") or "").lower() == language_code:
            return index
    return None


def _pick_subtitle_stream(
    subtitle_streams: list[dict],
    *,
    catalog: str,
    keyword_mode: str,
    active_audio_language: str | None = None,
) -> int | None:
    if not subtitle_streams:
        return None

    keywords = _keywords_for_mode(keyword_mode)
    if keywords:
        match = _find_keyword_stream(subtitle_streams, keywords)
        if match is not None:
            return match
        # Dub watch: don't fall back to dialogue subs when only keyword tracks are wanted.
        if keyword_mode_active(keyword_mode) and (active_audio_language or "").lower() == "eng":
            if _preferred_subtitle_code(catalog) is None:
                return None

    preferred = _preferred_subtitle_code(catalog)
    match = _find_language_stream(subtitle_streams, preferred)
    if match is not None:
        return match

    for index, stream in enumerate(subtitle_streams):
        if isinstance(stream, dict) and stream.get("isdefault"):
            return index
    return 0


def _should_show_subtitles(
    *,
    catalog: str,
    subtitle_streams: list[dict],
    active_audio_language: str | None,
    keyword_mode: str,
) -> bool:
    keywords = _keywords_for_mode(keyword_mode)
    if keywords and _find_keyword_stream(subtitle_streams, keywords) is not None:
        return True

    preferred_sub = _preferred_subtitle_code(catalog)
    if preferred_sub is None:
        return False

    preferred_audio = _preferred_audio_code(catalog)
    active_audio = active_audio_language or preferred_audio

    if active_audio == "jpn":
        return True

    return preferred_sub is not None


def apply_anime_streams(player: xbmc.Player, *, catalog: str = "anime") -> None:
    """Select keyword-matched / dub subtitle streams after playback starts."""
    from resources.lib.modules.catalog_profiles import normalize_catalog

    if normalize_catalog(catalog) != "anime":
        return

    keyword_mode = get_subtitle_keyword_mode()
    if not keyword_mode_active(keyword_mode):
        return

    subtitle_streams: list[dict] = []
    audio_streams: list[dict] = []

    for _attempt in range(8):
        if not player.isPlayingVideo():
            return
        props = _get_player_properties(_active_video_player_id())
        subtitle_streams = [
            row for row in (props.get("subtitles") or []) if isinstance(row, dict)
        ]
        audio_streams = [
            row for row in (props.get("audiostreams") or []) if isinstance(row, dict)
        ]
        if subtitle_streams or audio_streams:
            break
        time.sleep(0.25)

    if not subtitle_streams and not audio_streams:
        g.log("Playback streams: no embedded streams reported by Kodi", "debug")
        return

    active_audio = _current_audio_language(player, audio_streams)
    show_subs = _should_show_subtitles(
        catalog=catalog,
        subtitle_streams=subtitle_streams,
        active_audio_language=active_audio,
        keyword_mode=keyword_mode,
    )

    try:
        player.showSubtitles(show_subs)
    except RuntimeError:
        pass

    if not show_subs:
        return

    stream_index = _pick_subtitle_stream(
        subtitle_streams,
        catalog=catalog,
        keyword_mode=keyword_mode,
        active_audio_language=active_audio,
    )
    if stream_index is None:
        return

    try:
        player.setSubtitleStream(stream_index)
        g.log(
            f"Playback streams: selected subtitle stream {stream_index} "
            f"(keyword_mode={keyword_mode})",
            "debug",
        )
    except RuntimeError:
        g.log("Playback streams: failed to set subtitle stream", "debug")


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
