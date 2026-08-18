"""Shared Simkl genre browse menu handlers."""
from __future__ import annotations

from urllib import parse

import xbmcgui

from resources.lib.discover.renderer import discover_list_kwargs
from resources.lib.modules.globals import g
from resources.lib.simkl import browse
from resources.lib.simkl.search_menus import notify_empty_search, persist_search_pagination

GENRE_GET_ACTIONS = {
    "movie": "movieGenresGet",
    "tv": "showGenresGet",
    "anime": "animeGenresGet",
}

MULTI_GENRE_ACTIONS = {
    "movie": ("movieGenresMulti", "movieGenresMultiGet"),
    "tv": ("showGenresMulti", "showGenresMultiGet"),
    "anime": ("animeGenresMulti", "animeGenresMultiGet"),
}


def _render_genre_browse_page(
    catalog: str,
    page,
    list_builder,
    *,
    next_action: str,
    next_args,
) -> None:
    """Paint genre browse via catalog payload + CDN hydrate + Simkl detail gap-fill."""
    from resources.lib.discover.renderer import discover_list_kwargs
    from resources.lib.discover.sync_bridge import simkl_refs
    from resources.lib.meta.list_paint import render_catalog_discover_refs

    list_kwargs = discover_list_kwargs(enrichment_reason="genre")
    list_kwargs["genre_paint"] = True
    list_kwargs["prefer_catalog_payload"] = True
    if page.has_next_page:
        list_kwargs["has_next_page"] = True
        list_kwargs["next_action"] = next_action
        list_kwargs["next_args"] = next_args

    render_catalog_discover_refs(
        catalog,
        simkl_refs(page.items),
        list_builder,
        list_kwargs=list_kwargs,
        payload_rows=page.items,
        prefer_catalog_payload=True,
    )


def show_genre_picker(catalog: str) -> None:
    genres = browse.get_simkl_genres(catalog)
    if not genres:
        g.cancel_directory()
        return

    if catalog in MULTI_GENRE_ACTIONS:
        multi_action, _ = MULTI_GENRE_ACTIONS[catalog]
        from resources.lib.meta.provider_settings import provider_enabled

        if catalog == "anime" or provider_enabled("tmdb"):
            g.add_directory_item(
                g.get_language_string(30940),
                action=multi_action,
                menu_item=browse.genre_icon_dict(catalog, "multi-select"),
            )

    for genre in genres:
        g.add_directory_item(
            genre["label"],
            action=GENRE_GET_ACTIONS[catalog],
            action_args=genre["slug"],
            catalog=catalog,
            menu_item=browse.genre_icon_dict(catalog, genre["slug"]),
        )
    g.close_directory(g.CONTENT_MENU)


def show_tmdb_genre_multiselect(catalog: str, page_limit: int, list_builder) -> None:
    from resources.lib.meta.provider_settings import notify_tmdb_required, provider_enabled

    if not provider_enabled("tmdb"):
        notify_tmdb_required()
        g.cancel_directory()
        return
    if catalog not in MULTI_GENRE_ACTIONS:
        g.cancel_directory()
        return

    genres = browse.get_tmdb_genres(catalog)
    if not genres:
        g.cancel_directory()
        return

    labels = [genre["name"] for genre in genres]
    selected = xbmcgui.Dialog().multiselect(g.get_language_string(30941), labels)
    if not selected:
        g.cancel_directory()
        return

    genre_ids = ",".join(str(genres[index]["id"]) for index in selected)
    render_multi_genre_list(catalog, {"genres": genre_ids}, page_limit, list_builder)


def show_tenrai_anime_multiselect(page_limit: int, list_builder) -> None:
    picker = browse.get_tenrai_anime_picker_items()
    genres = picker.get("genres") or []
    tags = picker.get("tags") or []
    if not genres and not tags:
        g.cancel_directory()
        return

    labels = [item["name"] for item in genres] + [item["name"] for item in tags]
    selected = xbmcgui.Dialog().multiselect(g.get_language_string(30941), labels)
    if not selected:
        g.cancel_directory()
        return

    genre_count = len(genres)
    mal_ids: list[str] = []
    for index in selected:
        if index < genre_count:
            mal_ids.append(str(genres[index]["mal_id"]))
        elif index - genre_count < len(tags):
            mal_ids.append(str(tags[index - genre_count]["mal_id"]))

    if not mal_ids:
        g.cancel_directory()
        return

    render_anime_multi_genre_list({"genres": ",".join(mal_ids)}, page_limit, list_builder)


