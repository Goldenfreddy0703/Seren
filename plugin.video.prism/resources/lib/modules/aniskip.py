from functools import cached_property

from resources.lib.database.cache import use_cache
from resources.lib.modules.globals import g

API_BASE = "https://api.aniskip.com/v2"

# AniSkip skip types -> Prism segment types. Dedicated types win over their "mixed" variants.
SKIP_TYPE_MAP = {
    "op": "intro",
    "mixed-op": "intro",
    "ed": "credits",
    "mixed-ed": "credits",
    "recap": "recap",
}

REQUEST_TYPES = ("op", "ed", "recap", "mixed-op", "mixed-ed")


def _valid_int(value):
    try:
        return int(value) > 0
    except (ValueError, TypeError):
        return False


def _is_anime(info):
    return info.get("catalog") == "anime" or bool(info.get("mal_id")) or bool(info.get("mal_show_id"))


def _resolve_mal_id(info):
    """Resolve the anime's MAL id, looking up the parent show row when the episode lacks it."""
    for key in ("mal_id", "mal_show_id", "tvshow.mal_id"):
        value = info.get(key)
        if _valid_int(value):
            return int(value)

    show_id = info.get("simkl_show_id") or (info.get("simkl_id") if info.get("mediatype") != "episode" else None)
    if not show_id:
        return None
    try:
        from resources.lib.database.session import get_sync_database

        show = get_sync_database().get_show(show_id)
        show_info = (show or {}).get("info") if isinstance(show, dict) else None
        if show_info and _valid_int(show_info.get("mal_id")):
            return int(show_info["mal_id"])
    except Exception:
        g.log_stacktrace()
    return None


class AniSkip:
    """Read-only client for AniSkip OP/ED/recap timestamps (anime, keyed by MAL id)."""

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=0.1,
            status_forcelist=[500, 502, 503, 504, 520, 521, 522, 524],
        )
        session.mount("https://", HTTPAdapter(max_retries=retries, pool_maxsize=10))
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"{g.ADDON_NAME} Kodi Addon",
            }
        )
        return session

    def get_segments(self, item_information):
        """Return normalized skip segments for the given anime episode.

        Matches the IntroDB client output: a list of {"type", "start", "end"} dicts
        (seconds). Returns [] when disabled, not anime, unidentifiable, or on error.
        """
        if not g.get_bool_setting("introdb.enabled"):
            return []

        info = (item_information or {}).get("info", {})
        if not info or not _is_anime(info):
            return []

        # AniSkip is episode-keyed; anime movies fall through to IntroDB.
        if info.get("mediatype") == "movie":
            return []

        mal_id = _resolve_mal_id(info)
        episode = info.get("episode")
        if episode is None:
            episode = info.get("number")
        if not (_valid_int(mal_id) and _valid_int(episode)):
            g.log(f"AniSkip: unresolved mal_id/episode (mal={mal_id}, ep={episode})", "debug")
            return []

        g.log(f"AniSkip: querying mal_id={mal_id} episode={episode}", "debug")
        return self._fetch_segments(mal_id=int(mal_id), episode=int(episode))

    @use_cache(cache_hours=72)
    def _fetch_segments(self, mal_id, episode):
        data = self._request(mal_id, episode)
        if not data or not data.get("found"):
            return []
        return self._normalize(data.get("results") or [])

    def _request(self, mal_id, episode):
        query = [("types", t) for t in REQUEST_TYPES]
        query.append(("episodeLength", 0))
        try:
            import requests

            response = self.session.get(f"{API_BASE}/skip-times/{mal_id}/{episode}", params=query, timeout=8)
            if response.status_code == 404:
                return {}
            if response.status_code not in (200, 201):
                g.log(f"AniSkip returned HTTP {response.status_code}", "warning")
                return None
            return response.json()
        except requests.exceptions.RequestException as error:
            g.log(f"AniSkip request error: {error}", "warning")
            return None
        except Exception:
            g.log_stacktrace()
            return None

    def _normalize(self, results):
        by_type = {}
        for entry in results:
            if not isinstance(entry, dict):
                continue
            seg_type = SKIP_TYPE_MAP.get(entry.get("skipType"))
            if not seg_type:
                continue
            interval = entry.get("interval") or {}
            start = interval.get("startTime")
            end = interval.get("endTime")
            if start is None and end is None:
                continue
            candidate = {
                "type": seg_type,
                "start": float(start) if start is not None else None,
                "end": float(end) if end is not None else None,
                "mixed": str(entry.get("skipType", "")).startswith("mixed"),
            }
            existing = by_type.get(seg_type)
            if existing is None or self._is_better(candidate, existing):
                by_type[seg_type] = candidate

        segments = []
        for segment in by_type.values():
            segment.pop("mixed", None)
            segments.append(segment)
        segments.sort(key=lambda s: (s["start"] if s["start"] is not None else 0))
        return segments

    @staticmethod
    def _is_better(candidate, existing):
        # Prefer a dedicated type over a mixed one; otherwise prefer the longer interval.
        if existing["mixed"] != candidate["mixed"]:
            return not candidate["mixed"]
        return AniSkip._duration(candidate) > AniSkip._duration(existing)

    @staticmethod
    def _duration(segment):
        start = segment.get("start") or 0
        end = segment.get("end")
        return (end - start) if end is not None else 0
