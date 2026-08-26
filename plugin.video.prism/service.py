from __future__ import annotations

import sqlite3
import sys
import threading
import time
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


def _service_shutdown() -> None:
    """Service exit hook."""
    try:
        from resources.lib.common.thread_pool import release_global_executors

        release_global_executors(wait=False, cancel_futures=True)
    except Exception:
        pass


def _run_long_life_manager() -> None:
    try:
        from resources.lib.modules.providers.service_manager import ProvidersServiceManager

        ProvidersServiceManager().run_long_life_manager()
    except Exception:
        g.log_stacktrace()


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

try:
    from resources.lib.database.session import get_sync_database

    get_sync_database()
except Exception:
    g.log_stacktrace()

monitor = PrismMonitor()
_long_life_thread: threading.Thread | None = None
try:
    _long_life_thread = threading.Thread(
        target=_run_long_life_manager,
        daemon=True,
        name="prism-long-life",
    )
    _long_life_thread.start()

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

    g.set_runtime_setting("cache_maintenance.service_started_at", time.time())

    while not monitor.abortRequested():
        if monitor.waitForAbort(0):
            break

        try:
            from resources.lib.discover.browse_catalog_seed import process_idle_browse_catalog_seed
            from resources.lib.meta.enrichment import MetaEnrichmentQueue
            from resources.lib.modules.cache_maintenance import process_idle_deferred_vacuum

            MetaEnrichmentQueue.process_idle()
            if monitor.abortRequested():
                break
            process_idle_browse_catalog_seed()
            if monitor.abortRequested():
                break
            process_idle_deferred_vacuum()
        except Exception:
            pass

        if monitor.abortRequested():
            break

        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runMaintenance")')
        if monitor.waitForAbort(15):
            break
        if monitor.abortRequested():
            break
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=syncSimklActivities")')
        if monitor.waitForAbort(15):
            break
        if monitor.abortRequested():
            break
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=cleanOrphanedMetadata")')
        if monitor.waitForAbort(15):
            break
        if monitor.abortRequested():
            break
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=updateLocalTimezone")')
        sync_wait_minutes = randint(20, 30) if g.PLATFORM == "android" else randint(13, 17)
        if monitor.waitForAbort(60 * sync_wait_minutes):
            break
finally:
    _service_shutdown()
    if _long_life_thread is not None and _long_life_thread.is_alive():
        _long_life_thread.join(timeout=3.0)
    del monitor
    g.deinit()
