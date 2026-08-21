"""Shared helpers for Simkl title search and TMDB actor search menus."""

from __future__ import annotations



from typing import Any



from resources.lib.common import tools

from resources.lib.modules.globals import g





def normalize_search_query(action_args: Any) -> str | None:

    if action_args is None:

        return None

    if isinstance(action_args, dict):

        value = action_args.get("query") or action_args.get("q")

        return str(value).strip() if value else None

    if isinstance(action_args, (list, tuple)) and action_args:

        return normalize_search_query(action_args[0])

    text = str(action_args).strip()

    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):

        text = text[1:-1].strip()

    return text or None





def search_result_score(item: dict) -> float:

    try:

        info = (item.get("simkl_object") or {}).get("info") or {}

        return float(info.get("score") or info.get("rank") or 1.0)

    except (TypeError, ValueError):

        return 1.0





def filter_search_results(items: list[dict]) -> list[dict]:

    return [item for item in items if search_result_score(item) > 0]





def persist_search_pagination(action_args: Any) -> None:

    """Keep query / actor args on REQUEST_PARAMS so list paging works."""

    if isinstance(action_args, dict):
        g.REQUEST_PARAMS["action_args"] = action_args
    elif action_args:
        g.REQUEST_PARAMS["action_args"] = {"query": str(action_args).strip()}


SEARCH_RESULTS_ACTIONS = {
    "movie": "moviesSearchResults",
    "tv": "showsSearchResults",
    "anime": "animeSearchResults",
}

_SEARCH_API = {
    "movie": ("search/movie", "movies"),
    "tv": ("search/show", "shows"),
    "anime": ("search/anime", "shows"),
}


def render_search_results_list(catalog: str, query: str, page_limit: int, list_builder) -> None:
    """Render a paginated Simkl title search with hybrid page-1 enrich + prefetch for next page."""
    from resources.lib.meta.list_paint import render_catalog_discover_refs
    from resources.lib.meta.list_pipeline import get_list_store, make_list_id
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.simkl.media_ref import persist_search_results
    from resources.lib.simkl.search import search_page

    url, media_type = _SEARCH_API[catalog]
    list_id = make_list_id("search", catalog, query)
    page = g.PAGE
    store = get_list_store("search")

    def loader():
        return search_page(url, media_type, page, page_limit, query)

    media_list = store.load_page_items(catalog, list_id, page, loader)
    filtered = filter_search_results(media_list)
    if not filtered:
        notify_empty_search(30766)
        return

    refs = persist_search_results(catalog, filtered)
    has_next = len(filtered) >= page_limit
    list_kwargs = profile_list_kwargs(
        MenuPaintProfile.SEARCH,
        hide_unaired=False,
        hide_watched=False,
        has_next_page=has_next,
        next_action=SEARCH_RESULTS_ACTIONS[catalog],
        next_args={"query": query},
    )

    render_catalog_discover_refs(
        catalog,
        refs,
        list_builder,
        list_kwargs=list_kwargs,
        prefer_catalog_payload=True,
        payload_rows=filtered,
    )





def notify_empty_search(string_id: int = 30766) -> None:

    g.notification(g.ADDON_NAME, g.get_language_string(string_id))

    g.cancel_directory()





def normalize_actor_args(action_args: Any) -> dict[str, Any]:
    from resources.lib.simkl.person_ref import normalize_person_ref

    return normalize_person_ref(action_args)


def _actor_catalog_hint(action_args: dict[str, Any] | None = None) -> str | None:
    from resources.lib.simkl.person_ref import actor_catalog_hint

    return actor_catalog_hint(action_args)


def _actor_pagination_catalog() -> dict[str, str]:
    catalog = _actor_catalog_hint()
    return {"catalog": catalog} if catalog else {}


def render_person_picker(people: list[dict], query: str) -> None:
    from resources.lib.simkl.person_ref import render_person_picker as _render

    _render(people, query)


def render_search_history(

    history_type: str,

    *,

    new_search_action: str,

    new_search_label_id: int,

    new_search_description_id: int,

    results_action: str,

    clear_mediatype: str,

) -> None:

    from resources.lib.database.searchHistory import SearchHistory



    history = SearchHistory().get_search_history(history_type)

    g.add_directory_item(

        g.get_language_string(new_search_label_id),

        action=new_search_action,

        description=g.get_language_string(new_search_description_id),

        menu_item=g.create_icon_dict("search", g.ICONS_PATH),

    )

    g.add_directory_item(

        g.get_language_string(30180),

        action="clearSearchHistory",

        mediatype=clear_mediatype,

        is_folder=False,

        description=g.get_language_string(30381),

        menu_item=g.create_icon_dict("clear_search", g.ICONS_PATH),

    )

    for term in history:

        remove_path = g.create_url(

            g.BASE_URL,

            {"action": "removeSearchHistory", "mediatype": clear_mediatype, "endpoint": term},

        )

        g.add_directory_item(

            term,

            action=results_action,

            action_args=tools.construct_action_args(term),

            cm=[(g.get_language_string(30565), f"RunPlugin({remove_path})")],

        )

    g.close_directory(g.CONTENT_MENU)


