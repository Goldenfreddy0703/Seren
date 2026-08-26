"""Post-sync episode catalog warm for library episode menus (Next Up, Continue Watching, etc.)."""
from __future__ import annotations

import datetime
import time
from typing import TYPE_CHECKING, Callable

from resources.lib.modules.globals import g
from resources.lib.simkl.library import _unwrap_sync_items, sync_entry_media_blob

if TYPE_CHECKING:
    from resources.lib.database.simkl_sync.activities import SimklSyncDatabase

EPISODE_WARM_BATCH_SIZE = 8
CHANGES_POLL_INTERVAL_SEC = 24 * 60 * 60
RECENT_WATCHED_SHOW_LIMIT = 100
WATCHING_WARM_CAP = 50
LAZY_WARM_SHOW_CAP = 10

BUCKET_NEXT_UP = "next_up"
BUCKET_CONTINUE = "continue_watching"
BUCKET_WATCHED = "watched_episodes"
BUCKET_CATALOG = "catalog_update"
BUCKET_DELTA = "sync_delta"

_BUCKET_STRING_IDS = {
    BUCKET_NEXT_UP: 31005,
    BUCKET_CONTINUE: 31006,
    BUCKET_WATCHED: 31007,
    BUCKET_CATALOG: 31008,
    BUCKET_DELTA: 31009,
}

_BUCKET_PROGRESS_STRING_IDS = {
    BUCKET_NEXT_UP: 31005,
    BUCKET_CONTINUE: 31014,
    BUCKET_WATCHED: 31015,
    BUCKET_CATALOG: 31016,
    BUCKET_DELTA: 31017,
}


def bucket_label(bucket: str) -> str:
    string_id = _BUCKET_STRING_IDS.get(bucket, 31008)
    return g.get_language_string(string_id)


def bucket_progress_label(bucket: str) -> str:
    """Short menu label for the sync progress dialog (limited width)."""
    string_id = _BUCKET_PROGRESS_STRING_IDS.get(bucket, 31005)
    return g.get_language_string(string_id)


def episode_warm_enabled() -> bool:
    return g.get_bool_setting("simkl.syncEpisodeWarm", True)


def show_ids_from_payload(payload) -> set[int]:
    ids: set[int] = set()
    if not payload:
        return ids
    for media_key in ("shows", "anime"):
        for entry in _unwrap_sync_items(payload, media_key):
            if not isinstance(entry, dict):
                continue
            blob = sync_entry_media_blob(entry, media_key)
            simkl_id = (blob.get("ids") or {}).get("simkl")
            if simkl_id is not None:
                ids.add(int(simkl_id))
    return ids


def _active_watchlist_show_ids(db: "SimklSyncDatabase") -> set[int]:
    ids: set[int] = set()
    for status in ("watching", "plantowatch", "hold"):
        for catalog in ("tv", "anime"):
            for ref in db.get_shows_by_simkl_status(status, catalog=catalog):
                if ref.get("simkl_id") is not None:
                    ids.add(int(ref["simkl_id"]))
    return ids


def fetch_catalog_change_ids(db: "SimklSyncDatabase", *, force: bool = False) -> set[int]:
    """Return Simkl IDs from GET /changes intersected with active watchlist shows."""
    db.refresh_activities()
    last_poll = int(db.activities.get("last_changes_poll") or 0)
    now = int(time.time())
    if not force and last_poll and (now - last_poll) < CHANGES_POLL_INTERVAL_SEC:
        return set()

    date_from = datetime.datetime.utcfromtimestamp(last_poll or (now - CHANGES_POLL_INTERVAL_SEC)).strftime(
        "%Y-%m-%d"
    )
    payload = db.simkl_api.get_changes(date_from=date_from)
    db.execute_sql("UPDATE activities SET last_changes_poll=? WHERE sync_id=1", (now,))
    db.refresh_activities()

    if not isinstance(payload, dict):
        return set()

    changed: set[int] = set()
    for key in ("shows", "anime"):
        for simkl_id in payload.get(key) or []:
            try:
                changed.add(int(simkl_id))
            except (TypeError, ValueError):
                continue

    active = _active_watchlist_show_ids(db)
    return changed & active


