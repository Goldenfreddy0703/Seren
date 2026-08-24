"""Simkl API client — auth, sync, search, scrobble. Keys from context.prism/info.db or settings."""
from __future__ import annotations

import json
import os
import threading
from functools import cached_property, wraps
from typing import Any
from urllib import parse

import xbmcgui

from resources.lib.common import tools
from resources.lib.database.cache import use_cache
from resources.lib.database.keys import get_client_id
from resources.lib.modules.exceptions import RanOnceAlready
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g

SIMKL_API_URL = "https://api.simkl.com"

_thread_local = threading.local()


def thread_simkl_api() -> "SimklAPI":
    """One Simkl client + HTTP connection pool per worker thread (safe for parallel milling)."""
    api = getattr(_thread_local, "simkl_api", None)
    if api is None:
        api = SimklAPI()
        _thread_local.simkl_api = api
    return api

# Simkl path segments for GET /sync/playback/{type}
PLAYBACK_PATH_TYPES = {
    "movie": "movies",
    "movies": "movies",
    "episode": "episodes",
    "episodes": "episodes",
}


def simkl_guard_response(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        import requests

        try:
            response = func(*args, **kwargs)
            if response is None:
                return None
            if response.status_code in (200, 201, 204):
                return response
            g.log(
                f"Simkl HTTP {response.status_code} for {response.url.split('?')[0]}",
                "warning" if response.status_code != 404 else "debug",
            )
            return None
        except requests.exceptions.ConnectionError:
            return None
        except Exception:
            xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30024).format("Simkl"))
            if g.get_runtime_setting("run.mode") == "test":
                raise
            g.log_stacktrace()
            return None

    return wrapper


