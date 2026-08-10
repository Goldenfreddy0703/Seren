"""Render discover lists into Kodi directories."""
from __future__ import annotations

import json

import xbmcgui

from resources.lib.discover.definitions import Catalog, DiscoverList, get_list
from resources.lib.discover.normalize import runtime_minutes
from resources.lib.discover.cdn_store import rows_for_catalog
from resources.lib.discover.query_engine import query_all_rows
from resources.lib.indexers.simkl_cdn import SimklCDN
from resources.lib.modules.globals import g
from resources.lib.simkl.media_ref import normalize_discover_db_rows, normalize_simkl_items
from resources.lib.simkl.menu_helpers import list_filter_kwargs, paginate_refs_for_page


# Discover lists honor general-tab hide filters; skip_mill avoids sync milling on browse refs.


def discover_list_kwargs(**overrides) -> dict:
    """Shared list-builder kwargs for all fast browse menus (discover, search, library, etc.)."""
    from resources.lib.meta.menu_paint_profile import (
        MenuPaintProfile,
        current_action_profile_kwargs,
        profile_list_kwargs,
    )

    action = (g.REQUEST_PARAMS or {}).get("action")
    if action:
        return current_action_profile_kwargs(**overrides)
    return profile_list_kwargs(MenuPaintProfile.BROWSE, **overrides)

# DB lists that need extra Python ranking after query_engine pool sort.
_POST_FILTER_QUERIES = frozenset(
    {"top_simkl", "top_imdb", "top_mal", "hidden_gems", "completed", "quick_watch"}
)

# Icon filenames use the "shows" prefix for the tv catalog.
_ICON_PREFIX = {"movie": "movies", "tv": "shows", "anime": "anime"}

_CALENDAR_MENU = {
    "movie": ("movieAiringCalendar", "Weekly Movie Calendar", "movies_calendar"),
    "tv": ("tvAiringCalendar", "Weekly Show Calendar", "shows_calendar"),
    "anime": ("animeAiringCalendar", "Weekly Anime Calendar", "anime_calendar"),
}

_GENRE_MENU = {
    "movie": "movieGenres",
    "tv": "tvGenres",
    "anime": "animeGenres",
}

def _discover_genre_menu_icon(catalog: Catalog):
    from resources.lib.simkl import browse

    return browse.discover_genre_menu_icon(catalog)

# Map a discover list's trailing token to an icon suffix shared by all three catalogs.
_LIST_ICON_KIND = {
    "today": "trending",
    "week": "trending",
    "month": "trending",
    "popular": "popular",
    "most_watched": "watched",
    "anticipated": "anticipated",
    "top_simkl": "simkl",
    "top_imdb": "simkl",
    "top_mal": "simkl",
    "top_mdblist": "simkl",
    "hidden_gems": "recommended",
    "awards": "recommended",
    "completed": "collected",
    "ended": "collected",
    "binge": "progress",
    "new_year": "new",
    "new": "new",
    "quick_watch": "recent",
    "short": "recent",
    "dvd": "recent",
    "low_drop": "played",
    "ongoing": "calendar",
    "tba": "calendar",
}


def _discover_list_icon(item: DiscoverList) -> str:
    """Resolve a discover list to an icon slug, falling back to the base catalog icon."""
    prefix = _ICON_PREFIX.get(item.catalog, "movies")
    token = item.list_id.split("_", 1)[1] if "_" in item.list_id else item.list_id
    kind = _LIST_ICON_KIND.get(token)
    return f"{prefix}_{kind}" if kind else prefix


