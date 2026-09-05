"""
Handling of scraping and cache checking for sources
"""
import contextlib
import copy
import importlib
import json
import random
import re
import sys
import time
from collections import Counter
from collections import OrderedDict
from importlib import reload as reload_module
from urllib import parse

import xbmc
import xbmcgui

from resources.lib.common import source_utils
from resources.lib.common import tools
from resources.lib.common.thread_pool import ThreadPool
from resources.lib.database.skinManager import SkinManager
from resources.lib.database.torrentCache import TorrentCache
from resources.lib.debrid import all_debrid
from resources.lib.debrid import external_cache
from resources.lib.debrid import premiumize
from resources.lib.debrid import real_debrid
from resources.lib.debrid import offcloud
from resources.lib.debrid import torbox
from resources.lib.gui.windows.get_sources_window import GetSourcesWindow
from resources.lib.gui.windows.manual_caching import ManualCacheWindow
from resources.lib.modules import monkey_requests
from resources.lib.modules import resolver as resolver
from resources.lib.modules.cloud_scrapers import AllDebridCloudScraper
from resources.lib.modules.cloud_scrapers import PremiumizeCloudScraper
from resources.lib.modules.cloud_scrapers import RealDebridCloudScraper
from resources.lib.modules.cloud_scrapers import OffCloudCloudScraper
from resources.lib.modules.cloud_scrapers import TorBoxCloudScraper
from resources.lib.modules.local_scraper import LocalFileScraper
from resources.lib.modules.local_scraper import local_scraping_enabled
from resources.lib.modules.globals import g
from resources.lib.modules.source_sorter import SourceSorter

approved_qualities = ["4K", "1080p", "720p", "SD"]
approved_qualities_set = set(approved_qualities)


