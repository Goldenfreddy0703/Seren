"""Simkl playback sessions (Continue Watching / playback progress manager).

Read:  GET /sync/playback/movies, GET /sync/playback/episodes
Write: POST /scrobble/pause, POST /scrobble/stop (progress < 80%)

Each hub menu (My Movies / My Shows / My Anime) shows paused sessions for that
catalog, split using the PlaybackSession keys Simkl returns (movie / show / anime).
"""
from __future__ import annotations

from typing import Any

from resources.lib.indexers import simkl_auth_guard
from resources.lib.modules.globals import g

CATALOG_MOVIE = "movie"
CATALOG_TV = "tv"
CATALOG_ANIME = "anime"


def is_valid_playback_progress(progress: Any) -> bool:
    """Simkl stores paused playbacks with 0 < progress < 100."""
    if progress is None:
        return False
    try:
        value = float(progress)
    except (TypeError, ValueError):
        return False
    return 0 < value < 100


def catalog_from_session(session: dict[str, Any]) -> str | None:
    """Map a PlaybackSession to a Prism catalog using Simkl response keys."""
    if session.get("movie"):
        return CATALOG_MOVIE
    if session.get("anime"):
        return CATALOG_ANIME
    if session.get("show"):
        show = session.get("show") or {}
        ids = show.get("ids") or {}
        if ids.get("mal") or ids.get("anidb") or ids.get("anilist"):
            return CATALOG_ANIME
        return CATALOG_TV
    return None


