"""Background pre-mill of show episode rows after season list opens."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from resources.lib.modules.globals import g

_DONE_KEY = "drilldown_prefetch.done_keys"
_IN_FLIGHT_KEY = "drilldown_prefetch.in_flight"
_NAV_ACTIVE_KEY = "drilldown.user_navigating"
_MAX_DONE_KEYS = 128

_DRILLDOWN_MENU_ACTIONS = frozenset({"showSeasons", "seasonEpisodes", "flatEpisodes"})


def set_drilldown_navigation_active(active: bool) -> None:
    """Shared across plugin processes — blocks background episode pre-mill during drilldown."""
    g.set_runtime_setting(_NAV_ACTIVE_KEY, bool(active))


def drilldown_navigation_active() -> bool:
    return g.get_bool_runtime_setting(_NAV_ACTIVE_KEY)


def _prefetch_key(work: dict[str, Any]) -> str:
    stable = {key: work[key] for key in sorted(work)}
    return hashlib.md5(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def _done_keys() -> set[str]:
    raw = g.get_runtime_setting(_DONE_KEY)
    if isinstance(raw, list):
        return {str(key) for key in raw}
    return set()


def _mark_done(key: str) -> None:
    done = list(_done_keys())
    if key in done:
        return
    done.append(key)
    if len(done) > _MAX_DONE_KEYS:
        done = done[-_MAX_DONE_KEYS:]
    g.set_runtime_setting(_DONE_KEY, done)


def _in_flight_keys() -> set[str]:
    raw = g.get_runtime_setting(_IN_FLIGHT_KEY)
    if isinstance(raw, dict):
        return {str(key) for key, active in raw.items() if active}
    return set()


def _set_in_flight(key: str, active: bool) -> None:
    raw = g.get_runtime_setting(_IN_FLIGHT_KEY)
    flight = dict(raw) if isinstance(raw, dict) else {}
    if active:
        flight[key] = True
    else:
        flight.pop(key, None)
    g.set_runtime_setting(_IN_FLIGHT_KEY, flight)


def _user_browsing() -> bool:
    return g.get_bool_runtime_setting("browse.menu_active")


def _should_abort_prefetch() -> bool:
    return drilldown_navigation_active() or _user_browsing()


def schedule_show_episode_premill(show_id: int, catalog: str | None = None) -> None:
    """Pre-mill the next un-milled season while the user views the season list."""
    if not show_id:
        return
    if g.get_bool_setting("general.flatten.episodes"):
        return

    work = {
        "simkl_show_id": int(show_id),
        "catalog": catalog or "tv",
    }
    key = _prefetch_key(work)
    if key in _done_keys() or key in _in_flight_keys():
        return

    def _launch() -> None:
        try:
            time.sleep(1.5)
            if drilldown_navigation_active():
                return
            for _ in range(24):
                if not _user_browsing():
                    break
                time.sleep(0.25)
            if _should_abort_prefetch():
                return
            if key in _done_keys() or key in _in_flight_keys():
                return
            import xbmc

            launch = {
                "action": "drilldownPrefetch",
                "simkl_show_id": int(show_id),
                "catalog": work["catalog"],
                "prefetch_key": key,
            }
            url = g.create_url(g.BASE_URL, launch)
            xbmc.executebuiltin(f'RunPlugin("{url}")')
        except Exception:
            g.log_stacktrace()

    threading.Thread(target=_launch, daemon=True, name="prism-drilldown-prefetch").start()


def run_drilldown_prefetch_invoke(params: dict[str, Any] | None) -> None:
    """Router entry for background drilldownPrefetch action (-1 handle)."""
    if not isinstance(params, dict):
        return
    show_id = params.get("simkl_show_id")
    if show_id is None:
        return
    key = str(params.get("prefetch_key") or _prefetch_key(params))
    if key in _done_keys():
        return
    if _should_abort_prefetch():
        g.log(f"drilldown_prefetch skipped show={show_id} foreground_busy=1", "debug")
        return
    if key in _in_flight_keys():
        return

    _set_in_flight(key, True)
    try:
        import time as _time

        from resources.lib.database.session import get_sync_database

        start = _time.time()
        milled = premill_show_episodes(int(show_id), catalog=params.get("catalog"), max_seasons=1)
        remaining = get_sync_database()._next_season_needing_episode_mill(int(show_id))
        g.log(
            f"drilldown_prefetch complete show={show_id} seasons_milled={milled} "
            f"remaining={remaining is not None} ms={(_time.time() - start) * 1000:.0f}",
            "debug",
        )
        if remaining is None:
            _mark_done(key)
        elif milled > 0 and not _should_abort_prefetch():
            schedule_show_episode_premill(int(show_id), catalog=params.get("catalog"))
    except Exception:
        g.log_stacktrace()
    finally:
        _set_in_flight(key, False)


def premill_show_episodes(
    simkl_show_id: int,
    *,
    catalog: str | None = None,
    max_seasons: int = 0,
) -> int:
    """Mill episodes for seasons that are not yet present locally."""
    from resources.lib.database.session import get_sync_database

    hide_specials = g.get_bool_setting("general.hideSpecials")
    return get_sync_database().premill_missing_episode_seasons(
        int(simkl_show_id),
        catalog=catalog,
        stop_if_busy=True,
        hide_specials=hide_specials,
        max_seasons=max_seasons,
    )
