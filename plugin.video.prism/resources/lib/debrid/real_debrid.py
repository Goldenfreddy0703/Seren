import time
from functools import cached_property

import xbmc
import xbmcgui

from resources.lib.common import source_utils
from resources.lib.common import tools
from resources.lib.database.cache import use_cache
from resources.lib.database.keys import get_client_id
from resources.lib.modules.exceptions import RanOnceAlready
from resources.lib.modules.exceptions import UnexpectedResponse
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g
import os

RD_AUTH_KEY = "rd.auth"
RD_STATUS_KEY = "rd.premiumstatus"
RD_REFRESH_KEY = "rd.refresh"
RD_EXPIRY_KEY = "rd.expiry"
RD_SECRET_KEY = "rd.secret"
RD_CLIENT_ID_KEY = "rd.client_id"
RD_USERNAME_KEY = "rd.username"


class RealDebrid:
    def __init__(self):
        self.oauth_url = "https://api.real-debrid.com/oauth/v2/"
        self.device_code_url = "device/code?{}"
        self.device_credentials_url = "device/credentials?{}"
        self.token_url = "token"
        self.device_code = ""
        self.oauth_timeout = 0
        self.oauth_time_step = 0
        self.base_url = "https://api.real-debrid.com/rest/1.0/"
        self.cache_check_results = {}
        self._load_settings()

    @cached_property
    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3 import Retry

        session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries, pool_maxsize=100))
        return session

    def _auth_loop(self):
        bootstrap_id = get_client_id("Real-Debrid") or self.client_id
        url = f"client_id={bootstrap_id}&code={self.device_code}"
        url = self.oauth_url + self.device_credentials_url.format(url)
        response = self.session.get(url).json()
        if "error" not in response and response.get("client_secret"):
            try:
                g.set_setting(RD_CLIENT_ID_KEY, response["client_id"])
                g.set_setting(RD_SECRET_KEY, response["client_secret"])
                self.client_secret = response["client_secret"]
                self.client_id = response["client_id"]
                return True
            except Exception:
                from resources.lib.modules.qr_auth import show_auth_failed

                show_auth_failed("Real-Debrid")
                raise
        return False

    def auth(self):
        from resources.lib.modules.qr_auth import (
            auth_progress_percent,
            capped_auth_timeout,
            open_auth_dialog,
            show_auth_timeout,
        )

        url = f"client_id={self.client_id}&new_credentials=yes"
        url = self.oauth_url + self.device_code_url.format(url)
        response = self.session.get(url).json()
        success = False
        cancelled = False
        verification_url = "https://real-debrid.com/device"
        progress = open_auth_dialog(
            f"{g.ADDON_NAME}: {g.get_language_string(30017)}",
            verification_url,
            user_code=response["user_code"],
        )
        try:
            self.oauth_timeout = capped_auth_timeout(response["expires_in"])
            token_ttl = self.oauth_timeout
            self.oauth_time_step = int(response["interval"])
            self.device_code = response["device_code"]
            while not success and token_ttl > 0 and not progress.iscanceled():
                xbmc.sleep(1000)
                if token_ttl % self.oauth_time_step == 0:
                    success = self._auth_loop()
                progress.update(auth_progress_percent(token_ttl, self.oauth_timeout))
                token_ttl -= 1
            cancelled = progress.iscanceled()
        finally:
            progress.close()

        if success:
            self.token_request()

            user_information = self.get_url("user")
            if user_information["type"] != "premium":
                xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30194))
        elif not cancelled:
            show_auth_timeout("Real-Debrid")

    def token_request(self):
        if not self.client_secret:
            return

        url = self.oauth_url + self.token_url
        response = self.session.post(
            url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": self.device_code,
                "grant_type": "http://oauth.net/grant_type/device/1.0",
            },
        ).json()
        self._save_settings(response)
        self._save_user_status()
        xbmcgui.Dialog().ok(g.ADDON_NAME, f"Real Debrid {g.get_language_string(30020)}")
        g.log("Authorised Real Debrid successfully", "info")

    def _save_settings(self, response):
        self.token = response["access_token"]
        self.refresh = response["refresh_token"]
        self.expiry = time.time() + int(response["expires_in"])
        g.set_setting(RD_AUTH_KEY, self.token)
        g.set_setting(RD_REFRESH_KEY, self.refresh)
        g.set_setting(RD_EXPIRY_KEY, self.expiry)

    def _save_user_status(self):
        username = self.get_url("user").get("username")
        status = self.get_account_status().title()
        g.set_setting(RD_USERNAME_KEY, username)
        g.set_setting(RD_STATUS_KEY, status)

    def _load_settings(self):
        bootstrap_id = get_client_id("Real-Debrid") or ""
        self.client_id = g.get_setting("rd.client_id") or bootstrap_id
        self.token = g.get_setting(RD_AUTH_KEY)
        self.refresh = g.get_setting(RD_REFRESH_KEY)
        self.expiry = g.get_float_setting(RD_EXPIRY_KEY)
        self.client_secret = g.get_setting(RD_SECRET_KEY)

    @staticmethod
    def _handle_error(response):
        g.log(f"Real Debrid API return a {response.status_code} response")
        g.log(response.text)
        g.log(response.request.url)

    def _is_response_ok(self, response):
        if response.ok:
            return True
        self._handle_error(response)
        return False

    def try_refresh_token(self, force=False):
        if not self.refresh:
            return
        if not force and self.expiry > float(time.time()):
            return

        try:
            with GlobalLock(self.__class__.__name__, True, self.token):
                url = f"{self.oauth_url}token"
                response = self.session.post(
                    url,
                    data={
                        "grant_type": "http://oauth.net/grant_type/device/1.0",
                        "code": self.refresh,
                        "client_secret": self.client_secret,
                        "client_id": self.client_id,
                    },
                )
                if not self._is_response_ok(response):
                    response = response.json()
                    g.notification(g.ADDON_NAME, "Failed to refresh RD token, please manually re-auth")
                    g.log(f"RD Refresh error: {response['error']}")
                    g.log(f"Invalid response from Real Debrid - {response}", "error")
                    return False
                response = response.json()
                self._save_settings(response)
                g.log("Real Debrid Token Refreshed")
                return True
        except RanOnceAlready:
            self._load_settings()
            return

    def _get_headers(self):
        headers = {
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post_url(self, url, post_data, fail_check=False):
        original_url = url
        url = self.base_url + url
        if not self.token:
            return None

        response = self.session.post(url, data=post_data, headers=self._get_headers(), timeout=10)
        if not self._is_response_ok(response) and not fail_check:
            self.try_refresh_token(True)
            response = self.post_url(original_url, post_data, fail_check=True)
        try:
            return response.json()
        except (ValueError, AttributeError):
            return response

    def get_url(self, url, fail_check=False):
        original_url = url
        url = self.base_url + url
        if not self.token:
            g.log("No Real Debrid Token Found")
            return None

        response = self.session.get(url, headers=self._get_headers(), timeout=10)

        if not self._is_response_ok(response) and not fail_check:
            self.try_refresh_token(True)
            response = self.get_url(original_url, fail_check=True)
        try:
            return response.json()
        except (ValueError, AttributeError):
            return response

    def delete_url(self, url, fail_check=False):
        original_url = url
        url = self.base_url + url
        if not self.token:
            g.log("No Real Debrid Token Found")
            return None

        response = self.session.delete(url, headers=self._get_headers(), timeout=10)

        if not self._is_response_ok(response) and not fail_check:
            self.try_refresh_token(True)
            response = self.delete_url(original_url, fail_check=True)
        try:
            return response.json()
        except (ValueError, AttributeError):
            return response

    def _get_url_no_refresh(self, url):
        """GET without token refresh — used for deprecated instantAvailability."""
        if not self.token:
            return None
        try:
            response = self.session.get(
                self.base_url + url,
                headers=self._get_headers(),
                timeout=10,
            )
            if response.status_code in (403, 429):
                return {"_rd_status": response.status_code}
            if not response.ok:
                g.log(f"RD instantAvailability HTTP {response.status_code}", "warning")
                return None
            return response.json()
        except Exception as exc:
            g.log(f"RD instantAvailability request error: {exc}", "warning")
            return None

    def check_cache_batch(self, hash_list):
        cached_hashes = set()
        chunk_size = 50
        for index in range(0, len(hash_list), chunk_size):
            chunk = hash_list[index:index + chunk_size]
            hash_string = "/" + "/".join(chunk)
            try:
                response = self._get_url_no_refresh(f"torrents/instantAvailability{hash_string}")
                if response is None:
                    continue
                if isinstance(response, dict) and "_rd_status" in response:
                    status = response["_rd_status"]
                    g.log(
                        f"RD instantAvailability: HTTP {status} — stopping chunk loop.",
                        "warning",
                    )
                    break
                if not isinstance(response, dict):
                    continue
                for info_hash, info in response.items():
                    if isinstance(info, dict) and info.get("rd"):
                        cached_hashes.add(info_hash.lower())
            except Exception as exc:
                g.log(f"RD instantAvailability chunk error: {exc}", "warning")
            if index + chunk_size < len(hash_list):
                time.sleep(0.2)
        return cached_hashes
        
    def check_hash(self, hash_value):
        magnet = f'magnet:?xt=urn:btih:{hash_value}'
        response = self.add_magnet(magnet)

        if 'id' not in response:
            return {}

        torrent_id = response['id']
        self.torrent_select_all(torrent_id)
        torrent_info = self.torrent_info(torrent_id)

        if "files" in torrent_info:
            torrent_info["files"] = [file for file in torrent_info["files"] if 'sample' not in file['path'].lower() and source_utils.is_file_ext_valid(file["path"])]

        if torrent_info.get('status') == 'downloaded':
            hash_dict = {
                hash_value: {"torrent_id": torrent_id, "torrent_info": torrent_info, 'rd': [
                    {str(file['id']): {'filename': file['path'], 'filesize': file['bytes']}}
                    for file in torrent_info.get('files', []) if file.get('selected') == 1
                ]}
            }

            if g.get_bool_setting("rd.autodelete"):
                self.delete_torrent(torrent_id)
        else:
            self.delete_torrent(torrent_id)
            hash_dict = {}

        return hash_dict

    def torrent_select_all(self, torrent_id):
        try:
            torrent_info = self.torrent_info(torrent_id)
            files = torrent_info.get('files', [])
            
            valid_file_ids = [
                str(file['id']) for file in files
                if 'sample' not in file['path'].lower() 
                and source_utils.is_file_ext_valid(file['path'])
            ]

            if valid_file_ids:
                file_string = ','.join(valid_file_ids)
                return self.torrent_select(torrent_id, file_string)
            
            return self.torrent_select(torrent_id, 'all')

        except Exception as e:
            g.log(f"Error selecting files for torrent {torrent_id}: {e}", "error")
            return None

    def add_magnet(self, magnet):
        post_data = {"magnet": magnet}
        url = "torrents/addMagnet"
        return self.post_url(url, post_data)

    def list_torrents(self):
        url = "torrents"
        return self.get_url(url)

    def torrent_info(self, id):
        url = f"torrents/info/{id}"
        return self.get_url(url)

    def torrent_select(self, torrent_id, file_id):
        url = f"torrents/selectFiles/{torrent_id}"
        post_data = {"files": file_id}
        return self.post_url(url, post_data)

    def resolve_hoster(self, link):
        url = "unrestrict/link"
        post_data = {"link": link}
        response = self.post_url(url, post_data)
        try:
            return response["download"]
        except KeyError as e:
            raise UnexpectedResponse(response) from e

    def delete_torrent(self, id):
        url = f"torrents/delete/{id}"
        self.delete_url(url)

    @staticmethod
    def is_streamable_storage_type(storage_variant):
        """
        Confirms that all files within the storage variant are video files
        This ensure the pack from RD is instantly streamable and does not require a download
        :param storage_variant:
        :return: BOOL
        """
        return len([i for i in storage_variant.values() if not source_utils.is_file_ext_valid(i["filename"])]) <= 0

    @use_cache(1)
    def get_relevant_hosters(self):
        host_list = self.get_url("hosts/status")
        if "error" in host_list:
            return []
        return [domain for domain, status in host_list.items() if status["supported"] == 1 and status["status"] == "up"]

    def get_hosters(self, hosters):
        host_list = self.get_relevant_hosters()
        if host_list is None:
            host_list = self.get_relevant_hosters()
        if host_list is not None:
            hosters["premium"]["real_debrid"] = [(i, i.split(".")[0]) for i in host_list]
        else:
            hosters["premium"]["real_debrid"] = []

    @staticmethod
    def is_service_enabled():
        return g.get_bool_setting("realdebrid.enabled") and g.get_setting(RD_AUTH_KEY) is not None

    def get_account_status(self):
        status = None
        status_response = self.get_url("user")
        if isinstance(status_response, dict):
            status = status_response.get("type")
        return status or "unknown"