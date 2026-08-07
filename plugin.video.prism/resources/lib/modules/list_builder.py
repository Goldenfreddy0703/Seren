from __future__ import annotations

import datetime

import xbmcplugin

from resources.lib.common import tools
from resources.lib.database.simkl_sync import movies
from resources.lib.database.simkl_sync import shows
from resources.lib.modules.air_date_delay import item_has_aired
from resources.lib.modules.globals import g


class ListBuilder:
    """
    Ease of use class to handle building menus of lists or list items
    """

    def __init__(self):
        self.title_appends_mixed = g.get_int_setting("general.appendtitles", 3)
        self.title_appends_general = g.get_int_setting("general.appendepisodegeneral", 0)
        self.page_limit = g.get_int_setting("item.limit")
        self.hide_unaired = g.get_bool_setting("general.hideUnAired")
        self.hide_watched = g.get_bool_setting("general.hideWatched")
        self.hide_specials = g.get_bool_setting("general.hideSpecials")
        self.list_title_appends = g.get_int_setting("general.appendListTitles")
        self.show_original_title = g.get_bool_setting("general.meta.showoriginaltitle", False)

    def _apply_list_filters(self, params):
        """Respect general-tab hide settings unless the caller overrides them."""
        params.setdefault("hide_unaired", self.hide_unaired)
        hide_watched = self.hide_watched
        if g.FROM_WIDGET:
            hide_watched = True
        params.setdefault("hide_watched", hide_watched)
        params.setdefault("hide_specials", self.hide_specials)
        return params

    @staticmethod
    def _skip_watched_widget_item(item) -> bool:
        """POV-style: do not build list rows for watched items in widgets."""
        if not g.FROM_WIDGET:
            return False
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        mediatype = info.get("mediatype")
        if mediatype == "movie":
            return bool(item.get("play_count") or info.get("playcount"))
        if mediatype == "tvshow":
            try:
                episode_count = int(item.get("episode_count") or info.get("episode_count") or 0)
                watched_episodes = int(item.get("watched_episodes") or info.get("watched_episodes_count") or 0)
            except (TypeError, ValueError):
                return False
            return episode_count > 0 and watched_episodes >= episode_count
        if mediatype == "episode":
            return bool(item.get("play_count") or info.get("playcount"))
        return False

    def season_list_builder(self, show_id, media_list=None, **params):
        """
        Builds a menu list of a shows seasons
        :param show_id: Simkl ID of show
        :param media_list: Pre-painted season rows (from render_catalog_seasons)
        :param params: Parameters to send to common_menu_builder method
        :return: List list_items if smart_play Kwarg is True else None
        """
        from resources.lib.meta.list_paint import (
            ensure_show_metadata_async,
            load_show_menu_context,
            overlay_parent_context_on_rows,
            set_show_menu_plugin_category,
        )

        if media_list is None:
            ensure_show_metadata_async(show_id)
            show_ctx = load_show_menu_context(show_id)
            set_show_menu_plugin_category(show_ctx)
            self._fast_list_defaults(params)
            rows = self._sync_db().get_season_list(show_id, **self._apply_list_filters(params))
            rows = overlay_parent_context_on_rows(rows, show_ctx)
        else:
            rows = media_list
        show_status = None
        try:
            show_row = self._sync_db().fetchone(
                "SELECT simkl_status FROM shows WHERE simkl_id=?",
                (int(show_id),),
            )
            show_status = (show_row or {}).get("simkl_status")
        except Exception:
            show_status = None
        if show_status:
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                info = row.get("info")
                if not isinstance(info, dict):
                    info = {}
                    row["info"] = info
                info.setdefault("simkl_status", show_status)
        return self._common_menu_builder(
            rows,
            g.CONTENT_SEASON,
            "seasonEpisodes",
            library_status=show_status,
            **params,
        )

    @staticmethod
    def _sync_db():
        from resources.lib.database.session import get_sync_database

        return get_sync_database()

    def episode_list_builder(self, show_id, season=None, simkl_show_id=None, simkl_season=None, season_row_id=None, media_list=None, **params):
        """
        Builds a menu list of episodes for a show's season.
        :param show_id: Show simkl_id
        :param season: Season number (1, 2, …)
        :param media_list: Pre-painted episode rows (from render_catalog_drilldown_episodes)
        :param simkl_show_id: Deprecated alias for show_id
        :param simkl_season: Deprecated alias for season number or legacy row id
        :param season_row_id: Deprecated internal row id — use season= instead
        """
        show_id = show_id if show_id is not None else simkl_show_id
        if season is None:
            if season_row_id is not None:
                from resources.lib.simkl.ids import resolve_season_filter

                _, season = resolve_season_filter(show_id, season_row_id=season_row_id)
            elif simkl_season is not None:
                from resources.lib.simkl.ids import resolve_season_filter

                _, season = resolve_season_filter(show_id, season=simkl_season, season_row_id=simkl_season)

        params["is_folder"] = False
        params["is_playable"] = True
        action = "getSources"

        if media_list is None:
            from resources.lib.meta.list_paint import (
                ensure_show_metadata_async,
                load_season_menu_context,
                load_show_menu_context,
                overlay_parent_context_on_rows,
                set_show_menu_plugin_category,
            )

            ensure_show_metadata_async(show_id)
            show_ctx = load_show_menu_context(show_id)
            season_ctx = load_season_menu_context(show_id, season) if season is not None else None
            set_show_menu_plugin_category(show_ctx, season_num=season)
            self._fast_list_defaults(params)
            rows = self._sync_db().get_episode_list(
                show_id,
                season=season,
                minimum_episode=params.pop("minimum_episode", None),
                **self._apply_list_filters(params),
            )
            rows = overlay_parent_context_on_rows(rows, show_ctx, season_ctx=season_ctx)
        else:
            rows = media_list

        return self._common_menu_builder(
            rows,
            g.CONTENT_EPISODE,
            action,
            **params,
        )

    def mixed_episode_builder(self, media_list, **params):
        """
        Builds a menu list of episodes of mixed shows/seasons
        :param media_list: List of episode objects
        :param params: Parameters to send to common_menu_builder method
        :return: List list_items if smart_play Kwarg is True else None
        """
        params["is_folder"] = False
        params["is_playable"] = True
        params["mixed_list"] = True
        action = "getSources"
        overlay_parents = params.pop("overlay_parent_shows", False)
        preloaded_rows = params.pop("preloaded_episode_rows", None)
        enrichment_refs = params.pop("enrichment_refs", None)
        enrichment_media_type = params.pop("enrichment_media_type", None)

        self._fast_list_defaults(params)
        paint_profile = params.get("paint_profile")
        if preloaded_rows is not None:
            rows = preloaded_rows
        else:
            rows = self._sync_db().get_mixed_episode_list(media_list, **self._apply_list_filters(params))
            if overlay_parents:
                catalog = params.get("catalog_hint") or params.get("catalog") or "tv"
                from resources.lib.meta.list_paint import finish_episode_parent_paint

                rows, enrichment_refs, enrichment_media_type = finish_episode_parent_paint(
                    rows,
                    catalog,
                    paint_profile=paint_profile,
                )
        return self._common_menu_builder(
            rows,
            g.CONTENT_EPISODE,
            action,
            enrichment_refs=enrichment_refs,
            enrichment_media_type=enrichment_media_type,
            **params,
        )

    @staticmethod
    def _schedule_next_page_prefetch(page_params: dict | None) -> None:
        from resources.lib.modules.page_prefetch import schedule_page_prefetch_chain

        schedule_page_prefetch_chain(page_params)

    @staticmethod
    def _build_next_page_params(
        *,
        no_paging: bool,
        has_next_page: bool,
        list_items: list,
        page_limit: int,
        next_action: str | None,
        next_args,
        catalog_hint: str | None = None,
        list_id: str | None = None,
    ) -> dict | None:
        if (
            g.FROM_WIDGET
            and g.get_bool_setting("general.widget.hide_next")
        ) or no_paging:
            return None
        if not list_items:
            return None
        if not (has_next_page or len(list_items) >= page_limit):
            return None
        page_params = dict(g.REQUEST_PARAMS)
        page_params["page"] = g.PAGE + 1
        if next_args:
            page_params["action_args"] = next_args
        elif page_params.get("action_args") is not None:
            page_params["action_args"] = page_params.get("action_args")
        page_params["special_sort"] = "bottom"
        if next_action:
            page_params["action"] = next_action
        if catalog_hint in ("movie", "tv", "anime"):
            page_params["catalog"] = catalog_hint
        if list_id:
            page_params["list_id"] = list_id
        return page_params

    def _fast_list_defaults(self, params: dict) -> None:
        """Browse/drilldown lists: apply MenuPaintProfile defaults when action is mapped."""
        from resources.lib.meta.menu_paint_profile import current_action_profile_kwargs, profile_for_action

        profile_kwargs = current_action_profile_kwargs()
        params.setdefault("skip_mill", profile_kwargs.get("skip_mill", True))
        params.setdefault("skip_update", profile_kwargs.get("skip_update", True))
        if "paint_only" in profile_kwargs:
            params.setdefault("paint_only", profile_kwargs["paint_only"])
        if profile_kwargs.get("menu_cache"):
            params.setdefault("menu_cache", True)
        profile = profile_for_action((g.REQUEST_PARAMS or {}).get("action"))
        if profile is not None:
            params.setdefault("paint_profile", profile.value)

    @staticmethod
    def _schedule_background_enrichment(refs: list[dict] | None, media_type: str | None, *, reason: str = "list_open", catalog: str | None = None) -> None:
        if not refs or not media_type:
            return
        from resources.lib.meta.enrichment import MetaEnrichmentQueue

        MetaEnrichmentQueue.schedule_run_plugin(refs, media_type, reason=reason, catalog=catalog)

    def show_list_builder(self, media_list, **params):
        """
        Builds a menu list of shows
        :param media_list: List of show objects
        :param params: Parameters to send to common_menu_builder method
        :return: List list_items if smart_play Kwarg is True else None
        """
        catalog = params.pop("catalog", None)
        content_type = g.CONTENT_ANIME if catalog == "anime" else g.CONTENT_SHOW
        action = "flatEpisodes" if g.get_bool_setting("general.flatten.episodes") else "showSeasons"
        if g.get_bool_setting("smartplay.clickresume"):
            params["is_folder"] = False
            params["is_playable"] = True
            action = "forceResumeShow"

        self._fast_list_defaults(params)
        show_db = self._sync_db()
        media_rows = show_db.get_show_list(media_list, **self._apply_list_filters(params))
        enrichment_refs, enrichment_media_type = show_db.consume_list_enrichment_refs()
        self._common_menu_builder(
            media_rows,
            content_type,
            action,
            enrichment_refs=enrichment_refs,
            enrichment_media_type=enrichment_media_type,
            **params,
        )

    def movie_menu_builder(self, media_list, **params):
        """
        Builds a mneu list of movies
        :param media_list: List of movie objects
        :param params: Parameters to send to common_menu_builder method
        :return: List list_items if smart_play Kwarg is True else None
        """
        params["is_folder"] = False
        params["is_playable"] = True
        action = "getSources"

        self._fast_list_defaults(params)
        movie_db = self._sync_db()
        media_rows = movie_db.get_movie_list(media_list, **self._apply_list_filters(params))
        enrichment_refs, enrichment_media_type = movie_db.consume_list_enrichment_refs()
        self._common_menu_builder(
            media_rows,
            g.CONTENT_MOVIE,
            action,
            enrichment_refs=enrichment_refs,
            enrichment_media_type=enrichment_media_type,
            **params,
        )

    @staticmethod
    def _sync_dict_to_menu_row(sync_item: dict, *, _allow_catalog_poster_lookup: bool = True) -> dict | None:
        blob = sync_item.get("simkl_object") or {}
        info = blob.get("info")
        if not isinstance(info, dict):
            info = {}
        info = dict(info)
        simkl_id = sync_item.get("simkl_id")
        if simkl_id is not None and info.get("simkl_id") is None:
            info["simkl_id"] = int(simkl_id)
        catalog = sync_item.get("catalog")
        if catalog in ("movie", "tv", "anime") and not info.get("catalog"):
            info["catalog"] = catalog
        if not info.get("mediatype"):
            info["mediatype"] = "movie" if catalog == "movie" else "tvshow"
        art = blob.get("art") if isinstance(blob.get("art"), dict) else {}
        if not info.get("title") and simkl_id is not None and catalog in ("movie", "tv", "anime") and _allow_catalog_poster_lookup:
            from resources.lib.discover.catalog_store import get_items_batch

            loaded = get_items_batch(catalog, [int(simkl_id)])
            full_item = loaded.get(int(simkl_id))
            if isinstance(full_item, dict) and full_item is not sync_item:
                return ListBuilder._sync_dict_to_menu_row(full_item, _allow_catalog_poster_lookup=False)
        if not info.get("title"):
            return None
        if not art.get("poster") and simkl_id is not None and catalog in ("movie", "tv", "anime") and _allow_catalog_poster_lookup:
            from resources.lib.discover.catalog_store import get_items_batch

            loaded = get_items_batch(catalog, [int(simkl_id)])
            full_item = loaded.get(int(simkl_id))
            if isinstance(full_item, dict) and full_item is not sync_item:
                return ListBuilder._sync_dict_to_menu_row(full_item, _allow_catalog_poster_lookup=False)
        menu_row = {
            "info": info,
            "art": art,
            "cast": blob.get("cast") or [],
        }
        if sync_item.get("args"):
            menu_row["args"] = sync_item["args"]
        else:
            from resources.lib.simkl.ids import build_action_args

            menu_row["args"] = build_action_args(
                {"info": info, "simkl_object": blob, "simkl_id": simkl_id, "catalog": catalog}
            )
        return menu_row

    @staticmethod
    def _credit_label2(item: dict, credit: dict | None = None) -> str | None:
        role = item.get("_credit_role")
        if role:
            return str(role)
        if credit:
            runtime = credit.get("runtime")
            if runtime:
                try:
                    minutes = int(runtime)
                    hours, mins = divmod(minutes, 60)
                    return f"{hours}:{mins:02d}:00" if hours else f"{mins}:00"
                except (TypeError, ValueError):
                    pass
            character = credit.get("character") or credit.get("job")
            if character:
                return str(character)
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        duration = info.get("duration")
        if duration:
            try:
                minutes = int(duration) // 60
                hours, mins = divmod(minutes, 60)
                return f"{hours}:{mins:02d}:00" if hours else f"{mins}:00"
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _mixed_media_content_type(catalog_hint: str | None, movie_count: int, show_count: int) -> str:
        if catalog_hint == "anime":
            return g.CONTENT_ANIME
        if catalog_hint == "movie" and show_count == 0 and movie_count > 0:
            return g.CONTENT_MOVIE
        if catalog_hint == "tv":
            return g.CONTENT_SHOW
        if show_count >= movie_count:
            return g.CONTENT_SHOW
        if movie_count > 0 and show_count == 0:
            return g.CONTENT_MOVIE
        return g.CONTENT_SHOW

    def _load_milled_menu_rows(
        self,
        media_list,
        *,
        skip_mill=True,
        skip_update=False,
        paint_only=True,
        hide_unaired=None,
        hide_watched=None,
    ) -> tuple[dict[int, dict], dict[int, dict], list[tuple[list[dict], str]]]:
        """Run sync DB local merge and return menu rows plus pending enrichment batches."""
        filter_params = {
            "hide_unaired": self.hide_unaired if hide_unaired is None else hide_unaired,
            "hide_watched": self.hide_watched if hide_watched is None else hide_watched,
            "skip_mill": skip_mill,
            "skip_update": skip_update,
            "paint_only": paint_only,
        }
        movie_refs = [
            {"simkl_id": i["simkl_id"]}
            for i in media_list
            if isinstance(i, dict) and i.get("catalog") == "movie" and i.get("simkl_id") is not None
        ]
        show_refs = [
            {"simkl_id": i["simkl_id"]}
            for i in media_list
            if isinstance(i, dict) and i.get("catalog") in ("tv", "anime") and i.get("simkl_id") is not None
        ]

        movie_rows: dict[int, dict] = {}
        show_rows: dict[int, dict] = {}
        enrichment_batches: list[tuple[list[dict], str]] = []
        sync_db = self._sync_db()
        if movie_refs:
            for row in sync_db.get_movie_list(movie_refs, **filter_params) or []:
                if isinstance(row, dict) and row.get("simkl_id") is not None and isinstance(row.get("info"), dict):
                    movie_rows[int(row["simkl_id"])] = row
        if show_refs:
            for row in sync_db.get_show_list(show_refs, **filter_params) or []:
                if isinstance(row, dict) and row.get("simkl_id") is not None and isinstance(row.get("info"), dict):
                    show_rows[int(row["simkl_id"])] = row
        enrichment_batches = sync_db.consume_list_enrichment_batches()
        return movie_rows, show_rows, enrichment_batches

    def _mixed_media_from_sync_dicts(
        self,
        media_list,
        *,
        label2_for_item=None,
        catalog_hint=None,
        next_action=None,
        **params,
    ):
        """Build a mixed movie + show/anime list from Simkl sync dicts."""
        if not media_list:
            g.log("No mixed media refs to build a list", "warning")
            g.cancel_directory()
            return

        smart_play = params.pop("smart_play", False)
        no_paging = params.pop("no_paging", False)
        sort = params.pop("sort", False)
        prepend_date = params.pop("prepend_date", False)
        next_args = params.pop("next_args", None)
        has_next_page = params.pop("has_next_page", False)
        list_id = params.pop("list_id", None)
        skip_mill = params.pop("skip_mill", True)
        skip_update = params.pop("skip_update", True)
        paint_only = params.pop("paint_only", True)
        display_rating_priority = params.pop("display_rating_priority", None)
        menu_cache = params.pop("menu_cache", None)
        enrichment_reason = params.pop("enrichment_reason", "discover")
        library_status = params.pop("library_status", None)
        hide_unaired = params.pop("hide_unaired", self.hide_unaired)
        hide_watched = params.pop("hide_watched", self.hide_watched)
        params.pop("hide_specials", None)
        params.pop("ignore_cache", None)

        import time

        paint_start = time.time()
        preloaded_paint_rows = params.pop("preloaded_paint_rows", None)
        preloaded_paint_complete = params.pop("preloaded_paint_complete", False)
        if preloaded_paint_rows:
            from resources.lib.meta.paint_cache import overlay_display_meta_stamps
            from resources.lib.meta.paint_complete import rows_page_paint_ready

            preloaded_paint_rows = overlay_display_meta_stamps(preloaded_paint_rows)
            paint_profile = str(params.get("paint_profile") or "browse")
            if not preloaded_paint_complete:
                preloaded_paint_complete = rows_page_paint_ready(preloaded_paint_rows, profile=paint_profile)
            movie_rows = {}
            show_rows = {}
            for row in preloaded_paint_rows:
                if not isinstance(row, dict) or row.get("simkl_id") is None:
                    continue
                sid = int(row["simkl_id"])
                cat = row.get("catalog") or catalog_hint
                if cat == "movie":
                    movie_rows[sid] = row
                else:
                    show_rows[sid] = row
            if not preloaded_paint_complete:
                from resources.lib.meta.paint_cache import _merge_cached_cast
                from resources.lib.meta.paint_complete import partition_paint_rows

                sync_db = self._sync_db()
                handler = sync_db.metadataHandler
                paint_profile = str(params.get("paint_profile") or "browse")
                if movie_rows:
                    movie_list = list(movie_rows.values())
                    _complete, incomplete_movies = partition_paint_rows(
                        movie_list, "movie", profile=paint_profile
                    )
                    if incomplete_movies:
                        merged_movies = _merge_cached_cast(incomplete_movies, "movie", sync_db)
                        prepared, _refs, _stats = handler.prepare_list_rows_for_paint(
                            merged_movies,
                            "movie",
                            db=sync_db,
                            profile=paint_profile,
                            overlay_sync=False,
                        )
                        for row in prepared:
                            if row.get("simkl_id") is not None:
                                movie_rows[int(row["simkl_id"])] = row
                if show_rows:
                    show_list = list(show_rows.values())
                    _complete, incomplete_shows = partition_paint_rows(
                        show_list, "tvshow", profile=paint_profile
                    )
                    if incomplete_shows:
                        merged_shows = _merge_cached_cast(incomplete_shows, "tvshow", sync_db)
                        prepared, _refs, _stats = handler.prepare_list_rows_for_paint(
                            merged_shows,
                            "tvshow",
                            db=sync_db,
                            profile=paint_profile,
                            overlay_sync=False,
                        )
                        for row in prepared:
                            if row.get("simkl_id") is not None:
                                show_rows[int(row["simkl_id"])] = row
            enrichment_batches = []
            if preloaded_paint_complete:
                enrichment_batches = self._sync_db().consume_list_enrichment_batches()
        else:
            movie_rows, show_rows, enrichment_batches = self._load_milled_menu_rows(
                media_list,
                skip_mill=skip_mill,
                skip_update=skip_update,
                paint_only=paint_only,
                hide_unaired=hide_unaired,
                hide_watched=hide_watched,
            )

        list_items = []
        movie_count = 0
        show_count = 0
        seen_simkl: set[int] = set()

        try:
            params["bulk_add"] = True
            for item in media_list:
                if not isinstance(item, dict):
                    continue
                catalog = item.get("catalog")
                simkl_id = item.get("simkl_id")
                if simkl_id is None or catalog not in ("movie", "tv", "anime"):
                    continue
                simkl_id = int(simkl_id)
                if simkl_id in seen_simkl:
                    continue
                seen_simkl.add(simkl_id)

                menu_row = movie_rows.get(simkl_id) if catalog == "movie" else show_rows.get(simkl_id)
                if not menu_row:
                    menu_row = self._sync_dict_to_menu_row(item)
                if not menu_row or self._skip_watched_widget_item(menu_row):
                    continue

                if catalog == "movie":
                    movie_count += 1
                    action = "getSources"
                    row_params = {"is_folder": False, "is_playable": True}
                else:
                    show_count += 1
                    action = "flatEpisodes" if g.get_bool_setting("general.flatten.episodes") else "showSeasons"
                    row_params = {"is_folder": True, "is_playable": False}
                    if g.get_bool_setting("smartplay.clickresume"):
                        row_params = {"is_folder": False, "is_playable": True}
                        action = "forceResumeShow"

                label2 = label2_for_item(item) if label2_for_item else None
                processed = self._post_process(
                    self._copy_menu_row(menu_row),
                    prepend_date,
                    False,
                    library_status=library_status,
                    display_rating_priority=display_rating_priority,
                )
                if processed is None:
                    continue
                list_items.append(
                    g.add_directory_item(
                        processed.get("name"),
                        action=action,
                        menu_item=processed,
                        action_args=self._menu_action_args(processed),
                        label2=label2,
                        **row_params,
                        **params,
                    )
                )

            if not list_items:
                g.log("Mixed media list had no displayable rows", "warning")
                g.cancel_directory()
                return

            if smart_play:
                return list_items

            xbmcplugin.addDirectoryItems(g.PLUGIN_HANDLE, list_items, len(list_items))
        except Exception:
            g.log_stacktrace()
            if not smart_play:
                g.cancel_directory()
            raise
        finally:
            if not smart_play:
                page_params = self._build_next_page_params(
                    no_paging=no_paging,
                    has_next_page=has_next_page,
                    list_items=list_items,
                    page_limit=self.page_limit,
                    next_action=next_action,
                    next_args=next_args,
                    catalog_hint=catalog_hint,
                    list_id=list_id,
                )
                if page_params:
                    g.add_directory_item(
                        g.get_language_string(33078, addon=False),
                        menu_item=g.create_icon_dict("next", base_path=g.ICONS_PATH),
                        **page_params,
                    )
                    self._schedule_next_page_prefetch(page_params)
                content_type = self._mixed_media_content_type(catalog_hint, movie_count, show_count)
                use_cache = menu_cache if menu_cache is not None else g.kodi_menu_caching_enabled()
                g.close_directory(content_type, sort=sort, cache=use_cache)
                g.log(
                    f"list_paint_ms={(time.time() - paint_start) * 1000:.0f} items={len(list_items)}",
                    "debug",
                )
                from resources.lib.meta.menu_paint_profile import log_paint_timing

                action = (g.REQUEST_PARAMS or {}).get("action")
                log_paint_timing(action, (time.time() - paint_start) * 1000, item_count=len(list_items))
                for refs, media_type in enrichment_batches:
                    self._schedule_background_enrichment(
                        refs,
                        media_type,
                        reason=enrichment_reason,
                        catalog=catalog_hint,
                    )

    @staticmethod
    def _discover_catalog_hint(default_hint: str, params: dict) -> str:
        """Resolve catalog_hint without duplicating the kwarg on _mixed_media_from_sync_dicts."""
        return params.pop("catalog_hint", default_hint)

    def show_discover_builder(self, media_list, **params):
        """Discover TV browse lists — mill TMDB/TVDB metadata like anime discover."""
        catalog_hint = self._discover_catalog_hint("tv", params)
        self._mixed_media_from_sync_dicts(
            media_list,
            catalog_hint=catalog_hint,
            **params,
        )

    def movie_discover_builder(self, media_list, **params):
        """Discover movie browse lists — mill TMDB/Fanart metadata like TV/anime discover."""
        catalog_hint = self._discover_catalog_hint("movie", params)
        self._mixed_media_from_sync_dicts(
            media_list,
            catalog_hint=catalog_hint,
            **params,
        )

    def anime_discover_builder(self, media_list, **params):
        """Anime browse/search lists — route anime_type=movie rows as playable movies."""
        catalog_hint = self._discover_catalog_hint("anime", params)
        self._mixed_media_from_sync_dicts(
            media_list,
            catalog_hint=catalog_hint,
            label2_for_item=lambda item: self._credit_label2(item),
            **params,
        )

    def actor_credits_builder(self, media_list, **params):
        """Build mixed filmography from Simkl-resolved sync dicts."""
        catalog_hint = params.pop("catalog", None)
        if catalog_hint not in ("movie", "tv", "anime"):
            from resources.lib.simkl.search_menus import _actor_catalog_hint

            catalog_hint = _actor_catalog_hint(g.REQUEST_PARAMS.get("action_args"))

        self._mixed_media_from_sync_dicts(
            media_list,
            label2_for_item=self._credit_label2,
            catalog_hint=catalog_hint,
            next_action="actorCredits",
            **params,
        )

    @staticmethod
    def _menu_action_args(item):
        """Return routing dict from MenuRow args — encode only when building the URL."""
        from resources.lib.simkl.ids import parse_stored_action_args

        raw = item.get("args")
        parsed = parse_stored_action_args(raw)
        return parsed if parsed is not None else raw

    @staticmethod
    def _use_parallel_list_build(count: int, content_type: str | None = None) -> bool:
        from resources.lib.modules.globals import g

        if content_type == g.CONTENT_EPISODE:
            return count > 0
        return count > 3

    def _action_args_for_item(self, processed, library_status=None):
        args = self._menu_action_args(processed)
        if not library_status or not isinstance(args, dict):
            return args
        stamped = dict(args)
        stamped["library_status"] = library_status
        return stamped

    def _build_directory_item_entry(self, item, action, params, library_status=None):
        if not item:
            return None
        return g.add_directory_item(
            item.get("name"),
            action=action,
            menu_item=item,
            action_args=self._action_args_for_item(item, library_status),
            **params,
        )

    def _build_directory_items(
        self,
        media_list,
        action,
        params,
        prepend_date=False,
        mixed_list=False,
        library_status=None,
        display_rating_priority=None,
    ):
        content_type = params.pop("content_type", None)
        processed = self._post_process_list(
            media_list,
            prepend_date,
            mixed_list,
            library_status=library_status,
            display_rating_priority=display_rating_priority,
        )
        if not self._use_parallel_list_build(len(processed), content_type=content_type):
            return [
                entry
                for item in processed
                if item is not None
                for entry in [self._build_directory_item_entry(item, action, params, library_status)]
                if entry is not None
            ]

        from threading import Thread

        from resources.lib.common.task_pool import TaskPool

        build_params = dict(params)
        slots: list = [None] * len(processed)
        work = [(idx, item) for idx, item in enumerate(processed) if item is not None]

        def build_at(_position, pair):
            idx, item = pair
            entry = self._build_directory_item_entry(
                item, action, build_params, library_status
            )
            if entry is not None:
                slots[idx] = entry

        threads = TaskPool().tasks_enumerate(build_at, work, Thread)
        for thread in threads:
            thread.join()
        return [entry for entry in slots if entry is not None]

    def _common_menu_builder(self, media_list, content_type, action="getSources", **params):
        if len(media_list) == 0:
            g.log("We received no titles to build a list", "warning")
            g.cancel_directory()
            return

        list_items = []
        smart_play = params.pop("smart_play", False)
        no_paging = params.pop("no_paging", False)
        sort = params.pop("sort", False)
        prepend_date = params.pop("prepend_date", False)
        mixed_list = params.pop("mixed_list", False)
        next_args = params.pop("next_args", None)
        next_action = params.pop("next_action", None)
        has_next_page = params.pop("has_next_page", False)
        library_status = params.pop("library_status", None)
        display_rating_priority = params.pop("display_rating_priority", None)
        menu_cache = params.pop("menu_cache", None)
        enrichment_refs = params.pop("enrichment_refs", None)
        enrichment_media_type = params.pop("enrichment_media_type", None)
        enrichment_reason = params.pop("enrichment_reason", "list_open")
        catalog_hint = params.pop("catalog_hint", None)

        params.pop("hide_unaired", None)
        params.pop("hide_watched", None)
        params.pop("hide_specials", None)
        params.pop("skip_mill", None)
        params.pop("skip_update", None)
        params.pop("ignore_cache", None)
        params.pop("action_args", None)

        import time

        paint_start = time.time()
        try:
            params["bulk_add"] = True
            params["content_type"] = content_type
            list_items = self._build_directory_items(
                media_list,
                action,
                params,
                prepend_date=prepend_date,
                mixed_list=mixed_list,
                library_status=library_status,
                display_rating_priority=display_rating_priority,
            )

            if smart_play:
                return list_items
            else:
                xbmcplugin.addDirectoryItems(g.PLUGIN_HANDLE, list_items, len(list_items))
        except Exception as e:
            g.log_stacktrace()
            if not smart_play:
                g.cancel_directory()
            raise e

        finally:
            if not smart_play:
                page_params = self._build_next_page_params(
                    no_paging=no_paging,
                    has_next_page=has_next_page,
                    list_items=list_items,
                    page_limit=self.page_limit,
                    next_action=next_action,
                    next_args=next_args,
                    catalog_hint=catalog_hint,
                )
                if page_params:
                    g.add_directory_item(
                        g.get_language_string(33078, addon=False),
                        menu_item=g.create_icon_dict("next", base_path=g.ICONS_PATH),
                        **page_params,
                    )
                    self._schedule_next_page_prefetch(page_params)
                use_cache = menu_cache if menu_cache is not None else g.kodi_menu_caching_enabled()
                g.close_directory(content_type, sort=sort, cache=use_cache)
                g.log(
                    f"list_paint_ms={(time.time() - paint_start) * 1000:.0f} items={len(list_items)}",
                    "debug",
                )
                from resources.lib.meta.menu_paint_profile import log_paint_timing

                action = (g.REQUEST_PARAMS or {}).get("action")
                log_paint_timing(action, (time.time() - paint_start) * 1000, item_count=len(list_items))
                self._schedule_background_enrichment(
                    enrichment_refs,
                    enrichment_media_type,
                    reason=enrichment_reason,
                    catalog=catalog_hint,
                )

    def is_aired(self, item):
        """
        Confirms supplied item has aired based on meta
        :param info: Meta of item
        :return: Bool, True if object has aired else False
        """
        air_date = item.get("air_date")
        if air_date is None and isinstance(item.get("info"), dict):
            air_date = item["info"].get("aired", item["info"].get("premiered"))

        if not air_date:
            return False

        if int(air_date[:4]) < 1970:
            return True

        return item_has_aired(air_date)

    def _post_process_list(
        self,
        item_list,
        prepend_date=False,
        mixed_list=False,
        library_status=None,
        display_rating_priority=None,
    ):
        return [
            self._post_process(
                item,
                prepend_date,
                mixed_list,
                library_status=library_status,
                display_rating_priority=display_rating_priority,
            )
            for item in item_list
        ]

    @staticmethod
    def _apply_completed_watched_display(item, library_status=None):
        info = item.get("info")
        if not isinstance(info, dict):
            return item

        completed = library_status == "completed" if library_status else info.get("simkl_status") == "completed"
        if not completed:
            return item

        mediatype = info.get("mediatype")
        if mediatype == "movie":
            item["play_count"] = 1
            info["playcount"] = 1
        elif mediatype == "tvshow":
            episode_count = item.get("episode_count") or info.get("episode_count") or info.get("total_episodes_count")
            watched_episodes = item.get("watched_episodes") or info.get("watched_episodes_count")
            try:
                episode_count = int(episode_count) if episode_count is not None else 0
            except (TypeError, ValueError):
                episode_count = 0
            try:
                watched_episodes = int(watched_episodes) if watched_episodes is not None else 0
            except (TypeError, ValueError):
                watched_episodes = 0

            if episode_count > 0:
                item["episode_count"] = episode_count
                item["watched_episodes"] = episode_count
            else:
                item["watched_episodes"] = max(watched_episodes, 1)
                item["episode_count"] = item["watched_episodes"]
            info["playcount"] = 1
        elif mediatype == "season":
            episode_count = item.get("episode_count") or info.get("episode_count")
            watched_episodes = item.get("watched_episodes") or info.get("watched_episodes_count")
            try:
                episode_count = int(episode_count) if episode_count is not None else 0
            except (TypeError, ValueError):
                episode_count = 0
            try:
                watched_episodes = int(watched_episodes) if watched_episodes is not None else 0
            except (TypeError, ValueError):
                watched_episodes = 0
            if episode_count > 0 and watched_episodes >= episode_count:
                item["episode_count"] = episode_count
                item["watched_episodes"] = episode_count
                info["playcount"] = 1

        item["info"] = info
        return item

    @staticmethod
    def _copy_menu_row(menu_row: dict) -> dict:
        """Shallow copy with a fresh info dict — cheaper than deepcopy for list paint."""
        copied = dict(menu_row)
        info = menu_row.get("info")
        if isinstance(info, dict):
            copied["info"] = dict(info)
        return copied

    def _post_process(
        self,
        item,
        prepend_date=False,
        mixed_list=False,
        library_status=None,
        display_rating_priority=None,
    ):
        if not item:
            return

        if self._skip_watched_widget_item(item):
            return None

        item = self._apply_completed_watched_display(item, library_status=library_status)

        info = item.get("info")
        if not isinstance(info, dict):
            g.log(f"Skipping list item without info dict: simkl_id={item.get('simkl_id')}", "warning")
            return None

        from resources.lib.simkl.field_map import sanitize_list_info

        info = sanitize_list_info(info, catalog=info.get("catalog"))
        item["info"] = info

        from resources.lib.simkl.field_map import (
            apply_display_rating,
            default_display_rating_priority,
            promote_named_ratings,
        )

        promote_named_ratings(info)
        apply_display_rating(
            info,
            display_rating_priority or default_display_rating_priority(info.get("catalog")),
        )

        if self.show_original_title and info.get("originaltitle"):
            name = info.get("originaltitle")
        else:
            name = info.get("title") or item.get("name")

        if not name and info.get("mediatype") == "season":
            from resources.lib.simkl.field_map import ensure_season_title

            ensure_season_title(info)
            name = info.get("title")

        if not name and info.get("mediatype") == "episode":
            from resources.lib.simkl.field_map import ensure_episode_title

            ensure_episode_title(info)
            name = info.get("title") or item.get("name")

        if info.get("mediatype") == "season":
            g.log(
                f"[season trace] season menu item show={info.get('simkl_show_id')} "
                f"info.season={info.get('season')} label={name!r}",
                "debug",
            )

        if not name:
            g.log(f"Item has no title: {item}", "error")
            return None

        if info.get("mediatype") != "list" and not self.hide_unaired and not self.is_aired(item):
            name = g.color_string(tools.italic_string(name), "red")

        if info.get("mediatype") == "episode":
            if mixed_list:
                style = int(self.title_appends_mixed or 0)
                if style > 0:
                    name = self._handle_episode_title_appending(name, item, style)
            elif int(self.title_appends_general or 0) > 0:
                name = self._handle_episode_title_appending(
                    name, item, int(self.title_appends_general or 0)
                )

        if info.get("mediatype") == "list" and self.list_title_appends == 1:
            name += f" - {g.color_string(info['username'])}"

        if info.get("mediatype") != "list" and prepend_date:
            if release_date := g.utc_to_local(item.get("air_date", info.get("aired", None))):
                release_day = tools.parse_datetime(release_date, date_only=False).strftime(
                    f"%a %d %b @ {g.KODI_TIME_NO_SECONDS_FORMAT}"
                )
                name = f"[{release_day}] {name}"
        item.update({"name": name})
        info["title"] = name
        item["info"] = info

        return item

    @staticmethod
    def _handle_episode_title_appending(name, item, title_append_style):
        from resources.lib.simkl.ids import episode_num_from_info

        style = int(title_append_style or 0)
        if style == 0:
            return name

        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        season = info.get("season")
        episode = episode_num_from_info(info)
        show_title = info.get("tvshowtitle")
        if not show_title:
            tvshow = info.get("tvshow")
            if isinstance(tvshow, dict):
                show_title = tvshow.get("title")
        if style == 1 and season is not None and episode is not None:
            name = f"{str(season).zfill(2)}x{str(episode).zfill(2)} {name}"

        elif style == 2 and show_title:
            name = f"{g.color_string(show_title)}: {name}"

        elif style == 3 and show_title and season is not None and episode is not None:
            name = (
                f"{g.color_string(show_title)}: "
                f"{str(season).zfill(2)}x{str(episode).zfill(2)} {name}"
            )

        return name
