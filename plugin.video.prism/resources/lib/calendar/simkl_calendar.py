"""Simkl CDN airing calendars for movies, TV, and anime."""
from __future__ import annotations

import datetime
import json
import os
import time
from typing import Any

import xbmcgui

from resources.lib.common import tools
from resources.lib.database.simkl_discover.mdblist_enrich import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SLEEP,
    MdblistRateLimitError,
    lookup_target,
    mdblist_batch_response,
    mdblist_ratings_to_map,
    resolve_mdblist_api_key,
    response_lookup_id,
)
from resources.lib.discover.cdn_store import get_rows_by_ids
from resources.lib.indexers.simkl_cdn import SimklCDN
from resources.lib.modules.globals import g
from resources.lib.simkl.images import simkl_image_url

CACHE_SECONDS = 86400
BUNDLE_CACHE_VERSION = 2
WEEKLY_CACHE_VERSION = 4
DEFAULT_WINDOW_DAYS = 7  # Sunday–Saturday calendar week containing today.
CALENDAR_MDBLIST_BATCH_SIZE = DEFAULT_BATCH_SIZE  # MDBList allows up to 200 ids per POST
CALENDAR_MDBLIST_SLEEP = DEFAULT_SLEEP


def merge_v2_calendar_rows(
    calendar: list[dict[str, Any]],
    metadata: dict[str, Any],
    catalog: str,
) -> list[dict[str, Any]]:
    """Join Simkl v2 calendar airings with deduplicated show metadata."""
    rows: list[dict[str, Any]] = []
    for entry in calendar:
        if not isinstance(entry, dict):
            continue
        simkl_id = entry.get("simkl_id")
        if simkl_id is None:
            continue
        try:
            sid = int(simkl_id)
        except (TypeError, ValueError):
            continue

        meta = metadata.get(str(sid)) or metadata.get(sid) or {}
        if not isinstance(meta, dict):
            meta = {}

        ids = dict(meta.get("ids") or {})
        if ids.get("simkl_id") is None:
            ids["simkl_id"] = sid

        row = dict(meta)
        row["ids"] = ids
        row["simkl_id"] = sid
        if entry.get("date"):
            row["date"] = entry["date"]
        if entry.get("episode"):
            row["episode"] = entry["episode"]
        if entry.get("finale_type") is not None:
            row["finale_type"] = entry["finale_type"]
        if not row.get("release_date") and meta.get("release_date"):
            row["release_date"] = meta["release_date"]
        rows.append(row)

    g.log(f"Simkl calendar v2: merged {len(rows)} {catalog} rows from CDN", "debug")
    return rows


def prefetch_calendars_enabled() -> bool:
    return True

_CALENDAR_RATING_SOURCES = (
    "simkl",
    "imdb",
    "trakt",
    "tmdb",
    "mal",
)


def _cache_dir() -> str:
    path = tools.translate_path(os.path.join(g.ADDON_USERDATA_PATH, "calendar"))
    tools.makedirs(path, exist_ok=True)
    return path


def _cache_file(catalog: str) -> str:
    return os.path.join(_cache_dir(), f"{catalog}.json")


def _weekly_cache_file(catalog: str) -> str:
    return os.path.join(_cache_dir(), f"{catalog}_weekly.json")


def _current_week_start() -> str:
    start, _ = _week_window_bounds()
    return start.date().isoformat()


def _load_weekly_cache(
    catalog: str,
    *,
    _return_reason: bool = False,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]] | None | tuple[
    tuple[list[dict[str, Any]], dict[int, dict[str, Any]]] | None,
    str,
]:
    path = _weekly_cache_file(catalog)
    if not os.path.exists(path):
        result: tuple[list[dict[str, Any]], dict[int, dict[str, Any]]] | None = None
        reason = "missing_file"
        return (result, reason) if _return_reason else result

    last_error = "unknown"
    for attempt in (1, 2):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                last_error = "payload_not_dict"
                break
            if payload.get("week_start") != _current_week_start():
                last_error = "week_start_mismatch"
                break
            if payload.get("cache_version") != WEEKLY_CACHE_VERSION:
                last_error = "cache_version_mismatch"
                break
            filtered_rows = payload.get("filtered_rows")
            metadata_raw = payload.get("metadata_cache")
            if not isinstance(filtered_rows, list) or not isinstance(metadata_raw, dict):
                last_error = "invalid_rows_or_metadata"
                break
            metadata_cache: dict[int, dict[str, Any]] = {}
            for key, value in metadata_raw.items():
                if not isinstance(value, dict):
                    continue
                try:
                    metadata_cache[int(key)] = value
                except (TypeError, ValueError):
                    continue
            result = filtered_rows, metadata_cache
            reason = "ok"
            return (result, reason) if _return_reason else result
        except json.JSONDecodeError as exc:
            last_error = f"json_decode_error_attempt_{attempt}:{exc.msg}"
            if attempt == 1:
                time.sleep(0.05)
                continue
            break
        except (OSError, TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}:{exc}"
            break

    result = None
    return (result, last_error) if _return_reason else result


