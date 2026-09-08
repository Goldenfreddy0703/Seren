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
_ROW_SNAPSHOT_KEY = "kodi_watched_bridge.row_snapshot"
_SNAPSHOT_READY_KEY = "kodi_watched_bridge.snapshot_ready"
_PENDING_FIRST_SIGHT_KEY = "kodi_watched_bridge.pending_first_sight"
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

    snapshot = _load_row_snapshot()
    sync_debounce = _load_sync_debounce_cache()
    pending_first_sight = _load_pending_first_sight()
    snapshot_ready = g.get_bool_runtime_setting(_SNAPSHOT_READY_KEY, False)
    now = time.time()
    queued = 0
    seen_ids: set[str] = set()

    for row in rows:
        id_file = str(row.get("idFile") or row.get("idfile") or "")
        if not id_file:
            continue
        seen_ids.add(id_file)

        filename = (row.get("strFilename") or row.get("strfilename") or "").strip()
        play_count = _normalize_play_count(row.get("playCount") if "playCount" in row else row.get("playcount"))
        last_played = _normalize_last_played(row.get("lastPlayed") if "lastPlayed" in row else row.get("lastplayed"))
        row_state = _row_state_tuple(play_count, last_played)

        prev_state = snapshot.get(id_file)

        if prev_state is None:
            snapshot[id_file] = _snapshot_entry(filename, play_count, last_played)
            if not snapshot_ready:
                pending_first_sight.add(id_file)
                continue
        elif _snapshot_tuple(prev_state) == row_state:
            if id_file not in pending_first_sight:
                continue
        else:
            snapshot[id_file] = _snapshot_entry(filename, play_count, last_played)

        pending_first_sight.discard(id_file)

        kodi_watched = _kodi_row_is_watched(play_count, last_played)

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
        pending_first_sight.discard(stale_id)

    _save_row_snapshot(snapshot)
    _save_pending_first_sight(pending_first_sight)
    _save_sync_debounce_cache(sync_debounce)
    if rows and not snapshot_ready:
        g.set_runtime_setting(_SNAPSHOT_READY_KEY, True)
    if queued:
        g.log(f"Kodi watched bridge: queued {queued} sync(s)", "info")


def _normalize_play_count(raw) -> int:
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _normalize_last_played(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _row_state_tuple(play_count: int, last_played: str | None) -> tuple[int, str | None]:
    return play_count, last_played


def _snapshot_entry(filename: str, play_count: int, last_played: str | None) -> dict:
    return {
        "str_filename": filename,
        "play_count": play_count,
        "last_played": last_played,
    }


def _snapshot_tuple(entry: dict) -> tuple[int, str | None]:
    return _row_state_tuple(
        _normalize_play_count(entry.get("play_count")),
        _normalize_last_played(entry.get("last_played")),
    )


def _kodi_row_is_watched(play_count: int, last_played: str | None) -> bool:
    """Kodi marks plugin movies unwatched by clearing playCount and lastPlayed."""
    return play_count > 0 or last_played is not None


def _movie_prism_is_watched(info: dict, item_information: dict) -> bool:
    """Match Simkl Manager movie watched state (completed list + history_cleared)."""
    sid = info.get("simkl_id")
    history_cleared = bool(info.get("watch_history_cleared"))
    simkl_status = info.get("simkl_status")

    if sid is not None:
        try:
            from resources.lib.database.session import get_sync_database

            row = get_sync_database().fetchone(
                "SELECT watched, simkl_status, history_cleared FROM movies WHERE simkl_id=?",
                (int(sid),),
            )
            if row:
                history_cleared = history_cleared or int(row.get("history_cleared") or 0) > 0
                if row.get("simkl_status"):
                    simkl_status = row.get("simkl_status")
                if history_cleared:
                    return int(row.get("watched") or 0) > 0
        except Exception:
            pass

    if history_cleared:
        play_count = item_information.get("play_count")
        if play_count is None:
            play_count = info.get("playcount")
        try:
            return int(play_count or 0) > 0
        except (TypeError, ValueError):
            return False

    if simkl_status == "completed":
        return True

    play_count = item_information.get("play_count")
    if play_count is None:
        play_count = info.get("playcount")
    try:
        return int(play_count or 0) > 0
    except (TypeError, ValueError):
        return False


def _prism_is_watched(item_information: dict) -> bool:
    info = _library_info(item_information)
    mediatype = (info.get("mediatype") or "").lower()
    if mediatype == "movie":
        return _movie_prism_is_watched(info, item_information)

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
        ok = apply_mark_watched(item_information, silent=False, refresh=True)
    else:
        ok = apply_mark_unwatched(item_information, silent=False, refresh=True)

    if ok and id_file:
        snapshot = _load_row_snapshot()
        filename = ""
        play_count = 1 if watched else 0
        last_played = None
        try:
            for row in _fetch_prism_file_rows():
                if str(row.get("idFile") or row.get("idfile") or "") == str(id_file):
                    filename = (row.get("strFilename") or row.get("strfilename") or "").strip()
                    play_count = _normalize_play_count(
                        row.get("playCount") if "playCount" in row else row.get("playcount")
                    )
                    last_played = _normalize_last_played(
                        row.get("lastPlayed") if "lastPlayed" in row else row.get("lastplayed")
                    )
                    break
        except Exception:
            pass
        snapshot[str(id_file)] = _snapshot_entry(filename, play_count, last_played)
        _save_row_snapshot(snapshot)

    return ok


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
                SELECT f.idFile, f.strFilename, f.playCount, f.lastPlayed, p.strPath
                FROM files f
                LEFT JOIN path p ON p.idPath = f.idPath
                WHERE f.strFilename LIKE %s OR p.strPath LIKE %s
                """,
                (f"%{_PRISM_URL_MARKER}%", f"%{_PRISM_URL_MARKER}%"),
            )
        return video_database.fetchall(
            """
            SELECT f.idFile, f.strFilename, f.playCount, f.lastPlayed, p.strPath
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


def _load_row_snapshot() -> dict[str, dict]:
    cached = g.get_runtime_setting(_ROW_SNAPSHOT_KEY, {})
    if not isinstance(cached, dict) or not cached:
        legacy = g.get_runtime_setting("kodi_watched_bridge.playcount_snapshot", {})
        if isinstance(legacy, dict) and legacy:
            cached = {
                str(key): _snapshot_entry("", _normalize_play_count(value), None)
                for key, value in legacy.items()
            }
    snapshot: dict[str, dict] = {}
    for key, value in (cached or {}).items():
        if isinstance(value, dict):
            snapshot[str(key)] = value
        else:
            snapshot[str(key)] = _snapshot_entry("", _normalize_play_count(value), None)
    return snapshot


def _save_row_snapshot(snapshot: dict[str, dict]) -> None:
    g.set_runtime_setting(_ROW_SNAPSHOT_KEY, snapshot)


def _load_pending_first_sight() -> set[str]:
    cached = g.get_runtime_setting(_PENDING_FIRST_SIGHT_KEY, [])
    if not isinstance(cached, (list, tuple, set)):
        return set()
    return {str(item) for item in cached}


def _save_pending_first_sight(ids: set[str]) -> None:
    g.set_runtime_setting(_PENDING_FIRST_SIGHT_KEY, sorted(ids))


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
