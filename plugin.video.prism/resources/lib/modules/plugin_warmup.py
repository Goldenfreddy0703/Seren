"""Lightweight plugin invoke to prime imports and DB handles after Kodi boot."""
from __future__ import annotations

import time

from resources.lib.modules.globals import g


def run_plugin_warmup() -> None:
    """Run once at boot (RunPlugin) so the first user drilldown pays less cold-start cost."""
    if g.PLUGIN_HANDLE > 0:
        return
    start = time.time()
    try:
        from resources.lib.database.session import get_sync_database
        from resources.lib.database.simkl_sync.milling import fetch_raw_show_episodes  # noqa: F401
        from resources.lib.indexers.simkl import SimklAPI
        from resources.lib.meta.menu_paint_profile import MenuPaintProfile  # noqa: F401
        from resources.lib.modules.metadataHandler import MetadataHandler

        db = get_sync_database()
        SimklAPI()
        MetadataHandler()
        db.fetchone("SELECT 1")
    except Exception:
        g.log_stacktrace()
    else:
        g.log(f"plugin_warmup_ms={(time.time() - start) * 1000:.0f}", "debug")