def _show_row(db: "SimklSyncDatabase", simkl_id: int) -> dict | None:
    return db.fetchone(
        """
        SELECT s.simkl_id, m.value AS simkl_object, s.tmdb_id, s.tvdb_id, s.needs_milling
        FROM shows AS s
                 LEFT JOIN shows_meta AS m ON m.id = s.simkl_id AND m.type = 'simkl'
        WHERE s.simkl_id = ?
        """,
        (int(simkl_id),),
    )


def show_catalog_is_warm(db: "SimklSyncDatabase", simkl_id: int) -> bool:
    row = db.fetchone(
        """
        SELECT 1 AS ok
        FROM episodes AS e
                 INNER JOIN episodes_meta AS em ON em.id = e.simkl_id AND em.type = 'simkl'
        WHERE e.simkl_show_id = ?
        LIMIT 1
        """,
        (int(simkl_id),),
    )
    return row is not None


def resolve_warm_targets(
    db: "SimklSyncDatabase",
    *,
    delta_show_ids: set[int] | None = None,
    changes_show_ids: set[int] | None = None,
) -> list[tuple[dict, str]]:
    """Return (show_row, bucket) pairs deduped by simkl_id (first bucket wins)."""
    delta_show_ids = delta_show_ids or set()
    changes_show_ids = changes_show_ids or set()
    ordered: list[tuple[int, str]] = []

    def add(ids: set[int], bucket: str) -> None:
        seen = {show_id for show_id, _ in ordered}
        for show_id in ids:
            if show_id not in seen:
                ordered.append((int(show_id), bucket))

    watching_candidates: list[int] = []
    for catalog in ("tv", "anime"):
        rows = db.fetchall(
            """
            SELECT simkl_id, watched_episodes, needs_milling
            FROM shows
            WHERE COALESCE(simkl_status, '') = 'watching'
            """
        )
        for row in rows or []:
            show_id = row.get("simkl_id")
            if show_id is None:
                continue
            if db.show_catalog(int(show_id)) != catalog:
                continue
            if not row.get("needs_milling"):
                continue
            if int(row.get("watched_episodes") or 0) <= 0:
                continue
            watching_candidates.append(int(show_id))
    add(set(watching_candidates[:WATCHING_WARM_CAP]), BUCKET_NEXT_UP)

    bookmark_rows = db.fetchall(
        """
        SELECT DISTINCT e.simkl_show_id AS simkl_id
        FROM bookmarks AS b
                 INNER JOIN episodes AS e ON e.simkl_id = b.simkl_id
        WHERE b.type = 'episode'
        """
    )
    add({int(row["simkl_id"]) for row in bookmark_rows if row.get("simkl_id") is not None}, BUCKET_CONTINUE)

    recent_rows = db.fetchall(
        f"""
        SELECT DISTINCT simkl_show_id AS simkl_id
        FROM episodes
        WHERE watched > 0
        ORDER BY last_watched_at DESC
        LIMIT {RECENT_WATCHED_SHOW_LIMIT}
        """
    )
    add({int(row["simkl_id"]) for row in recent_rows if row.get("simkl_id") is not None}, BUCKET_WATCHED)

    completed_rows = db.fetchall(
        """
        SELECT simkl_id
        FROM shows
        WHERE COALESCE(simkl_status, '') = 'completed'
          AND COALESCE(watched_episodes, 0) > 0
        """
    )
    add({int(row["simkl_id"]) for row in completed_rows if row.get("simkl_id") is not None}, BUCKET_WATCHED)

    add(delta_show_ids, BUCKET_DELTA)
    add(changes_show_ids, BUCKET_CATALOG)

    targets: list[tuple[dict, str]] = []
    for show_id, bucket in ordered:
        if bucket not in (BUCKET_DELTA, BUCKET_CATALOG) and show_catalog_is_warm(db, show_id):
            continue
        if bucket == BUCKET_CATALOG and show_catalog_is_warm(db, show_id):
            continue
        row = _show_row(db, show_id)
        if row:
            targets.append((row, bucket))
        else:
            targets.append(({"simkl_id": show_id}, bucket))
    return targets


