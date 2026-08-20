import xbmcgui

from resources.lib.common import tools
from resources.lib.database.cache import use_cache
from resources.lib.database.providerCache import ProviderCache
from resources.lib.database.skinManager import SkinManager
from resources.lib.gui.windows.resolver_window import ResolverWindow
from resources.lib.modules.getSources import Sources
from resources.lib.modules.globals import g
from resources.lib.modules.resolver import Resolver
from resources.lib.modules.source_sorter import SourceSorter


def _valid_stream_link(stream_link) -> bool:
    return bool(stream_link) and stream_link != "none"


def _resolved_source_from_list(sources, release_title):
    if not release_title or not sources:
        return None
    for source in sources:
        if isinstance(source, dict) and source.get("release_title") == release_title:
            return source
    return None


class Resolverhelper:
    """
    Helper object to stream line resolving items
    """

    window = None

    @use_cache(1)
    def resolve_silent_or_visible(
        self,
        sources,
        item_information,
        pack_select=False,
        overwrite_cache=False,
        smart_play_context=False,
    ):
        """
        Method to handle automatic background or foreground resolving
        :param sources: list of sources to handle
        :param item_information: information on item to play
        :param pack_select: True if you want to perform a manual file selection
        :param overwrite_cache: Set to true if you wish to overwrite the current cached return value
        :param smart_play_context: True when Smart Play digit reorder was active for this resolve
        :return: None if unsuccessful otherwise a playable object
        """
        stream_link = None
        release_title = None
        resolved_source = None

        if g.get_bool_runtime_setting('tempSilent') or g.get_bool_setting("general.resolverHide", False):
            stream_link, release_title, resolved_source = Resolver().resolve_multiple_until_valid_link(
                sources, item_information, pack_select, True
            )
        else:
            self.window = ResolverWindow(
                *SkinManager().confirm_skin_path('resolver.xml'),
                item_information=item_information,
                close_callback=self.close_window,
            )
            tools.run_threaded(self.window.doModal, sources, pack_select)
            while not g.wait_for_abort(0.30):
                stream_link, release_title = self.window.get_return_data()
                if _valid_stream_link(stream_link):
                    resolved_source = _resolved_source_from_list(sources, release_title)
                    break

        if not _valid_stream_link(stream_link):
            stream_link = None

        if item_information['info']['mediatype'] == g.MEDIA_EPISODE and release_title:
            from resources.lib.simkl.ids import release_title_cache_key

            cache_key = release_title_cache_key(item_information["info"])
            if cache_key:
                g.set_runtime_setting(cache_key, release_title)

        if smart_play_context and resolved_source:
            from resources.lib.modules.last_played_source import save_last_played_source

            save_last_played_source(resolved_source)
        return stream_link

    def close_window(self):
        if self.window:
            self.window.close()
            del self.window
            self.window = None


class SourcesHelper:
    """
    Helper object to stream line scraping of items
    """

    @use_cache(1)
    def get_sources(self, action_args, overwrite_cache=False):
        """
        Method to handle automatic background or foreground scraping
        :param action_args: action arguments from request uri
        :param overwrite_cache: Set to true if you wish to overwrite the current cached return value
        :return: (uncached, sources_list, item_information) or None when the user cancels
        """
        item_information = tools.get_item_information(action_args)
        if not ProviderCache().get_provider_packages():
            has_local_directory = g.configured_directory_path('local.location') or g.configured_directory_path(
                'download.location'
            )
            if not has_local_directory:
                yesno = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30443))
                if not yesno:
                    g.cancel_playback()
                    return None
        return Sources(item_information).get_sources(overwrite_torrent_cache=overwrite_cache)

    def sort_sources(
        self,
        item_information,
        sources_list,
        *,
        smart_play_context=False,
        source_select=False,
    ):
        """
        Method to handle source filtering, sorting and notifications
        :param item_information: The item information
        :type item_information: dict
        :param sources_list: the list of sources to be sorted
        :type sources_list list
        :param smart_play_context: Smart Play binge / pre-scrape context
        :param source_select: True when user picks from Source Select window
        :return: Filtered and Sorted sources
        :rtype: list
        """
        skip_last_release = bool(smart_play_context and not source_select)
        sorter = SourceSorter(item_information, skip_last_release_priority=skip_last_release)
        sources = sorter.sort_sources(sources_list)
        if sources is None or len(sources) < 1:
            g.cancel_playback()
            g.notification(g.ADDON_NAME, g.get_language_string(30032), time=5000)
            return

        if (
            smart_play_context
            and not source_select
            and item_information.get("info", {}).get("mediatype") == g.MEDIA_EPISODE
        ):
            from resources.lib.simkl.ids import episode_num_from_info
            from resources.lib.modules.last_played_source import reorder_sources

            episode = episode_num_from_info(item_information["info"])
            if episode is not None:
                sources, matched = reorder_sources(sources, episode, source_select=source_select)
                if matched:
                    return sources
            if skip_last_release:
                sources = sorter.apply_last_release_name_fallback(sources)

        return sources


def show_persistent_window_if_required(item_information):
    """
    Displays a constant window in the background, used to fill in gaps between windows dropping and opening
    :param item_information:
    :return: WindowDialog
    """
    if g.get_int_setting('general.scrapedisplay') != 0 or g.get_runtime_setting('tempSilent'):
        return None
    from resources.lib.database.skinManager import SkinManager
    from resources.lib.gui.windows.persistent_background import PersistentBackground

    background = PersistentBackground(
        *SkinManager().confirm_skin_path('persistent_background.xml'), item_information=item_information
    )
    background.set_text(g.get_language_string(30030))
    background.show()
    return background
