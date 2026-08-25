"""Serialize optional skin widget inter-load delay (reads are not globally locked)."""
from __future__ import annotations

import time

from resources.lib.modules.globals import g

_LAST_LOAD_MS_KEY = "widget.last_load_ms"
_SESSION_PREFIX = "widget.session."


def widget_hide_empty_setting() -> bool:
    """Read hide-empty toggle (Seren PR uses ADDON.getSettingBool)."""
    try:
        return bool(g.ADDON.getSettingBool("general.widget.hide_empty"))
    except Exception:
        return g.get_bool_setting("general.widget.hide_empty", False)


def widget_hide_empty_enabled() -> bool:
    """When true, empty widget folders return zero items (no placeholder row)."""
    return bool(g.FROM_WIDGET and widget_hide_empty_setting())


def finish_empty_widget_directory() -> None:
    """Close a widget folder with no list items (see general.widget.hide_empty)."""
    import xbmcplugin

    xbmcplugin.setContent(g.PLUGIN_HANDLE, g.CONTENT_MENU)
    xbmcplugin.endOfDirectory(g.PLUGIN_HANDLE, succeeded=True, cacheToDisc=False)


def widget_stagger_enabled() -> bool:
    if g.IS_SERVICE or not g.FROM_WIDGET:
        return False
    if not g.get_bool_setting("general.widget.stagger", False):
        return False
    return True


def widget_request_key() -> str:
    action = g.REQUEST_PARAMS.get("action", "")
    parts = [action]
    for key in ("catalog", "status", "mediatype", "endpoint", "list_id"):
        value = g.REQUEST_PARAMS.get(key)
        if value:
            parts.append(f"{key}={value}")
    action_args = g.REQUEST_PARAMS.get("action_args")
    if isinstance(action_args, dict):
        for key in ("simkl_id", "catalog", "status"):
            value = action_args.get(key)
            if value is not None:
                parts.append(f"args.{key}={value}")
    return "|".join(parts) or action or "widget"


def mark_widget_session_loaded(request_key: str | None = None) -> bool:
    """
    Return True when this widget URL has not been loaded yet this Kodi session.

    First load can prefer warm caches; later refreshes may bypass them.
    """
    if not g.FROM_WIDGET:
        return False
    key = f"{_SESSION_PREFIX}{request_key or widget_request_key()}"
    if g.get_bool_runtime_setting(key):
        return False
    g.set_runtime_setting(key, True)
    return True


def clear_widget_session_flags() -> None:
    """Clear per-session widget markers (e.g. before a forced home refresh)."""
    prefix = f"{g.ADDON_ID}.{g.VERSION}.runtime.{_SESSION_PREFIX}"
    try:
        for key in g.HOME_WINDOW.getProperties().keys():
            if key.startswith(prefix):
                g.HOME_WINDOW.clearProperty(key)
    except Exception:
        pass


class WidgetLoadGate:
    """Context manager: optional inter-widget delay only (parallel reads allowed)."""

    def __enter__(self):
        if widget_stagger_enabled():
            delay_ms = max(0, g.get_int_setting("general.widget.delay", 1000))
            if delay_ms > 0:
                last_ms = g.get_int_runtime_setting(_LAST_LOAD_MS_KEY, 0)
                wait_ms = delay_ms - (int(time.time() * 1000) - last_ms)
                if wait_ms > 0:
                    g.log(f"Widget stagger waiting {wait_ms}ms before {widget_request_key()}", "debug")
                    g.wait_for_abort(wait_ms / 1000.0)
            g.log(f"Widget load started: {widget_request_key()}", "debug")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if widget_stagger_enabled():
            g.set_runtime_setting(_LAST_LOAD_MS_KEY, int(time.time() * 1000))
            g.log(f"Widget load finished: {widget_request_key()}", "debug")
        return False