def _show_title(show_row: dict) -> str:
    from resources.lib.modules.metadataHandler import MetadataHandler

    info = MetadataHandler.simkl_info(show_row) or {}
    title = info.get("title") or info.get("name")
    if title:
        return str(title)
    obj = show_row.get("simkl_object") or {}
    info = obj.get("info") if isinstance(obj, dict) else {}
    if isinstance(info, dict):
        return str(info.get("title") or info.get("name") or f"Show {show_row.get('simkl_id')}")
    return f"Show {show_row.get('simkl_id')}"


def warm_episode_catalogs(
    db: "SimklSyncDatabase",
    targets: list[tuple[dict, str]],
    *,
    on_progress: Callable[[int, int, str, str], None] | None = None,
    notify_silent: bool = False,
) -> int:
    """Mill episode catalogs for target shows (Simkl meta only — paint on menu open)."""
    if not targets:
        return 0

    if notify_silent:
        g.notification(g.ADDON_NAME, g.get_language_string(31004))

    total = len(targets)
    warmed = 0
    pull_started = time.time()
    for start in range(0, total, EPISODE_WARM_BATCH_SIZE):
        batch = targets[start : start + EPISODE_WARM_BATCH_SIZE]
        show_rows = [row for row, _ in batch]
        db.force_mill_shows(
            show_rows,
            mill_episodes=True,
            skip_provider_episode_updates=True,
            defer_episode_format=True,
        )
        for offset, (show_row, bucket) in enumerate(batch):
            current = start + offset + 1
            if on_progress:
                on_progress(current, total, _show_title(show_row), bucket)
        warmed += len(batch)

    pull_ms = (time.time() - pull_started) * 1000
    g.log(
        f"Simkl episode catalog warm complete: pulled={warmed} pull_ms={pull_ms:.0f}",
        "info",
    )
    return warmed


def run_post_sync_episode_warm(
    db: "SimklSyncDatabase",
    *,
    payload,
    force: bool = False,
    notify_silent: bool = False,
    on_progress: Callable[[int, int, str, str], None] | None = None,
) -> int:
    changes_ids = fetch_catalog_change_ids(db, force=force)
    delta_ids = show_ids_from_payload(payload)
    targets = resolve_warm_targets(
        db,
        delta_show_ids=delta_ids,
        changes_show_ids=changes_ids,
    )
    if targets:
        g.log(f"Simkl episode catalog warm: {len(targets)} show(s)", "info")
    return warm_episode_catalogs(
        db,
        targets,
        on_progress=on_progress,
        notify_silent=notify_silent,
    )


def schedule_lazy_episode_warm(db: "SimklSyncDatabase", show_ids: set[int] | list[int]) -> None:
    """On-demand episode catalog warm for Next Up / Continue Watching menus."""
    if not show_ids or not episode_warm_enabled():
        return

    import threading

    ids = [int(show_id) for show_id in show_ids if show_id is not None]
    unwarmed = [show_id for show_id in ids if not show_catalog_is_warm(db, show_id)]
    if not unwarmed:
        return

    def _warm() -> None:
        try:
            targets: list[tuple[dict, str]] = []
            for show_id in unwarmed[:LAZY_WARM_SHOW_CAP]:
                row = _show_row(db, show_id)
                targets.append((row or {"simkl_id": show_id}, BUCKET_NEXT_UP))
            if targets:
                g.log(f"Simkl lazy episode warm: {len(targets)} show(s)", "debug")
                warm_episode_catalogs(db, targets, notify_silent=True)
        except Exception:
            g.log_stacktrace()

    from resources.lib.common.thread_pool import defer_background

    defer_background(_warm)
