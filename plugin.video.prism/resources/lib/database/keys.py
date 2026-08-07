"""Read shared API credentials from context.prism/info.db (Otaku pattern)."""
from __future__ import annotations

import os
import sqlite3

import xbmcaddon

CONTEXT_ADDON_ID = "context.prism"
_cached_info_db_path = None

# Advanced → Optional API Keys (user value wins when set, then info.db).
_SETTING_API_KEYS = {
    "TMDB": "tmdb.apikey",
    "TVDB": "tvdb.apikey",
    "Fanart-TV": "fanart.apikey",
    "MDBList": "mdblist.apikey",
}
_SETTING_CLIENT_IDS = {
    "Simkl": "simkl.client_id",
}


def _info_db_path() -> str:
    global _cached_info_db_path
    if _cached_info_db_path is None:
        context_addon = xbmcaddon.Addon(CONTEXT_ADDON_ID)
        _cached_info_db_path = os.path.join(context_addon.getAddonInfo("path"), "info.db")
    return _cached_info_db_path


def get_info(api_name: str):
    """
    Return one row from info.db for the given api_name.

    Row shape: (api_name, api_key, client_id, client_secret, description)
    """
    db_path = _info_db_path()
    if not os.path.isfile(db_path):
        return None
    with sqlite3.connect(db_path, timeout=10) as conn:
        cur = conn.execute("SELECT * FROM info WHERE api_name=?", (api_name,))
        return cur.fetchone()


def _setting_value(setting_id: str) -> str | None:
    from resources.lib.modules.globals import g

    value = (g.get_setting(setting_id) or "").strip()
    return value or None


def _db_api_key(row) -> str | None:
    if not row:
        return None
    key = (row[1] or "").strip()
    return key or None


def _db_client_id(row) -> str | None:
    if not row:
        return None
    client_id = (row[2] or "").strip()
    return client_id or None


def _db_client_secret(row) -> str | None:
    if not row:
        return None
    secret = (row[3] or "").strip()
    return secret or None


def _metadata_provider_for_api(api_name: str) -> str | None:
    from resources.lib.meta.provider_settings import _API_NAME_BY_PROVIDER

    for provider, mapped_name in _API_NAME_BY_PROVIDER.items():
        if mapped_name == api_name:
            return provider
    return None


def get_api_key(api_name: str, fallback: str | None = None) -> str | None:
    provider = _metadata_provider_for_api(api_name)
    if provider is not None:
        from resources.lib.meta.provider_settings import provider_enabled

        if not provider_enabled(provider):
            return None

    setting_id = _SETTING_API_KEYS.get(api_name)
    if setting_id:
        key = _setting_value(setting_id)
        if key:
            return key
    key = _db_api_key(get_info(api_name))
    if key:
        return key
    return fallback


def get_client_id(api_name: str, fallback: str | None = None) -> str | None:
    setting_id = _SETTING_CLIENT_IDS.get(api_name)
    if setting_id:
        client_id = _setting_value(setting_id)
        if client_id:
            return client_id
    client_id = _db_client_id(get_info(api_name))
    if client_id:
        return client_id
    return fallback


def get_client_secret(api_name: str, fallback: str | None = None) -> str | None:
    secret = _db_client_secret(get_info(api_name))
    if secret:
        return secret
    return fallback