class SimklAPI:
    ApiUrl = SIMKL_API_URL
    username_setting_key = "simkl.username"

    http_codes = {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        429: "Too Many Requests",
        500: "Internal Server Error",
    }

    def __init__(self):
        self._load_settings()

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter

        g.ensure_addon()
        session = requests.Session()
        session.headers.update({"User-Agent": f"{g.ADDON_ID}/{g.ADDON.getAddonInfo('version')}"})
        adapter = HTTPAdapter(pool_maxsize=50, pool_connections=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @cached_property
    def client_id(self) -> str:
        return get_client_id("Simkl") or ""

    @cached_property
    def meta_hash(self):
        return tools.md5_hash([self.client_id, g.get_language_code()])

    def _load_settings(self):
        self.access_token = g.get_setting("simkl.auth")
        self.username = g.get_setting(self.username_setting_key)

    def _save_settings(self, response: dict):
        if response.get("access_token"):
            g.set_setting("simkl.auth", response["access_token"])
            self.access_token = response["access_token"]
        if response.get("username"):
            g.set_setting(self.username_setting_key, response["username"])
            self.username = response["username"]

    def _get_headers(self, authorized: bool = True) -> dict:
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
        }
        if authorized and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _cdn_query(self) -> dict:
        g.ensure_addon()
        version = getattr(g, "VERSION", None) or g.ADDON.getAddonInfo("version")
        return {
            "client_id": self.client_id,
            "app-name": g.ADDON_ID,
            "app-version": version,
        }

    @simkl_guard_response
    def get(self, url, authorized: bool = True, **params):
        timeout = params.pop("timeout", 15)
        return self.session.get(
            parse.urljoin(self.ApiUrl, url),
            params=params or None,
            headers=self._get_headers(authorized=authorized),
            timeout=timeout,
        )

    @simkl_guard_response
    def post(self, url, json_data=None, authorized: bool = True, **params):
        timeout = params.pop("timeout", 15)
        return self.session.post(
            parse.urljoin(self.ApiUrl, url),
            params=params or None,
            json=json_data,
            headers=self._get_headers(authorized=authorized),
            timeout=timeout,
        )

    def get_json(self, url, authorized: bool = True, **params):
        response = self.get(url, authorized=authorized, **params)
        if response is None or not response.text:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return None

    @staticmethod
    def parse_pagination_headers(response) -> dict[str, int]:
        """Parse Simkl ``X-Pagination-*`` headers from an HTTP response."""
        if response is None:
            return {}
        mapping = (
            ("X-Pagination-Page", "page"),
            ("X-Pagination-Limit", "limit"),
            ("X-Pagination-Page-Count", "page_count"),
            ("X-Pagination-Item-Count", "item_count"),
        )
        parsed: dict[str, int] = {}
        for header_name, key in mapping:
            value = response.headers.get(header_name)
            if value is None:
                continue
            try:
                parsed[key] = int(value)
            except (TypeError, ValueError):
                continue
        return parsed

    def get_json_with_pagination(self, url, authorized: bool = True, **params) -> tuple[Any, dict[str, int]]:
        """Return ``(json_body, pagination)`` with Simkl pagination headers when present."""
        response = self.get(url, authorized=authorized, **params)
        if response is None or not response.text:
            return None, {}
        try:
            body = response.json()
        except json.JSONDecodeError:
            return None, SimklAPI.parse_pagination_headers(response)
        return body, SimklAPI.parse_pagination_headers(response)

    @use_cache(cache_hours=300 / 3600)
    def get_json_cached(self, url, authorized: bool = True, **params):
        return self.get_json(url, authorized=authorized, **params)

    def auth(self):
        """Device PIN OAuth flow with QR dialog."""
        if not self.client_id:
            xbmcgui.Dialog().ok(g.ADDON_NAME, "Simkl client_id missing from context.prism/info.db")
            return False

        from resources.lib.modules.qr_auth import auth_progress_percent, open_auth_dialog, wait_auth_interval

        params = self._cdn_query()
        response = self.get("/oauth/pin", authorized=False, **params)
        if response is None:
            return False

        try:
            device_code = response.json()
        except json.JSONDecodeError:
            g.log("Simkl pin response was not valid JSON", "error")
            return False

        if not isinstance(device_code, dict) or device_code.get("result") != "OK":
            g.log(f"Simkl pin request failed: {device_code}", "error")
            xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30023))
            return False

        user_code = str(device_code.get("user_code") or "").strip()
        if not user_code:
            g.log(f"Simkl pin response missing user_code: {device_code}", "error")
            xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30023))
            return False

        verification_url = (
            device_code.get("verification_uri")
            or device_code.get("verification_url")
            or "https://simkl.com/pin/"
        )

        interval = int(device_code.get("interval", 5))
        expires_in = int(device_code.get("expires_in", 900))
        attempts = max(1, expires_in // interval)

        heading = f"{g.ADDON_NAME}: {g.get_language_string(30131).rstrip('.')}"
        progress = open_auth_dialog(heading, verification_url, user_code=user_code)

        try:
            for i in range(attempts):
                if progress.iscanceled():
                    return False
                progress.update(auth_progress_percent(attempts - i, attempts))

                pin_response = self.get(
                    f"/oauth/pin/{user_code}",
                    authorized=False,
                    **params,
                )
                if pin_response is None:
                    if not wait_auth_interval(interval, progress):
                        return False
                    continue

                try:
                    pin_data = pin_response.json()
                except json.JSONDecodeError:
                    if not wait_auth_interval(interval, progress):
                        return False
                    continue

                if pin_data.get("result") == "OK" and pin_data.get("access_token"):
                    self.access_token = pin_data["access_token"]
                    save = {"access_token": pin_data["access_token"]}
                    settings_response = self.post("/users/settings", json_data={})
                    if settings_response is not None:
                        try:
                            save["username"] = settings_response.json()["user"]["name"]
                        except (KeyError, TypeError, json.JSONDecodeError):
                            pass
                    if not save.get("username"):
                        save["username"] = "Simkl User"
                    self._save_settings(save)
                    xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30273))
                    self._queue_sync_after_auth()
                    return True
                if not wait_auth_interval(interval, progress):
                    return False
        finally:
            progress.close()

        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30023))
        return False

    @staticmethod
    def _queue_sync_after_auth():
        """Pull Simkl library + playback state immediately after a successful login."""
        import xbmc

        xbmc.executebuiltin(
            'RunPlugin("plugin://plugin.video.prism/?action=syncSimklActivities&force=true")'
        )

    def revoke(self):
        g.set_setting("simkl.auth", "")
        g.set_setting(self.username_setting_key, "")
        self.access_token = None
        self.username = None

    def is_authenticated(self) -> bool:
        return bool(self.access_token)

    def search(self, query: str, media_type: str = "movie", limit: int = 25, *, exact: bool | None = None):
        """Search Simkl. media_type: movie, tv, anime."""
        from resources.lib.simkl.search import simkl_search_exact_enabled

        if exact is None:
            exact = simkl_search_exact_enabled()
        endpoint = {"movie": "movie", "tv": "tv", "anime": "anime"}.get(media_type, "movie")
        params: dict[str, str | int] = {
            "q": query,
            "limit": limit,
            "extended": "full",
        }
        if exact:
            params["exact"] = "true"
        return self.get_json(
            f"/search/{endpoint}",
            **params,
        )

    def get_activities(self):
        return self.get_json("/sync/activities")

    def get_changes(self, date_from: str | None = None, media_types: str | None = None):
        """Catalog IDs whose metadata changed recently (public; pair with watchlist intersect)."""
        params: dict[str, str] = {}
        if date_from:
            params["date_from"] = date_from
        if media_types:
            params["type"] = media_types
        return self.get_json("/changes", authorized=False, client_id=self.client_id, **params)

    def get_all_items(
        self,
        media_type: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        **params,
    ):
        url = "/sync/all-items/"
        if media_type:
            url += f"{media_type}/"
            if status:
                url += f"{status}/"
        query = dict(params)
        if date_from:
            query["date_from"] = date_from
        query.update(self._cdn_query())
        return self.get_json(url, **query)

    @use_cache(cache_hours=24)
    def get_tv_episodes(self, simkl_id: int, slug: str | None = None):
        from resources.lib.simkl.ids import tv_episodes_api_path

        return self.get_json(
            tv_episodes_api_path(simkl_id, slug),
            authorized=False,
            client_id=self.client_id,
        )

    @use_cache(cache_hours=24)
    def get_anime_episodes(self, simkl_id: int, extended: str | None = None, slug: str | None = None):
        from resources.lib.simkl.ids import anime_episodes_api_path

        params = {"client_id": self.client_id}
        if extended:
            params["extended"] = extended
        return self.get_json(anime_episodes_api_path(simkl_id, slug), authorized=False, **params)

    def get_show_json(self, simkl_id: int, slug: str | None = None, **params):
        from resources.lib.simkl.ids import show_api_path

        return self.get_json(show_api_path(simkl_id, slug), **params)

    def get_movie_json(self, simkl_id: int, slug: str | None = None, **params):
        from resources.lib.simkl.ids import movie_api_path

        return self.get_json(movie_api_path(simkl_id, slug), **params)

    def post_json(self, url, json_data=None, authorized: bool = True, **params):
        response = self.post(url, json_data=json_data, authorized=authorized, **params)
        if response is None or not response.text:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return {}

    @simkl_guard_response
    def delete(self, url, authorized: bool = True, **params):
        timeout = params.pop("timeout", 15)
        return self.session.delete(
            parse.urljoin(self.ApiUrl, url),
            params=params or None,
            headers=self._get_headers(authorized=authorized),
            timeout=timeout,
        )

    def delete_request(self, url, authorized: bool = True, **params):
        return self.delete(url, authorized=authorized, **params)

    def add_to_history(self, payload: dict):
        return self.post_json("/sync/history", payload)

    def remove_from_history(self, payload: dict):
        return self.post_json("/sync/history/remove", payload)

    def add_to_list(self, payload: dict):
        return self.post_json("/sync/add-to-list", payload)

    def add_ratings(self, payload: dict):
        return self.post_json("/sync/ratings", payload)

    def remove_ratings(self, payload: dict):
        return self.post_json("/sync/ratings/remove", payload)

    def scrobble_start(self, payload: dict):
        return self.post("/scrobble/start", json_data=payload)

    def scrobble_pause(self, payload: dict):
        return self.post("/scrobble/pause", json_data=payload)

    def scrobble_stop(self, payload: dict):
        return self.post("/scrobble/stop", json_data=payload)

    def delete_playback(self, playback_id):
        return self.delete(f"/sync/playback/{playback_id}")

    def get_playback(self, media_type: str | None = None, date_from: str | None = None, **params):
        if media_type:
            segment = PLAYBACK_PATH_TYPES.get(media_type, media_type)
            url = f"/sync/playback/{segment}"
        else:
            url = "/sync/playback"
        query = dict(params)
        if date_from:
            query["date_from"] = date_from
        # Default API hide_watched=true drops playbacks if Simkl also has watch history.
        query.setdefault("hide_watched", "false")
        return self.get_json(url, **query)

    @use_cache(cache_hours=168)
    def redirect_simkl_id(
        self,
        *,
        imdb: str | None = None,
        type: str | None = None,
        tmdb: int | None = None,
    ) -> tuple[int, str] | None:
        """Resolve external id via GET /redirect (read Location header, do not follow)."""
        import re

        params = dict(self._cdn_query())
        params["to"] = "simkl"
        if imdb:
            from resources.lib.simkl.field_map import _normalize_imdb_id

            normalized = _normalize_imdb_id(imdb)
            if normalized:
                params["imdb"] = normalized
        if tmdb is not None:
            params["tmdb"] = int(tmdb)
        if type in ("movie", "tv"):
            params["type"] = type
        if not any(key in params for key in ("imdb", "tmdb")):
            return None
        try:
            response = self.session.get(
                parse.urljoin(self.ApiUrl, "/redirect"),
                params=params,
                headers=self._get_headers(authorized=False),
                allow_redirects=False,
                timeout=15,
            )
        except Exception:
            g.log_stacktrace()
            return None
        if response is None or response.status_code not in (301, 302, 303, 307, 308):
            return None
        location = response.headers.get("Location") or ""
        match = re.search(r"/(movies|tv|anime)/(\d+)", location)
        if not match:
            return None
        segment, simkl_id = match.group(1), int(match.group(2))
        catalog = {"movies": "movie", "tv": "tv", "anime": "anime"}.get(segment, "tv")
        return simkl_id, catalog
