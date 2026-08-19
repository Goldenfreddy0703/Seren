"""Simkl CDN airing calendars for movies, TV, and anime."""
from __future__ import annotations

import datetime
import html
import json
import os
import re
import time
import urllib.error
from datetime import date as _calendar_date
from datetime import datetime as _calendar_datetime
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

WEEKLY_CACHE_SECONDS = 7 * 86400  # Match Sunday–Saturday calendar window.
BUNDLE_CACHE_VERSION = 3
WEEKLY_CACHE_VERSION = 8
DEFAULT_WINDOW_DAYS = 7  # Sunday–Saturday calendar week containing today.
CALENDAR_MDBLIST_BATCH_SIZE = DEFAULT_BATCH_SIZE  # MDBList allows up to 200 ids per POST
CALENDAR_MDBLIST_SLEEP = DEFAULT_SLEEP
CALENDAR_IMDB_BATCH_SIZE = 50
CALENDAR_IMDB_SLEEP = 0.25
CALENDAR_SIMKL_API_SLEEP = 0.15


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

_PLACEHOLDER_PLOTS = frozenset(
    {
        "n/a",
        "na",
        "none",
        "null",
        "-",
        "tba",
        "tbd",
        "unknown",
        "not available",
    }
)


_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _normalize_plot_markup(text: str) -> str:
    """Strip MAL/MDBList-style HTML markup from synopsis text for Kodi labels."""
    text = html.unescape(text)
    text = _HTML_BREAK_RE.sub("\n\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _usable_plot_text(value: Any) -> str | None:
    """Return plot text when meaningful; treat MDBList-style placeholders as missing."""
    if not isinstance(value, str):
        return None
    text = _normalize_plot_markup(value)
    if not text:
        return None
    normalized = text.lower().rstrip(".")
    if normalized in _PLACEHOLDER_PLOTS:
        return None
    return text


def _enrichment_plot_text(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    for key in ("overview", "plot", "description"):
        plot = _usable_plot_text(item.get(key))
        if plot:
            return plot
    return ""


def _sanitize_enrichment_plots(item: dict[str, Any] | None) -> dict[str, Any]:
    """Drop placeholder plot strings so later providers can gap-fill."""
    if not item:
        return {}
    cleaned = dict(item)
    plot = _enrichment_plot_text(cleaned)
    for key in ("overview", "plot", "description"):
        if plot:
            cleaned[key] = plot
        else:
            cleaned.pop(key, None)
    return cleaned


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


def _bundle_cache_is_valid(payload: dict[str, Any]) -> bool:
    """CDN bundle cache is valid for the current calendar week (or legacy 7-day TTL)."""
    if payload.get("version") != BUNDLE_CACHE_VERSION:
        return False
    if payload.get("week_start") == _current_week_start():
        return True
    ts = float(payload.get("timestamp") or 0)
    return datetime.datetime.now().timestamp() - ts <= WEEKLY_CACHE_SECONDS


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
                    metadata_cache[int(key)] = _sanitize_enrichment_plots(value)
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
        if not _bundle_cache_is_valid(payload):
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
        "week_start": _current_week_start(),
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
                if isinstance(payload, dict) and _bundle_cache_is_valid(payload):
                    calendar = payload.get("calendar")
                    metadata = payload.get("metadata")
                    if isinstance(calendar, list) and isinstance(metadata, dict):
                        g.log(
                            f"Simkl calendar: using cached v2 {catalog} bundle "
                            f"({len(calendar)} airings, week {_current_week_start()})",
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


def _parse_date_only(raw: Any) -> _calendar_date | None:
    """Parse Simkl CDN date-only or ISO datetime strings for movie calendar rows."""
    if raw is None:
        return None
    if isinstance(raw, _calendar_date) and not isinstance(raw, _calendar_datetime):
        return raw
    if isinstance(raw, _calendar_datetime):
        return _calendar_date(raw.year, raw.month, raw.day)

    text = str(raw).strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return _calendar_date.fromisoformat(text[:10])
        except ValueError:
            pass

    air_dt = _parse_air_datetime(text)
    if air_dt is not None:
        return _calendar_date(air_dt.year, air_dt.month, air_dt.day)
    return None


def _format_countdown_from_date(day: _calendar_date) -> str:
    today = _calendar_date.today()
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
        release_day = _parse_date_only(row.get("release_date") or row.get("date"))
        if release_day is not None:
            weekday = _calendar_datetime.combine(release_day, datetime.time.min).strftime("%A")
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


def _fetch_trending_cdn_metadata(
    catalog: str,
    simkl_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Gap-fill from Simkl CDN trending JSON (today/week/month + DVD)."""
    unique_ids = sorted({int(value) for value in simkl_ids if value})
    if not unique_ids:
        return {}

    trending_rows = get_rows_by_ids(catalog, unique_ids)
    out: dict[int, dict[str, Any]] = {}
    for simkl_id, db_row in trending_rows.items():
        enrichment = _discover_row_to_enrichment(db_row)
        if _enrichment_has_plot(enrichment) or _enrichment_has_ratings(enrichment):
            out[int(simkl_id)] = enrichment

    g.log(
        f"Simkl calendar: trending CDN matched {len(out)}/{len(unique_ids)} "
        f"{catalog} titles for plot/rating gap-fill",
        "info",
    )
    return out


def _fetch_simkl_api_metadata(
    catalog: str,
    simkl_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Last-resort gap-fill via Simkl API title detail."""
    from resources.lib.simkl.related import _fetch_detail

    unique_ids = sorted({int(value) for value in simkl_ids if value})
    if not unique_ids:
        return {}

    out: dict[int, dict[str, Any]] = {}
    for index, simkl_id in enumerate(unique_ids):
        try:
            detail = _fetch_detail(catalog, simkl_id)
        except Exception:
            g.log_stacktrace()
            detail = None
        if not isinstance(detail, dict) or detail.get("error"):
            continue
        enrichment = _simkl_api_to_enrichment(detail)
        if enrichment:
            out[simkl_id] = enrichment
        if index + 1 < len(unique_ids):
            time.sleep(CALENDAR_SIMKL_API_SLEEP)

    g.log(
        f"Simkl calendar: Simkl API matched {len(out)}/{len(unique_ids)} "
        f"{catalog} titles for plot/rating gap-fill",
        "info",
    )
    return out


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
    overview = _usable_plot_text(db_row.get("overview"))
    return {
        "title": db_row.get("title"),
        "poster": db_row.get("poster"),
        "overview": overview,
        "description": overview,
        "genres": genres,
        "ratings": ratings_list,
        "score_average": score_average,
        "score": score_average,
    }


def _enrichment_has_plot(item: dict[str, Any] | None) -> bool:
    return bool(_enrichment_plot_text(item))


def _enrichment_has_ratings(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    if item.get("score_average") not in (None, "", 0, 0.0):
        return True
    ratings_map = mdblist_ratings_to_map(item.get("ratings"))
    for source in _CALENDAR_RATING_SOURCES:
        lookup = "myanimelist" if source == "mal" else source
        entry = ratings_map.get(source) or ratings_map.get(lookup)
        if entry and _display_rating(entry.get("rating") or entry.get("score")) != "-":
            return True
    return False


def _calendar_row_enrichment(row: dict[str, Any] | None) -> dict[str, Any]:
    """Plot/ratings from Simkl calendar v2 CDN row metadata."""
    if not row:
        return {}
    plot = _usable_plot_text(row.get("overview") or row.get("plot") or row.get("description"))
    payload: dict[str, Any] = {}
    if plot:
        payload["overview"] = plot
        payload["plot"] = plot
        payload["description"] = plot
    ratings = row.get("ratings")
    if isinstance(ratings, dict) and ratings:
        payload["ratings"] = _ratings_dict_to_list(ratings)
    genres = row.get("genres")
    if genres:
        payload["genres"] = genres
    return payload


def _simkl_api_to_enrichment(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape Simkl GET /movies|tv|anime/{id} JSON for calendar gap-fill."""
    plot = _usable_plot_text(payload.get("overview") or payload.get("description"))
    ratings_raw = payload.get("ratings")
    ratings_list: list[dict[str, Any]] = []
    if isinstance(ratings_raw, dict) and ratings_raw:
        ratings_list = _ratings_dict_to_list(ratings_raw)
    genres = payload.get("genres")

    result: dict[str, Any] = {}
    if plot:
        result["overview"] = plot
        result["plot"] = plot
        result["description"] = plot
    if ratings_list:
        result["ratings"] = ratings_list
    if genres:
        result["genres"] = genres
    return result


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
    merged = _sanitize_enrichment_plots(base)
    if not extra:
        return merged
    extra = _sanitize_enrichment_plots(extra)
    for key in ("title", "poster", "score_average", "score"):
        if not merged.get(key) and extra.get(key):
            merged[key] = extra[key]
    plot = _enrichment_plot_text(merged) or _enrichment_plot_text(extra)
    if plot:
        merged["overview"] = plot
        merged["plot"] = plot
        merged["description"] = plot
    else:
        for key in ("overview", "plot", "description"):
            merged.pop(key, None)
    if not merged.get("genres") and extra.get("genres"):
        merged["genres"] = extra["genres"]
    if extra.get("ratings"):
        if merged.get("ratings"):
            merged["ratings"] = _merge_ratings_lists(merged["ratings"], extra["ratings"])
        else:
            merged["ratings"] = extra["ratings"]
    return merged


def _mdblist_item_to_enrichment(item: dict[str, Any]) -> dict[str, Any]:
    overview = _usable_plot_text(
        item.get("overview") or item.get("description") or item.get("plot")
    )
    score = item.get("score_average") or item.get("score")
    return {
        "title": item.get("title"),
        "poster": item.get("poster"),
        "overview": overview,
        "plot": overview,
        "description": overview,
        "genres": item.get("genres"),
        "ratings": item.get("ratings"),
        "score_average": score,
        "score": score,
    }


def _imdb_plot_text(title: dict[str, Any]) -> str:
    plot = title.get("plot")
    if isinstance(plot, str):
        return _usable_plot_text(plot) or ""
    if not isinstance(plot, dict):
        return ""
    plot_text = plot.get("plotText")
    if isinstance(plot_text, str):
        return _usable_plot_text(plot_text) or ""
    if isinstance(plot_text, dict):
        return _usable_plot_text(plot_text.get("plainText")) or ""
    return ""


def _imdb_title_to_enrichment(title: dict[str, Any]) -> dict[str, Any]:
    plot = _imdb_plot_text(title)
    ratings_summary = title.get("ratingsSummary") if isinstance(title.get("ratingsSummary"), dict) else {}
    aggregate = ratings_summary.get("aggregateRating")
    vote_count = ratings_summary.get("voteCount")

    ratings_list: list[dict[str, Any]] = []
    if aggregate not in (None, "", 0, 0.0):
        ratings_list.append(
            {
                "source": "imdb",
                "value": aggregate,
                "score": aggregate,
                "votes": vote_count,
            }
        )

    genres_block = title.get("genres")
    genres: list[str] = []
    if isinstance(genres_block, dict):
        for genre in genres_block.get("genres") or []:
            if isinstance(genre, dict):
                text = str(genre.get("text") or "").strip()
                if text:
                    genres.append(text)
            elif isinstance(genre, str) and genre.strip():
                genres.append(genre.strip())

    payload: dict[str, Any] = {}
    if plot:
        payload["overview"] = plot
        payload["plot"] = plot
        payload["description"] = plot
    if ratings_list:
        payload["ratings"] = ratings_list
    if genres:
        payload["genres"] = genres
    return payload


def _fetch_imdb_calendar_metadata(
    catalog: str,
    rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    from resources.lib.indexers.imdb import imdb_runtime_enabled, thread_imdb_api
    from resources.lib.meta.provider_settings import imdb_metadata_enabled
    from resources.lib.simkl.field_map import _normalize_imdb_id

    if not imdb_metadata_enabled() or not imdb_runtime_enabled() or not rows:
        return {}

    imdb_to_simkl: dict[str, list[int]] = {}
    for row in rows:
        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is None:
            continue
        ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        imdb_id = _normalize_imdb_id(ids.get("imdb"))
        if not imdb_id:
            continue
        imdb_to_simkl.setdefault(imdb_id, []).append(simkl_id)

    if not imdb_to_simkl:
        return {}

    api = thread_imdb_api()
    out: dict[int, dict[str, Any]] = {}
    id_list = list(imdb_to_simkl.keys())
    matched_titles = 0
    for index in range(0, len(id_list), CALENDAR_IMDB_BATCH_SIZE):
        chunk = id_list[index : index + CALENDAR_IMDB_BATCH_SIZE]
        try:
            titles = api.titles_batch(chunk)
        except Exception:
            g.log_stacktrace()
            titles = {}
        for imdb_id, title in titles.items():
            if not isinstance(title, dict):
                continue
            enrichment = _imdb_title_to_enrichment(title)
            if not enrichment:
                continue
            matched_titles += 1
            for simkl_id in imdb_to_simkl.get(imdb_id, []):
                out[int(simkl_id)] = enrichment
        if index + CALENDAR_IMDB_BATCH_SIZE < len(id_list):
            time.sleep(CALENDAR_IMDB_SLEEP)

    g.log(
        f"Simkl calendar: IMDb matched {matched_titles}/{len(imdb_to_simkl)} titles "
        f"for {catalog} plot/rating gap-fill",
        "info",
    )
    return out


def _fetch_anilist_calendar_metadata(
    catalog: str,
    rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    from resources.lib.indexers.anilist import anilist_runtime_enabled, thread_anilist_api
    from resources.lib.meta.provider_settings import anilist_metadata_enabled

    if catalog != "anime" or not anilist_metadata_enabled() or not anilist_runtime_enabled() or not rows:
        return {}

    mal_to_simkl: dict[int, list[int]] = {}
    for row in rows:
        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is None:
            continue
        ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
        try:
            mal_id = int(ids.get("mal"))
        except (TypeError, ValueError):
            continue
        mal_to_simkl.setdefault(mal_id, []).append(simkl_id)

    if not mal_to_simkl:
        return {}

    metadata_by_mal = thread_anilist_api().get_metadata_batch_by_mal_ids(list(mal_to_simkl.keys()))
    out: dict[int, dict[str, Any]] = {}
    matched_titles = 0
    for mal_id, enrichment in metadata_by_mal.items():
        if not enrichment:
            continue
        matched_titles += 1
        for simkl_id in mal_to_simkl.get(int(mal_id), []):
            out[int(simkl_id)] = enrichment

    g.log(
        f"Simkl calendar: AniList matched {matched_titles}/{len(mal_to_simkl)} anime titles "
        "for plot/rating gap-fill",
        "info",
    )
    return out


def _log_mdblist_weekly_cache_status(
    metadata_cache: dict[int, dict[str, Any]],
    *,
    mdblist_targets: int,
) -> None:
    """Log when MDBList was expected but returned no scores (cache still saved for the week)."""
    api_key = resolve_mdblist_api_key()
    if not api_key or mdblist_targets <= 0:
        return
    enriched = sum(
        1
        for item in metadata_cache.values()
        if isinstance(item, dict) and item.get("score_average") not in (None, "", 0, 0.0)
    )
    if enriched > 0:
        return
    g.log(
        "Simkl calendar: MDBList returned no scores "
        f"for {mdblist_targets} lookup targets — keeping weekly cache until next week "
        "(likely rate limited)",
        "warning",
    )


def _build_metadata_cache(
    catalog: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], int]:
    """Gap-fill overview and ratings (CDN row, MDBList, IMDb, AniList, trending CDN, Simkl API)."""
    mdblist_map, mdblist_targets = _fetch_mdblist_by_simkl_id(catalog, rows)
    imdb_map = _fetch_imdb_calendar_metadata(catalog, rows)
    anilist_map = _fetch_anilist_calendar_metadata(catalog, rows)
    row_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        simkl_id = _simkl_ids_for_row(row)
        if simkl_id is not None:
            row_by_id[simkl_id] = row

    cache: dict[int, dict[str, Any]] = {}

    for simkl_id in _simkl_ids_from_rows(rows):
        row = row_by_id.get(simkl_id)
        item = _merge_enrichment({}, _calendar_row_enrichment(row))
        if simkl_id in mdblist_map:
            item = _merge_enrichment(item, _mdblist_item_to_enrichment(mdblist_map[simkl_id]))
        if simkl_id in imdb_map:
            item = _merge_enrichment(item, imdb_map[simkl_id])
        if simkl_id in anilist_map:
            item = _merge_enrichment(item, anilist_map[simkl_id])
        cache[simkl_id] = item

    fallback_ids = sorted(
        sid
        for sid, item in cache.items()
        if not _enrichment_has_plot(item) or not _enrichment_has_ratings(item)
    )
    trending_map = _fetch_trending_cdn_metadata(catalog, fallback_ids)
    for simkl_id in fallback_ids:
        enrichment = trending_map.get(simkl_id)
        if enrichment:
            cache[simkl_id] = _merge_enrichment(cache.get(simkl_id), enrichment)

    api_ids = sorted(
        sid
        for sid, item in cache.items()
        if not _enrichment_has_plot(item) or not _enrichment_has_ratings(item)
    )
    api_map = _fetch_simkl_api_metadata(catalog, api_ids)
    for simkl_id in api_ids:
        enrichment = api_map.get(simkl_id)
        if enrichment:
            cache[simkl_id] = _merge_enrichment(cache.get(simkl_id), enrichment)

    for simkl_id, item in cache.items():
        cache[simkl_id] = _sanitize_enrichment_plots(item)

    filled = sum(1 for sid in cache if _enrichment_has_plot(cache.get(sid)))
    rated = sum(1 for sid in cache if _enrichment_has_ratings(cache.get(sid)))
    g.log(
        f"Simkl calendar: plot/overview text for {filled}/{len(cache)} titles "
        f"({len(rows)} calendar rows this week); ratings for {rated}/{len(cache)} "
        f"(CDN / MDBList / IMDb / AniList / trending CDN / Simkl API gap-fill)",
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
            except urllib.error.HTTPError as exc:
                g.log(
                    f"Simkl calendar: MDBList batch {batch_no}/{batch_total} skipped "
                    f"({provider}/{media_type}, {len(chunk)} ids) — HTTP {exc.code}",
                    "warning",
                )
                continue
            except urllib.error.URLError as exc:
                g.log(
                    f"Simkl calendar: MDBList batch {batch_no}/{batch_total} skipped "
                    f"({provider}/{media_type}, {len(chunk)} ids) — {exc.reason}",
                    "warning",
                )
                continue

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
            plot = _enrichment_plot_text(enrichment)
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
            filtered_rows, _metadata_cache = cached
            g.log(
                f"Simkl calendar prefetch: {catalog} weekly cache already warm "
                f"({len(filtered_rows)} rows, week {_current_week_start()})",
                "debug",
            )
            return len(filtered_rows)

    filtered, metadata_cache, mdblist_targets = _build_weekly_calendar(catalog)
    _log_mdblist_weekly_cache_status(metadata_cache, mdblist_targets=mdblist_targets)
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
            g.log(
                f"Simkl calendar: using prefetched {catalog} weekly cache "
                f"({len(items)} items, week {_current_week_start()})",
                "info",
            )
            return items, week_label

    filtered, metadata_cache, mdblist_targets = _build_weekly_calendar(catalog, window_days=window_days)
    if window_days == DEFAULT_WINDOW_DAYS:
        _log_mdblist_weekly_cache_status(metadata_cache, mdblist_targets=mdblist_targets)
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
