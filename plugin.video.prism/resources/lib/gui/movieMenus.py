import datetime
from functools import cached_property

import xbmcgui

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
    def movies_database(self):
        from resources.lib.database.session import get_sync_database

        return get_sync_database()

    @cached_property
    def search_history(self):
        from resources.lib.database.searchHistory import SearchHistory

        return SearchHistory()

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

    @staticmethod
    def discover_movies():
        from resources.lib.discover.renderer import DiscoverRenderer

        DiscoverRenderer.show_discover_menu("movie")

    @staticmethod
    @simkl_auth_guard
    def my_movies():
        from resources.lib.simkl.library_menus import my_movies_hub

        my_movies_hub()

    def generic_endpoint(self, endpoint):
        if not browse.render_discover_endpoint("movie", endpoint):
            g.cancel_directory()

    def movie_popular_recent(self):
        browse.render_discover_endpoint("movie", "popular")

    def movie_trending_recent(self):
        browse.render_discover_endpoint("movie", "trending")

    @simkl_auth_guard
    def on_deck_movies(self):
        from resources.lib.simkl.library_menus import render_continue_watching

        render_continue_watching("movie")

    @simkl_auth_guard
    def movies_recommended(self):
        browse.render_discover_endpoint("movie", "anticipated")

    def movies_updated(self):
        browse.render_discover_endpoint("movie", "updated")

    def movies_search_history(self):
        from resources.lib.simkl.search_menus import render_search_history

        render_search_history(
            "movie",
            new_search_action="moviesSearch",
            new_search_label_id=30181,
            new_search_description_id=30371,
            results_action="moviesSearchResults",
            clear_mediatype="movie",
        )

    def movies_search(self, query=None):
        from resources.lib.simkl.search_menus import normalize_search_query, persist_search_pagination

        query = normalize_search_query(query)
        if query is None:
            query = g.get_keyboard_input(heading=g.get_language_string(30013))
            if not query:
                g.cancel_directory()
                return

        if g.get_bool_setting("searchHistory"):
            self.search_history.add_search_history("movie", query)

        persist_search_pagination(query)
        self.movies_search_results(query)

    def movies_search_results(self, query):
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
        render_search_results_list("movie", query, self.page_limit, self.list_builder)

    def movies_related(self, args):
        from resources.lib.simkl.related import render_recommendations

        render_recommendations(args if isinstance(args, dict) else None)

    @staticmethod
    def movies_years():
        from datetime import datetime

        year = int(datetime.today().year)

        for year in range(year, 1899, -1):
            g.add_directory_item(str(year), action="movieYearsMovies", action_args=year)
        g.close_directory(g.CONTENT_MENU)

    def movie_years_results(self, year):
        items = browse.discover_by_year("movie", int(year), g.PAGE, self.page_limit)
        from resources.lib.discover.renderer import discover_list_kwargs
        from resources.lib.meta.list_paint import render_catalog_discover_refs
        from resources.lib.simkl.media_ref import enrich_and_persist

        if not items:
            g.cancel_directory()
            return
        refs = enrich_and_persist("movie", items, enrich=False)
        render_catalog_discover_refs(
            "movie",
            refs,
            self.list_builder,
            list_kwargs=discover_list_kwargs(),
            next_action="movieYearsMovies",
            next_args=int(year),
            has_next_page=len(items) >= self.page_limit,
        )

    def movies_genres(self):
        from resources.lib.simkl.genre_menus import show_genre_picker

        show_genre_picker("movie")

    def movies_genre_list(self, args):
        from resources.lib.simkl.genre_menus import render_genre_list

        render_genre_list("movie", args, self.page_limit, self.list_builder)

    def movies_genres_multi(self):
        from resources.lib.simkl.genre_menus import show_tmdb_genre_multiselect

        show_tmdb_genre_multiselect("movie", self.page_limit, self.list_builder)

    def movies_genres_multi_list(self, args):
        from resources.lib.simkl.genre_menus import render_multi_genre_list

        render_multi_genre_list("movie", args, self.page_limit, self.list_builder)

    @simkl_auth_guard
    def my_watched_movies(self):
        from resources.lib.simkl.library_menus import render_recently_watched_movies

        render_recently_watched_movies()

    def recently_watched_movies(self):
        self.my_watched_movies()
