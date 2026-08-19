"""MDBList batch enrichment for simkl_cdn.db (embedded in addon)."""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from typing import Any

MDBLIST_BASE = "https://api.mdblist.com"
DEFAULT_BATCH_SIZE = 200  # MDBList Media Info Batch max per request
DEFAULT_SLEEP = 0.75
USER_AGENT = "plugin.video.prism/simkl-discover-builder"


@dataclass(frozen=True)
class MdblistRateLimitState:
    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    retry_after: int | None = None


@dataclass(frozen=True)
class MdblistBatchResponse:
    items: list[dict[str, Any]]
    rate_limit: MdblistRateLimitState


class MdblistRateLimitError(Exception):
    """Daily MDBList API quota exceeded (HTTP 429)."""

    def __init__(self, message: str, *, state: MdblistRateLimitState | None = None) -> None:
        super().__init__(message)
        self.state = state or MdblistRateLimitState()


def _header_int(headers: Message | Any, name: str) -> int | None:
    raw = headers.get(name) if headers is not None else None
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_rate_limit_headers(headers: Message | Any) -> MdblistRateLimitState:
    return MdblistRateLimitState(
        limit=_header_int(headers, "X-RateLimit-Limit"),
        remaining=_header_int(headers, "X-RateLimit-Remaining"),
        reset=_header_int(headers, "X-RateLimit-Reset"),
        retry_after=_header_int(headers, "Retry-After"),
    )


def resolve_mdblist_api_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    from resources.lib.meta.provider_settings import mdblist_calendar_enabled

    if not mdblist_calendar_enabled():
        return ""
    try:
        from resources.lib.database.keys import get_api_key

        key = get_api_key("MDBList")
        if key:
            return key
    except Exception:
        pass
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_mdblist_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(simkl_rows)")}
    for name, col_type in (
        ("mdblist_score", "INTEGER"),
        ("streams_json", "TEXT"),
        ("watch_providers_json", "TEXT"),
        ("extras_json", "TEXT"),
    ):
        if name not in existing:
            conn.execute(f"ALTER TABLE simkl_rows ADD COLUMN {name} {col_type}")


