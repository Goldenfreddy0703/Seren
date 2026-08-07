"""IMDb web GraphQL + suggestion API (a4kstreaming-compatible patterns)."""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime
from functools import cached_property
from typing import Any

from resources.lib.database.cache import use_cache
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

# Canonical IMDb title genres for advancedTitleSearch allGenreIds.
# Source: https://help.imdb.com/article/contribution/titles/genres/GZDRMS6R742JRGAG
# Help displays "Film Noir" / "Game Show"; graphql.imdb.com expects "Film-Noir" / "Game-Show".
IMDB_GENRES: tuple[str, ...] = (
    "Action",
    "Adult",
    "Adventure",
    "Animation",
    "Biography",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "Film-Noir",
    "Game-Show",
    "History",
    "Horror",
    "Music",
    "Musical",
    "Mystery",
    "News",
    "Reality-TV",
    "Romance",
    "Sci-Fi",
    "Short",
    "Sport",
    "Talk-Show",
    "Thriller",
    "War",
    "Western",
)

ADULT_BLOCKED_IMDB_GENRES = frozenset({"Adult"})


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


def _imdb_sort_for_segment(segment: str) -> tuple[str, str]:
    if segment == "release-date":
        return "RELEASE_DATE", "DESC"
    if segment == "rank":
        return "USER_RATING", "DESC"
    if segment == "voted":
        return "POPULARITY", "DESC"
    return "POPULARITY", "ASC"


def _title_types_for_catalog(catalog: str) -> list[str]:
    if catalog == "movie":
        return ["movie", "tvMovie"]
    return ["tvSeries", "tvMiniSeries"]


def get_imdb_genres(catalog: str) -> list[dict[str, str]]:
    from resources.lib.simkl.browse import adult_content_enabled

    if catalog not in ("movie", "tv"):
        return []
    visible = IMDB_GENRES
    if not adult_content_enabled():
        visible = tuple(g for g in IMDB_GENRES if g not in ADULT_BLOCKED_IMDB_GENRES)
    return [{"id": name, "name": name} for name in visible]


