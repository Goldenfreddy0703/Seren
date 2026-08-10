"""
Offcloud debrid service integration for Prism.
API Documentation: https://offcloud.com/api
"""

import re
import time
from functools import cached_property

import xbmc
import xbmcgui

from resources.lib.common import source_utils
from resources.lib.modules.globals import g

OC_TOKEN_KEY = "oc.token"
OC_USERNAME_KEY = "oc.username"
OC_STATUS_KEY = "oc.premiumstatus"


class OffCloud:
    """Offcloud API wrapper for Prism."""

    def __init__(self):
        self.base_url = "https://offcloud.com/api/"
        self.oauth_base = "https://offcloud.com/oauth/"
        self._load_settings()
        self._last_request_id = None
        self._from_cloud_queue = False

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries, pool_maxsize=100))
        return session

    def _load_settings(self):
        self.token = g.get_setting(OC_TOKEN_KEY)

    def _get_headers(self, include_content_type=True):
        headers = {"User-Agent": f"Prism/{g.VERSION}"}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, url, params=None):
        full_url = self.base_url + url if not url.startswith("http") else url
        if not self.token:
            g.log("No Offcloud token found", "warning")
            return None
        try:
            response = self.session.get(
                full_url, params=params, headers=self._get_headers(), timeout=30
            )
        except Exception as e:
            g.log(f"Offcloud request error: {e}", "error")
            return None
        if not response.ok:
            g.log(f"Offcloud API returned {response.status_code}: {response.text}", "error")
            return None
        try:
            return response.json()
        except (ValueError, AttributeError):
            return None

    def _post(self, url, json_data=None, data=None):
        full_url = self.base_url + url if not url.startswith("http") else url
        if not self.token:
            g.log("No Offcloud token found", "warning")
            return None
        include_content_type = json_data is not None and data is None
        try:
            response = self.session.post(
                full_url,
                json=json_data,
                data=data,
                headers=self._get_headers(include_content_type=include_content_type),
                timeout=30,
            )
        except Exception as e:
            g.log(f"Offcloud request error: {e}", "error")
            return None
        if not response.ok:
            g.log(f"Offcloud API returned {response.status_code}: {response.text}", "error")
            return None
        try:
            return response.json()
        except (ValueError, AttributeError):
            return None

    # =========================================================================
    # Authentication
    # =========================================================================

    def auth(self):
        """Authenticate with Offcloud using OAuth device-code flow."""
        from resources.lib.modules.qr_auth import auth_progress_percent, open_auth_dialog

        resp = self._request_device_code()
        if not resp:
            xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30065))
            return

        device_code = resp.get("device_code", "")
        user_code = resp.get("user_code", "")
        verification_url = resp.get("verification_uri") or "https://offcloud.com/activate"
        interval = int(resp.get("interval", 5)) or 5
        oauth_timeout = int(resp.get("expires_in", 600)) or 600
        token_ttl = oauth_timeout
        poll_counter = 0

        success = False
        progress = open_auth_dialog(
            f"{g.ADDON_NAME}: Offcloud Auth",
            verification_url,
            user_code=user_code,
        )
        try:
            while not success and token_ttl > 0 and not progress.iscanceled():
                xbmc.sleep(1000)
                token_ttl -= 1
                poll_counter += 1
                if poll_counter >= interval:
                    poll_counter = 0
                    success = self._auth_loop(device_code)
                progress.update(auth_progress_percent(token_ttl, oauth_timeout))
        finally:
            progress.close()

        if not success:
            return

        g.set_setting(OC_TOKEN_KEY, self.token)
        self._save_user_status()
        xbmcgui.Dialog().ok(g.ADDON_NAME, f"Offcloud {g.get_language_string(30020)}")
        g.log("Authorised Offcloud successfully", "info")

    def _request_device_code(self):
        try:
            response = self.session.post(
                self.oauth_base + "device/code",
                json={},
                headers={"User-Agent": f"Prism/{g.VERSION}", "Content-Type": "application/json"},
                timeout=20,
            )
        except Exception as e:
            g.log(f"Offcloud auth start error: {e}", "error")
            return None
        if not response.ok:
            g.log(f"Offcloud auth start returned {response.status_code}: {response.text}", "error")
            return None
        try:
            return response.json()
        except (ValueError, AttributeError):
            return None

    def _auth_loop(self, device_code):
        try:
            response = self.session.post(
                self.oauth_base + "token",
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                },
                headers={"User-Agent": f"Prism/{g.VERSION}", "Content-Type": "application/json"},
                timeout=20,
            )
        except Exception as e:
            g.log(f"Offcloud auth poll error: {e}", "error")
            return False

        if response.ok:
            try:
                token = response.json().get("access_token")
            except (ValueError, AttributeError):
                return False
            if token:
                self.token = token
                return True
            return False

        try:
            error = response.json().get("error", "")
        except (ValueError, AttributeError):
            error = ""
        if error in ("authorization_pending", "slow_down"):
            return False
        return False

    def _save_user_status(self):
        account_info = self.account_info()
        if account_info:
            username = account_info.get("email", "Unknown")
            if account_info.get("is_premium"):
                expiration = account_info.get("expiration_date") or ""
                status = f"Premium ({expiration})" if expiration else "Premium"
            else:
                status = "Free"
            g.set_setting(OC_USERNAME_KEY, username)
            g.set_setting(OC_STATUS_KEY, status)

    # =========================================================================
    # Account
    # =========================================================================

    def account_info(self):
        return self._get("account/info")

    def get_account_status(self):
        account_info = self.account_info()
        if account_info:
            return "premium" if account_info.get("is_premium") else "free"
        return "unknown"

    def days_remaining(self):
        import datetime

        try:
            account_info = self.account_info()
            if not account_info or not account_info.get("is_premium"):
                return None
            expiration = account_info.get("expiration_date")
            if not expiration:
                return None
            expires = datetime.datetime.strptime(expiration, "%Y-%m-%d")
            return max((expires - datetime.datetime.utcnow()).days, 0)
        except Exception as e:
            g.log(f"Error getting Offcloud days remaining: {e}", "error")
            return None

    # =========================================================================
    # Cache / torrent operations
    # =========================================================================

    def check_hash(self, hash_list):
        if not hash_list:
            return []
        result = self._post("cache", json_data={"hashes": [h.lower() for h in hash_list]})
        if not result:
            return []
        return result.get("cachedItems", []) if isinstance(result, dict) else []

    def get_cached_files(self, magnet):
        result = self._post("cache/download", json_data={"url": magnet})
        if isinstance(result, list):
            return result
        return []

    def add_to_cloud(self, url):
        return self._post("cloud", json_data={"url": url})

    def add_magnet(self, magnet):
        """Alias for cache assist compatibility."""
        return self.add_to_cloud(magnet)

    def cloud_status(self, request_id):
        result = self._post("cloud/status", json_data={"requestId": request_id})
        if isinstance(result, dict):
            return result.get("status") or result
        return result

    def wait_for_cloud_download(self, request_id, max_attempts=120, interval=2):
        for _ in range(max_attempts):
            status = self.cloud_status(request_id)
            if not status:
                time.sleep(interval)
                continue
            state = status.get("status", "")
            if state == "downloaded":
                return True
            if state == "error":
                g.log(f"Offcloud download error for {request_id}: {status.get('message')}", "error")
                return False
            time.sleep(interval)
        return False

    def cloud_explore(self, request_id, detailed=True):
        if detailed:
            result = self._get(f"cloud/explore/{request_id}", params={"format": "detailed"})
            if isinstance(result, dict):
                if result.get("error") == "Bad archive":
                    return self._explore_single_file(request_id)
                return result.get("files", [])
            if isinstance(result, list):
                return result
            return []
        return self._explore_single_file(request_id)

    def _explore_single_file(self, request_id):
        result = self._get(f"cloud/explore/{request_id}")
        if isinstance(result, list) and result:
            status = self.cloud_status(request_id) or {}
            filename = status.get("fileName", "file")
            return [
                {
                    "id": "1",
                    "name": filename,
                    "path": filename,
                    "size": 0,
                    "url": result[0],
                }
            ]
        return []

    @staticmethod
    def _normalize_file(file_item, request_id=None):
        folder = file_item.get("folder", [])
        if isinstance(folder, str):
            folder = [folder] if folder else []
        filename = file_item.get("filename") or file_item.get("name", "")
        path = file_item.get("path")
        if not path:
            path = "/".join(folder + [filename]) if folder else filename
        file_id = file_item.get("id") or path or filename
        return {
            "path": path,
            "release_title": path.rsplit("/", 1)[-1] if path else filename,
            "short_name": filename or path.rsplit("/", 1)[-1],
            "name": path,
            "size": file_item.get("size", 0),
            "id": str(file_id),
            "url": file_item.get("url"),
            "request_id": request_id,
        }

    def _filter_video_files(self, files):
        return [
            f
            for f in files
            if "sample" not in (f.get("path") or f.get("name", "")).lower()
            and source_utils.is_file_ext_valid(f.get("path") or f.get("name", ""))
        ]

    def get_torrent_files(self, hash_value=None, magnet=None):
        self._from_cloud_queue = False
        self._last_request_id = None

        if not magnet and hash_value:
            magnet = f"magnet:?xt=urn:btih:{hash_value}"
        if not magnet:
            g.log("Offcloud: No magnet or hash provided", "error")
            return {}

        hash_value = hash_value or self._hash_from_magnet(magnet)
        cached_hashes = self.check_hash([hash_value]) if hash_value else []
        is_cached = hash_value and hash_value.lower() in [h.lower() for h in cached_hashes]

        if is_cached:
            raw_files = self.get_cached_files(magnet)
            files = self._filter_video_files([self._normalize_file(f) for f in raw_files])
            return {"request_id": None, "files": files, "cached": True}

        response = self.add_to_cloud(magnet)
        if not response or not response.get("requestId"):
            g.log(f"Offcloud: Failed to add magnet to cloud: {response}", "error")
            return {}

        request_id = response["requestId"]
        self._last_request_id = request_id
        self._from_cloud_queue = True

        if not self.wait_for_cloud_download(request_id):
            return {}

        raw_files = self.cloud_explore(request_id, detailed=True)
        files = self._filter_video_files([self._normalize_file(f, request_id) for f in raw_files])
        return {"request_id": request_id, "files": files, "cached": False}

    def resolve_torrent_file(self, request_id, file_id, file_info=None):
        if file_info and file_info.get("url"):
            return file_info["url"]
        if request_id:
            for file_item in self.cloud_explore(request_id, detailed=True):
                normalized = self._normalize_file(file_item, request_id)
                if str(normalized.get("id")) == str(file_id):
                    return normalized.get("url")
        return None

    def list_cloud_history(self):
        result = self._get("cloud/history")
        return result if isinstance(result, list) else []

    def remove_requests(self, request_ids):
        if not request_ids:
            return False
        if not isinstance(request_ids, list):
            request_ids = [request_ids]
        result = self._post("cloud/remove", json_data={"requests": request_ids})
        return bool(result and result.get("success"))

    def delete_torrent(self, request_id):
        return self.remove_requests(request_id)

    def resolve_hoster(self, link):
        self._from_cloud_queue = True
        response = self.add_to_cloud(link)
        if not response or not response.get("requestId"):
            return None
        request_id = response["requestId"]
        self._last_request_id = request_id
        if not self.wait_for_cloud_download(request_id):
            return None
        files = self.cloud_explore(request_id, detailed=True)
        if not files:
            return None
        normalized = self._normalize_file(files[0], request_id)
        return normalized.get("url")

    # =========================================================================
    # Hosters
    # =========================================================================

    def get_hosters(self, hosters):
        sites = self._get("sites")
        if not isinstance(sites, list):
            return
        host_list = []
        for site in sites:
            if site.get("status") != "up":
                continue
            for alias in site.get("aliases", [site.get("name", "")]):
                if alias:
                    host_list.append((alias, alias.split(".")[0]))
        hosters["premium"]["offcloud"] = host_list

    @staticmethod
    def _hash_from_magnet(magnet):
        match = re.search(r"btih:([a-fA-F0-9]{40})", magnet or "")
        if match:
            return match.group(1).lower()
        match = re.search(r"btih:([a-zA-Z0-9]{32})", magnet or "")
        if match:
            import base64
            try:
                return base64.b16encode(base64.b32decode(match.group(1).upper())).decode().lower()
            except Exception:
                return match.group(1).lower()
        return None

    @staticmethod
    def is_service_enabled():
        return (
            g.get_bool_setting("offcloud.enabled")
            and g.get_setting(OC_TOKEN_KEY) is not None
            and g.get_setting(OC_TOKEN_KEY) != ""
        )
