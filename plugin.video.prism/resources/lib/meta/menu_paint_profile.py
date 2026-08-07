"""Named paint profiles for library, discover, search, and drilldown menus."""
from __future__ import annotations

from enum import Enum

from resources.lib.modules.globals import g
from resources.lib.simkl.menu_helpers import list_filter_kwargs


class MenuPaintProfile(str, Enum):
    """Canonical list-paint presets — use instead of ad-hoc boolean flags."""

    BROWSE = "browse"
    SEARCH = "search"
    LIBRARY = "library"
    LIBRARY_EPISODES = "library_episodes"
    DRILLDOWN = "drilldown"
    RELATED = "related"
    AIRING = "airing"


# Router action → profile (in-scope content menus only).
_ACTION_PROFILES: dict[str, MenuPaintProfile] = {
    # Discover
    "simklDiscoverList": MenuPaintProfile.BROWSE,
    "genericEndpoint": MenuPaintProfile.BROWSE,
    "moviesUpdated": MenuPaintProfile.BROWSE,
    "showsUpdated": MenuPaintProfile.BROWSE,
    "showsNew": MenuPaintProfile.BROWSE,
    "moviesRecommended": MenuPaintProfile.BROWSE,
    "showsRecommended": MenuPaintProfile.BROWSE,
    "moviePopularRecent": MenuPaintProfile.BROWSE,
    "movieTrendingRecent": MenuPaintProfile.BROWSE,
    "showsPopularRecent": MenuPaintProfile.BROWSE,
    "showsTrendingRecent": MenuPaintProfile.BROWSE,
    "animePopularRecent": MenuPaintProfile.BROWSE,
    "animeTrendingRecent": MenuPaintProfile.BROWSE,
    # Genre / year / actor browse
    "movieGenresGet": MenuPaintProfile.BROWSE,
    "showGenresGet": MenuPaintProfile.BROWSE,
    "animeGenresGet": MenuPaintProfile.BROWSE,
    "movieGenresMultiGet": MenuPaintProfile.BROWSE,
    "showGenresMultiGet": MenuPaintProfile.BROWSE,
    "animeGenresMultiGet": MenuPaintProfile.BROWSE,
    "movieYearsMovies": MenuPaintProfile.BROWSE,
    "showYears": MenuPaintProfile.BROWSE,
    "actorCredits": MenuPaintProfile.BROWSE,
    # Search
    "moviesSearchResults": MenuPaintProfile.SEARCH,
    "showsSearchResults": MenuPaintProfile.SEARCH,
    "animeSearchResults": MenuPaintProfile.SEARCH,
    # Library / watchlist
    "simklLibraryList": MenuPaintProfile.LIBRARY,
    "libraryOnDeck": MenuPaintProfile.LIBRARY_EPISODES,
    "libraryNextUp": MenuPaintProfile.LIBRARY_EPISODES,
    "libraryRecentlyWatched": MenuPaintProfile.LIBRARY,
    "libraryWatchedEpisodes": MenuPaintProfile.LIBRARY_EPISODES,
    "libraryWatchedMovies": MenuPaintProfile.LIBRARY,
    # Episode drilldown
    "showSeasons": MenuPaintProfile.DRILLDOWN,
    "seasonEpisodes": MenuPaintProfile.DRILLDOWN,
    "flatEpisodes": MenuPaintProfile.DRILLDOWN,
    # Airing
    "showsMyRecentEpisodes": MenuPaintProfile.AIRING,
    "myUpcomingEpisodes": MenuPaintProfile.AIRING,
    # Related
    "moviesRelated": MenuPaintProfile.RELATED,
    "showsRelated": MenuPaintProfile.RELATED,
    "simklRecommendations": MenuPaintProfile.RELATED,
    "simklRelations": MenuPaintProfile.RELATED,
}


def profile_for_action(action: str | None) -> MenuPaintProfile | None:
    if not action:
        return None
    return _ACTION_PROFILES.get(str(action))


def _browse_hide_filters(**overrides) -> dict:
    """User general-tab hide filters; discover respects widget overrides elsewhere."""
    return list_filter_kwargs(**overrides)


def _library_hide_filters() -> dict:
    """Library status lists show all membership rows regardless of hide toggles."""
    return {"hide_unaired": False, "hide_watched": False}


def page_paint_flags_for_profile(paint_profile: str) -> dict[str, bool]:
    """Hide filters and payload richness for session page paint keys (must match menu render)."""
    profile = str(paint_profile or MenuPaintProfile.BROWSE.value).lower()
    if profile in (
        MenuPaintProfile.LIBRARY.value,
        MenuPaintProfile.LIBRARY_EPISODES.value,
        MenuPaintProfile.AIRING.value,
        MenuPaintProfile.SEARCH.value,
    ):
        return {
            "hide_unaired": False,
            "hide_watched": False,
            "prefer_rich_payload": True,
        }
    hide_unaired = g.get_bool_setting("general.hideUnAired")
    hide_watched = g.get_bool_setting("general.hideWatched")
    if g.FROM_WIDGET:
        hide_watched = True
    return {
        "hide_unaired": hide_unaired,
        "hide_watched": hide_watched,
        "prefer_rich_payload": False,
    }


