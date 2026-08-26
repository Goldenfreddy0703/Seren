"""IMDb GraphQL for cast/calendar metadata and suggestion API for getSources."""
from __future__ import annotations

import json
import random
import threading
import time
from functools import cached_property
from typing import Any

from resources.lib.modules.globals import g

GRAPHQL_URL = "https://graphql.imdb.com"
SUGGESTION_URL = "https://v2.sg.media-imdb.com/suggestion/{prefix}/{query}.json"

_thread_local = threading.local()
_graphql_lock = threading.Lock()
_rate_limit_until = 0.0

TITLE_FRAGMENT = """
    fragment Title on Title {
        id
        titleType { id }
        titleText { text }
        originalTitleText { text }
        primaryImage { url width height type }
        releaseYear { year }
        releaseDate { day month year }
        ratingsSummary { aggregateRating voteCount }
        runtime { seconds }
        plot { plotText { plainText } }
        genres { genres(limit: $genresLimit) { text } }
        principalCredits {
            category { text }
            credits {
                name {
                    id
                    nameText { text }
                    primaryImage { url width height type }
                }
                ... on Cast {
                    characters { name }
                }
            }
        }
        isAdult
    }
"""

def thread_imdb_api() -> "ImdbAPI":
    api = getattr(_thread_local, "imdb_api", None)
    if api is None:
        api = ImdbAPI()
        _thread_local.imdb_api = api
    return api


def imdb_runtime_enabled() -> bool:
    from resources.lib.meta.provider_settings import provider_enabled

    return provider_enabled("imdb")


def _random_digit_str(length: int) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def _auth_headers() -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-amzn-sessionid": f"{_random_digit_str(3)}-{_random_digit_str(7)}-{_random_digit_str(7)}",
        "x-imdb-client-name": "imdb-web-next",
        "x-imdb-user-language": "en-US",
        "x-imdb-user-country": "US",
    }
    return headers


def _auth_cookies() -> dict[str, str]:
    at_main = (g.get_setting("imdb.at-main") or "").strip()
    cookies = {
        "ubid-main": f"{_random_digit_str(3)}-{_random_digit_str(7)}-{_random_digit_str(7)}",
    }
    if at_main:
        cookies["at-main"] = at_main
    return cookies


def sanitize_graphql_response(response: Any) -> Any:
    if isinstance(response, list):
        return [sanitize_graphql_response(item) for item in response]
    if not isinstance(response, dict):
        return response

    edges = response.get("edges")
    node = response.get("node")
    text = response.get("text")

    title_type = response.get("titleType")
    if isinstance(title_type, dict) and title_type.get("id"):
        response = dict(response)
        tid = title_type["id"]
        if tid == "tvMiniSeries":
            tid = "tvSeries"
        response["titleType"] = tid

    if edges is not None:
        edges = sanitize_graphql_response(edges)
        if len(response) == 1:
            return edges
        response = dict(response)
        response["edges"] = edges
        return response
    if node is not None:
        return sanitize_graphql_response(node)
    if text is not None:
        return text
    if len(response) == 1:
        return sanitize_graphql_response(next(iter(response.values())))

    result = dict(response)
    for key, value in response.items():
        result[key] = sanitize_graphql_response(value)
    return result


def principal_credits_to_cast(principal_credits: list[dict] | None, *, limit: int = 15) -> list[dict]:
    if not principal_credits:
        return []
    cast: list[dict] = []
    order = 0
    for group in principal_credits:
        if not isinstance(group, dict):
            continue
        category = (group.get("category") or "").strip().lower()
        if category and category not in ("cast", "actor", "actress", "stars"):
            continue
        for credit in group.get("credits") or []:
            if not isinstance(credit, dict):
                continue
            name_blob = credit.get("name") or {}
            name = (name_blob.get("nameText") or "").strip()
            if not name:
                continue
            characters = credit.get("characters") or []
            role = ""
            if characters and isinstance(characters[0], dict):
                role = str(characters[0].get("name") or "")
            thumb = ""
            image = name_blob.get("primaryImage") or {}
            if isinstance(image, dict):
                thumb = str(image.get("url") or "")
            cast.append(
                {
                    "name": name,
                    "role": role,
                    "character": role,
                    "order": order,
                    "thumbnail": thumb,
                    "thumb": thumb,
                }
            )
            order += 1
            if order >= limit:
                return cast
    return cast


