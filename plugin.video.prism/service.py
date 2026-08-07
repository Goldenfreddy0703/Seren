import sqlite3
import sys
import threading
from random import randint

import xbmc

from resources.lib.common import tools

if tools.is_stub():
    # noinspection PyUnresolvedReferences
    from mock_kodi import MOCK

from resources.lib.modules.globals import g

from resources.lib.modules.prism_version import do_version_change
from resources.lib.modules.prismMonitor import PrismMonitor
from resources.lib.modules.update_news import do_update_news
from resources.lib.modules.manual_timezone import validate_timezone_detected

g.init_globals(sys.argv)
from resources.lib.common.backup_restore import apply_deferred_db_restore

apply_deferred_db_restore()
do_version_change()

g.log("##################  STARTING SERVICE  ######################")
g.log(f"### {g.ADDON_ID} {g.VERSION}")
g.log(f"### Platform: {g.PLATFORM}")
g.log(f"### Python: {sys.version.split(' ', 1)[0]}")
g.log(f"### SQLite: {sqlite3.sqlite_version}")  # pylint: disable=no-member
g.log(f"### Detected Kodi Version: {g.KODI_VERSION}")
g.log(f"### Detected timezone: {repr(g.LOCAL_TIMEZONE.zone)}")
g.log("#############  SERVICE ENTERED KEEP ALIVE  #################")

monitor = PrismMonitor()
try:
    xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=longLifeServiceManager")')

    do_update_news()
    validate_timezone_detected()
    try:
        g.clear_kodi_bookmarks()
    except TypeError:
        g.log(
            "Unable to clear bookmarks on service init. This is not a problem if it occurs immediately after install.",
            "warning",
        )

    xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=torrentCacheCleanup")')

    try:
        from resources.lib.modules.cache_maintenance import mark_service_boot_started

        mark_service_boot_started()

        def _deferred_warm_menu_caches() -> None:
            try:
                import time

                from resources.lib.modules.cache_maintenance import warm_menu_caches
                from resources.lib.modules.page_prefetch import foreground_browse_busy

                if g.wait_for_abort(8):
                    return
                for _ in range(48):
                    if g.abort_requested():
                        return
                    if not foreground_browse_busy():
                        break
                    time.sleep(0.25)
                if foreground_browse_busy():
                    g.log("Boot menu cache warm deferred — menu active", "debug")
                    return
                warm_menu_caches()
                if not foreground_browse_busy():
                    xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=pluginWarmup")')
            except Exception:
                pass

        threading.Thread(
            target=_deferred_warm_menu_caches,
            daemon=True,
            name="prism-boot-warm",
        ).start()
    except Exception:
        pass

    def _deferred_calendar_prefetch() -> None:
        try:
            import time

            from resources.lib.calendar.simkl_calendar import maybe_prefetch_calendars
            from resources.lib.modules.page_prefetch import foreground_browse_busy

            if not g.wait_for_abort(90):
                if foreground_browse_busy():
                    return
                maybe_prefetch_calendars()
        except Exception:
            pass

    threading.Thread(target=_deferred_calendar_prefetch, daemon=True, name="prism-calendar-defer").start()

    def _service_db_idle() -> bool:
        try:
            from resources.lib.modules.page_prefetch import foreground_browse_busy

            return not foreground_browse_busy()
        except Exception:
            return True

    while not monitor.abortRequested():
        if _service_db_idle():
            try:
                from resources.lib.meta.enrichment import MetaEnrichmentQueue

                MetaEnrichmentQueue.process_idle()
            except Exception:
                pass
        if _service_db_idle():
            if g.get_bool_runtime_setting("maintenance.deferred"):
                g.log("Running deferred maintenance", "debug")
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runMaintenance")')
        if not g.wait_for_abort(15):  # Sleep to make sure tokens refreshed during maintenance
            if _service_db_idle():
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=syncSimklActivities")')
        if not g.wait_for_abort(15):  # Sleep to make sure we don't possibly clobber settings
            if _service_db_idle():
                xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=cleanOrphanedMetadata")')
        if not g.wait_for_abort(15):  # Sleep to make sure we don't possibly clobber settings
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=updateLocalTimezone")')
        if g.wait_for_abort(60 * randint(13, 17)):
            break
finally:
    del monitor
    g.deinit()
