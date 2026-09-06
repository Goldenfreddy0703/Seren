"""Airable (aired-only) watch counters — match Simkl UI and Seren/Trakt semantics."""

from __future__ import annotations


def not_aired_count(info: dict | None) -> int:
    if not isinstance(info, dict):
        return 0
    try:
        return max(0, int(info.get("not_aired_episodes_count") or 0))
    except (TypeError, ValueError):
        return 0


def airable_unwatched(total: int, watched: int, not_aired: int = 0) -> int:
    return max(0, int(total) - int(watched) - int(not_aired or 0))


def airable_episode_count(total: int, not_aired: int = 0) -> int:
    return max(0, int(total) - int(not_aired or 0))


def is_caught_up(
    watched: int,
    total: int,
    *,
    not_aired: int = 0,
    unwatched: int | None = None,
    aired_episode_count: int | None = None,
) -> bool:
    if aired_episode_count is not None:
        aired = _int_or(aired_episode_count)
    else:
        aired = airable_episode_count(total, not_aired)
    if aired <= 0:
        return False
    if int(watched) >= aired:
        return True
    if unwatched is not None:
        try:
            return int(unwatched) <= 0
        except (TypeError, ValueError):
            pass
    return False


def _int_or(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def total_for_watch_math(item: dict, info: dict) -> int:
    for key in ("total_episodes_count", "total_episodes", "episode_count"):
        val = info.get(key)
        if val is not None:
            count = _int_or(val)
            if count > 0:
                return count
    return _int_or(item.get("episode_count"))


def apply_show_watch_fields(item: dict, info: dict, *, aired_episode_count: int | None = None) -> None:
    """Normalize tvshow counters for Kodi skins (aired-only totals)."""
    total = total_for_watch_math(item, info)
    watched = _int_or(item.get("watched_episodes"), _int_or(info.get("watched_episodes_count")))
    not_aired = not_aired_count(info)

    if aired_episode_count is not None:
        aired_episode_count = _int_or(aired_episode_count)
    else:
        aired_hint = _int_or(info.get("aired_episodes"))
        if aired_hint > 0:
            aired_episode_count = aired_hint
        elif not_aired > 0:
            aired_episode_count = airable_episode_count(total, not_aired)
        else:
            aired_episode_count = total

    if not_aired <= 0 and aired_episode_count > 0 and total > aired_episode_count:
        not_aired = total - aired_episode_count

    aired = aired_episode_count if aired_episode_count > 0 else airable_episode_count(total, not_aired)
    if aired <= 0:
        return

    unwatched = max(0, aired - watched)

    item["episode_count"] = aired
    item["watched_episodes"] = watched
    item["unwatched_episodes"] = unwatched
    info["episode_count"] = aired
    info["aired_episodes"] = aired
    info.setdefault("watched_episodes_count", watched)
    info["unwatched_episodes"] = unwatched


def apply_season_watch_fields(item: dict, info: dict) -> None:
    """Normalize season counters (episode_count should already be aired-only when milled)."""
    ep_count = _int_or(
        item.get("episode_count"),
        _int_or(info.get("episode_count"), _int_or(info.get("aired_episodes"))),
    )
    watched = _int_or(item.get("watched_episodes"), _int_or(info.get("watched_episodes_count")))
    if ep_count <= 0:
        return

    unwatched = item.get("unwatched_episodes")
    if unwatched is None:
        unwatched = info.get("unwatched_episodes")
    unwatched = max(0, ep_count - watched)

    item["episode_count"] = ep_count
    item["watched_episodes"] = watched
    item["unwatched_episodes"] = max(0, unwatched)
    info.setdefault("episode_count", ep_count)
    info.setdefault("aired_episodes", ep_count)
    info.setdefault("watched_episodes_count", watched)
    info.setdefault("unwatched_episodes", item["unwatched_episodes"])