class DiscoverRenderer:
    def __init__(self):
        self.page_size = g.get_int_setting("item.limit", 25)
        self.cdn = SimklCDN()

    @staticmethod
    def show_discover_menu(catalog: Catalog):
        from resources.lib.discover.definitions import CATALOG_LISTS
        from resources.lib.meta.provider_settings import filter_discover_lists, provider_enabled

        search_actions = {
            "movie": ("moviesSearch", "moviesSearchHistory"),
            "tv": ("showsSearch", "showsSearchHistory"),
            "anime": ("animeSearch", "animeSearchHistory"),
        }
        search_labels = {
            "movie": 30025,
            "tv": 30026,
            "anime": 30769,
        }
        actor_action = "actorSearchHistory" if g.get_bool_setting("searchHistory") else "searchByActor"

        cal_action, cal_label, cal_icon = _CALENDAR_MENU[catalog]
        g.add_directory_item(
            cal_label,
            action=cal_action,
            catalog=catalog,
            description=cal_label,
            menu_item=g.create_icon_dict(cal_icon, g.ICONS_PATH),
        )

        for item in filter_discover_lists(CATALOG_LISTS[catalog]):
            g.add_directory_item(
                item.label,
                action="simklDiscoverList",
                catalog=catalog,
                list_id=item.list_id,
                description=item.label,
                menu_item=g.create_icon_dict(_discover_list_icon(item), g.ICONS_PATH),
            )

        g.add_directory_item(
            g.get_language_string(30042),
            action=_GENRE_MENU[catalog],
            catalog=catalog,
            description=g.get_language_string(30042),
            menu_item=_discover_genre_menu_icon(catalog),
        )

        if catalog in search_actions:
            if provider_enabled("tmdb"):
                g.add_directory_item(
                    g.get_language_string(30327),
                    action=actor_action,
                    description=g.get_language_string(30776),
                    menu_item=g.create_icon_dict(f"{_ICON_PREFIX.get(catalog, 'movies')}_actor", g.ICONS_PATH),
                )
            direct_action, history_action = search_actions[catalog]
            action = history_action if g.get_bool_setting("searchHistory") else direct_action
            g.add_directory_item(
                g.get_language_string(search_labels[catalog]),
                action=action,
                description=g.get_language_string(30770 if catalog == "anime" else (30371 if catalog == "movie" else 30372)),
                menu_item=g.create_icon_dict(f"{_ICON_PREFIX.get(catalog, 'movies')}_search", g.ICONS_PATH),
            )
        g.close_directory(g.CONTENT_MENU, cache=False)

    def _cdn_loader(self, discover_list: DiscoverList, catalog: Catalog):
        def loader() -> list[dict]:
            raw_items = self._fetch_cdn(discover_list, catalog)
            return normalize_simkl_items(raw_items, catalog)

        return loader

    def _db_loader(self, discover_list: DiscoverList, catalog: Catalog):
        def loader() -> list[dict]:
            return self._build_full_db_list(discover_list, catalog)

        return loader

    def _list_loader(self, discover_list: DiscoverList, catalog: Catalog):
        if discover_list.source == "cdn":
            return self._cdn_loader(discover_list, catalog)
        return self._db_loader(discover_list, catalog)

    @staticmethod
    def _discover_hide_filters() -> tuple[bool, bool]:
        hide_unaired = g.get_bool_setting("general.hideUnAired")
        hide_watched = g.get_bool_setting("general.hideWatched")
        if g.FROM_WIDGET:
            hide_watched = True
        return hide_unaired, hide_watched

    def _paint_discover_page(
        self,
        catalog: Catalog,
        page_refs: list[dict],
        page_sync: list[dict],
        *,
        schedule_enrichment: bool = False,
    ) -> list[dict]:
        from resources.lib.database.session import get_sync_database
        from resources.lib.meta.paint_cache import paint_catalog_page_rows
        from resources.lib.modules.page_prefetch import schedule_refs_enrichment

        hide_unaired, hide_watched = self._discover_hide_filters()
        from resources.lib.meta.menu_paint_profile import MenuPaintProfile

        painted = paint_catalog_page_rows(
            page_refs,
            page_sync,
            hide_unaired=hide_unaired,
            hide_watched=hide_watched,
            paint_profile=MenuPaintProfile.BROWSE.value,
        )
        if schedule_enrichment:
            db = get_sync_database()
            for refs, _media_type in db.consume_list_enrichment_batches():
                schedule_refs_enrichment(refs, catalog, reason="prefetch_discover")
        return painted

    def _collect_page_refs(
        self,
        discover_list: DiscoverList,
        catalog: Catalog,
        page: int,
    ) -> tuple[list[dict], bool, list[dict]]:
        """Return (page_refs, has_next, page_sync_items for paint)."""
        from resources.lib.discover.list_cache import (
            load_discover_list_refs,
            paginate_sync_items_for_refs,
        )

        loader = self._list_loader(discover_list, catalog)
        all_refs = load_discover_list_refs(catalog, discover_list.list_id, loader, materialize=False)
        page_refs = paginate_refs_for_page(all_refs, page, page_limit=self.page_size)
        page_sync = paginate_sync_items_for_refs(
            catalog,
            discover_list.list_id,
            page_refs,
            loader,
        )
        has_next = page * self.page_size < len(all_refs)
        return page_refs, has_next, page_sync

    def prefetch_page(self, catalog: Catalog, list_id: str, page: int) -> bool:
        """Warm display_meta for a discover page. Returns True when refs are stamp-ready."""
        from resources.lib.meta.paint_stamp import page_refs_display_stamped

        discover_list = get_list(catalog, list_id)
        if discover_list is None:
            return False
        from resources.lib.meta.provider_settings import discover_list_visible

        if not discover_list_visible(discover_list):
            return False

        page_refs, _has_next, page_sync = self._collect_page_refs(discover_list, catalog, int(page))
        if not page_sync:
            return False
        if page_refs_display_stamped(page_refs):
            return True
        self._paint_discover_page(catalog, page_refs, page_sync, schedule_enrichment=True)
        return page_refs_display_stamped(page_refs)

    def render_list(self, catalog: Catalog, list_id: str):
        discover_list = get_list(catalog, list_id)
        if discover_list is None:
            xbmcgui.Dialog().ok(g.ADDON_NAME, f"Unknown discover list: {list_id}")
            g.cancel_directory()
            return

        from resources.lib.meta.provider_settings import discover_list_visible

        if not discover_list_visible(discover_list):
            xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30964))
            g.cancel_directory()
            return

        page_refs, has_next, page_sync = self._collect_page_refs(discover_list, catalog, g.PAGE)

        if not page_refs:
            empty_msg = (
                "No upcoming items found."
                if discover_list.cdn_path and discover_list.cdn_path.startswith("/calendar/")
                else "No items found."
            )
            xbmcgui.Dialog().ok(g.ADDON_NAME, empty_msg)
            g.cancel_directory()
            return

        movie_refs = [r for r in page_refs if r.get("catalog") == "movie"]
        show_refs = [r for r in page_refs if r.get("catalog") in ("tv", "anime")]
        # page_sync + insert_discover_page already seed catalog_items; avoid redundant force_meta upserts.
        if not page_sync:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()
            if movie_refs:
                db.ensure_catalog_refs_seeded(
                    movie_refs,
                    catalog if catalog == "movie" else "movie",
                    "movie",
                )
            if show_refs:
                db.ensure_catalog_refs_seeded(show_refs, catalog, "tvshow")

        from resources.lib.discover.sync_bridge import insert_discover_page
        from resources.lib.discover.catalog_store import catalog_refs_need_seed
        from resources.lib.modules.list_builder import ListBuilder
        from resources.lib.meta.list_paint import render_catalog_discover_refs
        from resources.lib.simkl.field_map import display_rating_priority_for_discover

        if page_sync and catalog_refs_need_seed(catalog, page_refs):
            insert_discover_page(catalog, page_sync, catalog_only=True)

        list_kwargs = discover_list_kwargs()
        list_kwargs["display_rating_priority"] = display_rating_priority_for_discover(
            catalog,
            discover_list.db_query if discover_list.source == "db" else None,
        )
        if has_next:
            list_kwargs["next_action"] = "simklDiscoverList"
            list_kwargs["has_next_page"] = True
            list_kwargs["list_id"] = list_id

        builder = ListBuilder()
        render_catalog_discover_refs(
            catalog,
            page_refs,
            builder,
            list_kwargs=list_kwargs,
            enrichment_reason="discover",
            payload_rows=page_sync,
        )

    def _build_full_db_list(self, discover_list: DiscoverList, catalog: Catalog) -> list[dict]:
        """Materialize a full db-source discover list (filter/sort once, cache as payload)."""
        query_name = discover_list.db_query or ""
        if not query_name:
            return []

        catalog_rows = rows_for_catalog(catalog)
        if not catalog_rows:
            return []

        if query_name in _POST_FILTER_QUERIES:
            rows = query_all_rows(catalog_rows, query_name, catalog=catalog)
            rows = self._post_filter(query_name, rows, catalog)
        else:
            rows = query_all_rows(catalog_rows, query_name, catalog=catalog)

        return normalize_discover_db_rows(rows, catalog)

    def _fetch_cdn(self, discover_list: DiscoverList, catalog: Catalog) -> list:
        if not discover_list.cdn_path:
            return []
        data = self.cdn.fetch_json(discover_list.cdn_path)
        if discover_list.cdn_path.startswith("/calendar/"):
            from resources.lib.calendar.simkl_calendar import filter_coming_soon_rows, merge_v2_calendar_rows

            if isinstance(data, dict) and isinstance(data.get("calendar"), list):
                rows = merge_v2_calendar_rows(
                    data.get("calendar") or [],
                    data.get("metadata") or {},
                    catalog,
                )
            elif isinstance(data, list):
                rows = data
            else:
                rows = []
            return filter_coming_soon_rows(rows, catalog=catalog)
        if not isinstance(data, list):
            return []
        return data

    def _post_filter(self, query_name: str, rows: list[dict], catalog: str) -> list[dict]:
        if query_name == "top_simkl":
            return self._sort_ratings(rows, "simkl", min_votes=50)[:100]
        if query_name == "top_imdb":
            return self._sort_ratings(rows, "imdb", min_votes=1000)[:100]
        if query_name == "top_mal":
            return self._sort_ratings(rows, "mal", min_votes=500)[:100]
        if query_name == "hidden_gems":
            return self._hidden_gems(rows)[:100]
        if query_name == "completed":
            return self._sort_ratings(rows, "imdb" if catalog != "anime" else "mal", min_votes=100, min_rating=7.5)[:100]
        if query_name == "quick_watch":
            return self._quick_watch(rows)[:100]
        return rows

    @staticmethod
    def _parse_ratings(row: dict) -> dict:
        raw = row.get("ratings_json")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _sort_ratings(self, rows, source: str, min_votes: int, min_rating: float = 0.0, limit: int = 100):
        scored = []
        for row in rows:
            ratings = self._parse_ratings(row)
            src = ratings.get(source) or {}
            rating = src.get("rating")
            votes = src.get("votes") or 0
            if rating is None or votes < min_votes or rating < min_rating:
                continue
            scored.append((float(rating), int(votes), row))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [row for _, _, row in scored[:limit]]

    def _hidden_gems(self, rows: list[dict]) -> list[dict]:
        watched_values = [r.get("watched") or 0 for r in rows if self._row_quality_score(r) > 0]
        if not watched_values:
            return rows
        median = sorted(watched_values)[len(watched_values) // 2]
        gems = [r for r in rows if (r.get("watched") or 0) <= median]
        gems.sort(key=self._row_quality_score, reverse=True)
        return gems

    def _row_quality_score(self, row: dict) -> float:
        mdblist_score = row.get("mdblist_score")
        if mdblist_score:
            try:
                return float(mdblist_score)
            except (TypeError, ValueError):
                pass
        ratings = self._parse_ratings(row)
        simkl = ratings.get("simkl") or {}
        try:
            return float(simkl.get("rating") or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _quick_watch(rows: list[dict]) -> list[dict]:
        result = []
        for row in rows:
            runtime = row.get("runtime") or ""
            minutes = runtime_minutes(runtime)
            if minutes is not None and minutes <= 90:
                result.append(row)
        result.sort(key=lambda r: DiscoverRenderer._row_quality_score_static(r), reverse=True)
        return result

    @staticmethod
    def _row_quality_score_static(row: dict) -> float:
        mdblist_score = row.get("mdblist_score")
        if mdblist_score:
            try:
                return float(mdblist_score)
            except (TypeError, ValueError):
                pass
        raw = row.get("ratings_json")
        if not raw:
            return 0.0
        try:
            ratings = json.loads(raw)
        except json.JSONDecodeError:
            return 0.0
        simkl = ratings.get("simkl") or {}
        try:
            return float(simkl.get("rating") or 0)
        except (TypeError, ValueError):
            return 0.0
