# Seren vs Prism: background contention audit

Reference comparison for menu/background performance decisions. Seren (`plugin.video.seren`) is the baseline Prism forked from; Prism adds Simkl sync, calendar warm, and list prefetch.

## Service loop

| | Seren | Prism |
|---|---|---|
| Maintenance interval | ~13–17 min (+ 15s steps for sync/cleanup) | Same pattern |
| Startup extras | `torrentCacheCleanup`, `longLifeServiceManager` | Same + `cache_maintenance.service_started_at` |
| Idle work in loop | None | `MetaEnrichmentQueue.process_idle`, `process_idle_browse_catalog_seed`, `process_idle_deferred_vacuum` |
| Sync action | `syncTraktActivities` | `syncSimklActivities` |

**Takeaway:** Prism runs more idle DB work between maintenance cycles. `service_background_idle_ready()` gates enrichment/vacuum when the addon is visible or within startup grace.

## List page prefetch

| | Seren | Prism |
|---|---|---|
| Next-page warm | **None** | `page_prefetch` module: RunPlugin `pagePrefetch` after `closeDirectory` |
| User control | N/A | `general.prefetch.pages` (0–5, default 0) |
| Requires | N/A | `general.menucaching` + depth > 0 |

**Takeaway:** Prefetch is opt-in warmth for page 2+. Page 1 paint path is unchanged (full cast + artwork).

## Calendar

| | Seren | Prism |
|---|---|---|
| Model | On-demand Trakt calendar API when user opens calendar | Simkl CDN bundles + weekly JSON cache |
| Background | No startup calendar prefetch | `prefetchCalendars` on service start + `runMaintenance` |
| Dedup | N/A | File lock (`.prefetch.lock`, 15 min TTL) |

**Takeaway:** Calendar prefetch can contend with browse paint (shared SQLite + provider HTTP). Prism now defers calendar warm when `is_addon_visible()`, `foreground_browse_busy()`, or `service_background_idle_ready()` is false.

## Widgets

| | Seren | Prism |
|---|---|---|
| Next page in widgets | `general.widget.hide_next` | Same |
| Load spacing | None | `general.widget.stagger` + `general.widget.delay` |
| Menu cache | `cacheToDisc` when menucaching on | Disabled for `FROM_WIDGET` |

**Takeaway:** Running Seren and Prism skin widgets on the same home rows duplicates TMDB/Fanart traffic. Use widget stagger or disable overlapping widgets.

## Thread pool

| | Seren | Prism |
|---|---|---|
| Scale setting | `general.threadpoolScale` (Default/Low/Medium/High/Extreme) | Same |
| Workers | 20 / 10 / 20 / 40 / 80 | Same + `get_shared_executor()` / `get_provider_executor()` |
| Runtime limiter | `threadpool.limiter` → max 1 worker | Same |

**Takeaway:** No benefit from more pools for menus; SQLite single-writer and API rate limits dominate. Keep threadpool scale as the user knob.

## Foreground list paint

| | Seren | Prism |
|---|---|---|
| DB | Trakt sync DB + `metadataHandler.update` on mill | Simkl sync DB + `prepare_list_rows_for_paint` |
| Parallelism | ThreadPool per indexer batch | Parallel cast + art waves, `ArtBatchCoordinator` |
| Paint cache | Provider blobs in sync DB | + `display_meta` paint stamps, session page cache |

**Takeaway:** Prism does more upfront work per cold row but caches aggressively for repeat opens. Deferring metadata on first paint was rejected; contention fixes target background work only.

## Background idle gate (Prism-only)

`service_background_idle_ready()` in `cache_maintenance.py` returns false when:

- Kodi abort requested
- Prism addon window is visible (`is_addon_visible()`)
- Optional: meta enrichment in flight
- Within startup grace (5–10 min after service start)

Used by: meta enrichment idle, deferred vacuum, calendar prefetch (after this audit).

## Foreground browse flag (Prism-only)

`browse.menu_active` via `set_foreground_menu_active()` in `prism.py` during directory builds (`PLUGIN_HANDLE > 0`, not widget).

Used by: page prefetch launch wait, drilldown episode pre-mill, `foreground_browse_busy()`, simkl_sync milling yield.

## Recommended user settings

| Goal | Settings |
|---|---|
| Seren-like default | `general.prefetch.pages = 0`, menucaching on |
| Faster Next Page | `general.prefetch.pages = 1–2`, menucaching on |
| Seren + Prism home widgets | `general.widget.stagger = on`, delay 1000–2000 ms |
| Stale menu art | menucaching off, or `prism_reload=true` on that folder |
