"""AniList GraphQL client — batched anime cast enrichment."""
from __future__ import annotations

import threading
import time
from functools import cached_property
from typing import Any

from resources.lib.database.cache import use_cache
from resources.lib.modules.globals import g

GRAPHQL_URL = "https://graphql.anilist.co"
_BATCH_CHUNK_SIZE = 50
_CAST_CHARACTER_LIMIT = 10

_thread_local = threading.local()
_graphql_lock = threading.Lock()
_rate_limit_until = 0.0

_MEDIA_CAST_FIELDS = """
    id
    idMal
    characters(page: 1, sort: ROLE, perPage: $perPage) {
        edges {
            node {
                name { userPreferred }
            }
            voiceActors(language: JAPANESE) {
                name { userPreferred }
                image { large }
            }
        }
    }
    studios {
        edges {
            node { name }
        }
    }
"""

_MAL_BATCH_QUERY = f"""
query ($page: Int, $malIds: [Int], $type: MediaType, $perPage: Int) {{
  Page(page: $page) {{
    pageInfo {{ hasNextPage }}
    media(idMal_in: $malIds, type: $type) {{
      {_MEDIA_CAST_FIELDS}
    }}
  }}
}}
"""

_ANILIST_BATCH_QUERY = f"""
query ($page: Int, $anilistIds: [Int], $type: MediaType, $perPage: Int) {{
  Page(page: $page) {{
    pageInfo {{ hasNextPage }}
    media(id_in: $anilistIds, type: $type) {{
      {_MEDIA_CAST_FIELDS}
    }}
  }}
}}
"""


def thread_anilist_api() -> "AnilistAPI":
    api = getattr(_thread_local, "anilist_api", None)
    if api is None:
        api = AnilistAPI()
        _thread_local.anilist_api = api
    return api


def anilist_runtime_enabled() -> bool:
    from resources.lib.meta.provider_settings import provider_enabled

    return provider_enabled("anilist")


def _chunk_ids(values: list[int], size: int = _BATCH_CHUNK_SIZE) -> list[list[int]]:
    if not values:
        return []
    return [values[index : index + size] for index in range(0, len(values), size)]


def _studio_name(media: dict[str, Any]) -> str | None:
    studios = media.get("studios")
    if not isinstance(studios, dict):
        return None
    for edge in studios.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()
        if name:
            return name
    return None


def media_to_cast_and_studio(media: dict[str, Any] | None, *, limit: int = _CAST_CHARACTER_LIMIT) -> tuple[list[dict], str | None]:
    """Map AniList media characters to Prism/Kodi cast rows."""
    if not isinstance(media, dict):
        return [], None

    cast: list[dict] = []
    characters = media.get("characters")
    if isinstance(characters, dict):
        for index, edge in enumerate(characters.get("edges") or []):
            if not isinstance(edge, dict) or index >= limit:
                break
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            name_block = node.get("name")
            role = ""
            if isinstance(name_block, dict):
                role = str(name_block.get("userPreferred") or "").strip()
            voice_actors = edge.get("voiceActors") or []
            if not voice_actors or not isinstance(voice_actors[0], dict):
                continue
            actor_block = voice_actors[0]
            actor_name = ""
            actor_name_block = actor_block.get("name")
            if isinstance(actor_name_block, dict):
                actor_name = str(actor_name_block.get("userPreferred") or "").strip()
            if not actor_name:
                continue
            thumb = ""
            image = actor_block.get("image")
            if isinstance(image, dict):
                thumb = str(image.get("large") or "")
            cast.append(
                {
                    "name": actor_name,
                    "role": role,
                    "character": role,
                    "order": index,
                    "thumbnail": thumb,
                    "thumb": thumb,
                }
            )

    return cast, _studio_name(media)


