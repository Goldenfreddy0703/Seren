import time

import xbmc

from resources.lib.modules.globals import g

ONWAKE_NETWORK_UP_DELAY = 5

_UI_REFRESH_SETTING_KEYS = ("searchHistory", "general.menucaching")


class PrismMonitor(xbmc.Monitor):
    def onSettingsChanged(self):
        callback_time = int(time.time())
        if g.get_int_runtime_setting("onSettingsChangedLastCalled") == callback_time:
            g.log("Debouncing onSettingsChange call", "debug")
            # This check is to debounce multiple onSettingsChange calls to the nearest second as the callbacks
            # can come a bit late after setting multiple settings programmatically and cause
            # the settings persisted flag to be cleared
            return
        g.set_runtime_setting("onSettingsChangedLastCalled", callback_time)
        g.log("SETTINGS UPDATED", "info")
        if g.SETTINGS_CACHE.get_settings_persisted_flag():
            return
        before_ui = {k: g.SETTINGS_CACHE.get_setting(k) for k in _UI_REFRESH_SETTING_KEYS}
        g.log("FLUSHING SETTINGS CACHE", "info")
        g.SETTINGS_CACHE.clear_cache()
        from resources.lib.modules.settings_hot_cache import warm_settings_dict

        warm_settings_dict()
        ui_settings_changed = before_ui != {k: g.get_setting(k) for k in _UI_REFRESH_SETTING_KEYS}
        try:
            if g.is_addon_visible() and ui_settings_changed:
                g.refresh_visible_container()
        except Exception:
            g.log_stacktrace()
        try:
            from resources.lib.modules.cache_maintenance import invalidate_paint_stamps

            invalidate_paint_stamps()
        except Exception:
            g.log_stacktrace()
        g.trigger_widget_refresh(if_playing=False)

    def onNotification(self, sender, method, data):
        if method == "System.OnWake":
            g.log("System.OnWake notification received", "info")
            if not g.wait_for_abort(ONWAKE_NETWORK_UP_DELAY):  # Sleep for 5 seconds to make sure network is up
                if g.PLATFORM == "android":
                    g.clear_runtime_setting("system.sleeping")
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runMaintenance")')
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=torrentCacheCleanup")')
            if not g.wait_for_abort(15):  # Sleep to make sure tokens refreshed during maintenance
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=syncSimklActivities")')

        if method == "System.OnSleep":
            g.log("System.OnSleep notification received", "info")
            if g.PLATFORM == "android":
                g.set_runtime_setting("system.sleeping", True)
