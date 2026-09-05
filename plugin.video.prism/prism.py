from __future__ import annotations

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


def _release_plugin_threads() -> None:
    """Ensure no pool workers keep this Kodi plugin invoker alive after the menu returns."""
    try:
        from resources.lib.common.thread_pool import plugin_action_allows_threads, release_global_executors

        action = (g.REQUEST_PARAMS or {}).get("action")
        release_global_executors(
            wait=plugin_action_allows_threads(action),
            cancel_futures=plugin_action_allows_threads(action),
        )
    except Exception:
        pass
    if g.abort_requested():
        try:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()
            for pool in (db.task_queue, db.mill_task_queue):
                pool.force_stop()
        except Exception:
            pass


def prism_endpoint():
    from resources.lib.common.thread_pool import (
        enter_prism_plugin_mode,
        exit_prism_plugin_mode,
        plugin_action_allows_threads,
    )

    enter_prism_plugin_mode()
    foreground_menu = False
    plugin_slot_token = None
    try:
        try:
            from resources.lib.common.plugin_invoker_gate import (
                acquire_prism_plugin_slot,
                release_prism_plugin_slot,
                should_hold_plugin_slot_from_argv,
            )

            if should_hold_plugin_slot_from_argv():
                plugin_slot_token = acquire_prism_plugin_slot()

            g.init_globals(sys.argv)
            action = (g.REQUEST_PARAMS or {}).get("action")
            if plugin_action_allows_threads(action):
                exit_prism_plugin_mode()
            else:
                g.set_runtime_setting("prism.inline_pool", True)

            if _sleeping_retry_handler() and not g.abort_requested():
                from resources.lib.modules.widget_loader import WidgetLoadGate

                action = (g.REQUEST_PARAMS or {}).get("action")
                if (
                    g.PLUGIN_HANDLE > 0
                    and not g.FROM_WIDGET
                    and action in ("seasonEpisodes", "flatEpisodes")
                ):
                    from resources.lib.modules.drilldown_prefetch import set_drilldown_navigation_active

                    set_drilldown_navigation_active(True)
                if g.PLUGIN_HANDLE > 0 and not g.FROM_WIDGET:
                    from resources.lib.common.plugin_invoker_gate import ensure_startup_ready_for_foreground
                    from resources.lib.modules.page_prefetch import set_foreground_menu_active

                    if ensure_startup_ready_for_foreground():
                        if not plugin_slot_token:
                            plugin_slot_token = acquire_prism_plugin_slot()
                        set_foreground_menu_active(True)
                        foreground_menu = True
                        with WidgetLoadGate(), TimeLogger(f"{g.REQUEST_PARAMS.get('action', '')}"):
                            router.dispatch(g.REQUEST_PARAMS)
                else:
                    with WidgetLoadGate(), TimeLogger(f"{g.REQUEST_PARAMS.get('action', '')}"):
                        router.dispatch(g.REQUEST_PARAMS)

        except Exception:
            g.cancel_directory()
            raise

        finally:
            try:
                if foreground_menu:
                    from resources.lib.modules.page_prefetch import set_foreground_menu_active

                    set_foreground_menu_active(False)
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
            if plugin_slot_token:
                from resources.lib.common.plugin_invoker_gate import release_prism_plugin_slot

                release_prism_plugin_slot(plugin_slot_token)
            _release_plugin_threads()
            g.clear_runtime_setting("prism.inline_pool")
            g.deinit()
    finally:
        exit_prism_plugin_mode()


if __name__ == "__main__":  # pragma: no cover
    prism_endpoint()