class Sources:
    """
    Handles fetching and processing of available sources for provided meta data
    """

    def __init__(self, item_information):
        self.hash_regex = re.compile(r'btih:(.*?)(?:&|$)')
        self.canceled = False
        self.torrent_cache = TorrentCache()
        self.torrent_threads = ThreadPool()
        self.hoster_threads = ThreadPool()
        self.adaptive_threads = ThreadPool()
        self.direct_threads = ThreadPool()
        self.item_information = item_information
        self.media_type = self.item_information['info']['mediatype']
        self.torrent_providers = []
        self.hoster_providers = []
        self.adaptive_providers = []
        self.direct_providers = []
        self.cloud_scrapers = []
        self.running_providers = []
        self.language = 'en'
        self.sources_information = {
            "directSources": [],
            "adaptiveSources": [],
            "torrentCacheSources": {},
            "hosterSources": {},
            "cloudFiles": [],
            "allTorrents": {},
            "cached_hashes": set(),
            "statistics": {
                "torrents": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "torrentsCached": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "hosters": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "cloudFiles": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "adaptiveSources": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "directSources": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "totals": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                "filtered": {
                    "torrents": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "torrentsCached": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "hosters": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "cloudFiles": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "adaptiveSources": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "directSources": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                    "totals": {"4K": 0, "1080p": 0, "720p": 0, "SD": 0, "total": 0},
                },
                "remainingProviders": [],
            },
        }

        self.hoster_domains = {}
        self.progress = 0
        self.timeout_progress = 0
        self.runtime = 0
        self.host_domains = []
        self.host_names = []
        self.timeout = g.get_int_setting('general.timeout')
        self.window = SourceWindowAdapter(self.item_information, self)

        self.silent = g.get_bool_runtime_setting('tempSilent')

        self.source_sorter = SourceSorter(self.item_information)

        self.preem_enabled = g.get_bool_setting('preem.enabled')
        self.preem_waitfor_directfiles = g.get_bool_setting("preem.waitfor.directfiles")
        self.preem_waitfor_cloudfiles = g.get_bool_setting("preem.waitfor.cloudfiles")
        self.preem_cloudfiles = g.get_bool_setting('preem.cloudfiles')
        self.preem_adaptive_sources = g.get_bool_setting('preem.adaptiveSources')
        self.preem_direct_sources = g.get_bool_setting('preem.directSources')
        self.preem_type = g.get_int_setting('preem.type')
        self.preem_limit = g.get_int_setting('preem.limit')
        self.preem_resolutions = approved_qualities[
            g.get_int_setting("general.maxResolution") : self._get_pre_term_min()
        ]
        self.local_scraping_enabled = local_scraping_enabled()

    def get_sources(self, overwrite_torrent_cache=False):
        """
        Main endpoint to initiate scraping process
        :param overwrite_cache:
        :return: Returns (uncached_sources, sorted playable sources, items metadata)
        :rtype: tuple
        """
        try:
            g.log('Starting Scraping', 'debug')
            g.log(f"Timeout: {self.timeout}", 'debug')
            g.log(f"Pre-term-enabled: {self.preem_enabled}", 'debug')
            g.log(f"Pre-term-limit: {self.preem_limit}", 'debug')
            g.log(f"Pre-term-res: {self.preem_resolutions}", 'debug')
            g.log(f"Pre-term-type: {self.preem_type}", 'debug')
            g.log(f"Pre-term-cloud-files: {self.preem_cloudfiles}", 'debug')
            g.log(f"Pre-term-adaptive-files: {self.preem_adaptive_sources}", 'debug')
            g.log(f"Pre-term-direct-files: {self.preem_direct_sources}", 'debug')

            self._handle_pre_scrape_modifiers()
            self._get_imdb_info()

            if overwrite_torrent_cache:
                self._clear_local_torrent_results()
            else:
                self._check_local_torrent_database()

            self._update_progress()
            if self._prem_terminate():
                return self._finalise_results()

            self.window.create()
            self.window.set_text(
                g.get_language_string(30054),
                self.progress,
                self.timeout_progress,
                self.sources_information,
                self.runtime,
            )
            self._init_providers()

            # Add the users cloud inspection to the threads to be run
            self.torrent_threads.put(self._user_cloud_inspection)
            if self.local_scraping_enabled:
                self.torrent_threads.put(self._user_local_inspection)

            # Load threads for all sources
            self._create_torrent_threads()
            self._create_hoster_threads()
            self._create_adaptive_threads()
            self._create_direct_threads()

            start_time = time.time()
            while (
                len(self.torrent_providers)
                + len(self.hoster_providers)
                + len(self.adaptive_providers)
                + len(self.direct_providers)
                + len(self.cloud_scrapers)
                <= 0
                and not self.local_scraping_enabled
            ):
                self.runtime = time.time() - start_time
                if self.runtime > 5:
                    g.notification(g.ADDON_NAME, g.get_language_string(30615))
                    g.log('No providers enabled', 'warning')
                    g.cancel_playback()
                    return [], [], self.item_information

            self.window.set_property("has_torrent_providers", "true" if len(self.torrent_providers) > 0 else "false")
            self.window.set_property("has_hoster_providers", "true" if len(self.hoster_providers) > 0 else "false")
            self.window.set_property("has_adaptive_providers", "true" if len(self.adaptive_providers) > 0 else "false")
            self.window.set_property("has_cloud_scrapers", "true" if len(self.cloud_scrapers) > 0 else "false")
            self.window.set_property(
                "has_direct_providers",
                "true" if len(self.direct_providers) > 0 or self.local_scraping_enabled else "false",
            )
            self._update_progress()
            self.window.set_property('process_started', 'true')

            # Keep alive for gui display and threading
            g.log('Entering Keep Alive', 'info')

            while self.progress < 100 and not g.abort_requested():
                self.runtime = time.time() - start_time
                self._update_progress()
                self.timeout_progress = int(100 - float(1 - (self.runtime / float(self.timeout))) * 100)
                self.progress = int(
                    100
                    - (
                        len(self.sources_information['statistics']['remainingProviders'])
                        / float(
                            len(self.torrent_providers)
                            + len(self.hoster_providers)
                            + len(self.adaptive_providers)
                            + len(self.direct_providers)
                            + (1 if self.cloud_scrapers else 0)
                            + (1 if self.local_scraping_enabled else 0)
                        )
                        * 100
                    )
                )

                try:
                    self.window.set_text(  # sourcery skip: use-fstring-for-formatting
                        "4K: {} | 1080: {} | 720: {} | SD: {}".format(
                            g.color_string(self.sources_information['statistics']['filtered']['totals']['4K']),
                            g.color_string(self.sources_information['statistics']['filtered']['totals']['1080p']),
                            g.color_string(self.sources_information['statistics']['filtered']['totals']['720p']),
                            g.color_string(self.sources_information['statistics']['filtered']['totals']['SD']),
                        ),
                        self.progress,
                        self.timeout_progress,
                        self.sources_information,
                        self.runtime,
                    )

                except (KeyError, IndexError) as e:
                    g.log(f"Failed to set window text, {e}", "error")

                g.log(f"Remaining Providers {self.sources_information['statistics']['remainingProviders']}", "debug")
                if self._prem_terminate() is True or (
                    len(self.sources_information['statistics']['remainingProviders']) == 0 and self.runtime > 5
                ):
                    # Give some time for scrapers to initiate
                    break

                if self.canceled or self.runtime >= self.timeout:
                    monkey_requests.PRE_TERM_BLOCK = True
                    break

                xbmc.sleep(200)

            g.log('Exited Keep Alive', 'info')
            return self._finalise_results()

        finally:
            self.window.close()

    def _handle_pre_scrape_modifiers(self):
        """
        Detects preScrape, disables pre-termination and sets timeout to maximum value
        :return:
        :rtype:
        """
        if g.REQUEST_PARAMS.get('action', '') == "preScrape":
            self.silent = True
            self.timeout = 180
            self._prem_terminate = self._disabled_prem_terminate

    def _disabled_prem_terminate(self):
        return False

    def _create_hoster_threads(self):
        if self._hosters_enabled():
            random.shuffle(self.hoster_providers)
            for i in self.hoster_providers:
                self.hoster_threads.put(self._get_hosters, self.item_information, i)

    def _create_torrent_threads(self):
        if self._torrents_enabled():
            random.shuffle(self.torrent_providers)
            for i in self.torrent_providers:
                self.torrent_threads.put(
                    self._get_provider_sources, self.item_information, i, 'torrentCache', self._process_torrent_source
                )

    def _create_adaptive_threads(self):
        for i in self.adaptive_providers:
            self.adaptive_threads.put(
                self._get_provider_sources, self.item_information, i, 'adaptive', self._process_adaptive_source
            )

    def _create_direct_threads(self):
        for i in self.direct_providers:
            self.direct_threads.put(
                self._get_provider_sources, self.item_information, i, 'direct', self._process_direct_source
            )

    def _check_local_torrent_database(self):
        if g.get_bool_setting('general.torrentCache'):
            self.window.set_text(
                g.get_language_string(30053),
                self.progress,
                self.timeout_progress,
                self.sources_information,
                self.runtime,
            )
            self._get_local_torrent_results()

    def _is_playable_source(self, filtered=False):
        stats = self.sources_information['statistics']
        stats = stats['filtered'] if filtered else stats
        return any(
            stats[stype]["total"] > 0
            for stype in ["torrentsCached", "cloudFiles", "adaptiveSources", "hosters", "directSources"]
        )

    def _finalise_results(self):
        monkey_requests.allow_provider_requests = False
        self._send_provider_stop_event()

        uncached = [
            i
            for i in self.sources_information['allTorrents'].values()
            if i['hash'] not in self.sources_information['cached_hashes']
        ]

        # Check to see if we have any playable unfiltered sources, if not do cache assist
        if not self._is_playable_source():
            self._build_cache_assist()
            g.cancel_playback()
            if self.silent:
                g.notification(g.ADDON_NAME, g.get_language_string(30055))
            return uncached, [], self.item_information

        # Return sources list
        sources_list = (
            list(self.sources_information['torrentCacheSources'].values())
            + list(self.sources_information['hosterSources'].values())
            + self.sources_information['cloudFiles']
            + self.sources_information['adaptiveSources']
            + self.sources_information['directSources']
        )
        return uncached, sources_list, self.item_information

    def _get_imdb_info(self):
        if self.media_type != 'movie':
            return
        # Confirm movie year against IMDb's information
        imdb_id = self.item_information['info'].get("imdb_id")
        if imdb_id is None:
            return
        import requests

        try:
            from resources.lib.indexers.imdb import ImdbAPI

            resp = ImdbAPI.suggestion_title_by_id(imdb_id)
            year = resp.get('y', self.item_information['info']['year'])
            if year is not None and year != self.item_information['info']['year']:
                self.item_information['info']['year'] = str(year)
        except requests.exceptions.ConnectionError as ce:
            g.log("Unable to obtain IMDB suggestions to confirm movie year", "warning")
            g.log(ce, "debug")

    @staticmethod
    def _imdb_suggestions(imdb_id):
        from resources.lib.indexers.imdb import ImdbAPI

        return ImdbAPI.suggestion_title_by_id(imdb_id)

    def _send_provider_stop_event(self):
        for provider in self.running_providers:
            if hasattr(provider, 'cancel_operations') and callable(provider.cancel_operations):
                provider.cancel_operations()

    @staticmethod
    def _torrents_enabled():
        return bool(
            (g.get_bool_setting('premiumize.torrents') and g.premiumize_enabled())
            or (g.get_bool_setting('rd.torrents') and g.real_debrid_enabled())
            or (g.get_bool_setting('alldebrid.torrents') and g.all_debrid_enabled())
            or (g.get_bool_setting('tb.torrents') and g.torbox_enabled())
            or (g.get_bool_setting('oc.torrents') and g.offcloud_enabled())
        )

    @staticmethod
    def _hosters_enabled():
        return bool(
            (g.get_bool_setting('premiumize.hosters') and g.premiumize_enabled())
            or (g.get_bool_setting('rd.hosters') and g.real_debrid_enabled())
            or (g.get_bool_setting('alldebrid.hosters') and g.all_debrid_enabled())
            or (g.get_bool_setting('tb.hosters') and g.torbox_enabled())
            or (g.get_bool_setting('oc.hosters') and g.offcloud_enabled())
        )

    def _store_torrent_results(self, torrent_list):
        if len(torrent_list) == 0:
            return
        self.torrent_cache.add_torrent(self.item_information, torrent_list)

    def _clear_local_torrent_results(self):
        if g.get_bool_setting('general.torrentCache'):
            g.log("Clearing existing local torrent cache items", "info")
            self.torrent_cache.clear_item(self.item_information)

    def _get_local_torrent_results(self):
        relevant_torrents = self.torrent_cache.get_torrents(self.item_information)[:100]

        if len(relevant_torrents) > 0:
            for torrent in relevant_torrents:
                torrent['provider'] = f"{torrent['provider']} (Local Cache)"

                self.sources_information['allTorrents'].update({torrent['hash']: torrent})

            TorrentCacheCheck(self).torrent_cache_check(relevant_torrents, self.item_information)

    @staticmethod
    def _get_best_torrent_to_cache(sources):
        sources = [i for i in sources if i.get('seeds', 0) != 0 and i.get("magnet")]

        for quality in [i for i in approved_qualities if i in source_utils.get_accepted_resolution_set()]:
            if quality_filter := [i for i in sources if i['quality'] == quality]:
                packtype_filter = [i for i in quality_filter if i['package'] in ['show', 'season']]

                sorted_list = sorted(packtype_filter, key=lambda k: k['seeds'], reverse=True)
                if len(sorted_list) > 0:
                    return sorted_list[0]
                package_type_list = [i for i in quality_filter if i['package'] == 'single']
                sorted_list = sorted(package_type_list, key=lambda k: k['seeds'], reverse=True)
                if len(sorted_list) > 0:
                    return sorted_list[0]

        return None

    def _build_cache_assist(self):
        if len(self.sources_information['allTorrents']) == 0:
            return
        valid_packages = {'show', 'season', 'single'}

        if self.media_type == 'episode' and self.item_information['is_airing']:
            valid_packages.remove('show')
            if int(self.item_information['info']['season']) >= int(self.item_information['season_count']):
                valid_packages.remove('season')

        sources = [i for i in self.sources_information['allTorrents'].values() if i['package'] in valid_packages]

        if g.get_bool_setting("general.autocache") and g.get_int_setting('general.cacheAssistMode') == 0:
            if sources := self._get_best_torrent_to_cache(sources):
                action_args = parse.quote(json.dumps(sources, default=tools.serialize_sets))
                xbmc.executebuiltin(f'RunPlugin({g.BASE_URL}?action=cacheAssist&action_args={action_args})')
        elif not self.silent:
            if confirmation := xbmcgui.Dialog().yesno(
                f'{g.ADDON_NAME} - {g.get_language_string(30308)}', g.get_language_string(30056)
            ):
                try:
                    window = ManualCacheWindow(
                        *SkinManager().confirm_skin_path('manual_caching.xml'),
                        item_information=self.item_information,
                        sources=sources,
                    )
                    window.doModal()
                finally:
                    del window

    def _scrape_catalog(self):
        from resources.lib.modules import catalog_profiles

        info = (self.item_information or {}).get("info") or {}
        catalog = info.get("catalog")
        if catalog:
            return catalog_profiles.normalize_catalog(catalog)
        if self.media_type == "movie":
            return "movie"
        if self.media_type == "episode":
            from resources.lib.simkl.anime_scraper_context import is_anime_item

            if is_anime_item(info, self.item_information):
                return "anime"
            return "tv"
        return "tv"

    def _init_providers(self):
        sys.path.append(g.ADDON_USERDATA_PATH)
        try:
            if g.ADDON_USERDATA_PATH not in sys.path:
                sys.path.append(g.ADDON_USERDATA_PATH)
                providers = importlib.import_module("providers")
            else:
                providers = reload_module(importlib.import_module("providers"))
        except ValueError:
            g.notification(g.ADDON_NAME, g.get_language_string(30443))
            g.log('No providers installed', 'warning')
            return

        scrape_catalog = self._scrape_catalog()
        g.log(f"Provider scrape catalog: {scrape_catalog}", "debug")
        providers_dict = providers.get_relevant(self.language, catalog=scrape_catalog)

        torrent_providers = providers_dict['torrent']
        hoster_providers = providers_dict['hosters']
        adaptive_providers = providers_dict['adaptive']
        direct_providers = providers_dict['direct']

        hoster_providers, torrent_providers = self._remove_duplicate_providers(torrent_providers, hoster_providers)

        self.hoster_domains = resolver.Resolver.get_hoster_list()
        self.torrent_providers = torrent_providers
        self.hoster_providers = hoster_providers
        self.adaptive_providers = adaptive_providers
        self.direct_providers = direct_providers
        self.host_domains = OrderedDict.fromkeys(
            [
                host[0].lower()
                for provider in self.hoster_domains['premium']
                for host in self.hoster_domains['premium'][provider]
            ]
        )
        self.host_names = OrderedDict.fromkeys(
            [
                host[1].lower()
                for provider in self.hoster_domains['premium']
                for host in self.hoster_domains['premium'][provider]
            ]
        )

    @staticmethod
    def _remove_duplicate_providers(torrent, hosters):
        temp_list = []
        filter_list = []
        for i in torrent:
            if i[1] not in filter_list:
                temp_list.append(i)
                filter_list.append(i[1])

        torrent = temp_list
        temp_list = []
        for i in hosters:
            if i[1] not in filter_list:
                temp_list.append(i)
                filter_list.append(i[1])

        hosters = temp_list

        return hosters, torrent

    def _exit_thread(self, provider_name):
        if provider_name in self.sources_information['statistics']['remainingProviders']:
            self.sources_information['statistics']['remainingProviders'].remove(provider_name)

    def _get_provider_sources(self, info, provider, provider_type, process_function):
        provider_name = provider[1].upper()
        try:
            self.sources_information['statistics']['remainingProviders'].append(provider_name)
            provider_module = importlib.import_module(f'{provider[0]}.{provider[1]}')
            if not hasattr(provider_module, "sources"):
                g.log("Invalid provider, Source Class missing", "warning")
                return
            provider_source = provider_module.sources()

            if not hasattr(provider_source, self.media_type):
                g.log(f"Skipping provider: {provider_name} - Does not support {self.media_type} types", "warning")
                return

            self.running_providers.append(provider_source)

            if self.media_type == g.MEDIA_EPISODE:
                simple_info = self._build_simple_show_info(info)

                results = provider_source.episode(simple_info, info)
            else:
                simple_info = self._build_simple_movie_info(info)

                try:
                    results = provider_source.movie(
                        info['info']['title'],
                        str(info['info']['year']),
                        info['info'].get('imdb_id'),
                        simple_info=simple_info,
                        info=info,
                    )
                except TypeError:
                    results = provider_source.movie(
                        info['info']['title'], str(info['info']['year']), simple_info=simple_info, info=info
                    )

            if results is None:
                self.sources_information['statistics']['remainingProviders'].remove(provider_name)
                return

            if self.canceled:
                return

            if len(results) > 0:
                # Begin filling in optional dictionary returns
                for result in results:
                    process_function(result, provider_name, provider, info)

                if provider_type == "torrentCache":
                    torrent_results = {value['hash']: value for value in results if value['hash']}

                    self._store_torrent_results(torrent_results.values())

                    if self.canceled:
                        return

                    start_time = time.time()

                    self.sources_information['allTorrents'].update(torrent_results)

                    TorrentCacheCheck(self).torrent_cache_check(list(torrent_results.values()), info)
                    g.log(f"{provider_name} cache check took {time.time() - start_time} seconds", "debug")
                else:
                    self.sources_information[f'{provider_type}Sources'] += results

            self.running_providers.remove(provider_source)

            return
        finally:
            self.sources_information['statistics']['remainingProviders'].remove(provider_name)

    def _process_torrent_source(self, source, provider_name, provider_module, info):
        source["type"] = "torrent"
        source["release_title"] = source.get("release_title", provider_name)
        source["source"] = provider_name.upper()
        source["quality"] = source.get("quality", source_utils.get_quality(source["release_title"]))
        source["size"] = self._torrent_filesize(source, info)
        source["info"] = set(source.get("info", source_utils.get_info(source["release_title"])))
        # Always coerce: a provider may supply seeds as a string (or junk), which would
        # otherwise crash the int/str mixed sort in _get_best_torrent_to_cache.
        source["seeds"] = self._torrent_seeds(source)
        source["provider_imports"] = provider_module
        source["provider"] = source.get("provider_name_override", provider_name.upper())
        source["hash"] = source.get("hash", self.hash_regex.findall(source["magnet"])[0]).lower()
        return source

    @staticmethod
    def _process_adaptive_source(source, provider_name, provider_module, info):
        source["type"] = "adaptive"
        source["release_title"] = source.get("release_title", provider_name)
        source["source"] = provider_name.upper()
        source["quality"] = source.get("quality", source_utils.get_quality(source["release_title"]))
        source["size"] = source.get("size", "Variable")
        source["info"] = set(source.get("info", {}))
        source["provider_imports"] = provider_module
        source["provider"] = source.get("provider_name_override", provider_name.upper())
        return source

    @staticmethod
    def _process_direct_source(source, provider_name, provider_module, info):
        source['type'] = 'direct'
        source['release_title'] = source.get("release_title", provider_name)
        source['source'] = provider_name.upper()
        source['quality'] = source.get("quality", source_utils.get_quality(source['release_title']))
        source['size'] = source.get("size", "Variable")
        source['info'] = set(source.get("info", {}))
        source['provider_imports'] = provider_module
        source['provider'] = source.get('provider_name_override', provider_name.upper())
        return source

    def _do_hoster_episode(self, provider_source, provider_name, info):
        if not hasattr(provider_source, 'tvshow'):
            return
        imdb, tvdb, title, localtitle, aliases, year = self._build_hoster_variables(info, 'tvshow')

        if self.canceled:
            self._exit_thread(provider_name)
            return

        url = provider_source.tvshow(imdb, tvdb, title, localtitle, aliases, year)

        if self.canceled:
            self._exit_thread(provider_name)
            return

        imdb, tvdb, title, premiered, season, episode = self._build_hoster_variables(info, 'episode')

        if self.canceled:
            self._exit_thread(provider_name)
            return

        url = provider_source.episode(url, imdb, tvdb, title, premiered, season, episode)

        if self.canceled:
            self._exit_thread(provider_name)
            return

        return url

    def _do_hoster_movie(self, provider_source, provider_name, info):
        if not getattr(provider_source, 'movie'):
            self._exit_thread(provider_name)
            return
        imdb, title, localtitle, aliases, year = self._build_hoster_variables(info, 'movie')
        return provider_source.movie(imdb, title, localtitle, aliases, year)

    def _get_hosters(self, info, provider):
        provider_name = provider[1].upper()
        self.sources_information['statistics']['remainingProviders'].append(provider_name.upper())
        try:
            provider_module = importlib.import_module(f'{provider[0]}.{provider[1]}')
            if hasattr(provider_module, "source"):
                provider_class = provider_module.source()
            else:
                self._exit_thread(provider_name)
                return

            self.running_providers.append(provider_class)

            if self.media_type == g.MEDIA_EPISODE:
                sources = self._do_hoster_episode(provider_class, provider_name, info)
            else:
                sources = self._do_hoster_movie(provider_class, provider_name, info)

            if not sources:
                self._exit_thread(provider_name)
                return

            host_dict, hostpr_dict = self._build_hoster_variables(info, 'sources')

            if self.canceled:
                self._exit_thread(provider_name)
                return

            sources = provider_class.sources(sources, host_dict, hostpr_dict)

            if not sources:
                g.log(f'{provider_name}: Found No Sources', 'info')
                return

            if self.media_type == g.MEDIA_EPISODE:
                title = f"{self.item_information['info']['tvshowtitle']} - {self.item_information['info']['title']}"
            else:
                title = f"{self.item_information['info']['title']} ({self.item_information['info']['year']})"

            for source in sources:
                source.update(
                    {
                        "type": "hoster",
                        "release_title": source.get('release_title', title),
                        "source": source['source'].upper().split('.')[0],
                        "size": source.get('size', '0'),
                        "info": source.get('info', []),
                        "provider_imports": provider,
                        "provider": source.get('provider_name_override', provider_name.upper()),
                    }
                )

            sources1 = [i for i in sources for host in self.host_domains if host in i['url']]
            sources2 = [i for i in sources if i['source'].lower() not in self.host_names and i['direct']]

            sources = sources1 + sources2

            self._debrid_hoster_duplicates(sources)
            self._exit_thread(provider_name)

        finally:
            with contextlib.suppress(ValueError):
                self.sources_information['statistics']['remainingProviders'].remove(provider_name)

    def _user_cloud_inspection(self):
        self.sources_information['statistics']['remainingProviders'].append("Cloud Inspection")
        try:
            thread_pool = ThreadPool()
            if self.media_type == g.MEDIA_EPISODE:
                simple_info = self._build_simple_show_info(self.item_information)
            else:
                simple_info = self._build_simple_movie_info(self.item_information)

            cloud_scrapers = [
                {
                    "setting": "premiumize.cloudInspection",
                    "provider": PremiumizeCloudScraper,
                    "enabled": g.premiumize_enabled(),
                },
                {
                    "setting": "rd.cloudInspection",
                    "provider": RealDebridCloudScraper,
                    "enabled": g.real_debrid_enabled(),
                },
                {
                    "setting": "alldebrid.cloudInspection",
                    "provider": AllDebridCloudScraper,
                    "enabled": g.all_debrid_enabled(),
                },
                {
                    "setting": "tb.cloudInspection",
                    "provider": TorBoxCloudScraper,
                    "enabled": g.torbox_enabled(),
                },
                {
                    "setting": "oc.cloudInspection",
                    "provider": OffCloudCloudScraper,
                    "enabled": g.offcloud_enabled(),
                },
            ]

            for cloud_scraper in cloud_scrapers:
                if cloud_scraper['enabled'] and g.get_bool_setting(cloud_scraper['setting']):
                    self.cloud_scrapers.append(cloud_scraper['provider'])
                    thread_pool.put(
                        cloud_scraper['provider'](self._prem_terminate).get_sources, self.item_information, simple_info
                    )

            sources = thread_pool.wait_completion()
            self.sources_information['cloudFiles'] = sources or []

        finally:
            self.sources_information['statistics']['remainingProviders'].remove("Cloud Inspection")

    def _user_local_inspection(self):
        self.sources_information['statistics']['remainingProviders'].append("Local Inspection")
        try:
            if not self.local_scraping_enabled:
                return

            if self.media_type == g.MEDIA_EPISODE:
                simple_info = self._build_simple_show_info(self.item_information)
            else:
                simple_info = self._build_simple_movie_info(self.item_information)

            sources = LocalFileScraper(self._prem_terminate).get_sources(self.item_information, simple_info)
            if sources:
                self.sources_information['directSources'].extend(sources)
        finally:
            with contextlib.suppress(ValueError):
                self.sources_information['statistics']['remainingProviders'].remove("Local Inspection")

    @staticmethod
    def _color_number(number):

        if int(number) > 0:
            return g.color_string(number, 'green')
        else:
            return g.color_string(number, 'red')

    def _update_progress(self):
        def _get_quality_count_dict(source_list):
            _4k = 0
            _1080p = 0
            _720p = 0
            _sd = 0
            _variable = 0

            for source in source_list:
                if '4K' in source['quality']:
                    _4k += 1
                elif '1080p' in source['quality']:
                    _1080p += 1
                elif '720p' in source['quality']:
                    _720p += 1
                elif 'SD' in source['quality']:
                    _sd += 1
                elif source["quality"] in ["Unknown", "Variable"]:
                    _variable += 1

            return {
                "4K": _4k,
                "1080p": _1080p,
                "720p": _720p,
                "SD": _sd,
                "total": _4k + _1080p + _720p + _sd + _variable,
            }

        def _get_total_quality_dict(quality_dict_list):
            total_counter = Counter()

            for quality_dict in quality_dict_list:
                total_counter.update(quality_dict)

            return dict(total_counter)

        # Get qualities by source type and store result
        self.sources_information['statistics']['torrents'] = _get_quality_count_dict(
            list(self.sources_information['allTorrents'].values())
        )
        self.sources_information['statistics']['torrentsCached'] = _get_quality_count_dict(
            list(self.sources_information['torrentCacheSources'].values())
        )
        self.sources_information['statistics']['hosters'] = _get_quality_count_dict(
            list(self.sources_information['hosterSources'].values())
        )
        self.sources_information['statistics']['cloudFiles'] = _get_quality_count_dict(
            self.sources_information['cloudFiles']
        )
        self.sources_information['statistics']['adaptiveSources'] = _get_quality_count_dict(
            self.sources_information['adaptiveSources']
        )
        self.sources_information['statistics']['directSources'] = _get_quality_count_dict(
            self.sources_information['directSources']
        )

        self.sources_information['statistics']['totals'] = _get_total_quality_dict(
            [
                self.sources_information['statistics']['torrents'],
                self.sources_information['statistics']['hosters'],
                self.sources_information['statistics']['cloudFiles'],
                self.sources_information['statistics']['adaptiveSources'],
                self.sources_information['statistics']['directSources'],
            ]
        )

        # Get qualities by source type after source filtering and store result
        self.sources_information['statistics']['filtered']['torrents'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(list(self.sources_information['allTorrents'].values()))
        )
        self.sources_information['statistics']['filtered']['torrentsCached'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(list(self.sources_information['torrentCacheSources'].values()))
        )
        self.sources_information['statistics']['filtered']['hosters'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(list(self.sources_information['hosterSources'].values()))
        )
        self.sources_information['statistics']['filtered']['cloudFiles'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(self.sources_information['cloudFiles'])
        )
        self.sources_information['statistics']['filtered']['adaptiveSources'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(self.sources_information['adaptiveSources'])
        )
        self.sources_information['statistics']['filtered']['directSources'] = _get_quality_count_dict(
            self.source_sorter.filter_sources(self.sources_information['directSources'])
        )
        self.sources_information['statistics']['filtered']['totals'] = _get_total_quality_dict(
            [
                self.sources_information['statistics']['filtered']['torrentsCached'],
                self.sources_information['statistics']['filtered']['hosters'],
                self.sources_information['statistics']['filtered']['cloudFiles'],
                self.sources_information['statistics']['filtered']['adaptiveSources'],
                self.sources_information['statistics']['filtered']['directSources'],
            ]
        )

    @staticmethod
    def _torrent_clean_alias(title):
        """Query-friendly variant of a title with characters that commonly break
        torrent/usenet search removed (colons, apostrophes, ampersands, brackets).
        Returns '' when the result is empty or unchanged-worthy."""
        if not title:
            return ""
        cleaned = re.sub(r"[:'&()\[\]]", " ", title)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _append_clean_alias(title, aliases):
        """Append a torrent-clean alias to the alias list if it adds anything new."""
        clean_alias = Sources._torrent_clean_alias(title)
        if clean_alias and clean_alias.lower() != (title or "").lower() and clean_alias not in aliases:
            aliases.append(clean_alias)

    @staticmethod
    def _append_scraper_title_aliases(ep_info, show_info, aliases):
        """Merge show + episode anime title slots, then append English/Romaji aliases."""
        from resources.lib.simkl.field_map import merge_anime_title_slots

        merged = dict(ep_info) if isinstance(ep_info, dict) else {}
        if isinstance(show_info, dict):
            show_inner = show_info.get("info") if isinstance(show_info.get("info"), dict) else show_info
            merge_anime_title_slots(merged, show_inner)
            merge_anime_title_slots(merged, show_info)
        Sources._append_language_aliases(merged, aliases)
        if isinstance(ep_info, dict) and ep_info is not merged:
            Sources._append_language_aliases(ep_info, aliases)

    @staticmethod
    def _append_language_aliases(info_dict, aliases):
        """For anime, queue both the English and Romaji titles as aliases so the
        anime scrapers (nyaa/anirena/animetosho) match releases regardless of which
        naming convention they use. These fields only exist for anime, so this is a
        no-op for everything else."""
        for key in ("title_en", "title_romaji"):
            alt = (info_dict.get(key) or "").strip()
            if alt and alt not in aliases:
                aliases.append(alt)
                Sources._append_clean_alias(alt, aliases)

    @staticmethod
    def _build_simple_show_info(info):
        from resources.lib.simkl.anime_scraper_context import (
            build_anime_simple_info_fields,
            is_anime_item,
        )
        from resources.lib.simkl.ids import episode_num_from_info

        ep_info = info.get("info") or {}
        show_info = info.get("_parent_show_info") or {}
        season_num = ep_info.get("season")
        episode_num = episode_num_from_info(ep_info)
        simple_info = {
            'show_title': ep_info.get('tvshowtitle', ''),
            'episode_title': ep_info.get('originaltitle', ''),
            'year': str(ep_info.get('tvshow.year', ep_info.get('year', ''))),
            'season_number': str(season_num if season_num is not None else ''),
            'episode_number': str(episode_num if episode_num is not None else ''),
            'show_aliases': list(ep_info.get('aliases', [])),
            'country': ep_info.get('country_origin', ''),
            'no_seasons': str(info.get('season_count', '')),
            'absolute_number': str(info.get('absoluteNumber', '')),
            'is_airing': info.get('is_airing', False),
            'no_episodes': str(info.get('episode_count', '')),
            'isanime': False,
        }

        if '.' in simple_info['show_title']:
            simple_info['show_aliases'].append(source_utils.clean_title(simple_info['show_title'].replace('.', '')))
        Sources._append_clean_alias(simple_info['show_title'], simple_info['show_aliases'])
        Sources._append_scraper_title_aliases(ep_info, show_info, simple_info['show_aliases'])
        simple_info['isanime'] = is_anime_item(ep_info, info)

        if simple_info['isanime']:
            simple_info.update(build_anime_simple_info_fields(ep_info, info, show_info))
            if simple_info.get("simkl_episode_number"):
                simple_info["absolute_number"] = simple_info["simkl_episode_number"]
            g.log(
                "Anime scraper handoff: isanime=%s, title='%s', aliases=%s, abs=%s, "
                "simkl_ep=%s, tvdb=%sx%s, alt_s=%s, alt_e=%s, anidb=%s, mal=%s, tvdb_season=%s, tvdb_part=%s"
                % (
                    simple_info['isanime'],
                    simple_info['show_title'],
                    simple_info['show_aliases'],
                    simple_info.get('absolute_number', ''),
                    simple_info.get('simkl_episode_number', ''),
                    simple_info.get('tvdb_season_number', ''),
                    simple_info.get('tvdb_episode_number', ''),
                    simple_info.get('alternative_season', ''),
                    simple_info.get('alternative_episode', ''),
                    simple_info.get('anidb_id', ''),
                    simple_info.get('mal_id', ''),
                    simple_info.get('thetvdb_season', ''),
                    simple_info.get('thetvdb_part', ''),
                ),
                "notice",
            )

        return simple_info

    @staticmethod
    def _build_simple_movie_info(info):
        simple_info = {
            'title': info['info'].get('title', ''),
            'year': str(info['info'].get('year', '')),
            'aliases': info['info'].get('aliases', []),
            'country': info['info'].get('country_origin', ''),
        }

        if '.' in simple_info['title']:
            simple_info['aliases'].append(source_utils.clean_title(simple_info['title'].replace('.', '')))
        Sources._append_clean_alias(simple_info['title'], simple_info['aliases'])
        Sources._append_language_aliases(info['info'], simple_info['aliases'])

        return simple_info

    def _build_hoster_variables(self, info, media_type):

        info = copy.deepcopy(info)

        if media_type == 'tvshow':
            return self.__build_hoster_tvshow_variables(info)
        elif media_type == 'episode':
            return self.__build_hoster_episode_variables(info)
        elif media_type == 'movie':
            return self.__build_hoster_movie_variables(info)
        elif media_type == 'sources':
            hostpr_dict = [host[0] for debrid in self.hoster_domains['premium'].values() for host in debrid]
            host_dict = self.hoster_domains['free']
            return host_dict, hostpr_dict

    @staticmethod
    def __build_hoster_movie_variables(info):
        imdb = info['info'].get('imdb_id')
        title = info['info'].get('originaltitle')
        localtitle = info['info'].get('title')
        aliases = info['info'].get('aliases', [])
        year = str(info['info'].get('year'))
        return imdb, title, localtitle, aliases, year

    @staticmethod
    def __build_hoster_episode_variables(info):
        imdb = info['info'].get('imdb_id')
        tvdb = info['info'].get('tvdb_id')
        title = info['info'].get('title')
        premiered = info['info'].get('premiered')
        season = str(info['info'].get('season'))
        episode = str(info['info'].get('episode'))
        return imdb, tvdb, title, premiered, season, episode

    @staticmethod
    def __build_hoster_tvshow_variables(info):
        imdb = info['info'].get('imdb_id')
        tvdb = info['info'].get('tvdb_id')
        title = info['info'].get('tvshowtitle')
        localtitle = ''
        aliases = info['info'].get('aliases', [])
        if '.' in title:
            aliases.append(source_utils.clean_title(title.replace('.', '')))
        return imdb, tvdb, title, localtitle, aliases, str(info['info']['year'])

    def _debrid_hoster_duplicates(self, sources):
        updated_sources = {}
        for provider in self.hoster_domains['premium']:
            for hoster in self.hoster_domains['premium'][provider]:
                for source in sources:
                    if hoster[1].lower() == source['source'].lower() or hoster[0].lower() in str(source['url']).lower():
                        source['debrid_provider'] = provider
                        updated_sources[f"{provider}_{source['url'].lower()}"] = source
        self.sources_information['hosterSources'].update(updated_sources)

    def _get_pre_term_min(self):
        return (
            g.get_int_setting('preem.tvres') + 1
            if self.media_type == 'episode'
            else g.get_int_setting('preem.movieres') + 1
        )

    def _get_filtered_count_by_resolutions(self, resolutions, quality_count_dict):
        return sum(quality_count_dict[resolution] for resolution in resolutions)

    def _direct_inspection_running(self):
        remaining = self.sources_information['statistics']['remainingProviders']
        if "Local Inspection" in remaining:
            return True
        for provider in self.direct_providers:
            if provider[1].upper() in remaining:
                return True
        return False

    def _prem_terminate(self):  # pylint: disable=method-hidden
        if self.canceled:
            monkey_requests.PRE_TERM_BLOCK = True
            return True

        if not self.preem_enabled:
            return False

        if self.preem_waitfor_directfiles and self._direct_inspection_running():
            return False

        if (
            self.preem_waitfor_cloudfiles
            and "Cloud Inspection" in self.sources_information['statistics']['remainingProviders']
        ):
            return False

        if self.preem_cloudfiles and self.sources_information['statistics']['filtered']['cloudFiles']['total'] > 0:
            monkey_requests.PRE_TERM_BLOCK = True
            return True
        if (
            self.preem_adaptive_sources
            and self.sources_information['statistics']['filtered']['adaptiveSources']['total'] > 0
        ):
            monkey_requests.PRE_TERM_BLOCK = True
            return True
        if (
            self.preem_direct_sources
            and self.sources_information['statistics']['filtered']['directSources']['total'] > 0
        ):
            monkey_requests.PRE_TERM_BLOCK = True
            return True

        pre_term_log_string = 'Pre-emptively Terminated'

        try:
            if (
                self.preem_type == 0
                and self._get_filtered_count_by_resolutions(
                    self.preem_resolutions, self.sources_information['statistics']['filtered']['torrentsCached']
                )
                >= self.preem_limit
            ):
                return self.__preterm_block(pre_term_log_string)
            if (
                self.preem_type == 1
                and self._get_filtered_count_by_resolutions(
                    self.preem_resolutions, self.sources_information['statistics']['filtered']['hosters']
                )
                >= self.preem_limit
            ):
                return self.__preterm_block(pre_term_log_string)
            if (
                self.preem_type == 2
                and self._get_filtered_count_by_resolutions(
                    self.preem_resolutions, self.sources_information['statistics']['filtered']['torrentsCached']
                )
                + self._get_filtered_count_by_resolutions(
                    self.preem_resolutions, self.sources_information['statistics']['filtered']['hosters']
                )
                >= self.preem_limit
            ):
                return self.__preterm_block(pre_term_log_string)
        except (ValueError, KeyError, IndexError) as e:
            g.log(f"Error getting data for preterm determination: {repr(e)}", "error")
        return False

    @staticmethod
    def __preterm_block(pre_term_log_string):
        g.log(pre_term_log_string, 'info')
        monkey_requests.PRE_TERM_BLOCK = True
        return True

    @staticmethod
    def _torrent_filesize(torrent, info):
        size = torrent.get("episode_size", torrent.get("size", 0))
        try:
            size = float(size)
        except (ValueError, TypeError):
            return 0
        size = int(size)

        if "episode_size" in torrent:
            return size

        if torrent['package'] == "show":
            size /= int(info['show_episode_count'])
        elif torrent['package'] == 'season':
            size /= int(info['episode_count'])
        return size

    @staticmethod
    def _torrent_seeds(torrent):
        seeds = torrent.get('seeds')
        if seeds is None or isinstance(seeds, str) and not seeds.isdigit():
            return 0

        return int(torrent['seeds'])


