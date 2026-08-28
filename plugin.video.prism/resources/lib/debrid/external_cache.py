"""
External debrid cache checking via third-party APIs and DMM.

RD: DMM /availability/check + Torrentio + AIOStreams + Comet (service-scoped).
AD: DMM /availability/ad/check + Torrentio + AIOStreams + Comet (service-scoped).

Hash results are unioned per debrid. Callers match any scraped torrent hash
(nyaa, torrentio, etc.) against the confirmed set.
"""
from __future__ import annotations

import base64
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from resources.lib.modules.globals import g

_BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
]

_session = None

_TIO_BASE = "https://torrentio.strem.fun"
_TIO_FALLBACK_PARAM = "realdebrid=T2iZoymNCCD1T5c2sX5u8tIZVcgcFWlCsCJ72rCmrU2mDdmvgieM"
_TIO_OPTIONS = "debridoptions=nodownloadlinks,nocatalog"
_HASH_PATTERN = re.compile(r"\b[a-fA-F0-9]{40}\b")
_TIO_TIMEOUT = 8
_TIO_SERVICE_PARAM = {"rd": "realdebrid", "ad": "alldebrid"}

_DMM_URL_RD = "https://debridmediamanager.com/api/availability/check"
_DMM_URL_AD = "https://debridmediamanager.com/api/availability/ad/check"
_DMM_TIMEOUT = 8

_AIO_URLS = [
    "https://aiostreams.fortheweak.cloud/api/v1/search",
    "https://aiostreams.stremio.ru/api/v1/search",
    "https://aiostreams.vortetos.com/api/v1/search",
]
_AIO_TIMEOUT = 8
_AIO_AD_DEMO_KEY = "staticDemoApikeyPrem"

_AIO_PRESETS = [
    {
        "type": "mediafusion",
        "instanceId": "5b8",
        "enabled": True,
        "options": {
            "name": "MediaFusion",
            "timeout": 6500,
            "resources": ["stream"],
            "useCachedResultsOnly": True,
            "enableWatchlistCatalogs": False,
            "downloadViaBrowser": False,
            "contributorStreams": False,
            "certificationLevelsFilter": [],
            "nudityFilter": [],
            "mediaTypes": [],
        },
    },
    {
        "type": "stremthruTorz",
        "instanceId": "548",
        "enabled": True,
        "options": {
            "name": "StremThru Torz",
            "timeout": 6500,
            "resources": ["stream"],
            "mediaTypes": [],
            "includeP2P": False,
            "useMultipleInstances": False,
        },
    },
]

_AIO_STATIC_CONFIG = {
    "formatter": {"id": "torrentio", "definition": {"name": "", "description": ""}},
    "sortCriteria": {"global": []},
    "deduplicator": {
        "enabled": False,
        "keys": ["infoHash"],
        "multiGroupBehaviour": "aggressive",
        "cached": "single_result",
        "uncached": "per_service",
        "p2p": "single_result",
        "excludeAddons": [],
    },
    "excludeUncached": True,
}

_COMET_BASES = [
    "https://comet.feels.legal",
    "https://comet.stremio.ru",
    "https://cometfortheweebs.midnightignite.me",
]
_COMET_SERVICE_MAP = {"ad": "alldebrid", "rd": "realdebrid"}
_DIRECT_TIMEOUT = 10


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503])
        _session.mount("https://", HTTPAdapter(max_retries=retries))
        _session.headers.update({
            "User-Agent": random.choice(_BROWSER_UAS),
            "Accept": "application/json",
        })
    return _session


def _extract_hashes_from_streams(streams):
    hashes = set()
    for stream in streams:
        info_hash = stream.get("infoHash", "")
        if info_hash and len(info_hash) == 40:
            hashes.add(info_hash.lower())
            continue
        stream_url = stream.get("url", "")
        if stream_url:
            matches = _HASH_PATTERN.findall(stream_url)
            if matches:
                hashes.add(matches[-1].lower())
    return hashes


def _get_torrentio_debrid_param(service, debrid_key):
    svc = (service or "rd").lower()
    if debrid_key:
        param_name = _TIO_SERVICE_PARAM.get(svc, "realdebrid")
        return f"{param_name}={debrid_key}"
    if svc == "rd":
        return _TIO_FALLBACK_PARAM
    return None