def _parse_tmdb_multi_genre_action_args(action_args) -> tuple[str | None, int, int]:
    if isinstance(action_args, dict):
        value = action_args.get("genres")
        genre_ids = str(value).strip() if value else None
        try:
            page = max(1, int(action_args.get("tmdb_page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            offset = max(0, int(action_args.get("tmdb_offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        return genre_ids, page, offset
    text = str(action_args or "").strip()
    return (text or None), 1, 0


def _parse_tenrai_multi_genre_action_args(action_args) -> tuple[str | None, int, int]:
    if isinstance(action_args, dict):
        value = action_args.get("genres")
        genre_ids = str(value).strip() if value else None
        try:
            page = max(1, int(action_args.get("tenrai_page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            offset = max(0, int(action_args.get("tenrai_offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        return genre_ids, page, offset
    text = str(action_args or "").strip()
    return (text or None), 1, 0


def render_multi_genre_list(catalog: str, action_args, page_limit: int, list_builder) -> None:
    genre_ids, tmdb_page, tmdb_offset = _parse_tmdb_multi_genre_action_args(action_args)
    if not genre_ids:
        g.cancel_directory()
        return

    pagination_args = {"genres": genre_ids}
    if tmdb_page > 1:
        pagination_args["tmdb_page"] = tmdb_page
    if tmdb_offset > 0:
        pagination_args["tmdb_offset"] = tmdb_offset
    persist_search_pagination(pagination_args)

    page = browse.discover_by_tmdb_genres(
        catalog,
        genre_ids,
        page_limit,
        tmdb_page=tmdb_page,
        tmdb_offset=tmdb_offset,
    )
    if not page.items:
        notify_empty_search(30766)
        return

    _, get_action = MULTI_GENRE_ACTIONS[catalog]
    next_args: dict[str, str | int] = {"genres": genre_ids}
    if page.has_next_page:
        next_args["tmdb_page"] = page.next_tmdb_page
        if page.next_tmdb_offset:
            next_args["tmdb_offset"] = page.next_tmdb_offset

    _render_genre_browse_page(
        catalog,
        page,
        list_builder,
        next_action=get_action,
        next_args=next_args,
    )


def render_anime_multi_genre_list(action_args, page_limit: int, list_builder) -> None:
    genre_ids, tenrai_page, tenrai_offset = _parse_tenrai_multi_genre_action_args(action_args)
    if not genre_ids:
        g.cancel_directory()
        return

    pagination_args = {"genres": genre_ids}
    if tenrai_page > 1:
        pagination_args["tenrai_page"] = tenrai_page
    if tenrai_offset > 0:
        pagination_args["tenrai_offset"] = tenrai_offset
    persist_search_pagination(pagination_args)

    page = browse.discover_by_tenrai_genres(
        genre_ids,
        page_limit,
        tenrai_page=tenrai_page,
        row_offset=tenrai_offset,
    )
    if not page.items:
        notify_empty_search(30766)
        return

    _, get_action = MULTI_GENRE_ACTIONS["anime"]
    next_args: dict[str, str | int] = {"genres": genre_ids}
    if page.has_next_page:
        next_args["tenrai_page"] = page.next_tmdb_page
        if page.next_tmdb_offset:
            next_args["tenrai_offset"] = page.next_tmdb_offset

    _render_genre_browse_page(
        "anime",
        page,
        list_builder,
        next_action=get_action,
        next_args=next_args,
    )


def render_genre_list(catalog: str, args, page_limit: int, list_builder) -> None:
    slug = parse.unquote(str(args or "")).strip().lower()
    if not slug:
        g.cancel_directory()
        return

    page = browse.discover_by_genre_slug(catalog, slug, g.PAGE, page_limit)
    if not page.items:
        g.cancel_directory()
        return

    _render_genre_browse_page(
        catalog,
        page,
        list_builder,
        next_action=GENRE_GET_ACTIONS[catalog],
        next_args=slug,
    )
