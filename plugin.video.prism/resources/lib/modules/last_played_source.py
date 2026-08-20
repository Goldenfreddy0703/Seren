"""Digit-position last-played source matching for Smart Play auto-resolve."""
from __future__ import annotations

import re
from typing import Any

from resources.lib.modules.globals import g

_SETTING_ID = "general.last_played_source"
_DIRECT_TYPES = frozenset({"direct"})
_TORRENT_STYLE_TYPES = frozenset(
    {"torrent", "torrent (uncached)", "cloud", "hoster", "local", "adaptive"}
)


def is_smart_play_reorder_context(
    item_information: dict[str, Any],
    *,
    action: str | None = None,
    smart_url_arg: bool = False,
) -> bool:
    """True when Smart Play digit reorder should run (episodes only)."""
    info = item_information.get("info") if isinstance(item_information, dict) else None
    if not isinstance(info, dict) or info.get("mediatype") != g.MEDIA_EPISODE:
        return False
    if action == "preScrape":
        return True
    if smart_url_arg:
        return True
    if not g.get_bool_setting("smartplay.playlistcreate"):
        return False
    try:
        if g.PLAYLIST.size() > 1:
            return True
    except Exception:
        pass
    return False


def clear_last_played_source() -> None:
    g.set_setting(_SETTING_ID, "")


def _info_tokens(info) -> list[str]:
    if isinstance(info, set):
        return sorted(str(token) for token in info)
    if isinstance(info, (list, tuple)):
        return [str(token) for token in info]
    if info is None:
        return []
    return [str(info)]


def _direct_source_key(source: dict[str, Any]) -> str:
    return source.get("provider", "") + " " + " ".join(_info_tokens(source.get("info")))


def save_last_played_source(source: dict[str, Any]) -> None:
    if not isinstance(source, dict):
        return
    source_type = source.get("type")
    if source_type in _DIRECT_TYPES:
        g.set_setting(_SETTING_ID, _direct_source_key(source))
    elif source_type in _TORRENT_STYLE_TYPES:
        g.set_setting(_SETTING_ID, str(source.get("release_title") or ""))


def reorder_sources(
    sources: list[dict[str, Any]],
    episode,
    *,
    source_select: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Move the best last-played match to index 0. Returns (sources, matched)."""
    if len(sources) <= 1 or source_select:
        return sources, False

    lp = g.get_setting(_SETTING_ID) or ""
    lp = re.sub(r"\[[0-9A-Fa-f]{8,}\]", "", lp).strip()
    if not lp:
        return sources, False

    ep = str(episode)
    length = len(ep)
    digit_positions = [
        index for index in range(len(lp) - length + 1) if lp[index : index + length].isdigit()
    ]

    for idx, source in enumerate(sources):
        source_type = source.get("type")

        if source_type in _DIRECT_TYPES:
            if _direct_source_key(source) == lp:
                sources[0], sources[idx] = sources[idx], sources[0]
                return sources, True

        elif source_type in _TORRENT_STYLE_TYPES:
            rel = str(source.get("release_title") or "")
            rel = re.sub(r"\[[0-9A-Fa-f]{8,}\]", "", rel).strip()

            if rel == lp:
                sources[0], sources[idx] = sources[idx], sources[0]
                return sources, True

            for pos in digit_positions:
                if rel.startswith(lp[:pos]) and rel.endswith(lp[pos + length :]):
                    sources[0], sources[idx] = sources[idx], sources[0]
                    return sources, True

    return sources, False