class ImdbAPI:
    def __init__(self) -> None:
        g.ensure_addon()

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        session.headers.update(
            {"User-Agent": f"{g.ADDON_ID}/{g.ADDON.getAddonInfo('version')}"}
        )
        retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_maxsize=50, pool_connections=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _wait_rate_limit(self) -> None:
        global _rate_limit_until
        now = time.monotonic()
        if now < _rate_limit_until:
            time.sleep(_rate_limit_until - now)

    def _set_rate_limit(self, seconds: float = 5.0) -> None:
        global _rate_limit_until
        _rate_limit_until = max(_rate_limit_until, time.monotonic() + seconds)

    def graphql(self, body: dict[str, Any]) -> dict[str, Any] | None:
        if not imdb_runtime_enabled():
            return None
        self._wait_rate_limit()
        payload = json.dumps(body)
        with _graphql_lock:
            try:
                response = self.session.post(
                    GRAPHQL_URL,
                    data=payload,
                    headers=_auth_headers(),
                    cookies=_auth_cookies(),
                    timeout=20,
                )
            except Exception:
                g.log_stacktrace()
                return None
        if response.status_code == 429:
            self._set_rate_limit(15.0)
            g.log("IMDb GraphQL rate limited (429)", "warning")
            return None
        if response.status_code != 200:
            g.log(
                f"IMDb GraphQL HTTP {response.status_code}: {(response.text or '')[:400]}",
                "warning",
            )
            return None
        try:
            data = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and data.get("errors"):
            g.log(f"IMDb GraphQL errors: {data.get('errors')}", "debug")
            return None
        return data.get("data") if isinstance(data, dict) else None

    def titles_batch(self, imdb_ids: list[str]) -> dict[str, dict[str, Any]]:
        ids = [str(i).strip() for i in imdb_ids if i]
        if not ids:
            return {}
        unique_ids = list(dict.fromkeys(ids))
        query = f"""
            query fn($ids: [ID!]!, $genresLimit: Int!) {{
                titles(ids: $ids) {{
                    ...Title
                }}
            }}
            {TITLE_FRAGMENT}
        """
        data = self.graphql(
            {
                "query": query,
                "operationName": "fn",
                "variables": {"ids": unique_ids, "genresLimit": 3},
            }
        )
        if not data:
            return {}
        rows = data.get("titles") or []
        if isinstance(rows, dict):
            rows = [rows]
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sanitized = sanitize_graphql_response(row)
            title_id = sanitized.get("id")
            if title_id:
                result[str(title_id)] = sanitized
        return result

    def get_cast_batch(self, imdb_ids: list[str], *, limit: int = 15) -> dict[str, list[dict]]:
        titles = self.titles_batch(imdb_ids)
        cast_by_id: dict[str, list[dict]] = {}
        for imdb_id, title in titles.items():
            credits = title.get("principalCredits")
            cast = principal_credits_to_cast(credits if isinstance(credits, list) else None, limit=limit)
            if cast:
                cast_by_id[imdb_id] = cast
        return cast_by_id

    @staticmethod
    def suggestion_title_by_id(imdb_id: str) -> dict[str, Any]:
        """Lookup a single title by IMDb id (used by getSources year confirm)."""
        normalized = str(imdb_id or "").strip()
        if normalized.startswith("tt"):
            normalized = normalized[2:]
        if not normalized:
            return {}
        api = thread_imdb_api()
        try:
            response = api.session.get(
                SUGGESTION_URL.format(prefix="t", query=normalized),
                timeout=15,
            )
            if response.status_code != 200:
                return {}
            payload = response.json()
            rows = payload.get("d") or []
            if rows and isinstance(rows[0], dict):
                return rows[0]
        except Exception:
            g.log("Failed to get IMDb suggestion by id", "debug")
        return {}
