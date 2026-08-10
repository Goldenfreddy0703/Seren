import xbmc
import xbmcgui

from resources.lib.modules.exceptions import NoPlayableSourcesException
from resources.lib.modules.globals import g

"""
    Dispatch module
"""


def dispatch(params):
    url = params.get("url")
    action = params.get("action")
    action_args = params.get("action_args")
    if isinstance(action_args, dict):
        from resources.lib.simkl.ids import normalize_action_args

        action_args = normalize_action_args(action_args)
        params["action_args"] = action_args
    pack_select = params.get("packSelect")
    source_select = params.get("source_select") == "true"
    overwrite_cache = params.get("prism_reload") == "true"
    resume = params.get("resume")
    force_resume_check = params.get("forceresumecheck") == "true"
    force_resume_off = params.get("forceresumeoff") == "true"
    force_resume_on = params.get("forceresumeon") == "true"
    smart_url_arg = params.get("smartPlay") == "true"
    mediatype = params.get("mediatype")
    endpoint = params.get("endpoint")

    g.log(f"Prism, Running Path - {g.REQUEST_PARAMS}")

    if action:
        from resources.lib.simkl.library_routes import dispatch_library_action

        if dispatch_library_action(action, params):
            return

    if action is None:
        from resources.lib.gui import homeMenu

        homeMenu.Menus().home()

    elif action == "genericEndpoint":
        if mediatype == "movies":
            from resources.lib.gui.movieMenus import Menus
        elif mediatype == "anime":
            from resources.lib.gui.animeMenus import Menus
        else:
            from resources.lib.gui.tvshowMenus import Menus
        Menus().generic_endpoint(endpoint)

    elif action == "forceResumeShow":
        from resources.lib.modules import smartPlay
        from resources.lib.common import tools

        smartPlay.SmartPlay(tools.get_item_information(action_args)).resume_show()

    elif action == "moviesHome":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().discover_movies()

    elif action == "moviesUpdated":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_updated()

    elif action == "moviesRecommended":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_recommended()

    elif action == "moviesSearch":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_search(action_args)

    elif action == "moviesSearchResults":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_search_results(action_args)

    elif action == "moviesSearchHistory":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_search_history()

    elif action == "actorSearchHistory":
        from resources.lib.gui import actorMenus

        actorMenus.ActorMenus().actor_search_history()

    elif action == "searchByActor":
        from resources.lib.gui import actorMenus

        actorMenus.ActorMenus().search_by_actor(action_args)

    elif action == "actorCredits":
        from resources.lib.gui import actorMenus

        actorMenus.ActorMenus().actor_credits(action_args)

    elif action == "openActorCredit":
        from resources.lib.gui import actorMenus

        actorMenus.ActorMenus().open_actor_credit(action_args)

    elif action == "moviesRelated":
        from resources.lib.simkl.related import render_recommendations

        render_recommendations(action_args)

    elif action == "simklRecommendations":
        from resources.lib.simkl.related import render_recommendations

        render_recommendations(action_args)

    elif action == "simklRelations":
        from resources.lib.simkl.related import render_relations

        render_relations(action_args)

    elif action == "colorPicker":
        g.color_picker()

    elif action == "authSimkl":
        from resources.lib.indexers.simkl import SimklAPI

        SimklAPI().auth()
        g.open_addon_settings(3, 6)

    elif action == "revokeSimkl":
        from resources.lib.indexers.simkl import SimklAPI

        SimklAPI().revoke()

    elif action == "simklDiscoverList":
        from resources.lib.discover.renderer import DiscoverRenderer

        DiscoverRenderer().render_list(g.REQUEST_PARAMS.get("catalog"), g.REQUEST_PARAMS.get("list_id"))

    elif action == "animeAiringCalendar":
        from resources.lib.calendar.simkl_calendar import open_calendar

        open_calendar("anime")

    elif action == "tvAiringCalendar":
        from resources.lib.calendar.simkl_calendar import open_calendar

        open_calendar("tv")

    elif action == "movieAiringCalendar":
        from resources.lib.calendar.simkl_calendar import open_calendar

        open_calendar("movie")


    elif action == "getSources":
        from resources.lib.modules.smartPlay import SmartPlay
        from resources.lib.common import tools
        from resources.lib.modules import helpers
        from resources.lib.database.providerCache import ProviderCache

        item_information = tools.get_item_information(action_args)
        smart_play = SmartPlay(item_information)
        background = None
        resolver_window = None
        g.set_runtime_setting("playback.pipeline_busy", True)

        try:
            # Check to confirm user has a debrid provider authenticated and enabled
            if not g.premium_check() and ProviderCache().debrid_providers_enabed():
                xbmcgui.Dialog().ok(
                    g.ADDON_NAME,
                    tools.create_multiline_message(
                        line1=g.get_language_string(30915),
                        line2=g.get_language_string(30916),
                    ),
                )
                return None

            # workaround for widgets not generating a playlist on playback request
            play_list = smart_play.playlist_present_check(smart_url_arg)

            if play_list:
                g.log("Cancelling non playlist playback", "warning")
                xbmc.Player().play(g.PLAYLIST)
                return

            resume_time = smart_play.handle_resume_prompt(resume, force_resume_off, force_resume_on, force_resume_check)
            background = helpers.show_persistent_window_if_required(item_information)
            # Clear out last resolved title for a show if we are doing a rescrape
            if overwrite_cache and item_information['info']['mediatype'] == g.MEDIA_EPISODE:
                from resources.lib.simkl.ids import release_title_cache_key

                cache_key = release_title_cache_key(item_information["info"])
                if cache_key:
                    g.clear_runtime_setting(cache_key)

            # Get Sources
            sources_helper = helpers.SourcesHelper()
            sources_result = sources_helper.get_sources(action_args, overwrite_cache=overwrite_cache)
            if sources_result is None:
                return
            uncached, sources_list, ii = sources_result
            if background:
                background.set_process_started()
                background.set_text("")

            # Sort sources
            sources = sources_helper.sort_sources(ii, sources_list)
            if sources is None:
                return

            # Select and resolve source
            if item_information['info']['mediatype'] == g.MEDIA_EPISODE:
                source_select_style = "Episodes"
            else:
                source_select_style = "Movie"

            if g.get_int_setting(f"general.playstyle{source_select_style}") == 1 or source_select:

                if background:
                    background.set_text(g.get_language_string(30178))
                from resources.lib.modules import sourceSelect

                xbmc.sleep(750)
                if background:
                    background.set_text("")
                stream_link = sourceSelect.source_select(uncached, sources, item_information)
            else:
                stream_link = helpers.Resolverhelper().resolve_silent_or_visible(
                    sources, ii, pack_select, overwrite_cache=overwrite_cache
                )
                if stream_link is None:
                    g.close_busy_dialog()
                    g.close_all_dialogs()
                    g.notification(g.ADDON_NAME, g.get_language_string(30032), time=5000)

            if not stream_link or stream_link == "none":
                raise NoPlayableSourcesException

            from resources.lib.modules import player

            try:
                prism_player = player.PrismPlayer()
                prism_player.play_source(stream_link, item_information, resume_time=resume_time)
            finally:
                if background:
                    try:
                        background.close()
                    finally:
                        del background
                del prism_player

        except NoPlayableSourcesException:
            try:
                background.close()
                del background
            except (UnboundLocalError, AttributeError):
                pass
            try:
                resolver_window.close()
                del resolver_window
            except (UnboundLocalError, AttributeError):
                pass

            g.cancel_playback()

        finally:
            g.clear_runtime_setting("playback.pipeline_busy")

    elif action == "preScrape":

        from resources.lib.database.skinManager import SkinManager
        from resources.lib.modules import helpers

        try:
            from resources.lib.common import tools

            item_information = tools.get_item_information(action_args)

            # Get Sources
            sources_helper = helpers.SourcesHelper()
            sources_result = sources_helper.get_sources(action_args)
            if sources_result is None:
                return
            uncached, sources_list, ii = sources_result

            # Sort sources
            sources = sources_helper.sort_sources(ii, sources_list)
            if sources is None:
                return

            if item_information["info"]["mediatype"] == g.MEDIA_EPISODE:
                source_select_style = "Episodes"
            else:
                source_select_style = "Movie"
            if g.get_int_setting(f"general.playstyle{source_select_style}") == 0 and sources:
                from resources.lib.modules import resolver

                helpers.Resolverhelper().resolve_silent_or_visible(sources, ii, pack_select)
        finally:
            g.set_runtime_setting("tempSilent", False)

        g.log("Pre-scraping completed")

    elif action == "authRealDebrid":
        from resources.lib.debrid import real_debrid

        real_debrid.RealDebrid().auth()
        g.open_addon_settings(3, 27)

    elif action == "showsHome":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().discover_shows()

    # ANIME SECTION - Discover Anime (TV Shows + Movies)
    elif action == "discoverAnime":
        from resources.lib.gui.animeMenus import Menus

        Menus.discover_anime()

    elif action == "showsMyRecentEpisodes":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().my_recent_episodes()

    elif action == "showsRecommended":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_recommended()

    elif action == "showsUpdated":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_updated()

    elif action == "showsSearch":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_search(action_args)

    elif action == "showsSearchResults":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_search_results(action_args)

    elif action == "showsSearchHistory":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_search_history()

    elif action == "animeSearch":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_search(action_args)

    elif action == "animeSearchResults":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_search_results(action_args)

    elif action == "animeSearchHistory":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_search_history()

    elif action == "showSeasons":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().show_seasons(action_args)

    elif action == "seasonEpisodes":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().season_episodes(action_args)

    elif action == "showsRelated":
        from resources.lib.simkl.related import render_recommendations

        render_recommendations(action_args)

    elif action == "showYears":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_years(action_args)

    elif action == "searchMenu":
        from resources.lib.gui import homeMenu

        homeMenu.Menus().search_menu()

    elif action == "toolsMenu":
        from resources.lib.gui import homeMenu

        homeMenu.Menus().tools_menu()

    elif action == "clearCache":
        from resources.lib.common import tools

        g.clear_cache()

    elif action == "simklManager":
        from resources.lib.gui.simkl_context_menu import SimklContextMenu
        from resources.lib.common import tools

        SimklContextMenu(tools.get_item_information(action_args))

    elif action == "cacheAssist":
        from resources.lib.modules.cacheAssist import CacheAssistHelper

        CacheAssistHelper().auto_cache(action_args)

    elif action == "tvGenres":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_genres()

    elif action == "showGenresGet":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_genre_list(action_args)

    elif action == "showGenresMulti":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_genres_multi()

    elif action == "showGenresMultiGet":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_genres_multi_list(action_args)

    elif action == "movieGenres":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_genres()

    elif action == "movieGenresGet":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_genre_list(action_args)

    elif action == "movieGenresMulti":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_genres_multi()

    elif action == "movieGenresMultiGet":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_genres_multi_list(action_args)

    elif action == "animeGenres":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_genres()

    elif action == "animeGenresGet":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_genre_list(action_args)

    elif action == "animeGenresMulti":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_genres_multi()

    elif action == "animeGenresMultiGet":
        from resources.lib.gui import animeMenus

        animeMenus.Menus().anime_genres_multi_list(action_args)

    elif action == "shufflePlay":
        from resources.lib.modules import smartPlay

        smartPlay.SmartPlay(action_args).shuffle_play()

    elif action == "resetSilent":
        g.set_runtime_setting("tempSilent", False)
        g.notification(
            f"{g.ADDON_NAME}: {g.get_language_string(30302)}",
            g.get_language_string(30033),
            time=5000,
        )

    elif action == "clearTorrentCache":
        from resources.lib.database.torrentCache import TorrentCache

        TorrentCache().clear_all()

    elif action == "openSettings":
        xbmc.executebuiltin(f"Addon.OpenSettings({g.ADDON_ID})")

    elif action == "nonActiveAssistClear":
        from resources.lib.gui import debridServices

        debridServices.Menus().assist_non_active_clear()

    elif action == "debridServices":
        from resources.lib.gui import debridServices

        debridServices.Menus().home()

    elif action == "cacheAssistStatus":
        from resources.lib.gui import debridServices

        debridServices.Menus().get_assist_torrents()

    elif action == "premiumize_transfers":
        from resources.lib.gui import debridServices

        debridServices.Menus().list_premiumize_transfers()

    elif action == "providerTools":
        from resources.lib.gui import homeMenu

        homeMenu.Menus().provider_menu()

    elif action == "installProviders":
        from resources.lib.modules.providers.install_manager import (
            ProviderInstallManager,
        )

        ProviderInstallManager().install_package(action_args)

    elif action == "uninstallProviders":
        from resources.lib.modules.providers.install_manager import (
            ProviderInstallManager,
        )

        ProviderInstallManager().uninstall_package()

    elif action == "showsNew":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_new()

    elif action == "realdebridTransfers":
        from resources.lib.gui import debridServices

        debridServices.Menus().list_rd_transfers()

    elif action == "cleanInstall":
        from resources.lib.common import maintenance

        maintenance.wipe_install()

    elif action == "importSettings":
        from resources.lib.common import maintenance

        maintenance.import_settings()

    elif action == "exportSettings":
        from resources.lib.common import maintenance

        maintenance.export_settings()

    elif action == "premiumizeCleanup":
        from resources.lib.common import maintenance

        maintenance.premiumize_transfer_cleanup()

    elif action == "manualProviderUpdate":
        from resources.lib.modules.providers.install_manager import (
            ProviderInstallManager,
        )

        ProviderInstallManager().manual_update()
    elif action == "removeSearchHistory":
        from resources.lib.database.searchHistory import SearchHistory

        SearchHistory().remove_search_history(mediatype, endpoint)
        g.container_refresh()
    elif action == "clearSearchHistory":
        from resources.lib.database.searchHistory import SearchHistory

        SearchHistory().clear_search_history(mediatype)

    elif action == "externalProviderInstall":
        from resources.lib.modules.providers.install_manager import (
            ProviderInstallManager,
        )

        confirmation = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30166))
        if confirmation == 0:
            return
        ProviderInstallManager().install_package(1, url=url)

    elif action == "externalProviderUninstall":
        from resources.lib.modules.providers.install_manager import (
            ProviderInstallManager,
        )

        confirmation = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30168).format(url))
        if confirmation == 0:
            return
        ProviderInstallManager().uninstall_package(package=url, silent=False)

    elif action == "showsNetworks":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_networks()

    elif action == "showsNetworkShows":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().shows_networks_results(action_args)

    elif action == "movieYears":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movies_years()

    elif action == "movieYearsMovies":
        from resources.lib.gui import movieMenus

        movieMenus.Menus().movie_years_results(action_args)

    elif action == "syncSimklActivities":
        from resources.lib.database.session import get_sync_database

        force = str(params.get("force", "")).lower() in ("1", "true", "yes")
        get_sync_database().sync_activities(silent=not force, force=force)

    elif action == "simklSyncTools":
        from resources.lib.gui import homeMenu

        homeMenu.Menus().simkl_sync_tools()

    elif action == "flushSimklActivities":
        from resources.lib.database.session import get_sync_database

        get_sync_database().flush_activities()

    elif action == "myFiles":
        from resources.lib.gui import myFiles

        myFiles.Menus().home()

    elif action == "myFilesFolder":
        from resources.lib.gui import myFiles

        myFiles.Menus().my_files_folder(action_args)

    elif action == "myFilesPlay":
        from resources.lib.gui import myFiles

        myFiles.Menus().my_files_play(action_args)

    elif action == "forceSimklSync":
        from resources.lib.database.session import get_sync_database

        simkl_db = get_sync_database()
        simkl_db.flush_activities()
        simkl_db.sync_activities(silent=False, force=True)

    elif action == "rebuildSimklDatabase":
        from resources.lib.database.session import get_sync_database

        get_sync_database().re_build_database()

    elif action == "cleanOrphanedMetadata":
        from resources.lib.database.session import get_sync_database

        get_sync_database().clean_orphaned_metadata()

    elif action == "myUpcomingEpisodes":
        from resources.lib.gui import tvshowMenus

        tvshowMenus.Menus().my_upcoming_episodes()

    elif action == "playFromRandomPoint":
        from resources.lib.modules import smartPlay

        smartPlay.SmartPlay(action_args).play_from_random_point()

    elif action == "refreshProviders":
        from resources.lib.modules.providers import CustomProviders

        providers = CustomProviders()
        providers.update_known_providers()
        providers.poll_database()

    elif action == "installSkin":
        from resources.lib.database.skinManager import SkinManager

        SkinManager().install_skin()

    elif action == "uninstallSkin":
        from resources.lib.database.skinManager import SkinManager

        SkinManager().uninstall_skin()

    elif action == "switchSkin":
        from resources.lib.database.skinManager import SkinManager

        SkinManager().switch_skin()

    elif action == "manageProviders":
        g.show_busy_dialog()
        from resources.lib.gui.windows.provider_packages import ProviderPackages
        from resources.lib.database.skinManager import SkinManager

        try:
            window = ProviderPackages(*SkinManager().confirm_skin_path("provider_packages.xml"))
            window.doModal()
        finally:
            del window

    elif action == "flatEpisodes":
        from resources.lib.gui.tvshowMenus import Menus

        Menus().flat_episode_list(action_args)

    elif action == "runPlayerDialogs":
        from resources.lib.modules.player import PlayerDialogs

        try:
            player_dialogs = PlayerDialogs()
            player_dialogs.display_dialog()
        finally:
            del player_dialogs

    elif action == "showSkipSegment":
        from resources.lib.gui.windows.skip_segment import SkipSegment
        from resources.lib.database.skinManager import SkinManager

        try:
            window = SkipSegment(
                *SkinManager().confirm_skin_path("skip_segment.xml"),
                segment_type=params.get("segment", "intro"),
                segment_end=params.get("end", 0),
            )
            window.doModal()
        finally:
            del window

    elif action == "authAllDebrid":
        from resources.lib.debrid.all_debrid import AllDebrid

        AllDebrid().auth()
        g.open_addon_settings(3, 36)

    elif action == "authTorBox":
        from resources.lib.debrid.torbox import TorBox

        TorBox().auth()
        g.open_addon_settings(3, 45)

    elif action == "authOffCloud":
        from resources.lib.debrid.offcloud import OffCloud

        OffCloud().auth()
        g.open_addon_settings(3, 53)

    elif action == "checkSkinUpdates":
        from resources.lib.database.skinManager import SkinManager

        SkinManager().check_for_updates()

    elif action == "authPremiumize":
        from resources.lib.debrid.premiumize import Premiumize

        Premiumize().auth()
        g.open_addon_settings(3, 13)

    elif action == "testWindows":
        from resources.lib.gui.homeMenu import Menus

        Menus().test_windows()

    elif action == "testPlayingNext":
        from resources.lib.gui import mock_windows

        mock_windows.mock_playing_next()

    elif action == "testStillWatching":
        from resources.lib.gui import mock_windows

        mock_windows.mock_still_watching()

    elif action == "testSkipSegment":
        from resources.lib.gui import mock_windows

        mock_windows.mock_skip_segment()

    elif action == "testSkipOutro":
        from resources.lib.gui import mock_windows

        mock_windows.mock_skip_outro()

    elif action == "testGetSourcesWindow":
        from resources.lib.gui import mock_windows

        mock_windows.mock_get_sources()

    elif action == "testResolverWindow":
        from resources.lib.gui import mock_windows

        mock_windows.mock_resolver()

    elif action == "testSourceSelectWindow":
        from resources.lib.gui import mock_windows

        mock_windows.mock_source_select()

    elif action == "testManualCacheWindow":
        from resources.lib.gui import mock_windows

        mock_windows.mock_cache_assist()

    elif action == "testDownloadManagerWindow":
        from resources.lib.gui import mock_windows

        mock_windows.mock_download_manager()

    elif action == "showsPopularRecent":
        from resources.lib.gui.tvshowMenus import Menus

        Menus().shows_popular_recent()

    elif action == "showsTrendingRecent":
        from resources.lib.gui.tvshowMenus import Menus

        Menus().shows_trending_recent()

    elif action == "moviePopularRecent":
        from resources.lib.gui.movieMenus import Menus

        Menus().movie_popular_recent()

    elif action == "movieTrendingRecent":
        from resources.lib.gui.movieMenus import Menus

        Menus().movie_trending_recent()

    elif action == "animePopularRecent":
        from resources.lib.gui.animeMenus import Menus

        Menus().anime_popular_recent()

    elif action == "animeTrendingRecent":
        from resources.lib.gui.animeMenus import Menus

        Menus().anime_trending_recent()

    elif action == "downloadManagerView":
        from resources.lib.gui.windows.download_manager import DownloadManager
        from resources.lib.database.skinManager import SkinManager

        try:
            window = DownloadManager(*SkinManager().confirm_skin_path("download_manager.xml"))
            window.doModal()
        finally:
            del window

    elif action == "longLifeServiceManager":
        from resources.lib.modules.providers.service_manager import (
            ProvidersServiceManager,
        )

        ProvidersServiceManager().run_long_life_manager()

    elif action == "toggleLanguageInvoker":
        from resources.lib.common.maintenance import toggle_reuselanguageinvoker

        toggle_reuselanguageinvoker()

    elif action == "runMaintenance":
        from resources.lib.common.maintenance import run_maintenance

        run_maintenance()

    elif action == "prefetchCalendars":
        from resources.lib.calendar.simkl_calendar import (
            prefetch_all_calendars,
            prefetch_calendars_enabled,
            weekly_cache_warm,
        )

        if prefetch_calendars_enabled() and not weekly_cache_warm():
            prefetch_all_calendars()

    elif action == "processMetaEnrichmentQueue":
        from resources.lib.meta.enrichment import MetaEnrichmentQueue

        try:
            MetaEnrichmentQueue.process_request(action_args)
        except Exception:
            g.log_stacktrace()

    elif action == "refreshLibraryCache":
        from resources.lib.simkl.library_cache import refresh_library_cache_background

        if isinstance(action_args, dict):
            refresh_library_cache_background(
                action_args.get("catalog"),
                action_args.get("status"),
            )

    elif action == "torrentCacheCleanup":
        from resources.lib.database import torrentCache

        torrentCache.TorrentCache().do_cleanup()

    elif action == "pagePrefetch":
        from resources.lib.modules.page_prefetch import run_page_prefetch_invoke

        run_page_prefetch_invoke(params)

    elif action == "drilldownPrefetch":
        from resources.lib.modules.drilldown_prefetch import run_drilldown_prefetch_invoke

        run_drilldown_prefetch_invoke(params)

    elif action == "pluginWarmup":
        from resources.lib.modules.plugin_warmup import run_plugin_warmup

        run_plugin_warmup()

    elif action == "chooseTimeZone":
        from resources.lib.modules.manual_timezone import choose_timezone

        choose_timezone()

    elif action == "widgetRefresh":
        if_playing = params.get('playing')
        if if_playing is not None:
            if_playing = False if if_playing.lower() == "false" else True if if_playing.lower() == "true" else None
        if if_playing is not None:
            g.trigger_widget_refresh(if_playing=if_playing)
        else:
            g.trigger_widget_refresh()

    elif action == "updateLocalTimezone":
        g.init_local_timezone()

    elif action == "choosePlaybackLocale":
        import resources.lib.gui.windows.locale_select as locale_select
        from resources.lib.modules import catalog_profiles

        catalog = catalog_profiles.normalize_catalog(g.REQUEST_PARAMS.get("catalog"))
        if not g.REQUEST_PARAMS.get("catalog"):
            catalog = catalog_profiles.get_last_catalog()

        try:
            window = locale_select.LocaleSelect("locale_select.xml", g.ADDON_PATH, catalog=catalog)
            window.doModal()
        finally:
            del window

    elif action == "chooseFilters":
        import resources.lib.gui.windows.filter_select as filter_select
        from resources.lib.modules import catalog_profiles

        catalog = catalog_profiles.normalize_catalog(g.REQUEST_PARAMS.get("catalog"))
        if not g.REQUEST_PARAMS.get("catalog"):
            catalog = catalog_profiles.get_last_catalog()

        try:
            window = filter_select.FilterSelect("filter_select.xml", g.ADDON_PATH, catalog=catalog)
            window.doModal()
        finally:
            del window

    elif action == "chooseSorting":
        import resources.lib.gui.windows.sort_select as sort_select
        from resources.lib.modules import catalog_profiles

        catalog = catalog_profiles.normalize_catalog(g.REQUEST_PARAMS.get("catalog"))
        if not g.REQUEST_PARAMS.get("catalog"):
            catalog = catalog_profiles.get_last_catalog()

        try:
            window = sort_select.SortSelect("sort_select.xml", g.ADDON_PATH, catalog=catalog)
            window.doModal()
        finally:
            del window
    else:
        g.log(f"Unknown action requested: {action}", "error")