def show_from_session(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("anime") or session.get("show") or {}


def unwrap_playback_list(payload: Any, *, movies: bool) -> list[dict[str, Any]]:
    """Normalize GET /sync/playback response to a list of PlaybackSession dicts."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    if movies:
        rows = payload.get("movies") or []
        return rows if isinstance(rows, list) else []
    merged: list[dict[str, Any]] = []
    for key in ("episodes", "anime", "shows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            merged.extend(rows)
    return merged


def sync_movie_playbacks(db) -> int:
    """Pull GET /sync/playback/movies into the local bookmarks table."""
    from resources.lib.simkl.library import simkl_entry_to_sync_dict

    raw = db.simkl_api.get_playback("movies")
    if raw is None:
        g.log("Simkl playback fetch failed for movies, keeping existing bookmarks", "warning")
        return 0

    payload = unwrap_playback_list(raw, movies=True)
    rows: list[tuple] = []
    movies_to_insert: list[dict] = []

    for session in payload:
        movie = session.get("movie") or session
        if not isinstance(movie, dict):
            continue
        progress = session.get("progress")
        if not is_valid_playback_progress(progress):
            continue
        simkl_id = (movie.get("ids") or {}).get("simkl")
        if not simkl_id:
            continue
        simkl_id = int(simkl_id)
        duration = db._movie_duration_seconds(simkl_id, movie)
        rows.append(
            (
                simkl_id,
                int(float(progress) / 100 * duration),
                float(progress),
                "movie",
                session.get("paused_at") or session.get("updated_at"),
                CATALOG_MOVIE,
            )
        )
        normalized = simkl_entry_to_sync_dict({"movie": movie}, CATALOG_MOVIE)
        if normalized:
            movies_to_insert.append(normalized)

    db.replace_playback_bookmarks("movie", rows)
    if movies_to_insert:
        db.insert_simkl_movies(movies_to_insert)
    g.log(f"Simkl playback sync: {len(rows)} movie session(s)", "debug")
    return len(rows)


def sync_episode_playbacks(db) -> int:
    """Pull GET /sync/playback/episodes into the local bookmarks table."""
    from resources.lib.simkl.library import simkl_entry_to_sync_dict

    raw = db.simkl_api.get_playback("episodes")
    if raw is None:
        g.log("Simkl playback fetch failed for episodes, keeping existing bookmarks", "warning")
        return 0

    payload = unwrap_playback_list(raw, movies=False)
    rows: list[tuple] = []
    shows_by_id: dict[int, tuple[dict, str]] = {}
    pending: list[tuple[dict, dict, str, float, str | None]] = []

    for session in payload:
        progress = session.get("progress")
        if not is_valid_playback_progress(progress):
            continue
        catalog = catalog_from_session(session)
        if catalog not in (CATALOG_TV, CATALOG_ANIME):
            continue
        show = show_from_session(session)
        episode = session.get("episode") or {}
        if not show or not episode:
            continue

        show_id = (show.get("ids") or {}).get("simkl")
        if show_id:
            shows_by_id[int(show_id)] = (show, catalog)

        pending.append(
            (
                show,
                episode,
                catalog,
                float(progress),
                session.get("paused_at") or session.get("updated_at"),
            )
        )

    # Parent shows must exist before season/episode rows (FK).
    for _show_id, (show, catalog) in shows_by_id.items():
        media_key = "anime" if catalog == CATALOG_ANIME else "show"
        normalized = simkl_entry_to_sync_dict({media_key: show}, catalog)
        if normalized:
            normalized["simkl_object"]["info"]["catalog"] = catalog
            db.insert_simkl_shows([normalized])

    for show, episode, catalog, progress, paused_at in pending:
        ep_num = episode.get("number")
        if ep_num is None:
            ep_num = episode.get("episode")

        db.ensure_playback_episode_row(show, episode, catalog)
        simkl_id = db._resolve_episode_simkl_id(show, episode)
        if not simkl_id:
            g.log(
                f"Simkl playback sync: could not resolve episode for "
                f"{show.get('title')} S{episode.get('season')}E{ep_num}",
                "debug",
            )
            continue

        duration = db._episode_duration_seconds(simkl_id, episode, show)
        rows.append(
            (
                int(simkl_id),
                int(progress / 100 * duration),
                progress,
                "episode",
                paused_at,
                catalog,
            )
        )

    db.replace_playback_bookmarks("episode", rows)
    g.log(f"Simkl playback sync: {len(rows)} episode session(s)", "debug")
    return len(rows)


def _page_slice(page: int, page_limit: int) -> tuple[int, int]:
    page_start = (page - 1) * page_limit
    return page_start, page_start + page_limit


def _refresh_playback_bookmarks_if_empty(db, catalog: str) -> None:
    """Pull GET /sync/playback when local bookmarks were cleared (e.g. DB rebuild)."""
    if catalog == CATALOG_MOVIE:
        if db.fetchone("SELECT 1 FROM bookmarks WHERE type='movie' LIMIT 1"):
            return
        sync_movie_playbacks(db)
        return
    if db.fetchone("SELECT 1 FROM bookmarks WHERE type='episode' LIMIT 1"):
        return
    sync_episode_playbacks(db)


def list_continue_watching(catalog: str, page: int | None = None, *, resync_if_empty: bool = False) -> list[dict]:
    """Return bookmark rows for a catalog (already excludes hidden items)."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.database.simkl_sync.hidden import SimklSyncDatabase as HiddenDatabase

    page = page or g.PAGE
    page_limit = g.get_int_setting("item.limit")
    page_start, page_end = _page_slice(page, page_limit)
    hidden_mediatype = "movies" if catalog == CATALOG_MOVIE else "tvshow"
    hidden = HiddenDatabase().get_hidden_simkl_ids("progress_watched", hidden_mediatype)
    db = get_sync_database()
    _refresh_playback_bookmarks_if_empty(db, catalog)
    items = db.get_continue_watching(catalog, hidden)
    if resync_if_empty and not items and catalog != CATALOG_MOVIE:
        sync_episode_playbacks(db)
        items = db.get_continue_watching(catalog, hidden)
    return items[page_start:page_end]


@simkl_auth_guard
def render_continue_watching_menu(catalog: str) -> None:
    """Render Continue Watching for movie, tv, or anime."""
    from resources.lib.database.session import get_sync_database
    from resources.lib.meta.list_paint import render_catalog_episodes, render_catalog_rows
    from resources.lib.meta.menu_paint_profile import MenuPaintProfile, profile_list_kwargs
    from resources.lib.modules.list_builder import ListBuilder
    from resources.lib.simkl.episode_catalog_sync import schedule_lazy_episode_warm

    page = g.PAGE

    items = list_continue_watching(catalog, page=page, resync_if_empty=True)
    for item in items:
        if isinstance(item, dict):
            item.setdefault("catalog", catalog)
            item["force_resume_indicator"] = True

    if catalog != CATALOG_MOVIE and not items:
        db = get_sync_database()
        bookmark_rows = db.fetchall(
            """
            SELECT DISTINCT e.simkl_show_id AS simkl_id
            FROM bookmarks AS b
                     INNER JOIN episodes AS e ON e.simkl_id = b.simkl_id
            WHERE b.type = 'episode'
            """
        )
        show_ids = {int(row["simkl_id"]) for row in bookmark_rows if row.get("simkl_id") is not None}
        if show_ids:
            schedule_lazy_episode_warm(db, show_ids)

    builder = ListBuilder()
    profile = MenuPaintProfile.LIBRARY_EPISODES if catalog != CATALOG_MOVIE else MenuPaintProfile.LIBRARY

    if catalog == CATALOG_MOVIE:
        paint_kwargs = profile_list_kwargs(profile, seeded=True)
        render_catalog_rows(
            catalog,
            items,
            builder,
            list_kwargs=paint_kwargs,
            **paint_kwargs,
        )
        return

    # Match Next Up: warm bookmark parent shows, then paint-first episode list (no paging).
    db = get_sync_database()
    show_ids = {
        int(item["simkl_show_id"])
        for item in items
        if isinstance(item, dict) and item.get("simkl_show_id") is not None
    }
    if show_ids:
        schedule_lazy_episode_warm(db, show_ids)
        from resources.lib.database.simkl_sync.milling import refresh_listed_episodes_from_simkl

        refresh_listed_episodes_from_simkl(db, items)

    render_catalog_episodes(
        catalog,
        items,
        builder,
        **profile_list_kwargs(
            MenuPaintProfile.LIBRARY_EPISODES,
            no_paging=True,
            seeded=True,
            overlay_parent_shows=True,
        ),
    )


def prefetch_continue_watching(catalog: str, page_params: dict[str, Any]) -> None:
    """Warm metadata for the next Continue Watching page."""
    from resources.lib.simkl.ids import show_id_from_item

    page = int(page_params.get("page") or 1)
    items = list_continue_watching(catalog, page=page)
    if catalog == CATALOG_MOVIE:
        simkl_ids = sorted({int(item["simkl_id"]) for item in items if item.get("simkl_id") is not None})
        if not simkl_ids:
            return
        from resources.lib.discover.catalog_store import sync_items_for_refs
        from resources.lib.simkl.enrich import enrich_page_for_paint
        from resources.lib.modules.page_prefetch import _schedule_prefetch_simkl_ids

        refs = [{"simkl_id": sid, "catalog": CATALOG_MOVIE} for sid in simkl_ids]
        page_sync = sync_items_for_refs(CATALOG_MOVIE, refs)
        if page_sync:
            enrich_page_for_paint(CATALOG_MOVIE, page_sync)
        _schedule_prefetch_simkl_ids(simkl_ids, "movie", catalog=CATALOG_MOVIE, reason="prefetch_ondeck")
        return

    show_ids = sorted({int(show_id_from_item(item)) for item in items if show_id_from_item(item) is not None})
    if not show_ids:
        return
    from resources.lib.modules.page_prefetch import _schedule_prefetch_simkl_ids

    _schedule_prefetch_simkl_ids(show_ids, "tvshow", catalog=catalog, reason="prefetch_ondeck")