def torrentio_check_cache(imdb, season, episode, service="rd", debrid_key=None):
    if not imdb:
        return set()

    if season is not None and str(season).isdigit():
        path = f"series/{imdb}:{season}:{episode}.json"
    else:
        path = f"movie/{imdb}.json"

    debrid_param = _get_torrentio_debrid_param(service, debrid_key)
    if not debrid_param:
        return set()

    hashes = set()
    try:
        url = f"{_TIO_BASE}/{_TIO_OPTIONS}|{debrid_param}/stream/{path}"
        resp = _get_session().get(url, timeout=_TIO_TIMEOUT)
        resp.raise_for_status()
        for stream in resp.json().get("streams", []):
            if "+" not in stream.get("name", ""):
                continue
            info_hash = stream.get("infoHash", "")
            if info_hash and len(info_hash) == 40:
                hashes.add(info_hash.lower())
                continue
            stream_url = stream.get("url", "")
            if stream_url:
                matches = _HASH_PATTERN.findall(stream_url)
                if matches:
                    hashes.add(matches[-1].lower())
        g.log(
            f"ExternalCache: Torrentio returned {len(hashes)} cached hashes for {(service or 'rd').upper()}",
            "info",
        )
    except Exception as exc:
        g.log(f"ExternalCache: Torrentio check failed ({service}): {exc}", "warning")
    return hashes


def _to_int32(value):
    value = value & 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _calc_value_alg(t, n, const):
    temp = t ^ n
    t = _to_int32(temp * const)
    t4 = _to_int32(t << 5)
    t5 = _to_int32((t & 0xFFFFFFFF) >> 27)
    return t4 | t5


def _slice_hash(s, n):
    half = len(s) // 2
    left_s, right_s = s[:half], s[half:]
    left_n, right_n = n[:half], n[half:]
    interleaved = "".join(ls + ln for ls, ln in zip(left_s, left_n))
    return interleaved + right_n[::-1] + right_s[::-1]


def _generate_hash(text):
    t = _to_int32(0xDEADBEEF ^ len(text))
    a = 1103547991 ^ len(text)
    for ch in text:
        n = ord(ch)
        t = _calc_value_alg(t, n, 2654435761)
        a = _calc_value_alg(a, n, 1597334677)
    t = _to_int32(t + _to_int32(a * 1566083941))
    a = _to_int32(a + _to_int32(t * 2024237689))
    return _to_int32(t ^ a) & 0xFFFFFFFF


def _dmm_get_secret():
    hex_str = f"{random.randrange(10 ** 80):064x}"[:8]
    timestamp = int(time.time())
    dmm_key = f"{hex_str}-{timestamp}"
    s = f"{_generate_hash(dmm_key):x}"
    n = f"{_generate_hash('debridmediamanager.com%%fe7#td00rA3vHz%VmI-' + hex_str):x}"
    return dmm_key, _slice_hash(s, n)


def _dmm_check_cache(hashes, imdb, service):
    cached_hashes = set()
    if not hashes or not imdb:
        return cached_hashes

    valid_hashes = [h for h in hashes if len(h) == 40]
    if len(valid_hashes) > 100:
        valid_hashes = valid_hashes[:100]
    if not valid_hashes:
        return cached_hashes

    url = _DMM_URL_RD if service == "rd" else _DMM_URL_AD
    label = "RD" if service == "rd" else "AD"
    try:
        dmm_key, solution = _dmm_get_secret()
        payload = {
            "dmmProblemKey": dmm_key,
            "solution": solution,
            "imdbId": imdb,
            "hashes": valid_hashes,
        }
        resp = _get_session().post(url, json=payload, timeout=_DMM_TIMEOUT)
        resp.raise_for_status()
        for item in resp.json().get("available", []):
            info_hash = item.get("hash", "")
            if info_hash:
                cached_hashes.add(info_hash.lower())
        g.log(
            f"ExternalCache: DMM {label} returned {len(cached_hashes)} cached hashes "
            f"(checked {len(valid_hashes)})",
            "info",
        )
    except Exception as exc:
        g.log(f"ExternalCache: DMM {label} check failed: {exc}", "warning")
    return cached_hashes


def dmm_check_cache_rd(hashes, imdb):
    return _dmm_check_cache(hashes, imdb, "rd")


def dmm_check_cache_ad(hashes, imdb):
    return _dmm_check_cache(hashes, imdb, "ad")


def _build_aio_user_data(service, api_key):
    svc = (service or "ad").lower()
    if svc == "ad":
        key = api_key or _AIO_AD_DEMO_KEY
        services = [{"id": "alldebrid", "enabled": True, "credentials": {"apiKey": key}}]
    elif svc == "rd":
        if not api_key:
            return None
        services = [{"id": "realdebrid", "enabled": True, "credentials": {"apiKey": api_key}}]
    else:
        return None
    payload = {"services": services, "presets": _AIO_PRESETS, **_AIO_STATIC_CONFIG}
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def aio_check_cache(imdb, season, episode, service="ad", api_key=None):
    if not imdb:
        return set()

    if season is not None and str(season).isdigit():
        params = {"type": "series", "id": f"{imdb}:{season}:{episode}"}
    else:
        params = {"type": "movie", "id": str(imdb)}

    user_data = _build_aio_user_data(service, api_key)
    if not user_data:
        return set()

    hashes = set()
    headers = {"x-aiostreams-user-data": user_data}
    for url in _AIO_URLS:
        try:
            resp = _get_session().get(url, params=params, headers=headers, timeout=_AIO_TIMEOUT)
            if resp.status_code >= 500:
                continue
            resp.raise_for_status()
            for item in resp.json().get("data", {}).get("results", []):
                info_hash = item.get("infoHash", "")
                if info_hash:
                    hashes.add(info_hash.lower())
            g.log(
                f"ExternalCache: AIOStreams returned {len(hashes)} cached hashes for {(service or 'ad').upper()}",
                "info",
            )
            return hashes
        except Exception as exc:
            g.log(f"ExternalCache: AIOStreams failed ({url}): {exc}", "warning")
    return hashes