def profile_list_kwargs(
    profile: MenuPaintProfile,
    *,
    mixed_list: bool = False,
    overlay_parent_shows: bool = False,
    prepend_date: bool = False,
    no_paging: bool = False,
    enrichment_reason: str | None = None,
    **overrides,
) -> dict:
    """Build list-builder kwargs for a paint profile."""
    base = {
        "skip_mill": True,
        "skip_update": True,
        "paint_only": profile != MenuPaintProfile.DRILLDOWN,
        "menu_cache": True,
        "library_paint": False,
        "simkl_detail_paint": False,
        "mixed_list": mixed_list,
        "overlay_parent_shows": overlay_parent_shows,
        "prepend_date": prepend_date,
        "no_paging": no_paging,
    }

    if profile == MenuPaintProfile.BROWSE:
        base.update(_browse_hide_filters())
        base.setdefault("enrichment_reason", "browse")
    elif profile == MenuPaintProfile.SEARCH:
        base.update(_library_hide_filters())
        base["simkl_detail_paint"] = True
        base.setdefault("enrichment_reason", "search")
    elif profile == MenuPaintProfile.LIBRARY:
        base.update(_library_hide_filters())
        base["library_paint"] = True
        base.setdefault("enrichment_reason", "library")
    elif profile == MenuPaintProfile.LIBRARY_EPISODES:
        base.update(_library_hide_filters())
        base["library_paint"] = True
        base["mixed_list"] = True
        base["overlay_parent_shows"] = overlay_parent_shows or True
        base.setdefault("enrichment_reason", "library")
    elif profile == MenuPaintProfile.DRILLDOWN:
        base.update(_browse_hide_filters())
        base["paint_only"] = False
        base.setdefault("enrichment_reason", "drilldown")
    elif profile == MenuPaintProfile.RELATED:
        base.update(_browse_hide_filters())
        base.setdefault("no_paging", True)
        base.setdefault("enrichment_reason", "related")
    elif profile == MenuPaintProfile.AIRING:
        base.update(_library_hide_filters())
        base["mixed_list"] = True
        base.setdefault("enrichment_reason", "airing")

    if enrichment_reason:
        base["enrichment_reason"] = enrichment_reason

    base.update(overrides)
    return base


def profile_kwargs_for_action(action: str | None, **overrides) -> dict:
    """Resolve list kwargs from the current router action."""
    profile = profile_for_action(action)
    if profile is None:
        from resources.lib.meta.list_paint import browse_list_kwargs

        return browse_list_kwargs(**overrides)
    return profile_list_kwargs(profile, **overrides)


def current_action_profile_kwargs(**overrides) -> dict:
    """List kwargs for g.REQUEST_PARAMS action when it maps to a known profile."""
    action = (g.REQUEST_PARAMS or {}).get("action")
    return profile_kwargs_for_action(action, **overrides)


_last_prepare_stats: dict | None = None
_last_paint_cache_context: dict | None = None


def record_prepare_stats(stats: dict | None) -> None:
    """Store latest Seren-style prepare metrics for log_paint_timing."""
    global _last_prepare_stats
    _last_prepare_stats = dict(stats) if stats else None


def record_paint_cache_context(*, layer: str, prepare_skipped: bool, stamp_trusted: bool = False) -> None:
    """Store latest cache layer / fast-path context for menu timing logs."""
    global _last_paint_cache_context
    _last_paint_cache_context = {
        "cache_layer": layer,
        "prepare_skipped": bool(prepare_skipped),
        "stamp_trusted": bool(stamp_trusted),
    }


def log_paint_timing(action: str | None, elapsed_ms: float, *, item_count: int = 0, prepare_stats: dict | None = None) -> None:
    """Append menu paint timing to addon_data for baseline comparison (Phase 0)."""
    import json
    import os
    import time

    from resources.lib.common import tools

    if not action:
        return
    stats = prepare_stats if prepare_stats is not None else _last_prepare_stats
    cache_ctx = _last_paint_cache_context or {}
    try:
        path = tools.translate_path(
            os.path.join(g.ADDON_USERDATA_PATH, "menu_paint_timings.jsonl")
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        sample = {
            "ts": int(time.time()),
            "action": str(action),
            "ms": round(float(elapsed_ms), 1),
            "items": int(item_count),
        }
        if stats:
            for key in (
                "complete",
                "incomplete",
                "cast_batch",
                "art_fetch",
                "art_deduped",
                "cast_art_parallel_ms",
                "prepare_ms",
                "prepare_skipped",
            ):
                if key in stats:
                    sample[key] = stats[key]
        if cache_ctx.get("cache_layer"):
            sample["cache_layer"] = cache_ctx["cache_layer"]
        if cache_ctx.get("stamp_trusted"):
            sample["stamp_trusted"] = 1
        if "prepare_skipped" in cache_ctx and "prepare_skipped" not in sample:
            sample["prepare_skipped"] = cache_ctx["prepare_skipped"]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    except Exception:
        g.log_stacktrace()


# In-scope router actions from the cleanup plan test matrix.
_PLAN_CONTENT_ACTIONS = frozenset(_ACTION_PROFILES.keys())


def verify_menu_paint_coverage(extra_actions: set[str] | None = None) -> list[str]:
    """Return content menu actions that lack a MenuPaintProfile mapping."""
    actions = set(extra_actions or ())
    actions.update(_PLAN_CONTENT_ACTIONS)
    missing = sorted(action for action in actions if profile_for_action(action) is None)
    return missing