class AnilistAPI:
    def __init__(self) -> None:
        g.ensure_addon()

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"{g.ADDON_ID}/{g.ADDON.getAddonInfo('version')}",
            }
        )
        retries = Retry(total=3, backoff_factor=0.25, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_maxsize=20, pool_connections=5)
        session.mount("https://", adapter)
        return session

    def _wait_rate_limit(self) -> None:
        global _rate_limit_until
        now = time.monotonic()
        if now < _rate_limit_until:
            time.sleep(_rate_limit_until - now)

    def _set_rate_limit(self, seconds: float = 30.0) -> None:
        global _rate_limit_until
        _rate_limit_until = max(_rate_limit_until, time.monotonic() + seconds)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any] | None:
        if not anilist_runtime_enabled():
            return None
        self._wait_rate_limit()
        payload = {"query": query, "variables": variables}
        try:
            with _graphql_lock:
                response = self.session.post(GRAPHQL_URL, json=payload, timeout=20)
            if response.status_code == 429:
                self._set_rate_limit()
                g.log("AniList GraphQL rate limited (429)", "warning")
                return None
            if response.status_code != 200:
                g.log(f"AniList GraphQL HTTP {response.status_code}", "debug")
                return None
            body = response.json()
            if body.get("errors"):
                g.log(f"AniList GraphQL errors: {body.get('errors')}", "debug")
                return None
            data = body.get("data")
            return data if isinstance(data, dict) else None
        except Exception:
            g.log_stacktrace()
            return None

    def _fetch_media_pages(self, query: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        page = 1
        while True:
            page_vars = dict(variables)
            page_vars["page"] = page
            page_vars.setdefault("type", "ANIME")
            page_vars.setdefault("perPage", _CAST_CHARACTER_LIMIT)
            data = self.graphql(query, page_vars)
            if not data:
                break
            page_block = data.get("Page") or {}
            rows = page_block.get("media") or []
            if isinstance(rows, list):
                media.extend(row for row in rows if isinstance(row, dict))
            if not page_block.get("pageInfo", {}).get("hasNextPage"):
                break
            page += 1
        return media

    def get_cast_batch_by_mal_ids(self, mal_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Return {mal_id: {cast, studio}} for each resolved title."""
        normalized = sorted({int(value) for value in mal_ids if value})
        if not normalized:
            return {}

        result: dict[int, dict[str, Any]] = {}
        for chunk in _chunk_ids(normalized):
            for media in self._fetch_media_pages(
                _MAL_BATCH_QUERY,
                {"malIds": chunk, "type": "ANIME", "perPage": _CAST_CHARACTER_LIMIT},
            ):
                mal_id = media.get("idMal")
                if mal_id is None:
                    continue
                cast, studio = media_to_cast_and_studio(media)
                if not cast and not studio:
                    continue
                payload: dict[str, Any] = {}
                if cast:
                    payload["cast"] = cast
                if studio:
                    payload["studio"] = studio
                result[int(mal_id)] = payload
        return result

    def get_cast_batch_by_anilist_ids(self, anilist_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Return {anilist_id: {cast, studio}} for each resolved title."""
        normalized = sorted({int(value) for value in anilist_ids if value})
        if not normalized:
            return {}

        result: dict[int, dict[str, Any]] = {}
        for chunk in _chunk_ids(normalized):
            for media in self._fetch_media_pages(
                _ANILIST_BATCH_QUERY,
                {"anilistIds": chunk, "type": "ANIME", "perPage": _CAST_CHARACTER_LIMIT},
            ):
                anilist_id = media.get("id")
                if anilist_id is None:
                    continue
                cast, studio = media_to_cast_and_studio(media)
                if not cast and not studio:
                    continue
                payload: dict[str, Any] = {}
                if cast:
                    payload["cast"] = cast
                if studio:
                    payload["studio"] = studio
                result[int(anilist_id)] = payload
        return result


@use_cache(cache_hours=24)
def cached_cast_batch_by_mal_ids(mal_ids_key: str) -> dict[int, dict[str, Any]]:
    mal_ids = [int(part) for part in str(mal_ids_key or "").split(",") if part.strip().isdigit()]
    return thread_anilist_api().get_cast_batch_by_mal_ids(mal_ids)


@use_cache(cache_hours=24)
def cached_cast_batch_by_anilist_ids(anilist_ids_key: str) -> dict[int, dict[str, Any]]:
    anilist_ids = [int(part) for part in str(anilist_ids_key or "").split(",") if part.strip().isdigit()]
    return thread_anilist_api().get_cast_batch_by_anilist_ids(anilist_ids)


def get_cast_batch_for_pending(
    pending: list[tuple[int, dict, int | None, int | None]],
) -> dict[int, dict[str, Any]]:
    """
    Batch-fetch cast for pending anime rows.

    pending items: (simkl_id, db_object, mal_id|None, anilist_id|None)
    Returns {simkl_id: {cast?, studio?}}.
    """
    if not anilist_runtime_enabled() or not pending:
        return {}

    mal_ids: list[int] = []
    anilist_ids: list[int] = []
    mal_by_simkl: dict[int, int] = {}
    anilist_by_simkl: dict[int, int] = {}

    for simkl_id, _db_object, mal_id, anilist_id in pending:
        if mal_id is not None:
            mal_ids.append(int(mal_id))
            mal_by_simkl[int(simkl_id)] = int(mal_id)
        elif anilist_id is not None:
            anilist_ids.append(int(anilist_id))
            anilist_by_simkl[int(simkl_id)] = int(anilist_id)

    mal_key = ",".join(str(value) for value in sorted(set(mal_ids)))
    anilist_key = ",".join(str(value) for value in sorted(set(anilist_ids)))
    mal_map = cached_cast_batch_by_mal_ids(mal_key) if mal_key else {}
    anilist_map = cached_cast_batch_by_anilist_ids(anilist_key) if anilist_key else {}

    resolved: dict[int, dict[str, Any]] = {}
    for simkl_id, mal_id in mal_by_simkl.items():
        payload = mal_map.get(int(mal_id))
        if payload:
            resolved[int(simkl_id)] = payload
    for simkl_id, anilist_id in anilist_by_simkl.items():
        if int(simkl_id) in resolved:
            continue
        payload = anilist_map.get(int(anilist_id))
        if payload:
            resolved[int(simkl_id)] = payload
    return resolved