class TorrentCacheCheck:
    def __init__(self, scraper_class):
        self.premiumize_cached = []
        self.realdebrid_cached = []
        self.all_debrid_cached = []
        self.threads = ThreadPool()

        self.episode_strings = None
        self.season_strings = None
        self.scraper_class = scraper_class
        self.rd_api = real_debrid.RealDebrid()
        self._db_cached_rows = []

    @staticmethod
    def _external_imdb_context(info):
        item_info = (info or {}).get("info") or {}
        imdb = item_info.get("tvshow.imdb_id") or item_info.get("imdb_id")
        season = item_info.get("alternative_season") or item_info.get("season")
        episode = item_info.get("alternative_episode") or item_info.get("episode")
        return imdb, season, episode

    def _init_db_cache_rows(self, torrent_list):
        hash_list = [torrent["hash"] for torrent in torrent_list if torrent.get("hash")]
        self._db_cached_rows = []
        if not g.get_bool_setting("general.torrentCache") or not hash_list:
            return
        try:
            from resources.lib.database.debridCache import DebridCache

            self._db_cached_rows = DebridCache().get_many(hash_list)
        except Exception:
            self._db_cached_rows = []

    def _split_by_db_cache(self, torrent_list, debrid_key):
        try:
            from resources.lib.database.debridCache import DebridCache

            dc = DebridCache()
            known_cached = dc.get_cached_hashes_for_service(self._db_cached_rows, debrid_key)
            known_all = dc.get_known_hashes_for_service(self._db_cached_rows, debrid_key)
        except Exception:
            return [], torrent_list

        db_cached = [torrent for torrent in torrent_list if torrent["hash"] in known_cached]
        unchecked = [torrent for torrent in torrent_list if torrent["hash"] not in known_all]
        return db_cached, unchecked

    @staticmethod
    def _write_cache_results(unchecked_torrents, cached_hashes_set, debrid_key):
        if not g.get_bool_setting("general.torrentCache"):
            return
        try:
            from resources.lib.database.debridCache import DebridCache

            results = []
            normalized = {info_hash.lower() for info_hash in cached_hashes_set}
            for torrent in unchecked_torrents:
                info_hash = torrent["hash"]
                cached = "True" if info_hash.lower() in normalized else "False"
                results.append((info_hash, cached))
            if results:
                DebridCache().set_many_background(results, debrid_key)
        except Exception:
            pass

    def _mark_confirmed_torrents(self, torrent_list, confirmed_hashes, debrid_provider):
        confirmed = {info_hash.lower() for info_hash in confirmed_hashes}
        stored = set()
        for torrent in torrent_list:
            info_hash = torrent.get("hash", "").lower()
            if info_hash and info_hash in confirmed:
                torrent["debrid_provider"] = debrid_provider
                self.store_torrent(torrent)
                stored.add(info_hash)
        return stored

    def store_torrent(self, torrent):
        """
        Pushes cached torrents back up to the calling class
        :param torrent: Torrent to return
        :type torrent: dict
        :return: None
        :rtype: None
        """
        try:
            sources_information = self.scraper_class.sources_information
            # Compare and combine source meta
            tor_key = torrent['hash'] + torrent['debrid_provider']
            sources_information['cached_hashes'].add(torrent['hash'])
            if tor_key in sources_information['torrentCacheSources']:
                c_size = sources_information['torrentCacheSources'][tor_key].get('size', 0)
                n_size = torrent.get('size', 0)
                info = torrent.get('info', [])

                if c_size < n_size:
                    sources_information['torrentCacheSources'].update({tor_key: torrent})

                    sources_information['torrentCacheSources'][tor_key]['info'].extend(
                        [
                            i
                            for i in info
                            if i not in sources_information['torrentCacheSources'][tor_key].get('info', [])
                        ]
                    )
            else:
                sources_information['torrentCacheSources'].update({tor_key: torrent})
        except AttributeError:
            return

    def torrent_cache_check(self, torrent_list, info):
        """
        Run cache check threads for given torrents
        :param torrent_list: List of torrents to check
        :type torrent_list: list
        :param info: Metadata on item to check
        :type info: dict
        :return: None
        :rtype: None
        """
        self._init_db_cache_rows(torrent_list)
        if g.real_debrid_enabled() and g.get_bool_setting('rd.torrents'):
            self.threads.put(self._realdebrid_worker, copy.deepcopy(torrent_list), info)

        if g.premiumize_enabled() and g.get_bool_setting('premiumize.torrents'):
            self.threads.put(self._premiumize_worker, copy.deepcopy(torrent_list))

        if g.all_debrid_enabled() and g.get_bool_setting('alldebrid.torrents'):
            self.threads.put(self._all_debrid_worker, copy.deepcopy(torrent_list), info)

        if g.torbox_enabled() and g.get_bool_setting('tb.torrents'):
            self.threads.put(self._torbox_worker, copy.deepcopy(torrent_list))
        if g.offcloud_enabled() and g.get_bool_setting('oc.torrents'):
            self.threads.put(self._offcloud_worker, copy.deepcopy(torrent_list))
        self.threads.wait_completion()

    def _all_debrid_worker(self, torrent_list, info):
        try:
            if not torrent_list:
                return

            db_cached, unchecked = self._split_by_db_cache(torrent_list, "ad")
            confirmed = self._mark_confirmed_torrents(db_cached, {t["hash"] for t in db_cached}, "all_debrid")

            needs_check = []
            for torrent in unchecked:
                if torrent.get("hash", "").lower() not in confirmed:
                    needs_check.append(torrent)

            if needs_check:
                imdb, season, episode = self._external_imdb_context(info)
                if imdb:
                    hash_list = [torrent["hash"].lower() for torrent in needs_check]
                    ext_cached, success = external_cache.check_ad_external(hash_list, imdb, season, episode)
                    if success is not False:
                        confirmed |= self._mark_confirmed_torrents(needs_check, ext_cached, "all_debrid")

            self._write_cache_results(unchecked, confirmed, "ad")
        except Exception:
            g.log_stacktrace()

    def _realdebrid_worker(self, torrent_list, info):
        try:
            if not torrent_list:
                return

            db_cached, unchecked = self._split_by_db_cache(torrent_list, "rd")
            confirmed = self._mark_confirmed_torrents(db_cached, {t["hash"] for t in db_cached}, "real_debrid")

            needs_check = []
            for torrent in unchecked:
                info_hash = torrent.get("hash", "").lower()
                if torrent.get("rd_cached"):
                    torrent["debrid_provider"] = "real_debrid"
                    self.store_torrent(torrent)
                    confirmed.add(info_hash)
                elif info_hash not in confirmed:
                    needs_check.append(torrent)

            if needs_check:
                imdb, season, episode = self._external_imdb_context(info)
                if imdb:
                    hash_list = [torrent["hash"].lower() for torrent in needs_check]
                    ext_cached, success = external_cache.check_rd_external(hash_list, imdb, season, episode)
                    if success is not False:
                        confirmed |= self._mark_confirmed_torrents(needs_check, ext_cached, "real_debrid")
                    elif success is False:
                        rd_cached_set = self.rd_api.check_cache_batch(hash_list)
                        if rd_cached_set:
                            confirmed |= self._mark_confirmed_torrents(
                                needs_check, rd_cached_set, "real_debrid"
                            )

            self._write_cache_results(unchecked, confirmed, "rd")
        except Exception:
            g.log_stacktrace()

    def _handle_movie_rd_worker(self, source, real_debrid_cache):
        for storage_variant in real_debrid_cache[source['hash']]['rd']:
            if not self.rd_api.is_streamable_storage_type(storage_variant):
                continue
            source['debrid_provider'] = 'real_debrid'
            self.store_torrent(source)

    def _handle_episode_rd_worker(self, source, real_debrid_cache, info):
        for storage_variant in real_debrid_cache[source['hash']]['rd']:

            if not self.rd_api.is_streamable_storage_type(storage_variant):
                continue

            if source_utils.get_best_episode_match('filename', storage_variant.values(), info):
                source['debrid_provider'] = 'real_debrid'
                self.store_torrent(source)
                break

    def _premiumize_worker(self, torrent_list):
        try:
            db_cached, unchecked = self._split_by_db_cache(torrent_list, "pm")
            confirmed = self._mark_confirmed_torrents(db_cached, {t["hash"] for t in db_cached}, "premiumize")

            hash_list = [torrent["hash"] for torrent in unchecked]
            if not hash_list:
                self._write_cache_results(unchecked, confirmed, "pm")
                return

            premiumize_cache = premiumize.Premiumize().hash_check(hash_list)
            premiumize_cache = premiumize_cache["response"]
            for count, torrent in enumerate(unchecked):
                if premiumize_cache[count] is True:
                    torrent["debrid_provider"] = "premiumize"
                    self.store_torrent(torrent)
                    confirmed.add(torrent["hash"])

            self._write_cache_results(unchecked, confirmed, "pm")
        except Exception:
            g.log_stacktrace()

    def _torbox_worker(self, torrent_list):
        try:
            db_cached, unchecked = self._split_by_db_cache(torrent_list, "tb")
            confirmed = self._mark_confirmed_torrents(db_cached, {t["hash"] for t in db_cached}, "torbox")

            hash_list = [torrent["hash"] for torrent in unchecked]
            if not hash_list:
                self._write_cache_results(unchecked, confirmed, "tb")
                return

            cached_hashes = torbox.TorBox().check_hash(hash_list) or []
            cached_lower = {info_hash.lower() for info_hash in cached_hashes}
            for torrent in unchecked:
                if torrent["hash"].lower() in cached_lower:
                    torrent["debrid_provider"] = "torbox"
                    self.store_torrent(torrent)
                    confirmed.add(torrent["hash"])

            self._write_cache_results(unchecked, confirmed, "tb")
        except Exception:
            g.log_stacktrace()

    def _offcloud_worker(self, torrent_list):
        try:
            db_cached, unchecked = self._split_by_db_cache(torrent_list, "oc")
            confirmed = self._mark_confirmed_torrents(db_cached, {t["hash"] for t in db_cached}, "offcloud")

            hash_list = [torrent["hash"] for torrent in unchecked]
            if not hash_list:
                self._write_cache_results(unchecked, confirmed, "oc")
                return

            cached_hashes = offcloud.OffCloud().check_hash(hash_list) or []
            cached_lower = {info_hash.lower() for info_hash in cached_hashes}
            for torrent in unchecked:
                if torrent["hash"].lower() in cached_lower:
                    torrent["debrid_provider"] = "offcloud"
                    self.store_torrent(torrent)
                    confirmed.add(torrent["hash"])

            self._write_cache_results(unchecked, confirmed, "oc")
        except Exception:
            g.log_stacktrace()


