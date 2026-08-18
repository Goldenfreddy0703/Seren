"""Session hot-cache for simkl_sync.db list metadata (POV MetaCache-style)."""

from __future__ import annotations

import datetime
from datetime import date
from typing import Any

from resources.lib.database.cache import MemCache
from resources.lib.modules.globals import g

_MOVIE = "movie"
_SHOW = "show"
_DEFAULT_TTL = datetime.timedelta(hours=24)
_AIRING_TTL = datetime.timedelta(days=4)
_ENDED_TTL = datetime.timedelta(days=182)
_ENRICH_MISS_TTL = datetime.timedelta(hours=48)
_PROVIDER_MISS_TTL = datetime.timedelta(hours=48)

def _prefetch_limit() -> int:
    try:
        from resources.lib.modules.cache_maintenance import SYNC_META_PREFETCH_LIMIT

        return int(SYNC_META_PREFETCH_LIMIT)
    except Exception:
        return 250


def meta_expiry(media_type: str, row: dict[str, Any] | None) -> datetime.timedelta:
    """Shorter TTL for airing titles; long TTL for library staples."""
    if not isinstance(row, dict):
        return _DEFAULT_TTL

    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    if media_type == _SHOW:
        if row.get("is_airing") or info.get("status") in ("Continuing", "In Production", "Returning Series"):
            return _AIRING_TTL
        next_air = info.get("next_episode_to_air")
        if isinstance(next_air, dict) and next_air.get("air_date"):
            return _AIRING_TTL
        return _ENDED_TTL

    status = (info.get("status") or "").lower()
    if status in ("released", "ended"):
        return _ENDED_TTL
    if row.get("air_date"):
        try:
            air = datetime.datetime.fromisoformat(str(row["air_date"]).replace("Z", ""))
            if air.date() > date.today():
                days = max(1, (air.date() - date.today()).days)
                return datetime.timedelta(days=min(days, 30))
        except (TypeError, ValueError):
            pass
    return _DEFAULT_TTL


def row_needs_refresh(media_type: str, row: dict[str, Any] | None) -> bool:
    """True when persisted sync metadata is older than its smart TTL."""
    if not isinstance(row, dict):
        return False
    last = row.get("last_updated")
    if not last:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        last = info.get("last_updated") or info.get("dateupdated") or info.get("dateadded")
    if not last:
        return False
    try:
        updated = datetime.datetime.fromisoformat(str(last).replace("Z", ""))
    except (TypeError, ValueError):
        return False
    ttl = meta_expiry(media_type, row)
    return datetime.datetime.now() - updated > ttl


