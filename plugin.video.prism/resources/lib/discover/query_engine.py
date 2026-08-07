"""Filter and sort discover rows in memory (CDN-backed store)."""
from __future__ import annotations

import datetime
import json
from typing import Any

CURRENT_YEAR = datetime.datetime.now().year

_POOL_LIMIT = 500
_HIDDEN_GEMS_LIMIT = 300
# Rolling window for "New Releases" / "New Series" / "New Anime" discover lists.
_NEW_RELEASE_LOOKBACK_DAYS = 365
_NEW_RELEASE_LOOKAHEAD_DAYS = 45


def _parse_ratings(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("ratings_json")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _has_rating_source(row: dict[str, Any], source: str) -> bool:
    return source in _parse_ratings(row)


def _simkl_rating(row: dict[str, Any]) -> float:
    payload = _parse_ratings(row).get("simkl") or {}
    rating = payload.get("rating")
    try:
        return float(rating)
    except (TypeError, ValueError):
        return 0.0


def _release_in_current_year(release_date: str | None) -> bool:
    if not release_date:
        return False
    return f"/{CURRENT_YEAR}" in release_date or release_date.startswith(f"{CURRENT_YEAR}-")


def _parsed_release_date(release_date: str | None) -> datetime.date | None:
    """Parse Simkl CDN release_date (ISO or MM/DD/YYYY) to a calendar date."""
    from resources.lib.discover.normalize import _normalize_air_date

    iso = _normalize_air_date(release_date)
    if not iso:
        return None
    try:
        return datetime.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None


def _is_recent_release(
    release_date: str | None,
    *,
    lookback_days: int = _NEW_RELEASE_LOOKBACK_DAYS,
    lookahead_days: int = _NEW_RELEASE_LOOKAHEAD_DAYS,
) -> bool:
    """True when a title premiered recently (or is about to within the lookahead window)."""
    day = _parsed_release_date(release_date)
    if day is None:
        return False
    today = datetime.date.today()
    earliest = today - datetime.timedelta(days=lookback_days)
    latest = today + datetime.timedelta(days=lookahead_days)
    return earliest <= day <= latest


def _release_date_sort_key(release_date: str | None) -> tuple[bool, datetime.date]:
    day = _parsed_release_date(release_date)
    return (day is None, day or datetime.date.min)


def _sort_key_desc(field: str):
    def key(row: dict[str, Any]):
        value = row.get(field)
        return (value is None, value if value is not None else 0)

    return key


def query_all_rows(
    rows: list[dict[str, Any]],
    query_name: str,
    *,
    catalog: str,
) -> list[dict[str, Any]]:
    """Return the full filtered/sorted discover list (pool caps only, no page slice)."""
    filtered = _filter_rows(rows, query_name, catalog=catalog)
    sorted_rows = _sort_rows(filtered, query_name, catalog=catalog)
    pool_limit = _pool_limit_for(query_name)
    if pool_limit is not None:
        sorted_rows = sorted_rows[:pool_limit]
    return sorted_rows


def query_rows(
    rows: list[dict[str, Any]],
    query_name: str,
    *,
    catalog: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    sorted_rows = query_all_rows(rows, query_name, catalog=catalog)
    return sorted_rows[offset : offset + limit]


def _pool_limit_for(query_name: str) -> int | None:
    if query_name in {"top_simkl", "top_imdb", "top_mal", "completed", "quick_watch"}:
        return _POOL_LIMIT
    if query_name == "hidden_gems":
        return _HIDDEN_GEMS_LIMIT
    return None


def _filter_rows(rows: list[dict[str, Any]], query_name: str, *, catalog: str) -> list[dict[str, Any]]:
    if query_name == "popular":
        return [r for r in rows if r.get("rank") is not None and int(r.get("rank") or 0) > 0]
    if query_name == "most_watched":
        return [r for r in rows if r.get("watched") is not None]
    if query_name == "anticipated":
        return [r for r in rows if r.get("plan_to_watch") is not None]
    if query_name == "top_mdblist":
        return [r for r in rows if _simkl_rating(r) > 0 or r.get("mdblist_score")]
    if query_name == "new_releases":
        return [r for r in rows if _is_recent_release(r.get("release_date"))]
    if query_name == "ongoing":
        return [r for r in rows if r.get("status") == "ongoing"]
    if query_name == "ongoing_movies":
        return [r for r in rows if catalog == "movie" and r.get("status") == "premiere"]
    if query_name == "ended":
        return [r for r in rows if r.get("status") == "ended"]
    if query_name == "tba":
        return [r for r in rows if r.get("status") == "tba"]
    if query_name == "low_drop":
        return [r for r in rows if r.get("drop_rate") is not None]
    if query_name == "binge":
        return [r for r in rows if (r.get("total_episodes") or 0) >= 24]
    if query_name == "short":
        total = lambda r: r.get("total_episodes")
        return [r for r in rows if total(r) is not None and int(total(r) or 0) <= 13]
    if query_name == "new_year":
        return [r for r in rows if _release_in_current_year(r.get("release_date"))]
    if query_name == "top_simkl":
        return [r for r in rows if _has_rating_source(r, "simkl")]
    if query_name == "top_imdb":
        return [r for r in rows if _has_rating_source(r, "imdb")]
    if query_name == "top_mal":
        return [r for r in rows if _has_rating_source(r, "mal")]
    if query_name == "hidden_gems":
        return [r for r in rows if r.get("mdblist_score") or _simkl_rating(r) > 0]
    if query_name == "completed":
        return [r for r in rows if r.get("status") == "ended"]
    if query_name == "awards":
        return [
            r
            for r in rows
            if r.get("extras_json") and "awards" in str(r.get("extras_json")).lower()
        ]
    if query_name == "quick_watch":
        return [r for r in rows if r.get("runtime") is not None]
    return rows


def _sort_rows(rows: list[dict[str, Any]], query_name: str, *, catalog: str) -> list[dict[str, Any]]:
    if query_name == "popular":
        return sorted(rows, key=lambda r: int(r.get("rank") or 0))
    if query_name in {"most_watched", "binge", "short", "ended"}:
        return sorted(rows, key=_sort_key_desc("watched"), reverse=True)
    if query_name == "anticipated":
        return sorted(rows, key=_sort_key_desc("plan_to_watch"), reverse=True)
    if query_name == "top_mdblist":
        return sorted(
            rows,
            key=lambda r: (r.get("mdblist_score") or 0, _simkl_rating(r)),
            reverse=True,
        )
    if query_name == "new_releases":
        return sorted(rows, key=lambda r: _release_date_sort_key(r.get("release_date")), reverse=True)
    if query_name == "ongoing":
        return sorted(rows, key=_sort_key_desc("watched"), reverse=True)
    if query_name == "ongoing_movies":
        return sorted(rows, key=_sort_key_desc("watched"), reverse=True)
    if query_name == "tba":
        return sorted(rows, key=_sort_key_desc("plan_to_watch"), reverse=True)
    if query_name == "low_drop":
        return sorted(rows, key=lambda r: r.get("drop_rate") or "")
    if query_name == "new_year":
        return sorted(rows, key=lambda r: _release_date_sort_key(r.get("release_date")), reverse=True)
    if query_name == "hidden_gems":
        return sorted(
            rows,
            key=lambda r: (r.get("mdblist_score") or 0, _simkl_rating(r)),
            reverse=True,
        )
    return rows