def _parse_int_id(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _non_empty_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return str(v)


def _imdb_id(raw: Any) -> str | None:
    s = _non_empty_str(raw)
    if not s:
        return None
    s = s.strip().lower()
    return s if s.startswith("tt") else None


def _mdblist_id(raw: Any) -> str | None:
    s = _non_empty_str(raw)
    return s.strip() if s else None


def _lookup_key(lookup_id: str | int) -> str:
    return str(lookup_id).strip().lower()


def lookup_target(catalog: str, ids: dict[str, Any]) -> tuple[str, str, str | int] | None:
    if catalog == "movie":
        candidates = [
            ("tmdb", "movie", _parse_int_id(ids.get("tmdb"))),
            ("imdb", "movie", _imdb_id(ids.get("imdb"))),
            ("tvdb", "movie", _parse_int_id(ids.get("tvdb"))),
            ("simkl", "movie", _parse_int_id(ids.get("simkl") or ids.get("simkl_id"))),
            ("mdblist", "movie", _mdblist_id(ids.get("mdblist"))),
        ]
    elif catalog == "tv":
        candidates = [
            ("tmdb", "show", _parse_int_id(ids.get("tmdb"))),
            ("tvdb", "show", _parse_int_id(ids.get("tvdb"))),
            ("imdb", "show", _imdb_id(ids.get("imdb"))),
            ("simkl", "show", _parse_int_id(ids.get("simkl") or ids.get("simkl_id"))),
            ("mdblist", "show", _mdblist_id(ids.get("mdblist"))),
        ]
    elif catalog == "anime":
        candidates = [
            ("mal", "any", _parse_int_id(ids.get("mal"))),
            ("tmdb", "show", _parse_int_id(ids.get("tmdb"))),
            ("tvdb", "show", _parse_int_id(ids.get("tvdb"))),
            ("imdb", "show", _imdb_id(ids.get("imdb"))),
            ("mdblist", "any", _mdblist_id(ids.get("mdblist"))),
        ]
    else:
        return None

    for provider, media_type, lookup_id in candidates:
        if lookup_id is not None:
            return (provider, media_type, lookup_id)
    return None


def response_lookup_id(provider: str, ids_obj: dict[str, Any]) -> str | int | None:
    if provider == "tmdb":
        return _parse_int_id(ids_obj.get("tmdb"))
    if provider == "mal":
        return _parse_int_id(ids_obj.get("mal"))
    if provider == "imdb":
        return _imdb_id(ids_obj.get("imdb"))
    if provider == "tvdb":
        return _parse_int_id(ids_obj.get("tvdb"))
    if provider == "simkl":
        return _parse_int_id(ids_obj.get("simkl"))
    if provider == "mdblist":
        return _mdblist_id(ids_obj.get("mdblist"))
    return None


def mdblist_ratings_to_map(ratings: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not ratings:
        return out
    for entry in ratings:
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        if not src:
            continue
        out[str(src)] = {
            "rating": entry.get("value"),
            "score": entry.get("score"),
            "votes": entry.get("votes"),
        }
    return out


def _format_runtime_minutes(minutes: Any) -> str | None:
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return None
    if mins <= 0:
        return None
    hours, rem = divmod(mins, 60)
    if hours and rem:
        return f"{hours}h {rem}m"
    if hours:
        return f"{hours}h"
    return f"{rem}m"


def _coalesce_str(primary: str | None, fallback: Any) -> str | None:
    if (primary or "").strip():
        return primary
    return _non_empty_str(fallback)


def _json_is_empty(raw: str | None) -> bool:
    if not raw:
        return True
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return True
    if parsed is None:
        return True
    if isinstance(parsed, (list, dict, str)):
        return len(parsed) == 0
    return False


def merge_ratings_json(existing_json: str | None, mdblist_map: dict[str, dict[str, Any]]) -> str | None:
    base: dict[str, Any] = {}
    if existing_json:
        try:
            parsed = json.loads(existing_json)
            if isinstance(parsed, dict):
                base = parsed
        except json.JSONDecodeError:
            pass
    for src, payload in mdblist_map.items():
        key = "mal" if src == "myanimelist" else src
        cur = base.get(key)
        if not isinstance(cur, dict) or not cur:
            base[key] = {k: v for k, v in payload.items() if v is not None}
            continue
        for field in ("rating", "votes", "score"):
            if cur.get(field) is None and payload.get(field) is not None:
                cur[field] = payload[field]
        base[key] = cur
    return json.dumps(base, ensure_ascii=False) if base else existing_json


def merge_genres_json(existing_json: str | None, genres: Any) -> str | None:
    if not _json_is_empty(existing_json):
        return existing_json
    if genres is None:
        return existing_json
    return json.dumps(genres, ensure_ascii=False)


def merge_ids_json(existing_json: str | None, mdblist_ids: dict[str, Any] | None) -> str | None:
    base: dict[str, Any] = {}
    if existing_json:
        try:
            parsed = json.loads(existing_json)
            if isinstance(parsed, dict):
                base = parsed
        except json.JSONDecodeError:
            pass
    if mdblist_ids:
        for key, val in mdblist_ids.items():
            if key not in base or base[key] in (None, ""):
                base[key] = val
    return json.dumps(base, ensure_ascii=False) if base else existing_json


def _release_date_fallback(item: dict[str, Any]) -> str | None:
    released = _non_empty_str(item.get("released"))
    if released:
        return released
    year = item.get("year")
    if year is not None:
        try:
            return str(int(year))
        except (TypeError, ValueError):
            pass
    return None


def build_extras_json(item: dict[str, Any]) -> str | None:
    extras: dict[str, Any] = {}
    for key in (
        "certification",
        "age_rating",
        "commonsense",
        "commonsense_media",
        "awards",
        "budget",
        "revenue",
        "production_companies",
        "language",
        "spoken_language",
        "type",
        "year",
    ):
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, (list, dict)) and len(val) == 0:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        extras[key] = val
    score = item.get("score")
    score_average = item.get("score_average")
    if score_average is not None and score_average != score:
        extras["score_average"] = score_average
    return json.dumps(extras, ensure_ascii=False) if extras else None


def mdblist_batch(
    api_key: str,
    provider: str,
    media_type: str,
    ids: list[str | int],
    *,
    timeout: float = 120.0,
    retries: int = 3,
) -> list[dict[str, Any]]:
    return mdblist_batch_response(
        api_key,
        provider,
        media_type,
        ids,
        timeout=timeout,
        retries=retries,
    ).items


def mdblist_batch_response(
    api_key: str,
    provider: str,
    media_type: str,
    ids: list[str | int],
    *,
    timeout: float = 120.0,
    retries: int = 3,
) -> MdblistBatchResponse:
    """POST Media Info Batch — one API request for up to 200 ids (see mdblist.apib)."""
    if not ids or not api_key:
        return MdblistBatchResponse(items=[], rate_limit=MdblistRateLimitState())
    url = f"{MDBLIST_BASE}/{provider}/{media_type}/?apikey={api_key}"
    body = json.dumps({"ids": ids}).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                rate_limit = _parse_rate_limit_headers(resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
            items = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            return MdblistBatchResponse(items=items, rate_limit=rate_limit)
        except urllib.error.HTTPError as exc:
            last_err = exc
            state = _parse_rate_limit_headers(exc.headers)
            if exc.code == 429:
                detail = ""
                try:
                    payload = json.loads(exc.read().decode("utf-8"))
                    if isinstance(payload, dict):
                        detail = str(payload.get("error") or payload.get("detail") or "").strip()
                except Exception:
                    detail = ""
                message = detail or "Daily MDBList API limit exceeded"
                if state.retry_after:
                    message = f"{message} (retry after {state.retry_after}s)"
                raise MdblistRateLimitError(message, state=state) from exc
            if exc.code in (502, 503) and attempt + 1 < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as exc:
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err
    return MdblistBatchResponse(items=[], rate_limit=MdblistRateLimitState())


def build_batch_plan(rows: list[tuple[int, str, str | None]]) -> dict[str, list[tuple[int, str, str | int]]]:
    buckets: dict[str, list[tuple[int, str, str | int]]] = {}
    for simkl_id, catalog, ids_json in rows:
        if not ids_json:
            continue
        try:
            ids = json.loads(ids_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(ids, dict):
            continue
        target = lookup_target(catalog, ids)
        if target is None:
            continue
        provider, media_type, lookup_id = target
        bucket_key = f"{provider}|{media_type}"
        buckets.setdefault(bucket_key, []).append((simkl_id, catalog, lookup_id))
    return buckets


def apply_mdblist_item(conn: sqlite3.Connection, simkl_id: int, catalog: str, item: dict[str, Any]) -> None:
    """Write MDBList ranking/scoring fields only — Simkl CDN owns display metadata."""
    mdblist_ratings = mdblist_ratings_to_map(item.get("ratings"))
    streams = item.get("streams")
    providers = item.get("watch_providers")
    extras_json = build_extras_json(item)
    mdblist_score = item.get("score")
    if mdblist_score is None:
        mdblist_score = item.get("score_average")

    ratings_json = None
    if mdblist_ratings:
        cur = conn.execute(
            "SELECT ratings_json FROM simkl_rows WHERE simkl_id = ? AND catalog = ?",
            (simkl_id, catalog),
        ).fetchone()
        ratings_json = merge_ratings_json(cur[0] if cur else None, mdblist_ratings)

    conn.execute(
        """
        UPDATE simkl_rows SET
          ratings_json = COALESCE(?, ratings_json),
          mdblist_score = COALESCE(?, mdblist_score),
          streams_json = COALESCE(?, streams_json),
          watch_providers_json = COALESCE(?, watch_providers_json),
          extras_json = COALESCE(?, extras_json)
        WHERE simkl_id = ? AND catalog = ?
        """,
        (
            ratings_json,
            mdblist_score,
            json.dumps(streams, ensure_ascii=False) if streams is not None else None,
            json.dumps(providers, ensure_ascii=False) if providers is not None else None,
            extras_json,
            simkl_id,
            catalog,
        ),
    )


def enrich_simkl_database(
    conn: sqlite3.Connection,
    api_key: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_s: float = DEFAULT_SLEEP,
    log=None,
) -> dict[str, int]:
    if not api_key:
        return {"rows_total": 0, "batch_requests": 0, "items_matched": 0, "rows_skipped_no_id": 0, "skipped_no_key": 1}

    ensure_mdblist_columns(conn)
    rows = conn.execute("SELECT simkl_id, catalog, ids_json FROM simkl_rows").fetchall()
    buckets = build_batch_plan(rows)

    stats = {"rows_total": len(rows), "batch_requests": 0, "items_matched": 0, "rows_skipped_no_id": 0}
    eligible = sum(len({(s, c) for s, c, _ in bucket_rows}) for bucket_rows in buckets.values())
    stats["rows_skipped_no_id"] = len(rows) - eligible

    enriched_at = _now_iso()
    rate_limited = False
    for bucket_key, bucket_rows in buckets.items():
        if rate_limited:
            break
        provider, media_type = bucket_key.split("|", 1)
        seen_ids: list[str | int] = []
        id_to_rows: dict[str, list[tuple[int, str]]] = {}
        for simkl_id, catalog, lookup_id in bucket_rows:
            key = _lookup_key(lookup_id)
            if key not in id_to_rows:
                seen_ids.append(lookup_id)
                id_to_rows[key] = []
            id_to_rows[key].append((simkl_id, catalog))

        for i in range(0, len(seen_ids), batch_size):
            chunk = seen_ids[i : i + batch_size]
            if log:
                log(f"MDBList batch {provider}/{media_type} ({len(chunk)} ids)")
            try:
                response = mdblist_batch_response(api_key, provider, media_type, chunk)
            except MdblistRateLimitError as exc:
                if log:
                    log(f"MDBList daily limit reached: {exc}")
                rate_limited = True
                break
            items = response.items
            stats["batch_requests"] += 1

            returned_keys: set[str] = set()
            for item in items:
                ids_obj = item.get("ids") or {}
                if not isinstance(ids_obj, dict):
                    continue
                match_id = response_lookup_id(provider, ids_obj)
                if match_id is None:
                    continue
                match_key = _lookup_key(match_id)
                returned_keys.add(match_key)
                for simkl_id, catalog in id_to_rows.get(match_key, []):
                    apply_mdblist_item(conn, simkl_id, catalog, item)
                    stats["items_matched"] += 1

            conn.commit()
            time.sleep(sleep_s)

    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("mdblist_enrich", json.dumps({**stats, "enriched_at": enriched_at}, ensure_ascii=False)),
    )
    return stats
