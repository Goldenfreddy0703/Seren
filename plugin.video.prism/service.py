import sqlite3
import sys
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

    g.set_runtime_setting("cache_maintenance.service_started_at", time.time())

    while not monitor.abortRequested():
        try:
            from resources.lib.discover.browse_catalog_seed import process_idle_browse_catalog_seed
            from resources.lib.meta.enrichment import MetaEnrichmentQueue
            from resources.lib.modules.cache_maintenance import process_idle_deferred_vacuum

            MetaEnrichmentQueue.process_idle()
            process_idle_browse_catalog_seed()
            process_idle_deferred_vacuum()
        except Exception:
            pass
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=runMaintenance")')
        if not g.wait_for_abort(15):  # Sleep to make sure tokens refreshed during maintenance
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=syncSimklActivities")')
        if not g.wait_for_abort(15):  # Sleep to make sure we don't possibly clobber settings
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=cleanOrphanedMetadata")')
        if not g.wait_for_abort(15):  # Sleep to make sure we don't possibly clobber settings
            xbmc.executebuiltin('RunPlugin("plugin://plugin.video.prism/?action=updateLocalTimezone")')
        sync_wait_minutes = randint(20, 30) if g.PLATFORM == "android" else randint(13, 17)
        if g.wait_for_abort(60 * sync_wait_minutes):
            break
finally:
    del monitor
    g.deinit()
