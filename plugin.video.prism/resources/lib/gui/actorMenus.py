"""Unified TMDB actor search — combined movie + TV filmography."""

from __future__ import annotations



from functools import cached_property



import xbmc



from resources.lib.common import tools

from resources.lib.modules.globals import g

from resources.lib.simkl import browse





class ActorMenus:

    def __init__(self):

        self.page_limit = g.get_int_setting("item.limit")



    @cached_property

    def search_history(self):

        from resources.lib.database.searchHistory import SearchHistory



        return SearchHistory()



    @cached_property

    def list_builder(self):

        from resources.lib.modules.list_builder import ListBuilder



        return ListBuilder()



    @cached_property

    def movies_database(self):

        from resources.lib.database.session import get_sync_database

        return get_sync_database()

    @cached_property

    def shows_database(self):

        from resources.lib.database.session import get_sync_database

        return get_sync_database()



    def actor_search_history(self):

        from resources.lib.simkl.search_menus import render_search_history



        render_search_history(

            "actor",

            new_search_action="searchByActor",

            new_search_label_id=30778,

            new_search_description_id=30776,

            results_action="searchByActor",

            clear_mediatype="actor",

        )



    def search_by_actor(self, query=None):

        from resources.lib.meta.provider_settings import notify_tmdb_required, provider_enabled
        from resources.lib.simkl.search_menus import (
            _actor_pagination_catalog,
            normalize_actor_args,
            normalize_search_query,
            notify_empty_search,
            persist_search_pagination,
            render_person_picker,
        )

        if not provider_enabled("tmdb"):
            notify_tmdb_required()
            g.cancel_directory()
            return

        args = normalize_actor_args(query)

        person_id = args.get("person_id")

        if person_id is not None:

            self.actor_credits(args)

            return



        search_query = normalize_search_query(args.get("query") or query)

        if search_query is None:

            search_query = g.get_keyboard_input(g.get_language_string(30013))

            if not search_query:

                g.cancel_directory()

                return



        if g.get_bool_setting("searchHistory"):

            self.search_history.add_search_history("actor", search_query)



        people = browse.search_people(g.transliterate_string(search_query))

        if not people:

            notify_empty_search(30767)

            return



        persist_search_pagination({"query": search_query, **_actor_pagination_catalog()})

        render_person_picker(people, search_query)



    def actor_credits(self, action_args):

        from resources.lib.meta.provider_settings import notify_tmdb_required, provider_enabled
        from resources.lib.simkl.person_ref import fetch_filmography_page, normalize_person_ref
        from resources.lib.simkl.search_menus import notify_empty_search, persist_search_pagination

        if not provider_enabled("tmdb"):
            notify_tmdb_required()
            g.cancel_directory()
            return

        args = normalize_person_ref(action_args)

        person_id = args.get("person_id")

        if person_id is None:

            g.cancel_directory()

            return



        persist_search_pagination(args)

        from resources.lib.meta.list_pipeline import get_list_store, make_list_id
        from resources.lib.discover.renderer import discover_list_kwargs
        from resources.lib.simkl.media_ref import render_mixed_sync_list

        person_key = make_list_id("actor", person_id, args.get("catalog") or "mixed")
        page = g.PAGE
        store = get_list_store("actor")
        items = store.load_page_items(
            args.get("catalog") or "movie",
            person_key,
            page,
            lambda: fetch_filmography_page(int(person_id), page, self.page_limit),
        )
        if not items:
            notify_empty_search(30768)
            return

        render_mixed_sync_list(
            items,
            **discover_list_kwargs(
                seeded=True,
                enrichment_reason="actor",
                mixed_list=True,
                has_next_page=len(items) >= self.page_limit,
                next_action="actorCredits",
                next_args=args,
            ),
        )



    def open_actor_credit(self, action_args):

        """Resolve a TMDB filmography row to Simkl, then open play/show navigation."""

        from resources.lib.simkl.search_menus import normalize_actor_args, notify_empty_search



        args = normalize_actor_args(action_args)

        tmdb_id = args.get("tmdb_id")

        catalog = args.get("catalog")

        if tmdb_id is None or catalog not in ("movie", "tv", "anime"):

            g.cancel_directory()

            return



        normalized = browse.resolve_tmdb_to_simkl(int(tmdb_id), catalog if catalog != "anime" else "tv")

        if not normalized:

            notify_empty_search(30768)

            return



        catalog = normalized.get("catalog") or catalog

        from resources.lib.database.session import get_sync_database
        from resources.lib.discover.browse_catalog_seed import defer_browse_catalog_seed

        db = get_sync_database()
        db.insert_browse_page(catalog, [normalized])
        defer_browse_catalog_seed(catalog, [normalized])
        sync_params = {"sync_path": True, "skip_update": False, "hide_unaired": False, "hide_watched": False}
        if catalog == "movie":
            rows = db.get_movie_list([normalized], **sync_params)
        else:
            rows = db.get_show_list([normalized], skip_mill=True, **sync_params)
        if not rows:
            notify_empty_search(30768)
            return

        menu_args = self.list_builder._menu_action_args(rows[0])

        if catalog == "movie":
            action = "getSources"
        elif g.get_bool_setting("smartplay.clickresume"):
            action = "forceResumeShow"
        elif g.get_bool_setting("general.flatten.episodes"):
            action = "flatEpisodes"
        else:
            action = "showSeasons"



        url = g.create_url(

            g.BASE_URL,

            {"action": action, "action_args": tools.construct_action_args(menu_args)},

        )

        xbmc.executebuiltin(f"RunPlugin({url})")