def _build_comet_config(api_key, service="ad"):
    comet_service = _COMET_SERVICE_MAP.get(service, "alldebrid")
    payload = {
        "maxResultsPerResolution": 0,
        "maxSize": 0,
        "cachedOnly": True,
        "removeTrash": True,
        "resultFormat": ["title", "metadata"],
        "debridService": comet_service,
        "debridApiKey": api_key,
        "debridStreamProxyPassword": "",
        "languages": {"required": [], "exclude": [], "preferred": []},
        "resolutions": {},
        "options": {
            "remove_ranks_under": -10000000000,
            "allow_english_in_languages": False,
            "remove_unknown_languages": False,
        },
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def comet_check_cache(imdb, season, episode, api_key=None, service="ad"):
    if not imdb:
        return set()

    svc = (service or "ad").lower()
    key = api_key or (_AIO_AD_DEMO_KEY if svc == "ad" else None)
    if not key:
        return set()

    if season is not None and str(season).isdigit():
        path = f"stream/series/{imdb}:{season}:{episode}.json"
    else:
        path = f"stream/movie/{imdb}.json"

    config = _build_comet_config(key, svc)
    hashes = set()
    for base in _COMET_BASES:
        try:
            url = f"{base}/{config}/{path}"
            resp = _get_session().get(url, timeout=_DIRECT_TIMEOUT)
            if resp.status_code >= 500:
                continue
            resp.raise_for_status()
            hashes = _extract_hashes_from_streams(resp.json().get("streams", []))
            g.log(
                f"ExternalCache: Comet returned {len(hashes)} cached hashes for {svc.upper()}",
                "info",
            )
            break
        except Exception as exc:
            g.log(f"ExternalCache: Comet failed ({base}): {exc}", "warning")
    return hashes


def _run_parallel_checks(futures_map, label, timeout=12):
    all_cached = set()
    checks_ran = 0
    with ThreadPoolExecutor(max_workers=len(futures_map)) as executor:
        futures = {executor.submit(fn): name for name, fn in futures_map.items()}
        try:
            for future in as_completed(futures, timeout=timeout):
                name = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        all_cached.update(result)
                        checks_ran += 1
                except Exception as exc:
                    g.log(f"ExternalCache {label}: {name} thread error: {exc}", "warning")
        except Exception as exc:
            g.log(f"ExternalCache {label}: timeout collecting partial results: {exc}", "warning")
            for future, name in futures.items():
                if not future.done():
                    continue
                try:
                    result = future.result()
                    if result is not None:
                        all_cached.update(result)
                        checks_ran += 1
                except Exception as thread_exc:
                    g.log(f"ExternalCache {label}: {name} partial error: {thread_exc}", "warning")

    if all_cached:
        return all_cached, True
    if checks_ran > 0:
        return all_cached, None
    return all_cached, False


def check_rd_external(hash_list, imdb, season, episode):
    rd_token = g.get_setting("rd.auth")
    futures_map = {
        "dmm": lambda: dmm_check_cache_rd(hash_list, imdb),
        "torrentio": lambda: torrentio_check_cache(imdb, season, episode, "rd", rd_token),
        "comet": lambda: comet_check_cache(imdb, season, episode, rd_token, "rd"),
    }
    if rd_token:
        futures_map["aiostreams"] = lambda: aio_check_cache(imdb, season, episode, "rd", rd_token)
    cached, success = _run_parallel_checks(futures_map, "RD")
    g.log(f"ExternalCache RD: {len(cached)} total cached hashes", "info")
    return cached, success


def check_ad_external(hash_list, imdb, season, episode):
    ad_key = g.get_setting("alldebrid.apikey")
    futures_map = {
        "dmm": lambda: dmm_check_cache_ad(hash_list, imdb),
        "torrentio": lambda: torrentio_check_cache(imdb, season, episode, "ad", ad_key),
        "aiostreams": lambda: aio_check_cache(imdb, season, episode, "ad", ad_key),
        "comet": lambda: comet_check_cache(imdb, season, episode, ad_key or None, "ad"),
    }
    cached, success = _run_parallel_checks(futures_map, "AD", timeout=30)
    g.log(f"ExternalCache AD: {len(cached)} total cached hashes", "info")
    return cached, success