class SyncMetaCache:
    """Window-property cache for movies/shows rows: info, art, cast, external ids."""

    def __init__(self) -> None:
        self._cache = MemCache()
        self._cache.cache_prefix = "sync_meta"
        self._miss_cache = MemCache()
        self._miss_cache.cache_prefix = "sync_meta.miss"
        self._provider_miss_cache = MemCache()
        self._provider_miss_cache.cache_prefix = "sync_meta.provider_miss"

    @staticmethod
    def _cache_key(media_type: str, simkl_id: int) -> str:
        return f"{media_type}.{int(simkl_id)}"

    @staticmethod
    def _miss_key(catalog: str, simkl_id: int) -> str:
        return f"{catalog}.{int(simkl_id)}"

    @staticmethod
    def row_has_display_meta(row: dict[str, Any] | None) -> bool:
        if not isinstance(row, dict):
            return False
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        art = row.get("art") if isinstance(row.get("art"), dict) else {}
        title = info.get("title") or info.get("originaltitle")
        poster = art.get("poster") or art.get("thumb") or info.get("poster")
        return bool(title and poster)

    @staticmethod
    def _payload_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(row, dict) or row.get("simkl_id") is None:
            return None
        payload: dict[str, Any] = {"simkl_id": int(row["simkl_id"])}
        for key in ("info", "art", "cast", "tmdb_id", "tvdb_id", "imdb_id", "is_airing", "air_date"):
            if key in row and row[key] is not None:
                payload[key] = row[key]
        return payload if payload.get("info") or payload.get("art") else None

    def get_row(self, media_type: str, simkl_id: int) -> dict[str, Any] | None:
        cached = self._cache.get(self._cache_key(media_type, simkl_id))
        return cached if isinstance(cached, dict) else None

    def get_many_rows(self, media_type: str, simkl_ids: list[int]) -> dict[int, dict[str, Any]]:
        hits: dict[int, dict[str, Any]] = {}
        for simkl_id in simkl_ids:
            row = self.get_row(media_type, int(simkl_id))
            if row:
                hits[int(simkl_id)] = row
        return hits

    def partition_complete(
        self, media_type: str, simkl_ids: list[int]
    ) -> tuple[dict[int, dict[str, Any]], list[int]]:
        hits: dict[int, dict[str, Any]] = {}
        misses: list[int] = []
        for simkl_id in simkl_ids:
            sid = int(simkl_id)
            row = self.get_row(media_type, sid)
            if row and self.row_has_display_meta(row):
                hits[sid] = row
            else:
                misses.append(sid)
        return hits, misses

    def set_row(self, media_type: str, row: dict[str, Any]) -> None:
        payload = self._payload_from_row(row)
        if not payload:
            return
        simkl_id = int(payload["simkl_id"])
        self._cache.set(
            self._cache_key(media_type, simkl_id),
            payload,
            expiration=meta_expiry(media_type, payload),
        )

    def set_many_rows(self, media_type: str, rows: list[dict[str, Any]]) -> None:
        for row in rows or []:
            if isinstance(row, dict):
                self.set_row(media_type, row)

    def delete_row(self, media_type: str, simkl_id: int) -> None:
        g.clear_runtime_setting(
            self._cache._create_key(self._cache_key(media_type, int(simkl_id)))
        )

    def is_enrich_miss(self, catalog: str, simkl_id: int) -> bool:
        return self._miss_cache.get(self._miss_key(catalog, int(simkl_id))) is not self._miss_cache.NOT_CACHED

    def mark_enrich_miss(self, catalog: str, simkl_id: int) -> None:
        self._miss_cache.set(
            self._miss_key(catalog, int(simkl_id)),
            True,
            expiration=_ENRICH_MISS_TTL,
        )

    def clear_enrich_miss(self, catalog: str, simkl_id: int) -> None:
        g.clear_runtime_setting(
            self._miss_cache._create_key(self._miss_key(catalog, int(simkl_id)))
        )

    @staticmethod
    def _provider_miss_key(media_type: str, simkl_id: int, gap: str) -> str:
        return f"{media_type}.{int(simkl_id)}.{gap}"

    def is_gap_miss(self, media_type: str, simkl_id: int, gap: str) -> bool:
        return (
            self._provider_miss_cache.get(self._provider_miss_key(media_type, int(simkl_id), gap))
            is not self._provider_miss_cache.NOT_CACHED
        )

    def mark_gap_miss(self, media_type: str, simkl_id: int, gap: str) -> None:
        self._provider_miss_cache.set(
            self._provider_miss_key(media_type, int(simkl_id), gap),
            True,
            expiration=_PROVIDER_MISS_TTL,
        )

    def clear_gap_misses(self, media_type: str, simkl_id: int) -> None:
        for gap in ("cast", "clearlogo", "clearart", "discart", "banner", "landscape"):
            g.clear_runtime_setting(
                self._provider_miss_cache._create_key(self._provider_miss_key(media_type, int(simkl_id), gap))
            )

    def is_provider_miss(self, media_type: str, simkl_id: int) -> bool:
        return any(
            self.is_gap_miss(media_type, int(simkl_id), gap)
            for gap in ("cast", "clearlogo", "clearart", "discart", "banner", "landscape")
        )

    def mark_provider_miss(self, media_type: str, simkl_id: int) -> None:
        for gap in ("cast", "clearlogo", "clearart", "discart", "banner", "landscape"):
            self.mark_gap_miss(media_type, int(simkl_id), gap)

    def clear_provider_miss(self, media_type: str, simkl_id: int) -> None:
        self.clear_gap_misses(media_type, int(simkl_id))

    def clear_session(self) -> None:
        """Drop all session-scoped sync row caches."""
        self._cache.clear_all()
        self._miss_cache.clear_all()
        self._provider_miss_cache.clear_all()

    def prefetch(self, limit: int | None = None) -> int:
        """Warm recent movies/shows metadata into window properties."""
        if limit is None:
            limit = _prefetch_limit()
        if limit <= 0:
            return 0
        warmed = 0
        half = max(1, limit // 2)

        try:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()
            movie_rows = db.fetchall(
                """
                SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id, air_date
                FROM movies
                WHERE info IS NOT NULL
                ORDER BY last_updated DESC
                LIMIT ?
                """,
                (half,),
            )
            show_rows = db.fetchall(
                """
                SELECT simkl_id, info, art, [cast], tmdb_id, tvdb_id, imdb_id, air_date, is_airing
                FROM shows
                WHERE info IS NOT NULL
                ORDER BY last_updated DESC
                LIMIT ?
                """,
                (max(1, limit - half),),
            )
            self.set_many_rows(_MOVIE, movie_rows or [])
            self.set_many_rows(_SHOW, show_rows or [])
            warmed = len(movie_rows or []) + len(show_rows or [])
        except Exception:
            g.log_stacktrace()
        return warmed


def row_has_display_meta(row: dict[str, Any] | None) -> bool:
    """True when a sync/display row has enough data to paint a list item."""
    return SyncMetaCache.row_has_display_meta(row)


def row_has_plot_meta(row: dict[str, Any] | None) -> bool:
    """True when a paint row includes overview/plot text."""
    if not isinstance(row, dict):
        return False
    info = row.get("info") if isinstance(row.get("info"), dict) else {}
    return bool(info.get("plot") or info.get("overview"))


def maybe_prefetch_sync_meta() -> None:
    """No-op — menu RAM warm removed for Seren parity."""
    return

