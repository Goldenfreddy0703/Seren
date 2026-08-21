import datetime
from functools import cached_property

import xbmcgui
import xbmcplugin

from resources.lib.common import tools
from resources.lib.indexers import simkl_auth_guard
from resources.lib.modules.globals import g
from resources.lib.simkl import browse


class Menus:
    def __init__(self):
        self.page_limit = g.get_int_setting("item.limit")
        self.page_start = (g.PAGE - 1) * self.page_limit
        self.page_end = g.PAGE * self.page_limit

    # Cached properties to lazy load imports

    @cached_property
    def shows_database(self):
        from resources.lib.database.session import get_sync_database

        return get_sync_database()

    @cached_property
    def search_history(self):
        from resources.lib.database.searchHistory import SearchHistory

        return SearchHistory()

    @cached_property
    def hidden_database(self):
        from resources.lib.database.simkl_sync.hidden import SimklSyncDatabase as HiddenDatabase

        return HiddenDatabase()

    @cached_property
    def bookmark_database(self):
        from resources.lib.database.simkl_sync.bookmark import SimklSyncDatabase as BookmarkDatabase

        return BookmarkDatabase()

    @cached_property
    def simkl_api(self):
        from resources.lib.indexers.simkl import SimklAPI

        return SimklAPI()

    @cached_property
    def list_builder(self):
        from resources.lib.modules.list_builder import ListBuilder

        return ListBuilder()

    ######################################################
    # MENUS
    ######################################################

    @simkl_auth_guard
    def on_deck(self):
        from resources.lib.simkl.library_menus import render_continue_watching

        render_continue_watching(self._library_catalog())

    @simkl_auth_guard
    def on_deck_shows(self):
        self.on_deck()

    def next_up(self):
        from resources.lib.simkl.library_menus import render_next_up

        render_next_up(self._library_catalog())

    def my_next_up(self):
        self.next_up()

    def recently_watched(self):
        from resources.lib.simkl.library_menus import render_recently_watched_shows

        render_recently_watched_shows(self._library_catalog())

    def shows_recently_watched(self):
        self.recently_watched()

    def watched_episodes(self):
        from resources.lib.simkl.library_menus import render_watched_episodes

        render_watched_episodes(self._library_catalog())

    def my_watched_episode(self):
        self.watched_episodes()

    @staticmethod
    def _library_catalog(default: str = "tv") -> str:
        return g.REQUEST_PARAMS.get("catalog", default)

    @staticmethod
    def discover_shows():
        from resources.lib.discover.renderer import DiscoverRenderer

        DiscoverRenderer.show_discover_menu("tv")

    @staticmethod
    @simkl_auth_guard
    def my_shows():
        from resources.lib.simkl.library_menus import my_shows_hub

        my_shows_hub()

    def generic_endpoint(self, endpoint):
        if not browse.render_discover_endpoint("tv", endpoint):
            g.cancel_directory()

    def shows_popular_recent(self):
        browse.render_discover_endpoint("tv", "popular")

    def shows_trending_recent(self):
        browse.render_discover_endpoint("tv", "trending")

    @simkl_auth_guard
    def shows_recommended(self):
        browse.render_discover_endpoint("tv", "anticipated")

    def shows_new(self):
        browse.render_discover_endpoint("tv", "new")

    @simkl_auth_guard
    def my_recent_episodes(self):
        from resources.lib.meta.list_paint import render_catalog_episodes
        from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
        from resources.lib.simkl.ids import show_id_from_item

        hidden_shows = self.hidden_database.get_hidden_items("calendar", "shows")
        episodes = [
            ep for ep in browse.airing_episodes("today") if show_id_from_item(ep) not in hidden_shows
        ]
        render_catalog_episodes(
            self._library_catalog(),
            episodes,
            self.list_builder,
            **profile_list_kwargs(
                MenuPaintProfile.AIRING,
                hide_unaired=False,
                no_paging=True,
                seeded=True,
            ),
        )

    @simkl_auth_guard
    def my_upcoming_episodes(self):
        from resources.lib.meta.list_paint import render_catalog_episodes
        from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs

        episodes = browse.airing_episodes("tomorrow")[: self.page_limit]
        render_catalog_episodes(
            self._library_catalog(),
            episodes,
            self.list_builder,
            **profile_list_kwargs(
                MenuPaintProfile.AIRING,
                hide_unaired=False,
                prepend_date=True,
                no_paging=True,
                seeded=True,
            ),
        )

    def shows_networks(self):
        xbmcgui.Dialog().ok(g.ADDON_NAME, "Network browse is not available with Simkl. Use Discover or Genres instead.")
        g.cancel_directory()

    def shows_networks_results(self, network):
        xbmcgui.Dialog().ok(g.ADDON_NAME, "Network browse is not available with Simkl. Use Discover or Genres instead.")
        g.cancel_directory()

    def shows_updated(self):
        browse.render_discover_endpoint("tv", "updated")

    def shows_search_history(self):
        from resources.lib.simkl.search_menus import render_search_history

        render_search_history(
            "tvshow",
            new_search_action="showsSearch",
            new_search_label_id=30182,
            new_search_description_id=30372,
            results_action="showsSearchResults",
            clear_mediatype="tvshow",
        )

    def shows_search(self, query=None):
        from resources.lib.simkl.search_menus import normalize_search_query, persist_search_pagination

        query = normalize_search_query(query)
        if query is None:
            query = g.get_keyboard_input(heading=g.get_language_string(30013))
            if not query:
                g.cancel_directory()
                return

        if g.get_bool_setting("searchHistory"):
            self.search_history.add_search_history("tvshow", query)

        persist_search_pagination(query)
        self.shows_search_results(query)

    def shows_search_results(self, query):
        from resources.lib.simkl.search_menus import (
            normalize_search_query,
            persist_search_pagination,
            render_search_results_list,
        )

        query = normalize_search_query(query)
        if not query:
            g.cancel_directory()
            return

        persist_search_pagination(query)
        render_search_results_list("tv", query, self.page_limit, self.list_builder)

    def show_seasons(self, args):
        from resources.lib.meta.list_paint import render_catalog_seasons
        from resources.lib.simkl.ids import normalize_action_args, show_id_from_args

        args = normalize_action_args(args)
        if g.get_bool_setting("general.flatten.episodes"):
            self.flat_episode_list(args)
            return
        show_id = show_id_from_args(args)
        if not show_id:
            g.cancel_directory()
            return
        render_catalog_seasons(show_id, self.list_builder, no_paging=True)

    def flat_episode_list(self, args):
        from resources.lib.meta.list_paint import render_catalog_drilldown_episodes
        from resources.lib.simkl.ids import normalize_action_args, show_id_from_args

        args = normalize_action_args(args)
        show_id = show_id_from_args(args)
        if not show_id:
            g.log(f"Invalid show action_args for flat episode list: {args}", "error")
            g.cancel_directory()
            return
        render_catalog_drilldown_episodes(
            show_id,
            self.list_builder,
            action_args=args,
        )

    def season_episodes(self, args):
        from resources.lib.meta.list_paint import render_catalog_drilldown_episodes
        from resources.lib.simkl.ids import normalize_action_args, season_num_from_args, show_id_from_args

        args = normalize_action_args(args)
        show_id = show_id_from_args(args)
        season_num = season_num_from_args(args)
        if not show_id or season_num is None:
            g.log(f"Invalid season action_args for episode list: {args}", "error")
            g.cancel_directory()
            return
        render_catalog_drilldown_episodes(
            show_id,
            self.list_builder,
            season=season_num,
            no_paging=True,
        )

    def shows_genres(self):
        from resources.lib.simkl.genre_menus import show_genre_picker

        show_genre_picker("tv")

    def shows_genre_list(self, args):
        from resources.lib.simkl.genre_menus import render_genre_list

        render_genre_list("tv", args, self.page_limit, self.list_builder)

    def shows_genres_multi(self):
        from resources.lib.simkl.genre_menus import show_tmdb_genre_multiselect

        show_tmdb_genre_multiselect("tv", self.page_limit, self.list_builder)

    def shows_genres_multi_list(self, args):
        from resources.lib.simkl.genre_menus import render_multi_genre_list

        render_multi_genre_list("tv", args, self.page_limit, self.list_builder)

    def shows_related(self, args):
        from resources.lib.simkl.related import render_recommendations

        render_recommendations(args if isinstance(args, dict) else None)

    def shows_years(self, year=None):
        if year is None:
            current_year = datetime.datetime.now().year
            for year in range(current_year, 1899, -1):
                g.add_directory_item(str(year), action="showYears", action_args=year)
            g.close_directory(g.CONTENT_MENU)
        else:
            from resources.lib.discover.renderer import discover_list_kwargs
            from resources.lib.meta.list_pipeline import get_list_store, make_list_id, render_list_page, seed_browse_page
            from resources.lib.modules.globals import g

            list_id = make_list_id(year)
            page = g.PAGE
            store = get_list_store("year")

            items = store.load_page_items(
                "tv",
                list_id,
                page,
                lambda: browse.discover_by_year("tv", int(year), page, self.page_limit),
            )
            if not items:
                g.cancel_directory()
                return

            seed_browse_page("tv", items)
            render_list_page(
                "paint_first",
                "tv",
                items,
                self.list_builder,
                list_kwargs=discover_list_kwargs(
                    seeded=True,
                    next_action="showYears",
                    next_args=int(year),
                    has_next_page=len(items) >= self.page_limit,
                ),
                payload_rows=items,
                seeded=True,
            )