def _save_weekly_cache(
    catalog: str,
    filtered_rows: list[dict[str, Any]],
    metadata_cache: dict[int, dict[str, Any]],
) -> None:
    path = _weekly_cache_file(catalog)
    payload = {
        "timestamp": datetime.datetime.now().timestamp(),
        "week_start": _current_week_start(),
        "cache_version": WEEKLY_CACHE_VERSION,
        "filtered_rows": filtered_rows,
        "metadata_cache": {str(simkl_id): item for simkl_id, item in metadata_cache.items()},
    }
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, path)
    except OSError:
        g.log_stacktrace()
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def weekly_cache_warm() -> bool:
    return all(_load_weekly_cache(catalog) is not None for catalog in ("movie", "tv", "anime"))


def _load_file_cache(catalog: str) -> list[dict[str, Any]] | None:
    path = _cache_file(catalog)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        ts = float(payload.get("timestamp") or 0)
        if datetime.datetime.now().timestamp() - ts > CACHE_SECONDS:
            return None
        if payload.get("version") == BUNDLE_CACHE_VERSION:
            calendar = payload.get("calendar")
            metadata = payload.get("metadata")
            if isinstance(calendar, list) and isinstance(metadata, dict):
                return merge_v2_calendar_rows(calendar, metadata, catalog)
        rows = payload.get("items")
        if isinstance(rows, list):
            return rows
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _save_file_cache(catalog: str, bundle: dict[str, Any]) -> None:
    path = _cache_file(catalog)
    payload = {
        "version": BUNDLE_CACHE_VERSION,
        "timestamp": datetime.datetime.now().timestamp(),
        "calendar": bundle.get("calendar") or [],
        "metadata": bundle.get("metadata") or {},
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        g.log_stacktrace()


def fetch_calendar_bundle(catalog: str, *, force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        path = _cache_file(catalog)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("version") == BUNDLE_CACHE_VERSION:
                    ts = float(payload.get("timestamp") or 0)
                    if datetime.datetime.now().timestamp() - ts <= CACHE_SECONDS:
                        calendar = payload.get("calendar")
                        metadata = payload.get("metadata")
                        if isinstance(calendar, list) and isinstance(metadata, dict):
                            g.log(
                                f"Simkl calendar: using cached v2 {catalog} bundle "
                                f"({len(calendar)} airings)",
                                "info",
                            )
                            return {"calendar": calendar, "metadata": metadata}
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

    cdn = SimklCDN()
    bundle = cdn.calendar_bundle(catalog)
    if bundle.get("calendar"):
        _save_file_cache(catalog, bundle)
        g.log(
            f"Simkl calendar: fetched v2 {catalog} bundle "
            f"({len(bundle['calendar'])} airings, {len(bundle.get('metadata') or {})} metadata)",
            "info",
        )
    return bundle


def fetch_calendar_rows(catalog: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    bundle = fetch_calendar_bundle(catalog, force_refresh=force_refresh)
    return merge_v2_calendar_rows(
        bundle.get("calendar") or [],
        bundle.get("metadata") or {},
        catalog,
    )


def _parse_air_datetime(raw: str | None) -> datetime.datetime | None:
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    if text == "0001-01-01T00:00:00Z":
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _row_air_datetime(row: dict[str, Any]) -> datetime.datetime | None:
    return _parse_air_datetime(row.get("date")) or _parse_air_datetime(row.get("release_date"))


def _has_airing_info(row: dict[str, Any]) -> bool:
    return _row_air_datetime(row) is not None


def _week_window_bounds() -> tuple[datetime.datetime, datetime.datetime]:
    """Local timezone: current calendar week (Sunday 00:00 through Saturday 23:59)."""
    now = datetime.datetime.now().astimezone()
    today = now.date()
    # Python weekday(): Mon=0 … Sun=6 → days back to most recent Sunday (including today).
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - datetime.timedelta(days=days_since_sunday)
    start = datetime.datetime.combine(week_start, datetime.time.min, tzinfo=now.tzinfo)
    end = start + datetime.timedelta(days=DEFAULT_WINDOW_DAYS)
    return start, end


def _week_range_label() -> str:
    start, end = _week_window_bounds()
    last = end - datetime.timedelta(days=1)
    if start.year != last.year:
        return f"Sun {start.strftime('%b %d, %Y')} – Sat {last.strftime('%b %d, %Y')}"
    if start.month != last.month:
        return f"Sun {start.strftime('%b %d')} – Sat {last.strftime('%b %d, %Y')}"
    return f"Sun {start.strftime('%b %d')} – Sat {last.strftime('%d, %Y')}"


def _row_in_week_window(catalog: str, row: dict[str, Any], start: datetime.datetime, end: datetime.datetime) -> bool:
    if catalog == "movie":
        day = _parse_date_only(row.get("release_date"))
        if day is None:
            return False
        return start.date() <= day < end.date()

    dt = _row_air_datetime(row)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    local_dt = dt.astimezone()
    return start <= local_dt < end


def filter_coming_soon_rows(
    rows: list[dict[str, Any]],
    *,
    catalog: str,
) -> list[dict[str, Any]]:
    """Keep calendar rows with future air/release dates, soonest first.

    TV/anime calendar files list upcoming episodes; dedupe to one row per show
    (nearest upcoming air date). Movies use release_date.
    """
    now = datetime.datetime.now().astimezone()
    today = now.date()
    upcoming: list[tuple[datetime.datetime, dict[str, Any]]] = []
    best_by_show: dict[int, tuple[datetime.datetime, dict[str, Any]]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        if catalog == "movie":
            day = _parse_date_only(row.get("release_date") or row.get("date"))
            if day is None or day < today:
                continue
            sort_dt = datetime.datetime.combine(day, datetime.time.min, tzinfo=now.tzinfo)
            upcoming.append((sort_dt, row))
            continue

        dt = _row_air_datetime(row)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        local_dt = dt.astimezone(now.tzinfo)
        if local_dt <= now:
            continue

        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is not None:
            existing = best_by_show.get(simkl_id)
            if existing is None or local_dt < existing[0]:
                best_by_show[simkl_id] = (local_dt, row)
        else:
            upcoming.append((local_dt, row))

    if catalog != "movie":
        upcoming.extend(best_by_show.values())

    upcoming.sort(key=lambda pair: pair[0])
    filtered = [row for _, row in upcoming]
    g.log(f"Simkl coming soon: {len(filtered)} {catalog} rows", "info")
    return filtered


def filter_calendar_rows(
    rows: list[dict[str, Any]],
    *,
    catalog: str = "anime",
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    start, end = _week_window_bounds()
    if window_days != DEFAULT_WINDOW_DAYS:
        end = start + datetime.timedelta(days=max(1, window_days))
    filtered: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict) or not _has_airing_info(row):
            continue
        if not _row_in_week_window(catalog, row, start, end):
            continue
        filtered.append(row)

    filtered.sort(
        key=lambda item: (_row_air_datetime(item) or datetime.datetime.max.replace(tzinfo=datetime.timezone.utc))
    )
    g.log(
        f"Simkl calendar: Sun–Sat week {_week_range_label()} — {len(filtered)} {catalog} rows",
        "info",
    )
    return filtered


def _parse_date_only(raw: str | None) -> datetime.date | None:
    if not raw:
        return None
    text = str(raw).strip()[:10]
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_countdown_from_date(day: datetime.date) -> str:
    today = datetime.date.today()
    delta = (day - today).days
    if delta < 0:
        return "Released"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"{delta}d"


def _format_air_display(catalog: str, row: dict[str, Any]) -> dict[str, str]:
    """Movies use release_date (date-only). TV/anime use episode air datetime."""
    if catalog == "movie":
        release_day = _parse_date_only(row.get("release_date"))
        if release_day is not None:
            weekday = datetime.datetime.combine(release_day, datetime.time.min).strftime("%A")
            date_fmt = release_day.strftime("%d %b")
            return {
                "raw_time": "",
                "raw_day": weekday,
                "raw_date_formatted": date_fmt,
                "air_date_label": f"{weekday} {date_fmt}",
                "raw_countdown": _format_countdown_from_date(release_day),
            }

    air_raw = row.get("date") or row.get("release_date")
    raw_day = _format_day(air_raw)
    raw_date = _format_date(air_raw)
    return {
        "raw_time": _format_time(air_raw),
        "raw_day": raw_day,
        "raw_date_formatted": raw_date,
        "air_date_label": f"{raw_day} {raw_date}" if raw_day != "TBA" and raw_date != "TBA" else raw_day,
        "raw_countdown": _format_countdown(air_raw),
    }


def _format_day(episode_date: str | None) -> str:
    dt = _parse_air_datetime(episode_date)
    if dt is None:
        return "TBA"
    try:
        return dt.astimezone().strftime("%A")
    except ValueError:
        return "TBA"


def _format_date(episode_date: str | None) -> str:
    dt = _parse_air_datetime(episode_date)
    if dt is None:
        return "TBA"
    try:
        return dt.astimezone().strftime("%d %b")
    except ValueError:
        return "TBA"


def _format_time(episode_date: str | None) -> str:
    dt = _parse_air_datetime(episode_date)
    if dt is None:
        return "TBA"
    try:
        return dt.astimezone().strftime("%I:%M %p")
    except ValueError:
        return "TBA"


def _format_countdown(episode_date: str | None) -> str:
    dt = _parse_air_datetime(episode_date)
    if dt is None:
        return "TBA"
    try:
        now = datetime.datetime.now(dt.tzinfo or datetime.timezone.utc)
        diff = dt - now
        if diff.total_seconds() < 0:
            return "Released"
        total_minutes = int(diff.total_seconds() / 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 24:
            days = hours // 24
            hours = hours % 24
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"
    except ValueError:
        return "TBA"


def _display_rating(value: Any) -> str:
    if value in (None, "", 0, 0.0):
        return "-"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num <= 0:
        return "-"
    if num > 10:
        return str(int(round(num)))
    if num == int(num):
        return str(int(num))
    return str(round(num, 1))


def _simkl_rating(row: dict[str, Any]) -> str:
    ratings = row.get("ratings") or {}
    simkl = ratings.get("simkl") if isinstance(ratings, dict) else None
    if isinstance(simkl, dict):
        return _display_rating(simkl.get("rating") or simkl.get("score"))
    return "-"


def _format_genres(genres: Any) -> str:
    if not genres:
        return ""
    if isinstance(genres, str):
        return genres.strip()
    if isinstance(genres, list):
        names: list[str] = []
        for entry in genres:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("title")
                if name:
                    names.append(str(name))
            elif entry:
                names.append(str(entry))
        return ", ".join(names)
    return str(genres)


def _lookup_key(provider: str, lookup_id: str | int) -> str:
    if provider == "imdb":
        return str(lookup_id).strip().lower()
    return str(lookup_id).strip()


def _simkl_ids_from_rows(rows: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        raw_ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        simkl_id = raw_ids.get("simkl_id") or raw_ids.get("simkl")
        try:
            sid = int(simkl_id)
        except (TypeError, ValueError):
            continue
        if sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def _fetch_discover_db_rows(catalog: str, rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Load cached CDN discover rows for calendar enrichment."""
    simkl_ids = _simkl_ids_from_rows(rows)
    if not simkl_ids:
        return {}
    return get_rows_by_ids(catalog, simkl_ids)


def _discover_row_to_enrichment(db_row: dict[str, Any]) -> dict[str, Any]:
    """Shape discover DB row like an MDBList batch item for calendar formatting."""
    genres = None
    if db_row.get("genres_json"):
        try:
            genres = json.loads(db_row["genres_json"])
        except json.JSONDecodeError:
            genres = None

    ratings = None
    if db_row.get("ratings_json"):
        try:
            ratings = json.loads(db_row["ratings_json"])
        except json.JSONDecodeError:
            ratings = None

    ratings_list: list[dict[str, Any]] = []
    if isinstance(ratings, dict):
        for source, payload in ratings.items():
            if not isinstance(payload, dict):
                continue
            ratings_list.append(
                {
                    "source": source,
                    "value": payload.get("rating"),
                    "score": payload.get("score"),
                    "votes": payload.get("votes"),
                }
            )

    score_average = db_row.get("mdblist_score")
    return {
        "title": db_row.get("title"),
        "poster": db_row.get("poster"),
        "overview": db_row.get("overview"),
        "description": db_row.get("overview"),
        "genres": genres,
        "ratings": ratings_list,
        "score_average": score_average,
        "score": score_average,
    }


def _enrichment_has_plot(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    for key in ("overview", "plot", "description"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return True
    return False


def _merge_ratings_lists(
    base: Any,
    extra: Any,
) -> list[dict[str, Any]]:
    merged_map = mdblist_ratings_to_map(base if isinstance(base, list) else None)
    for source, payload in mdblist_ratings_to_map(extra if isinstance(extra, list) else None).items():
        merged_map.setdefault(source, payload)
    return [
        {
            "source": source,
            "value": payload.get("rating"),
            "score": payload.get("score"),
            "votes": payload.get("votes"),
        }
        for source, payload in merged_map.items()
    ]


def _ratings_dict_to_list(ratings: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source, payload in ratings.items():
        if not isinstance(payload, dict):
            continue
        out.append(
            {
                "source": source,
                "value": payload.get("rating") or payload.get("score"),
                "score": payload.get("score") or payload.get("rating"),
                "votes": payload.get("votes"),
            }
        )
    return out


def _merge_enrichment(
    base: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base or {})
    if not extra:
        return merged
    for key in ("title", "poster", "overview", "plot", "description", "score_average", "score"):
        if not merged.get(key) and extra.get(key):
            merged[key] = extra[key]
    if not merged.get("genres") and extra.get("genres"):
        merged["genres"] = extra["genres"]
    if extra.get("ratings"):
        if merged.get("ratings"):
            merged["ratings"] = _merge_ratings_lists(merged["ratings"], extra["ratings"])
        else:
            merged["ratings"] = extra["ratings"]
    return merged


def _mdblist_item_to_enrichment(item: dict[str, Any]) -> dict[str, Any]:
    overview = item.get("overview") or item.get("description") or item.get("plot")
    score = item.get("score_average") or item.get("score")
    return {
        "title": item.get("title"),
        "poster": item.get("poster"),
        "overview": overview,
        "plot": overview,
        "description": item.get("description") or overview,
        "genres": item.get("genres"),
        "ratings": item.get("ratings"),
        "score_average": score,
        "score": score,
    }


def _metadata_cache_has_mdblist_data(metadata_cache: dict[int, dict[str, Any]]) -> int:
    return sum(
        1
        for item in metadata_cache.values()
        if isinstance(item, dict) and (item.get("ratings") or item.get("score_average"))
    )


def _should_save_weekly_cache(
    metadata_cache: dict[int, dict[str, Any]],
    *,
    mdblist_targets: int,
) -> bool:
    api_key = resolve_mdblist_api_key()
    if not api_key or mdblist_targets <= 0:
        return True
    enriched = _metadata_cache_has_mdblist_data(metadata_cache)
    if enriched > 0:
        return True
    g.log(
        "Simkl calendar: skipping weekly cache save — MDBList returned no ratings "
        f"for {mdblist_targets} lookup targets (likely rate limited)",
        "warning",
    )
    return False


def _build_metadata_cache(
    catalog: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], int]:
    """Gap-fill overview and MDBList ratings (v2 CDN supplies titles, art, and Simkl ratings)."""
    discover_rows = _fetch_discover_db_rows(catalog, rows)
    mdblist_map, mdblist_targets = _fetch_mdblist_by_simkl_id(catalog, rows)
    row_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is not None:
            row_by_id[simkl_id] = row

    cache: dict[int, dict[str, Any]] = {}

    for simkl_id in _simkl_ids_from_rows(rows):
        item: dict[str, Any] = {}
        row = row_by_id.get(simkl_id)
        if row:
            row_ratings = row.get("ratings")
            if isinstance(row_ratings, dict) and row_ratings:
                item["ratings"] = _ratings_dict_to_list(row_ratings)
        if simkl_id in discover_rows:
            item = _merge_enrichment(item, _discover_row_to_enrichment(discover_rows[simkl_id]))
        if simkl_id in mdblist_map:
            item = _merge_enrichment(item, _mdblist_item_to_enrichment(mdblist_map[simkl_id]))
        cache[simkl_id] = item

    filled = sum(1 for sid in cache if _enrichment_has_plot(cache.get(sid)))
    rated = sum(
        1
        for sid in cache
        if isinstance(cache.get(sid), dict) and (cache[sid].get("ratings") or cache[sid].get("score_average"))
    )
    discover_hits = len(discover_rows)
    discover_with_plot = sum(
        1
        for row in discover_rows.values()
        if _enrichment_has_plot(_discover_row_to_enrichment(row))
    )
    g.log(
        f"Simkl calendar: plot/overview text for {filled}/{len(cache)} titles "
        f"({len(rows)} calendar rows this week); ratings for {rated}/{len(cache)} "
        f"(CDN / discover DB / MDBList gap-fill)",
        "info",
    )
    return cache, mdblist_targets


def _simkl_ids_for_row(row: dict[str, Any]) -> int | None:
    ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
    simkl_id = ids.get("simkl_id") or ids.get("simkl") or row.get("simkl_id")
    try:
        return int(simkl_id)
    except (TypeError, ValueError):
        return None


def _fetch_mdblist_by_simkl_id(
    catalog: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], int]:
    api_key = resolve_mdblist_api_key()
    if not api_key:
        g.log("Simkl calendar: MDBList API key not set — using discover DB / Simkl CDN only", "info")
        return {}, 0
    if not rows:
        return {}, 0

    lookup_to_simkl: dict[tuple[str, str, str], list[int]] = {}
    lookup_ids: dict[tuple[str, str, str], str | int] = {}

    for row in rows:
        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is None:
            continue
        ids = dict(row.get("ids") or {})
        if ids.get("simkl_id") is None and ids.get("simkl") is not None:
            ids["simkl_id"] = ids.get("simkl")
        target = lookup_target(catalog, ids)
        if target is None:
            continue
        provider, media_type, lookup_id = target
        lookup = _lookup_key(provider, lookup_id)
        bucket_key = (provider, media_type, lookup)
        lookup_to_simkl.setdefault(bucket_key, []).append(simkl_id)
        lookup_ids[bucket_key] = lookup_id

    mdblist_targets = len(lookup_to_simkl)
    if mdblist_targets <= 0:
        return {}, 0

    buckets: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for (provider, media_type, lookup), simkl_ids in lookup_to_simkl.items():
        bucket_key = (provider, media_type)
        buckets.setdefault(bucket_key, {})[lookup] = {
            "lookup_id": lookup_ids[(provider, media_type, lookup)],
            "simkl_ids": simkl_ids,
        }

    total_batches = sum(
        (len(id_map) + CALENDAR_MDBLIST_BATCH_SIZE - 1) // CALENDAR_MDBLIST_BATCH_SIZE
        for id_map in buckets.values()
    )
    g.log(
        f"Simkl calendar: MDBList plan — {mdblist_targets} titles in {total_batches} batch "
        f"request(s) (POST /{{provider}}/{{type}}, up to {CALENDAR_MDBLIST_BATCH_SIZE} ids each)",
        "info",
    )

    out: dict[int, dict[str, Any]] = {}
    rate_limited = False
    for (provider, media_type), id_map in buckets.items():
        if rate_limited:
            break
        id_list = [entry["lookup_id"] for entry in id_map.values()]
        for i in range(0, len(id_list), CALENDAR_MDBLIST_BATCH_SIZE):
            chunk = id_list[i : i + CALENDAR_MDBLIST_BATCH_SIZE]
            batch_no = (i // CALENDAR_MDBLIST_BATCH_SIZE) + 1
            batch_total = (len(id_list) + CALENDAR_MDBLIST_BATCH_SIZE - 1) // CALENDAR_MDBLIST_BATCH_SIZE
            try:
                response = mdblist_batch_response(api_key, provider, media_type, chunk)
            except MdblistRateLimitError as exc:
                state = exc.state
                g.log(f"Simkl calendar: {exc}", "warning")
                if state.remaining is not None:
                    g.log(f"Simkl calendar: MDBList requests remaining today: {state.remaining}", "warning")
                if state.retry_after:
                    g.log(
                        f"Simkl calendar: MDBList quota resets in {state.retry_after}s "
                        "(see X-RateLimit-Reset / Retry-After headers)",
                        "warning",
                    )
                rate_limited = True
                break

            results = response.items
            if response.rate_limit.remaining is not None:
                g.log(
                    f"Simkl calendar: MDBList batch {batch_no}/{batch_total} "
                    f"({provider}/{media_type}, {len(chunk)} ids) — "
                    f"{response.rate_limit.remaining} API requests remaining today",
                    "debug",
                )
            else:
                g.log(
                    f"Simkl calendar: MDBList batch {batch_no}/{batch_total} "
                    f"({provider}/{media_type}, {len(chunk)} ids, {len(results)} matched)",
                    "debug",
                )

            by_lookup: dict[str, dict[str, Any]] = {}
            for item in results:
                if not isinstance(item, dict):
                    continue
                ids_obj = item.get("ids") if isinstance(item.get("ids"), dict) else {}
                response_id = response_lookup_id(provider, ids_obj)
                if response_id is not None:
                    by_lookup[_lookup_key(provider, response_id)] = item

            for lookup, entry in id_map.items():
                item = by_lookup.get(lookup)
                if not item:
                    continue
                for simkl_id in entry["simkl_ids"]:
                    out[int(simkl_id)] = item

            if i + CALENDAR_MDBLIST_BATCH_SIZE < len(id_list):
                time.sleep(CALENDAR_MDBLIST_SLEEP)

    g.log(f"Simkl calendar: MDBList matched {len(out)}/{mdblist_targets} titles", "info")
    return out, mdblist_targets


def _resolve_poster(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None,
) -> str:
    if enrichment:
        mdb_poster = enrichment.get("poster")
        if mdb_poster:
            text = str(mdb_poster).strip()
            if text.startswith("http://") or text.startswith("https://"):
                return text
            poster = simkl_image_url(text, kind="posters")
            if poster:
                return poster

    poster = simkl_image_url(row.get("poster"), kind="posters", placeholder=True)
    return poster or ""


def _rating_properties(
    row: dict[str, Any],
    mdblist_item: dict[str, Any] | None,
) -> dict[str, str]:
    props: dict[str, str] = {"rating_simkl": _simkl_rating(row)}

    row_ratings = row.get("ratings") if isinstance(row.get("ratings"), dict) else {}
    for source in _CALENDAR_RATING_SOURCES:
        if source == "simkl":
            continue
        payload = row_ratings.get(source) or row_ratings.get("myanimelist" if source == "mal" else "")
        if isinstance(payload, dict):
            rating = _display_rating(payload.get("rating") or payload.get("score"))
            if rating != "-":
                props[f"rating_{source}"] = rating

    mdblist_map = mdblist_ratings_to_map((mdblist_item or {}).get("ratings"))
    score_average = (mdblist_item or {}).get("score_average") or (mdblist_item or {}).get("score")

    for source in _CALENDAR_RATING_SOURCES:
        if source == "simkl":
            entry = mdblist_map.get("simkl")
            if entry and props.get("rating_simkl", "-") == "-":
                props["rating_simkl"] = _display_rating(entry.get("rating") or entry.get("score"))
            continue
        entry = mdblist_map.get(source)
        if entry and props.get(f"rating_{source}", "-") == "-":
            props[f"rating_{source}"] = _display_rating(entry.get("rating") or entry.get("score"))

    if score_average not in (None, "", 0, 0.0):
        try:
            avg = int(float(score_average))
        except (TypeError, ValueError):
            avg = 0
        props["rating_average"] = str(avg) if avg > 0 else "-"
    else:
        props["rating_average"] = "-"

    for source in _CALENDAR_RATING_SOURCES:
        props.setdefault(f"rating_{source}", "-")

    if props.get("rating_mal") == "-":
        mal_entry = mdblist_map.get("myanimelist")
        if mal_entry:
            props["rating_mal"] = _display_rating(mal_entry.get("rating") or mal_entry.get("score"))

    return props


def _airing_tag(catalog: str, row: dict[str, Any]) -> str:
    if catalog == "movie":
        return "Release"
    episode = row.get("episode") if isinstance(row.get("episode"), dict) else {}
    ep_num = episode.get("episode")
    ep_title = episode.get("title")
    if catalog == "tv":
        season = episode.get("season")
        if season is not None and ep_num is not None:
            tag = f"S{season} E{ep_num}"
        elif ep_num is not None:
            tag = f"E{ep_num}"
        else:
            tag = "Airing"
        if ep_title:
            return f"{tag} — {ep_title}"
        return tag
    if ep_num is not None:
        tag = f"Ep {ep_num}"
        if ep_title:
            return f"{tag} — {ep_title}"
        return tag
    return "Airing"


def _episode_number(catalog: str, row: dict[str, Any]) -> int:
    if catalog == "movie":
        return 1
    episode = row.get("episode") if isinstance(row.get("episode"), dict) else {}
    try:
        return int(episode.get("episode") or 0)
    except (TypeError, ValueError):
        return 0


def format_for_calendar(
    rows: list[dict[str, Any]],
    catalog: str,
    metadata_cache: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    metadata_cache = metadata_cache or {}
    formatted: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        simkl_id = ids.get("simkl_id") or ids.get("simkl")
        simkl_id_int = _simkl_ids_for_row(row)

        title = row.get("title") or ""
        enrichment = metadata_cache.get(simkl_id_int) if simkl_id_int is not None else None
        poster = _resolve_poster(row, enrichment)
        ratings = _rating_properties(row, enrichment)

        plot = ""
        genres = _format_genres(row.get("genres"))
        if enrichment:
            plot = (
                enrichment.get("overview")
                or enrichment.get("plot")
                or enrichment.get("description")
                or ""
            )
            if isinstance(plot, str):
                plot = plot.strip()
            if not genres:
                genres = _format_genres(enrichment.get("genres"))
            if not title:
                title = enrichment.get("title") or title

        if catalog == "anime":
            from resources.lib.simkl.field_map import pick_anime_display_title, resolve_anime_titles_from_source

            title_en, title_romaji = resolve_anime_titles_from_source(row)
            title_info = {
                "title": title or row.get("title"),
                "title_en": title_en,
                "title_romaji": title_romaji,
            }
            prefer_romaji = g.get_int_setting("general.anime.titlelanguage") == 1
            title = pick_anime_display_title(title_info, prefer_romaji=prefer_romaji) or title

        ep_num = _episode_number(catalog, row)
        episode = row.get("episode") if isinstance(row.get("episode"), dict) else {}
        air_fields = _format_air_display(catalog, row)
        avg = ratings.get("rating_average", "-")
        item = {
            "release_title": title,
            "title": title,
            "catalog": catalog,
            "simkl_id": simkl_id or 0,
            "mal_id": ids.get("mal") or 0,
            "poster": poster,
            "airing_tag": _airing_tag(catalog, row),
            "episode_title": episode.get("title") or "",
            "plot": plot,
            "genres": genres,
            "raw_episode": ep_num if catalog != "movie" else 0,
            "rating_average_label": f"{avg}%" if avg not in ("-", "", None) else "-",
            "_raw": row,
            **air_fields,
            **ratings,
        }
        formatted.append(item)

    return formatted


def _build_weekly_calendar(
    catalog: str,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], int]:
    rows = fetch_calendar_rows(catalog)
    filtered = filter_calendar_rows(rows, catalog=catalog, window_days=window_days)
    metadata_cache, mdblist_targets = _build_metadata_cache(catalog, filtered)
    return filtered, metadata_cache, mdblist_targets


def prefetch_calendar(catalog: str, *, force_refresh: bool = False) -> int:
    """Warm weekly calendar file cache (CDN rows + metadata) for instant open."""
    if not prefetch_calendars_enabled():
        return 0
    g.ensure_addon()
    if not force_refresh:
        cached, _load_reason = _load_weekly_cache(catalog, _return_reason=True)
        if cached is not None:
            g.log(
                f"Simkl calendar prefetch: {catalog} weekly cache already warm ({len(cached[0])} rows)",
                "debug",
            )
            return len(cached[0])

    filtered, metadata_cache, mdblist_targets = _build_weekly_calendar(catalog)
    if _should_save_weekly_cache(metadata_cache, mdblist_targets=mdblist_targets):
        _save_weekly_cache(catalog, filtered, metadata_cache)
    g.log(f"Simkl calendar prefetch: built {catalog} weekly cache ({len(filtered)} rows)", "info")
    return len(filtered)


def _prefetch_lock_path() -> str:
    return os.path.join(_cache_dir(), ".prefetch.lock")


def _try_acquire_prefetch_lock() -> bool:
    path = _prefetch_lock_path()
    try:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                age = time.time() - float(payload.get("timestamp") or 0)
                if age < 900:
                    return False
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"timestamp": time.time()}, handle)
        return True
    except OSError:
        return False


def _release_prefetch_lock() -> None:
    try:
        os.remove(_prefetch_lock_path())
    except OSError:
        pass


def prefetch_all_calendars(*, force_refresh: bool = False) -> int:
    g.ensure_addon()
    if not force_refresh and weekly_cache_warm():
        g.log("Simkl calendar prefetch: all weekly caches warm — skipping", "debug")
        total = 0
        for catalog in ("movie", "tv", "anime"):
            cached = _load_weekly_cache(catalog)
            if cached is not None:
                total += len(cached[0])
        return total
    if not _try_acquire_prefetch_lock():
        g.log("Simkl calendar prefetch: skipped — another prefetch is running", "debug")
        return 0
    total = 0
    try:
        for catalog in ("movie", "tv", "anime"):
            try:
                total += prefetch_calendar(catalog, force_refresh=force_refresh)
            except Exception:
                g.log(f"Simkl calendar prefetch failed for {catalog}", "warning")
                g.log_stacktrace()
    finally:
        _release_prefetch_lock()
    return total


def maybe_prefetch_calendars() -> None:
    """Background warm once per Kodi session when weekly cache is missing."""
    if not prefetch_calendars_enabled():
        return
    if g.get_bool_runtime_setting("calendar.prefetch.session_done"):
        return
    if weekly_cache_warm():
        g.set_runtime_setting("calendar.prefetch.session_done", True)
        return

    import threading

    def _run():
        try:
            g.ensure_addon()
            prefetch_all_calendars()
        except Exception:
            g.log_stacktrace()
        finally:
            g.set_runtime_setting("calendar.prefetch.session_done", True)

    threading.Thread(target=_run, daemon=True, name="prism-calendar-prefetch").start()


def get_calendar_items(catalog: str, *, window_days: int = DEFAULT_WINDOW_DAYS) -> tuple[list[dict[str, Any]], str]:
    week_label = _week_range_label()
    if window_days == DEFAULT_WINDOW_DAYS:
        cached = _load_weekly_cache(catalog)
        if cached is not None:
            filtered_rows, metadata_cache = cached
            items = format_for_calendar(filtered_rows, catalog, metadata_cache)
            g.log(f"Simkl calendar: using prefetched {catalog} weekly cache ({len(items)} items)", "info")
            return items, week_label

    filtered, metadata_cache, mdblist_targets = _build_weekly_calendar(catalog, window_days=window_days)
    if window_days == DEFAULT_WINDOW_DAYS and _should_save_weekly_cache(
        metadata_cache,
        mdblist_targets=mdblist_targets,
    ):
        _save_weekly_cache(catalog, filtered, metadata_cache)
    return format_for_calendar(filtered, catalog, metadata_cache), week_label


def open_calendar(catalog: str) -> None:
    labels = {
        "movie": "Weekly Movie Calendar",
        "tv": "Weekly Show Calendar",
        "anime": "Weekly Anime Calendar",
    }
    use_busy = _load_weekly_cache(catalog) is None
    if use_busy:
        g.show_busy_dialog()
    try:
        items, week_label = get_calendar_items(catalog)
    finally:
        if use_busy:
            g.close_busy_dialog()

    if not items:
        xbmcgui.Dialog().ok(
            g.ADDON_NAME,
            f"No upcoming items found for {labels.get(catalog, catalog)} ({week_label}).",
        )
        g.cancel_directory()
        return

    from resources.lib.database.skinManager import SkinManager
    from resources.lib.gui.windows.calendar_window import CalendarWindow

    xml_file, skin_path = SkinManager().confirm_skin_path("calendar.xml")
    window = CalendarWindow(
        xml_file,
        skin_path,
        calendar_items=items,
        catalog=catalog,
        week_label=week_label,
    )
    selection = window.doModal()

    if not selection:
        g.cancel_directory()
        return

    _navigate_calendar_selection(selection)


def _navigate_calendar_selection(selected: dict[str, Any]) -> None:
    from resources.lib.gui.tvshowMenus import Menus as ShowMenus
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.meta.enrichment import MetaEnrichmentQueue
    from resources.lib.simkl.media_ref import enrich_and_persist, normalize_simkl_item

    catalog = selected.get("catalog") or "tv"
    raw = selected.get("_raw")
    if not isinstance(raw, dict):
        g.cancel_directory()
        return

    sync = normalize_simkl_item(raw, catalog)
    if not sync:
        g.cancel_directory()
        return

    refs = enrich_and_persist(catalog, [sync], force_simkl_meta=True, enrich=False)

    if not refs:
        xbmcgui.Dialog().ok(g.ADDON_NAME, "Could not load item details.")
        g.cancel_directory()
        return

    if fast_path:
        media_type = "movie" if catalog == "movie" else "tvshow"
        MetaEnrichmentQueue.schedule_run_plugin(refs, media_type, reason="calendar", catalog=catalog)

    if catalog == "movie":
        from resources.lib.discover.renderer import discover_list_kwargs
        from resources.lib.meta.list_paint import render_catalog_discover_refs

        render_catalog_discover_refs(
            "movie",
            refs,
            ListBuilder(),
            list_kwargs=discover_list_kwargs(),
            no_paging=True,
            enrichment_reason="calendar",
        )
        g.close_directory(g.CONTENT_MOVIE)
        return

    args = {
        "simkl_id": refs[0].get("simkl_id") or sync.get("simkl_id"),
        "mediatype": g.MEDIA_SHOW,
        "catalog": catalog,
    }
    ShowMenus().show_seasons(args)
    g.close_directory(g.CONTENT_ANIME if catalog == "anime" else g.CONTENT_SHOW)
