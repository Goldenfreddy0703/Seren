"""Sync Kodi native Mark as Watched toggles on Prism plugin URLs into simklSync.db."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, quote

import xbmc

from resources.lib.common import tools
from resources.lib.modules.globals import g
from resources.lib.simkl.ids import encode_action_args, normalize_action_args, parse_stored_action_args
from resources.lib.simkl.library_status import _library_info
from resources.lib.simkl.watch_toggle import apply_mark_unwatched, apply_mark_watched

_PRISM_URL_MARKER = "plugin.video.prism"
_PLAYCOUNT_SNAPSHOT_KEY = "kodi_watched_bridge.playcount_snapshot"
_SNAPSHOT_READY_KEY = "kodi_watched_bridge.snapshot_ready"
_SYNC_DEBOUNCE_KEY = "kodi_watched_bridge.sync_debounce"
_DEBOUNCE_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 2.0
_LAST_SCAN_KEY = "kodi_watched_bridge.last_scan"
_SUPPORTED_MEDIATYPES = frozenset({"movie", "episode", "season", "tvshow"})


def bridge_enabled() -> bool:
    return g.get_bool_setting("general.kodiWatchedBridge", True)


def scan_kodi_watched_bridge(*, force: bool = False, trigger: str = "poll") -> None:
    """Detect Kodi/Prism watched mismatches on Prism plugin URLs and sync."""
    if not bridge_enabled():
        return
    if not force:
        try:
            if xbmc.getCondVisibility("Player.Playing"):
                return
        except Exception:
            pass
        last_scan = float(g.get_float_runtime_setting(_LAST_SCAN_KEY, 0) or 0)
        if time.time() - last_scan < _POLL_INTERVAL_SECONDS:
            return
    g.set_runtime_setting(_LAST_SCAN_KEY, time.time())

    try:
        rows = _fetch_prism_file_rows()
    except Exception as exc:
        if "locked" not in str(exc).lower():
            g.log_stacktrace()
        return

    snapshot = _load_playcount_snapshot()
    sync_debounce = _load_sync_debounce_cache()
    snapshot_ready = g.get_bool_runtime_setting(_SNAPSHOT_READY_KEY, False)
    now = time.time()
    queued = 0
    seen_ids: set[str] = set()

    for row in rows:
        id_file = str(row.get("idFile") or row.get("idfile") or "")
        if not id_file:
            continue
        seen_ids.add(id_file)
        try:
            play_count = int(row.get("playCount") or row.get("playcount") or 0)
        except (TypeError, ValueError):
            play_count = 0

        prev_play_count = snapshot.get(id_file)
        if prev_play_count is None:
            snapshot[id_file] = play_count
            if not snapshot_ready:
                continue
        elif play_count == prev_play_count:
            continue
        else:
            snapshot[id_file] = play_count

        kodi_watched = play_count > 0

        url = _row_plugin_url(row)
        action_args = action_args_from_prism_url(url)
        if not action_args:
            continue
        action_args = normalize_action_args(action_args)
        mediatype = (action_args.get("mediatype") or "").lower()
        if not action_args or mediatype not in _SUPPORTED_MEDIATYPES:
            continue

        item_information = tools.get_item_information(action_args)
        if not item_information:
            continue

        prism_watched = _prism_is_watched(item_information)
        if kodi_watched == prism_watched:
            continue

        debounce_key = f"{id_file}:{int(kodi_watched)}"
        if sync_debounce.get(debounce_key, 0) > now - _DEBOUNCE_SECONDS:
            continue

        queue_kodi_watched_sync(action_args, watched=kodi_watched, id_file=id_file)
        sync_debounce[debounce_key] = now
        queued += 1

    for stale_id in set(snapshot) - seen_ids:
        snapshot.pop(stale_id, None)

    _save_playcount_snapshot(snapshot)
    _save_sync_debounce_cache(sync_debounce)
    if rows and not snapshot_ready:
        g.set_runtime_setting(_SNAPSHOT_READY_KEY, True)
    if queued:
        g.log(f"Kodi watched bridge: queued {queued} sync(s)", "info")


def _prism_is_watched(item_information: dict) -> bool:
    info = _library_info(item_information)
    mediatype = (info.get("mediatype") or "").lower()
    play_count = item_information.get("play_count")
    if play_count is None:
        play_count = info.get("playcount")
    try:
        if play_count is not None and int(play_count) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if mediatype in ("tvshow", "season"):
        watched_eps = item_information.get("watched_episodes")
        if watched_eps is None:
            watched_eps = info.get("watched_episodes")
        ep_count = item_information.get("episode_count") or info.get("episode_count") or 0
        try:
            if int(ep_count) > 0 and int(watched_eps or 0) >= int(ep_count):
                return True
        except (TypeError, ValueError):
            pass
    return False


def apply_pending_transition(action_args: dict, *, watched: bool, id_file: str | None = None) -> None:
    """Router entry: resolve item metadata and apply watched state."""
    _apply_transition(action_args, watched=watched, id_file=id_file)


def _apply_transition(action_args: dict, *, watched: bool, id_file: str | None = None) -> bool:
    item_information = tools.get_item_information(action_args)
    if not item_information:
        return False
    info = item_information.get("info") if isinstance(item_information, dict) else None
    if not isinstance(info, dict) or info.get("simkl_id") is None:
        return False

    if watched:
        return apply_mark_watched(item_information, silent=False, refresh=True)
    return apply_mark_unwatched(item_information, silent=False, refresh=True)


def action_args_from_prism_url(url: str | None) -> dict | None:
    if not url or _PRISM_URL_MARKER not in url:
        return None
    if not url.startswith("plugin://"):
        idx = url.find("plugin://")
        if idx >= 0:
            url = url[idx:]
        else:
            return None

    query = urlparse(url).query
    if not query:
        return None

    params = dict(parse_qsl(query, keep_blank_values=True))
    raw = params.get("action_args")
    if not raw:
        return None

    parsed = parse_stored_action_args(raw)
    if parsed:
        return parsed

    decoded = raw
    for _ in range(4):
        try:
            candidate = json.loads(unquote(decoded))
            if isinstance(candidate, dict):
                return normalize_action_args(candidate)
        except (ValueError, TypeError):
            pass
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    return None


def _fetch_prism_file_rows() -> list[dict]:
    with g.get_kodi_video_db_connection(max_lock_retries=5, read_only=True) as video_database:
        config = g.get_kodi_video_db_config()
        if config.get("type") == "mysql":
            return video_database.fetchall(
                """
                SELECT f.idFile, f.strFilename, f.playCount, p.strPath
                FROM files f
                LEFT JOIN path p ON p.idPath = f.idPath
                WHERE f.strFilename LIKE %s OR p.strPath LIKE %s
                """,
                (f"%{_PRISM_URL_MARKER}%", f"%{_PRISM_URL_MARKER}%"),
            )
        return video_database.fetchall(
            """
            SELECT f.idFile, f.strFilename, f.playCount, p.strPath
            FROM files f
            LEFT JOIN path p ON p.idPath = f.idPath
            WHERE f.strFilename LIKE '%plugin.video.prism%'
               OR p.strPath LIKE '%plugin://plugin.video.prism%'
               OR f.strFilename LIKE 'plugin://plugin.video.prism%'
            """
        )


def _row_plugin_url(row: dict) -> str | None:
    filename = (row.get("strFilename") or row.get("strfilename") or "").strip()
    path = (row.get("strPath") or row.get("strpath") or "").strip()
    if filename.startswith("plugin://"):
        return filename
    if path.startswith("plugin://"):
        return f"{path}{filename}" if filename else path
    if _PRISM_URL_MARKER in filename:
        return filename
    combined = f"{path}{filename}"
    return combined if _PRISM_URL_MARKER in combined else None


def _load_playcount_snapshot() -> dict[str, int]:
    cached = g.get_runtime_setting(_PLAYCOUNT_SNAPSHOT_KEY, {})
    if not isinstance(cached, dict):
        return {}
    snapshot: dict[str, int] = {}
    for key, value in cached.items():
        try:
            snapshot[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return snapshot


def _save_playcount_snapshot(snapshot: dict[str, int]) -> None:
    g.set_runtime_setting(_PLAYCOUNT_SNAPSHOT_KEY, snapshot)


def _load_sync_debounce_cache() -> dict[str, float]:
    cached = g.get_runtime_setting(_SYNC_DEBOUNCE_KEY, {})
    return cached if isinstance(cached, dict) else {}


def _save_sync_debounce_cache(snapshot: dict[str, float]) -> None:
    g.set_runtime_setting(_SYNC_DEBOUNCE_KEY, snapshot)


def queue_kodi_watched_sync(action_args: dict, *, watched: bool, id_file: str) -> None:
    """Run sync in plugin context (GUI-safe notifications and container refresh)."""
    args = {
        "action": "kodiWatchedSync",
        "action_args": encode_action_args(action_args),
        "watched": "1" if watched else "0",
        "kodi_id_file": str(id_file),
    }
    plugin_url = f'plugin://plugin.video.prism/?{urlencode(args, quote_via=quote)}'
    xbmc.executebuiltin(f'RunPlugin("{plugin_url}")')