def _imdb_text_field(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "plainText", "markdown", "displayDate", "date"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _imdb_image_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def _imdb_known_for_titles(person_row: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    known = person_row.get("knownFor")
    if isinstance(known, dict):
        for edge in known.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            title = node.get("title")
            if not isinstance(title, dict):
                continue
            name = _imdb_text_field(title.get("titleText") or title.get("title"))
            if name:
                titles.append(name)
        return titles
    if not isinstance(known, list):
        return titles
    for item in known:
        if not isinstance(item, dict):
            continue
        title = item.get("title") if isinstance(item.get("title"), dict) else item
        if not isinstance(title, dict):
            continue
        name = _imdb_text_field(title.get("titleText") or title.get("title"))
        if name:
            titles.append(name)
    return titles


def imdb_person_summary_from_raw(person_row: dict[str, Any] | None) -> str:
    """Extract biography/summary from raw IMDb GraphQL Name object (before sanitize)."""
    if not isinstance(person_row, dict):
        return ""

    bios_block = person_row.get("bios")
    if isinstance(bios_block, dict):
        for edge in bios_block.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            text = _imdb_text_field(node.get("text"))
            if text:
                return text

    bio_block = person_row.get("bio")
    if isinstance(bio_block, dict):
        text = _imdb_text_field(bio_block.get("text"))
        if text:
            return text

    lines: list[str] = []
    birth_block = person_row.get("birthDate")
    if isinstance(birth_block, dict):
        birth = _imdb_text_field(birth_block)
        if birth:
            lines.append(f"Born: {birth}")

    birthplace_block = person_row.get("birthLocation")
    if isinstance(birthplace_block, dict):
        birthplace = _imdb_text_field(birthplace_block)
        if birthplace:
            lines.append(f"Birthplace: {birthplace}")

    titles = _imdb_known_for_titles(person_row)
    if titles:
        lines.append("Known for: " + ", ".join(titles[:8]))
    return "\n\n".join(lines)


def imdb_person_summary(person_row: dict[str, Any] | None) -> str:
    """Biography text, or a short IMDb-built summary when bio is unavailable."""
    if not isinstance(person_row, dict):
        return ""
    if person_row.get("bio") and isinstance(person_row.get("bio"), dict):
        return imdb_person_summary_from_raw(person_row)

    bio = person_row.get("bio")
    if isinstance(bio, str) and bio.strip():
        return bio.strip()
    if isinstance(bio, dict):
        text = _imdb_text_field(bio)
        if text:
            return text

    bios = person_row.get("bios")
    if isinstance(bios, list):
        for item in bios:
            if not isinstance(item, dict):
                continue
            text = _imdb_text_field(item.get("text") or item.get("plainText") or item)
            if text:
                return text

    lines: list[str] = []
    birth = _imdb_text_field(person_row.get("birthDate"))
    if birth:
        lines.append(f"Born: {birth}")
    birthplace = _imdb_text_field(person_row.get("birthLocation"))
    if birthplace:
        lines.append(f"Birthplace: {birthplace}")
    known = _imdb_known_for_titles(person_row)
    if known:
        lines.append("Known for: " + ", ".join(known[:8]))
    return "\n\n".join(lines)


def imdb_person_display_fields(person_row: dict[str, Any] | None) -> dict[str, Any]:
    """Map IMDb Name GraphQL row to picker fields (prefer raw GraphQL shape)."""
    if not isinstance(person_row, dict):
        return {}

    name_text = ""
    name_block = person_row.get("nameText")
    if isinstance(name_block, dict):
        name_text = str(name_block.get("text") or "").strip()
    if not name_text:
        name_text = _imdb_text_field(person_row.get("nameText"))

    image_url = None
    image_block = person_row.get("primaryImage")
    if isinstance(image_block, dict):
        image_url = image_block.get("url")
    if not image_url:
        image_url = _imdb_image_url(person_row.get("primaryImage"))

    summary = imdb_person_summary_from_raw(person_row)
    if not summary:
        summary = imdb_person_summary(person_row)

    fields: dict[str, Any] = {
        "name": name_text or None,
        "biography": summary or None,
        "profile_path": image_url,
    }
    return {key: value for key, value in fields.items() if value}


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

    def suggestion_search(self, query: str) -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if not text or not imdb_runtime_enabled():
            return []
        prefix = text[:1].lower() or "a"
        url = SUGGESTION_URL.format(prefix=prefix, query=text)
        try:
            response = self.session.get(url, timeout=15)
        except Exception:
            g.log_stacktrace()
            return []
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        results: list[dict[str, Any]] = []
        for row in payload.get("d") or []:
            if not isinstance(row, dict):
                continue
            imdb_id = row.get("id")
            if not imdb_id:
                continue
            title_type = row.get("q", "")
            if isinstance(title_type, str):
                title_type = title_type.lower()
            type_map = {
                "feature": "movie",
                "tv movie": "movie",
                "video": "movie",
                "tv series": "tvSeries",
                "tv mini-series": "tvSeries",
            }
            mapped_type = type_map.get(title_type)
            if str(imdb_id).startswith("nm"):
                mapped_type = "person"
            item: dict[str, Any] = {
                "id": str(imdb_id),
                "name": row.get("l") or row.get("t") or "",
                "titleType": mapped_type or title_type,
            }
            image = row.get("i") or {}
            if isinstance(image, dict) and image.get("imageUrl"):
                item["primaryImage"] = {
                    "url": image.get("imageUrl"),
                    "width": image.get("width"),
                    "height": image.get("height"),
                }
            if row.get("y"):
                item["year"] = row.get("y")
            results.append(item)
        return results

    def search_people(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        people = [
            row
            for row in self.suggestion_search(query)
            if str(row.get("id", "")).startswith("nm")
        ]
        return people[:limit]

    def person_detail(self, person_id: str) -> dict[str, Any] | None:
        """Fetch one person from IMDb GraphQL (raw Name object)."""
        imdb_person = str(person_id).strip()
        if not imdb_person.startswith("nm"):
            imdb_person = f"nm{imdb_person}"
        query = """
            query fn($id: ID!) {
                name(id: $id) {
                    id
                    nameText { text }
                    primaryImage { url width height }
                    bios(first: 1) {
                        edges {
                            node {
                                text { plainText }
                            }
                        }
                    }
                    birthDate { date }
                    birthLocation { text }
                    knownFor(first: 8) {
                        edges {
                            node {
                                title {
                                    titleText { text }
                                }
                            }
                        }
                    }
                }
            }
        """
        data = self.graphql(
            {
                "query": query,
                "operationName": "fn",
                "variables": {"id": imdb_person},
            }
        )
        if not data:
            return None
        row = data.get("name")
        return row if isinstance(row, dict) else None

    def person_details_batch(self, person_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch biography + headshot for IMDb person ids (nm…)."""
        normalized_ids: list[str] = []
        for person_id in person_ids:
            raw = str(person_id or "").strip()
            if not raw:
                continue
            if not raw.startswith("nm"):
                raw = f"nm{raw}"
            normalized_ids.append(raw)
        if not normalized_ids:
            return {}

        unique_ids = list(dict.fromkeys(normalized_ids))
        result: dict[str, dict[str, Any]] = {}
        for person_id in unique_ids:
            row = self.person_detail(person_id)
            if row:
                result[str(person_id)] = row
        return result

    def advanced_title_search(
        self,
        catalog: str,
        genre_ids: list[str],
        *,
        limit: int,
        cursor: str | None = None,
        sort_segment: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        from resources.lib.simkl.menu_helpers import genre_sort_segment

        segment = sort_segment or genre_sort_segment(catalog)
        sort_by, sort_order = _imdb_sort_for_segment(segment)
        now = datetime.now()
        end_date = f"{now.year}-{str(now.month).zfill(2)}-{str(now.day).zfill(2)}"
        exclude = ["Reality-TV", "Game-Show"]
        if catalog in ("movie", "tv"):
            exclude.append("Animation")
        query = f"""
            query fn($limit: Int!, $paginationToken: String, $endDate: Date!, $genresLimit: Int!) {{
                advancedTitleSearch(
                    first: $limit
                    after: $paginationToken
                    constraints: {{
                        genreConstraint: {{
                            allGenreIds: {json.dumps(genre_ids)},
                            excludeGenreIds: {json.dumps(exclude)}
                        }},
                        releaseDateConstraint: {{ releaseDateRange: {{ end: $endDate }} }},
                        titleTypeConstraint: {{
                            anyTitleTypeIds: {json.dumps(_title_types_for_catalog(catalog))},
                            excludeTitleTypeIds: []
                        }}
                    }}
                    sort: {{ sortBy: {sort_by}, sortOrder: {sort_order} }}
                ) {{
                    titles: edges {{
                        node {{
                            title {{
                                ...Title
                            }}
                        }}
                    }}
                    pageInfo {{
                        hasNextPage
                        endCursor
                    }}
                }}
            }}
            {TITLE_FRAGMENT}
        """
        variables: dict[str, Any] = {
            "limit": int(limit),
            "endDate": end_date,
            "genresLimit": 5,
        }
        if cursor:
            variables["paginationToken"] = cursor
        data = self.graphql({"query": query, "operationName": "fn", "variables": variables})
        if not data:
            return [], cursor, False
        block = data.get("advancedTitleSearch") or {}
        titles: list[dict[str, Any]] = []
        for edge in block.get("titles") or []:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node") or {}
            title = node.get("title")
            if isinstance(title, dict):
                titles.append(sanitize_graphql_response(title))
        page_info = block.get("pageInfo") or {}
        has_next = bool(page_info.get("hasNextPage"))
        next_cursor = page_info.get("endCursor") if has_next else None
        return titles, next_cursor, has_next

    def person_credits(
        self,
        person_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        imdb_person = str(person_id).strip()
        if not imdb_person.startswith("nm"):
            imdb_person = f"nm{imdb_person}"
        query = f"""
            query fn($id: ID!, $limit: Int!, $paginationToken: ID, $genresLimit: Int!) {{
                name(id: $id) {{
                    credits(
                        first: $limit,
                        after: $paginationToken,
                        filter: {{ categories: ["actor", "actress"], credited: CREDITED_ONLY }}
                    ) {{
                        titles: edges {{
                            node {{
                                title {{
                                    ...Title
                                    series {{
                                        series {{
                                            id
                                            titleType {{ id }}
                                            titleText {{ text }}
                                            primaryImage {{ url width height type }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                        pageInfo {{
                            hasNextPage
                            endCursor
                        }}
                    }}
                }}
            }}
            {TITLE_FRAGMENT}
        """
        variables: dict[str, Any] = {"id": imdb_person, "limit": int(limit), "genresLimit": 3}
        if cursor:
            variables["paginationToken"] = cursor
        data = self.graphql({"query": query, "operationName": "fn", "variables": variables})
        if not data:
            return [], cursor, False
        name_block = data.get("name") or {}
        credits_block = name_block.get("credits") or {}
        titles: list[dict[str, Any]] = []
        for edge in credits_block.get("titles") or []:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node") or {}
            title = node.get("title")
            if not isinstance(title, dict):
                continue
            title = sanitize_graphql_response(title)
            if title.get("titleType") == "tvEpisode":
                series = title.get("series") or {}
                parent = series.get("series") if isinstance(series, dict) else None
                if isinstance(parent, dict) and parent.get("id"):
                    title = sanitize_graphql_response(parent)
            if title.get("titleType") in ("movie", "tvSeries", "tvMovie", "tvMiniSeries"):
                titles.append(title)
        page_info = credits_block.get("pageInfo") or {}
        has_next = bool(page_info.get("hasNextPage"))
        next_cursor = page_info.get("endCursor") if has_next else None
        return titles, next_cursor, has_next

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


@use_cache(cache_hours=24)
def cached_advanced_title_search(
    catalog: str,
    genre_key: str,
    limit: int,
    cursor: str | None,
    sort_segment: str,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    genres = [part.strip() for part in genre_key.split(",") if part.strip()]
    return thread_imdb_api().advanced_title_search(
        catalog,
        genres,
        limit=limit,
        cursor=cursor,
        sort_segment=sort_segment,
    )


@use_cache(cache_hours=24)
def cached_person_credits(
    person_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    return thread_imdb_api().person_credits(person_id, limit=limit, cursor=cursor)


def imdb_numeric_id(imdb_id: str | None) -> int | None:
    from resources.lib.simkl.field_map import _normalize_imdb_id

    normalized = _normalize_imdb_id(imdb_id)
    if not normalized or not normalized.startswith("tt"):
        return None
    digits = normalized[2:]
    if not digits.isdigit():
        return None
    return int(digits)
