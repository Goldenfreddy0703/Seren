import sys

from resources.lib.modules import router
from resources.lib.modules.globals import g
from resources.lib.modules.prismMonitor import ONWAKE_NETWORK_UP_DELAY
from resources.lib.modules.timeLogger import TimeLogger


def _sleeping_retry_handler():
    sleeping = False

    if g.PLATFORM == "android":
        attempts = 0
        while (
            attempts <= ONWAKE_NETWORK_UP_DELAY
            and (sleeping := g.get_bool_runtime_setting("system.sleeping", False))
            and not g.wait_for_abort(1)
        ):
            attempts += 1
        if sleeping and not g.abort_requested():
            g.log(
                f"Ignoring {g.REQUEST_PARAMS.get('action', '')} plugin action as system is supposed to be \"sleeping\"",
                "info",
            )

    return not sleeping


def prism_endpoint():
    foreground_menu = False
    try:
        g.init_globals(sys.argv)
        foreground_menu = g.PLUGIN_HANDLE > 0 and not g.FROM_WIDGET

        if _sleeping_retry_handler() and not g.abort_requested():
            from resources.lib.modules.widget_loader import WidgetLoadGate

            action = (g.REQUEST_PARAMS or {}).get("action")
            drilldown_nav = (
                g.PLUGIN_HANDLE > 0
                and not g.FROM_WIDGET
                and action in ("seasonEpisodes", "flatEpisodes")
            )
            if drilldown_nav:
                from resources.lib.modules.drilldown_prefetch import set_drilldown_navigation_active

                set_drilldown_navigation_active(True)
            if foreground_menu:
                from resources.lib.modules.page_prefetch import set_foreground_menu_active

                set_foreground_menu_active(True)
            with WidgetLoadGate(), TimeLogger(f"{g.REQUEST_PARAMS.get('action', '')}"):
                router.dispatch(g.REQUEST_PARAMS)

    except Exception:
        g.cancel_directory()
        raise

    finally:
        if foreground_menu:
            try:
                from resources.lib.modules.page_prefetch import set_foreground_menu_active

                set_foreground_menu_active(False)
            except Exception:
                pass
        try:
            action = (g.REQUEST_PARAMS or {}).get("action")
            if (
                g.PLUGIN_HANDLE > 0
                and not g.FROM_WIDGET
                and action in ("seasonEpisodes", "flatEpisodes")
            ):
                from resources.lib.modules.drilldown_prefetch import set_drilldown_navigation_active

                set_drilldown_navigation_active(False)
        except Exception:
            pass
        g.deinit()


if __name__ == "__main__":  # pragma: no cover
    prism_endpoint()