class SourceWindowAdapter:
    """
    Class to handle different window style for scraper module
    """

    def __init__(self, item_information, scraper_sclass):
        self.simkl_id = 0
        self.silent = g.get_bool_runtime_setting('tempSilent')

        try:
            self.display_style = g.get_int_setting('general.scrapedisplay')
        except ValueError:
            self.display_style = 0
        self.item_information = item_information
        self.media_type = self.item_information['info']['mediatype']
        self.background_dialog = None
        self.dialog = None
        self.scraper_class = scraper_sclass

    def create(self):
        if self.silent:
            return
        if self.display_style == 2:
            self.background_dialog = xbmcgui.DialogProgressBG()
            self.background_dialog.create("Loading","")
            g.close_busy_dialog()
        if self.display_style == 1:
            # this one is deleted in `close()`
            self.background_dialog = xbmcgui.DialogProgressBG()
            self.simkl_id = self.item_information['simkl_id']
            if self.media_type == 'episode':
                self.background_dialog.create(
                    f"{self.item_information['info']['tvshowtitle']} - "
                    f"S{self.item_information['info']['season']}E{self.item_information['info']['episode']}"
                )
            else:
                self.background_dialog.create(
                    f"{self.item_information['info']['title']} ({self.item_information['info']['year']})"
                )
            g.close_busy_dialog()
        elif self.display_style == 0:
            # this one seems tricky, but is deleted in `close()`
            self.dialog = GetSourcesWindow(
                *SkinManager().confirm_skin_path('get_sources.xml'), item_information=self.item_information
            )
            self.dialog.set_scraper_class(self.scraper_class)
            self.dialog.show()

    def set_text(self, text, progress, timeout_progress, sources_information, runtime):
        if self.silent:
            return
        if self.display_style == 0 and self.dialog:
            if text is not None:
                self.dialog.setProperty("notification_text", text)
            self.dialog.update_properties(sources_information['statistics'])
            self.dialog.setProperty("progress", str(progress))
            self.dialog.setProperty("timeout_progress", str(timeout_progress))
            self.dialog.setProperty("runtime", str(f"{round(runtime, 2)} {g.get_language_string(30554)}"))
        elif self.display_style == 1 and self.background_dialog:
            self.background_dialog.update(progress, message=text)
        elif self.display_style == 2 and self.background_dialog:
            self.background_dialog.update(progress)

    def set_property(self, key, value):
        if self.silent:
            return
        if self.display_style == 0 and self.dialog:
            self.dialog.setProperty(key, str(value))
        elif self.display_style == 1 or self.display_style == 2:
            return

    def set_progress(self, progress):
        if self.silent:
            return
        if self.display_style == 0 and self.dialog:
            self.dialog.setProgress(progress)
        elif (self.display_style == 1 or self.display_style == 2) and self.background_dialog:
            self.background_dialog.update(progress)

    def close(self):
        if self.silent:
            return
        if self.display_style == 0 and self.dialog:
            self.dialog.close()
            del self.dialog
        elif (self.display_style == 1 or self.display_style == 2) and self.background_dialog:
            self.background_dialog.close()
            del self.background_dialog
