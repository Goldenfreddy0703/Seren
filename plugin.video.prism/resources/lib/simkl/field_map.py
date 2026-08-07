"""Map Simkl API / CDN payloads onto Prism info dicts for Kodi UI and a4kScrapers."""
from __future__ import annotations

import html
from typing import Any

from resources.lib.discover.normalize import _int_or_none, _normalize_air_date, ensure_info_duration

# Discover CDN fields that must not be copied into Kodi list info (popularity metrics).
_DISCOVER_POPULARITY_INFO_KEYS = frozenset({"watched", "plan_to_watch"})


def sanitize_list_info(info: dict[str, Any] | None, *, catalog: str | None = None) -> dict[str, Any]:
    """Strip Simkl CDN popularity metrics and mis-typed status from Kodi list info."""
    if not isinstance(info, dict):
        return {}
    cleaned = dict(info)
    mediatype = (cleaned.get("mediatype") or "").lower()
    is_movie = mediatype == "movie" or catalog == "movie"

    watched = cleaned.get("watched")
    if watched is not None:
        try:
            if int(watched) > 1:
                cleaned.pop("watched", None)
        except (TypeError, ValueError):
            cleaned.pop("watched", None)

    status = str(cleaned.get("status") or "").strip().lower()
    if is_movie and status == "ended":
        cleaned.pop("status", None)

    return cleaned


def _unescape(value):
    """Decode HTML entities (e.g. &#039; -> ') in Simkl-provided text."""
    return html.unescape(value) if isinstance(value, str) else value


def resolve_anime_titles_from_source(source: dict[str, Any]) -> tuple[str | None, str | None]:
    """Map Simkl CDN/API anime fields to (english, romaji) storage slots.

    CDN trending/discover JSON uses ``title`` for the English name and ``title_romaji``
    for the Romaji name (``null`` when both are the same, e.g. One Piece).
    """
    if not source:
        return None, None

    base_title = _unescape(source.get("title"))
    english = _unescape(source.get("en_title") or source.get("title_en"))
    if not english and base_title:
        english = base_title

    romaji_raw = source.get("title_romaji")
    if romaji_raw is not None and str(romaji_raw).strip():
        romaji = _unescape(romaji_raw)
    elif base_title:
        romaji = base_title
    elif english:
        romaji = english
    else:
        romaji = None

    return english, romaji


def pick_anime_display_title(info: dict[str, Any], *, prefer_romaji: bool = False) -> str | None:
    """Choose the menu/calendar display title from stored anime title slots."""
    if not info:
        return None
    en_title = info.get("title_en") or info.get("title")
    romaji_title = info.get("title_romaji") or info.get("title")
    if not en_title and not romaji_title:
        return None
    chosen = (romaji_title or en_title) if prefer_romaji else (en_title or romaji_title)
    return _unescape(chosen) if chosen else None


def ensure_anime_title_slots(info: dict[str, Any]) -> bool:
    """Populate title_en/title_romaji locally. Null CDN title_romaji uses the English title."""
    if not info:
        return False
    english, romaji = resolve_anime_titles_from_source(info)
    if english and not info.get("title_en"):
        info["title_en"] = english
    if romaji and not info.get("title_romaji"):
        info["title_romaji"] = romaji
    if not info.get("title") and english:
        info["title"] = english
    english = info.get("title_en") or info.get("title")
    romaji = info.get("title_romaji") or info.get("title")
    return bool(english and romaji)


def _normalize_imdb_id(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("tt"):
        return text
    if text.isdigit():
        return f"tt{text.zfill(7)}" if len(text) < 7 else f"tt{text}"
    return text


def _youtube_trailer(trailer) -> str | None:
    if not trailer:
        return None
    from resources.lib.common import tools

    text = str(trailer).strip()
    if not text:
        return None
    if text.startswith("plugin://"):
        return text
    if "v=" in text:
        text = text.split("v=")[-1].split("&")[0]
    elif "youtu.be/" in text:
        text = text.rsplit("/", 1)[-1].split("?")[0]
    if len(text) <= 20:
        return tools.youtube_url.format(text)
    return None


def _collect_aliases(source: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("alternate_titles", "alternate_titles_en", "titles", "aka"):
        raw = source.get(key)
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str) and entry.strip():
                    aliases.append(entry.strip())
                elif isinstance(entry, dict):
                    title = entry.get("title") or entry.get("name")
                    if title:
                        aliases.append(str(title).strip())
        elif isinstance(raw, str) and raw.strip():
            aliases.append(raw.strip())
    return aliases


def _apply_external_ids(info: dict[str, Any], ids: dict[str, Any]) -> None:
    if not ids:
        return
    mapping = {
        "simkl": "simkl_id",
        "simkl_id": "simkl_id",
        "tmdb": "tmdb_id",
        "tvdb": "tvdb_id",
        "imdb": "imdb_id",
        "mal": "mal_id",
        "anidb": "anidb_id",
        "anilist": "anilist_id",
        "kitsu": "kitsu_id",
        "slug": "slug",
    }
    for src_key, dest_key in mapping.items():
        if ids.get(src_key) is not None and not info.get(dest_key):
            value = ids[src_key]
            if dest_key.endswith("_id") and dest_key != "imdb_id":
                info[dest_key] = _int_or_none(value)
            elif dest_key == "imdb_id":
                info[dest_key] = _normalize_imdb_id(value)
            else:
                info[dest_key] = value

    nested = info.setdefault("ids", {})
    if isinstance(nested, dict):
        for src_key in ("simkl", "simkl_id", "tmdb", "tvdb", "imdb", "mal", "slug"):
            if ids.get(src_key) is not None and nested.get(src_key) is None:
                nested[src_key] = ids[src_key]

    from resources.lib.simkl.ids import sync_flat_ids_from_ids, sync_ids_from_flat

    sync_ids_from_flat(info)
    sync_flat_ids_from_ids(info)


def _rating_block_values(block: dict[str, Any]) -> tuple[Any, Any] | tuple[None, None]:
    if not isinstance(block, dict):
        return None, None
    rating = block.get("rating")
    if rating is None:
        rating = block.get("score")
    if rating is None:
        return None, None
    votes = block.get("votes")
    if votes is None:
        votes = block.get("scored_by", 0)
    return rating, votes


def _set_named_rating(info: dict[str, Any], source_key: str, block: dict[str, Any]) -> None:
    rating, votes = _rating_block_values(block)
    if rating is None:
        return
    named_key = f"rating.{source_key}"
    existing = info.get(named_key)
    if isinstance(existing, dict) and existing.get("rating") is not None:
        return
    info[named_key] = {"rating": rating, "votes": votes or 0}


_RATING_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "simkl": ("simkl",),
    "imdb": ("imdb",),
    "mal": ("mal", "myanimelist"),
    "tmdb": ("tmdb",),
    "mdblist": ("mdblist",),
}

_TOP_RATED_QUERY_PRIMARY: dict[str, str] = {
    "top_simkl": "simkl",
    "top_imdb": "imdb",
    "top_mal": "mal",
    "top_mdblist": "mdblist",
}


def default_display_rating_priority(catalog: str | None) -> tuple[str, ...]:
    """Browse/default lists: Simkl first, then catalog-appropriate secondary sources."""
    if catalog == "anime":
        return ("simkl", "mal", "imdb", "tmdb", "mdblist")
    return ("simkl", "imdb", "mal", "tmdb", "mdblist")


def display_rating_priority_for_discover(catalog: str, db_query: str | None) -> tuple[str, ...]:
    """Top-rated discover lists show the rating source that list is sorted by."""
    primary = _TOP_RATED_QUERY_PRIMARY.get(db_query or "")
    if not primary:
        return default_display_rating_priority(catalog)
    rest = default_display_rating_priority(catalog)
    return (primary,) + tuple(source for source in rest if source != primary)


def promote_named_ratings(info: dict[str, Any]) -> None:
    """Populate rating.{source} keys from nested Simkl ratings without picking display rating."""
    if not info:
        return

    nested = info.get("ratings")
    if isinstance(nested, dict):
        for source_key, block in nested.items():
            if isinstance(block, dict):
                _set_named_rating(info, str(source_key), block)

    mdblist_score = info.get("mdblist_score")
    if mdblist_score is not None and not isinstance(info.get("rating.mdblist"), dict):
        info["rating.mdblist"] = {"rating": mdblist_score, "votes": 0}


def _resolve_rating_from_source(info: dict[str, Any], source: str) -> tuple[Any, Any] | tuple[None, None]:
    for key in _RATING_SOURCE_ALIASES.get(source, (source,)):
        block = info.get(f"rating.{key}")
        if isinstance(block, dict):
            rating, votes = _rating_block_values(block)
            if rating is not None:
                return rating, votes
        nested = info.get("ratings")
        if isinstance(nested, dict):
            block = nested.get(key)
            if isinstance(block, dict):
                rating, votes = _rating_block_values(block)
                if rating is not None:
                    return rating, votes
    return None, None


def apply_display_rating(info: dict[str, Any], priority: tuple[str, ...] | None = None) -> None:
    """Set Kodi's top-level rating/votes from the preferred source order."""
    if not info:
        return
    if priority is None:
        priority = default_display_rating_priority(info.get("catalog"))

    for source in priority:
        rating, votes = _resolve_rating_from_source(info, source)
        if rating is None:
            continue
        info["rating"] = rating
        info["votes"] = votes or 0
        return

    info.pop("rating", None)
    info.pop("votes", None)


def promote_ratings_for_display(
    info: dict[str, Any],
    priority: tuple[str, ...] | None = None,
) -> None:
    """Expose named rating keys and apply the display-rating priority."""
    promote_named_ratings(info)
    apply_display_rating(info, priority)


def _apply_ratings(info: dict[str, Any], ratings: dict[str, Any] | None) -> None:
    if not ratings or not isinstance(ratings, dict):
        return
    info.setdefault("ratings", ratings)
    for source_key in ("imdb", "simkl", "mal", "tmdb", "mdblist"):
        block = ratings.get(source_key)
        if isinstance(block, dict):
            _set_named_rating(info, source_key, block)


def enrich_info_from_simkl(
    info: dict[str, Any],
    source: dict[str, Any],
    *,
    catalog: str | None = None,
    mediatype: str | None = None,
) -> None:
    """Merge Simkl fields into an existing info dict (non-destructive where possible)."""
    if not info or not source:
        return

    mt = mediatype or info.get("mediatype")
    if catalog is None and mt == "movie":
        catalog = "movie"
    elif catalog is None and mt in ("tvshow", "episode", "season"):
        catalog = "tv"

    overview = source.get("overview") or source.get("description")
    if overview and not info.get("plot"):
        info["plot"] = overview

    title = _unescape(source.get("title"))
    if title and not info.get("title"):
        info["title"] = title
    if title and mt in ("movie", "episode") and not info.get("originaltitle"):
        info["originaltitle"] = title

    # Capture anime English / Romaji titles so the title-language preference can pick at render time.
    if catalog == "anime" or source.get("anime_type") or info.get("mal_id"):
        en_title, romaji_title = resolve_anime_titles_from_source(source)
        if en_title and not info.get("title_en"):
            info["title_en"] = en_title
        if romaji_title and not info.get("title_romaji"):
            info["title_romaji"] = romaji_title

    if source.get("year") is not None and info.get("year") is None:
        info["year"] = _int_or_none(source.get("year"))

    air = _normalize_air_date(
        source.get("release_date") or source.get("first_aired") or source.get("released") or source.get("date")
    )
    if air:
        info.setdefault("premiered", air)
        info.setdefault("aired", air)
        if info.get("year") is None:
            info["year"] = _int_or_none(str(air)[:4])

    country = source.get("country") or source.get("country_origin")
    if country:
        info.setdefault("country", country)
        info.setdefault("country_origin", str(country).upper())

    genres = source.get("genres")
    if genres:
        if not info.get("genres"):
            info["genres"] = genres if isinstance(genres, list) else [genres]
        if not info.get("genre"):
            g_list = info.get("genres") or genres
            info["genre"] = g_list if isinstance(g_list, list) else [g_list]

    if source.get("runtime") is not None and info.get("runtime") is None:
        info["runtime"] = source.get("runtime")

    if source.get("status") and not info.get("status"):
        info["status"] = source.get("status")

    if source.get("network") and not info.get("studio"):
        info["studio"] = source.get("network")

    if source.get("certification") and not info.get("mpaa"):
        info["mpaa"] = source.get("certification")

    trailer = _youtube_trailer(source.get("trailer"))
    if trailer and not info.get("trailer"):
        info["trailer"] = trailer

    _apply_external_ids(info, source.get("ids") or {})
    _apply_ratings(info, source.get("ratings"))

    for alias in _collect_aliases(source):
        aliases = list(info.get("aliases") or [])
        if alias not in aliases:
            aliases.append(alias)
        info["aliases"] = aliases

    if source.get("season_count") is not None and info.get("season_count") is None:
        info["season_count"] = _int_or_none(source.get("season_count"))

    episode_count = source.get("total_episodes")
    if episode_count is None:
        episode_count = source.get("episode_count")
    if episode_count is not None and info.get("episode_count") is None:
        info["episode_count"] = _int_or_none(episode_count)

    if catalog == "tv" and mt == "tvshow" and title and not info.get("tvshowtitle"):
        info["tvshowtitle"] = title

    if source.get("rank") is not None and info.get("score") is None:
        info["score"] = source.get("rank")


def tvdb_from_episode(episode: dict[str, Any]) -> tuple[int | None, int | None]:
    """Read TVDB season/episode from Simkl anime rows (nested or flat)."""
    tvdb = episode.get("tvdb")
    if isinstance(tvdb, dict):
        season = _int_or_none(tvdb.get("season"))
        number = _int_or_none(tvdb.get("episode"))
    else:
        season = _int_or_none(episode.get("tvdb_season"))
        number = _int_or_none(episode.get("tvdb_number"))
    return season, number


def anime_menu_season(episode: dict[str, Any]) -> int:
    """
    Season bucket for anime season menus.
    Specials -> 0. Prefer TVDB season (Kodi/scraper season) over Simkl's anime part/cour
    numbering, which can disagree (e.g. Classroom of the Elite part 4 vs TVDB season 1).
    Simkl season 0 on regular episodes -> 1.
    """
    if episode.get("type") == "special":
        return 0

    tvdb_season, tvdb_ep = tvdb_from_episode(episode)
    if tvdb_season is not None:
        menu_season = 1 if tvdb_season == 0 else tvdb_season
        simkl_top = _int_or_none(episode.get("season"))
        if simkl_top is not None and simkl_top not in (0, menu_season):
            from resources.lib.modules.globals import g

            g.log(
                "[season trace] anime_menu_season ep="
                f"{episode.get('episode')} simkl.season={simkl_top} tvdb.season={tvdb_season} -> menu={menu_season}",
                "debug",
            )
        return menu_season

    simkl_season = _int_or_none(episode.get("season"))
    if simkl_season is not None:
        return 1 if simkl_season == 0 else simkl_season

    return 1


def anime_menu_episode_number(episode: dict[str, Any], season_num: int, fallback: int | None) -> int | None:
    """Within-season episode index: TVDB episode for numbered seasons, Simkl otherwise."""
    if season_num == 0:
        return fallback
    _, tvdb_ep = tvdb_from_episode(episode)
    if tvdb_ep is not None:
        return tvdb_ep
    return fallback


def enrich_episode_from_simkl_api(info: dict[str, Any], episode: dict[str, Any]) -> None:
    """Map GET /tv/episodes/{id} row onto episode info."""
    enrich_info_from_simkl(info, episode, mediatype="episode")

    ep_num = episode.get("episode")
    if ep_num is None:
        ep_num = episode.get("number")
    if ep_num is not None:
        info["episode"] = int(ep_num)
        info["number"] = int(ep_num)

    season = episode.get("season")
    if season is not None:
        prior = info.get("season")
        info["season"] = int(season)
        if prior is not None and int(season) != int(prior):
            from resources.lib.modules.globals import g

            g.log(
                f"[season trace] enrich_episode_from_simkl_api ep={ep_num} "
                f"overwrote info.season {prior} -> {int(season)} (simkl top-level season)",
                "debug",
            )

    if episode.get("title"):
        info["title"] = episode.get("title")
        info["originaltitle"] = episode.get("title")

    if episode.get("img"):
        info["simkl_img"] = episode.get("img")

    tvdb_season, tvdb_episode = tvdb_from_episode(episode)
    if tvdb_season is not None:
        info["tvdb_season"] = tvdb_season
    if tvdb_episode is not None:
        info["tvdb_episode"] = tvdb_episode

    ep_ids = episode.get("ids") or {}
    _apply_external_ids(info, ep_ids)

    if episode.get("type") == "special":
        info["episode_type"] = "special"


def inherit_show_fields(episode_info: dict[str, Any], show_info: dict[str, Any]) -> None:
    """Copy show-level fields onto episode info for scrapers — never replace Simkl episode metadata."""
    if not episode_info or not show_info:
        return

    from resources.lib.simkl.ids import parent_show_simkl_id

    show_simkl_id = parent_show_simkl_id(show_info) or show_info.get("simkl_id")
    if show_simkl_id is not None:
        episode_info.setdefault("simkl_show_id", int(show_simkl_id))
        tvshow = episode_info.setdefault("tvshow", {})
        if isinstance(tvshow, dict):
            tvshow.setdefault("simkl_id", int(show_simkl_id))

    show_ids = show_info.get("ids") if isinstance(show_info.get("ids"), dict) else {}

    if show_info.get("title") and not episode_info.get("tvshowtitle"):
        episode_info["tvshowtitle"] = show_info["title"]
    if show_info.get("year") is not None:
        episode_info.setdefault("tvshow.year", show_info.get("year"))
    if show_info.get("runtime") is not None and not episode_info.get("runtime"):
        episode_info["runtime"] = show_info.get("runtime")
    if show_info.get("is_airing") is not None:
        episode_info.setdefault("is_airing", show_info.get("is_airing"))
    if show_info.get("season_count") is not None:
        episode_info.setdefault("season_count", show_info.get("season_count"))
    if show_info.get("episode_count") is not None:
        episode_info.setdefault("episode_count", show_info.get("episode_count"))
    if show_info.get("country_origin") and not episode_info.get("country_origin"):
        episode_info.setdefault("country_origin", show_info.get("country_origin"))
    if show_info.get("aliases") and not episode_info.get("aliases"):
        episode_info.setdefault("aliases", show_info.get("aliases"))
    for title_key in ("title_en", "title_romaji"):
        if show_info.get(title_key) and not episode_info.get(title_key):
            episode_info.setdefault(title_key, show_info[title_key])
    if show_info.get("studio") and not episode_info.get("studio"):
        episode_info.setdefault("studio", show_info.get("studio"))
    tmdb_show = show_ids.get("tmdb") or show_info.get("tmdb_id")
    if tmdb_show is not None:
        episode_info.setdefault("tmdb_show_id", tmdb_show)
    tvdb_show = show_ids.get("tvdb") or show_info.get("tvdb_id")
    if tvdb_show is not None:
        episode_info.setdefault("tvdb_show_id", tvdb_show)
    imdb_show = show_ids.get("imdb") or show_info.get("imdb_id")
    if imdb_show:
        episode_info.setdefault("tvshow.imdb_id", imdb_show)
    mal_show = show_ids.get("mal") or show_info.get("mal_id")
    if mal_show is not None:
        episode_info.setdefault("mal_show_id", mal_show)
    if show_info.get("catalog") and not episode_info.get("catalog"):
        episode_info["catalog"] = show_info["catalog"]


def attach_show_scraper_context(item: dict[str, Any], show_info: dict[str, Any] | None) -> None:
    """Legacy a4kScrapers / hoster fields derived from the parent show row."""
    if not item or not show_info:
        return
    info = item.get("info")
    if isinstance(info, dict):
        inherit_show_fields(info, show_info)
    if show_info.get("season_count") is not None:
        item.setdefault("season_count", show_info["season_count"])
    if show_info.get("episode_count") is not None:
        item.setdefault("episode_count", show_info["episode_count"])
    if show_info.get("is_airing") is not None:
        item.setdefault("is_airing", show_info["is_airing"])
    item["showInfo"] = {
        "ids": {
            "imdb": show_info.get("imdb_id"),
            "tvdb": show_info.get("tvdb_id"),
            "tmdb": show_info.get("tmdb_id"),
        }
    }


# Simkl owns season/episode metadata; external APIs may only gap-fill ids/ratings for art/scraper hooks.
SIMKL_CHILD_SUPPLEMENTAL_INFO_KEYS = (
    "tmdb_id",
    "tvdb_id",
    "imdb_id",
    "imdbnumber",
    "rating",
    "votes",
    "rating.tmdb",
    "rating.tvdb",
    "rating.imdb",
    "rating.trakt",
)


def merge_simkl_child_supplemental_info(target: dict[str, Any], source_info: dict[str, Any] | None) -> None:
    """Fill missing external ids / ratings only — never overwrite Simkl season/episode metadata."""
    if not target or not source_info:
        return
    for key in SIMKL_CHILD_SUPPLEMENTAL_INFO_KEYS:
        if source_info.get(key) is not None and target.get(key) is None:
            target[key] = source_info[key]
    source_ratings = source_info.get("ratings")
    if isinstance(source_ratings, dict):
        ratings = target.setdefault("ratings", {})
        if isinstance(ratings, dict):
            for source_key, block in source_ratings.items():
                if isinstance(block, dict) and source_key not in ratings:
                    ratings[source_key] = block


def merge_episode_supplemental_info(target: dict[str, Any], source_info: dict[str, Any] | None) -> None:
    merge_simkl_child_supplemental_info(target, source_info)


def merge_season_supplemental_info(target: dict[str, Any], source_info: dict[str, Any] | None) -> None:
    merge_simkl_child_supplemental_info(target, source_info)


def simkl_child_external_patch(external: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce external season/episode response to artwork only for caching."""
    if not external or not external.get("art"):
        return {}
    return {"art": external["art"]}


def episode_external_patch(external: dict[str, Any] | None) -> dict[str, Any]:
    return simkl_child_external_patch(external)


def season_external_patch(external: dict[str, Any] | None) -> dict[str, Any]:
    return simkl_child_external_patch(external)


# Backwards-compatible alias
EPISODE_SUPPLEMENTAL_INFO_KEYS = SIMKL_CHILD_SUPPLEMENTAL_INFO_KEYS


def ensure_episode_title(info: dict[str, Any]) -> None:
    """Default episode labels when Simkl stubs lack a per-episode name (playback sync)."""
    if not info or info.get("mediatype") != "episode" or info.get("title"):
        return
    title = _unescape(info.get("name"))
    if title:
        info["title"] = title
        info.setdefault("sorttitle", title)
        return
    ep_num = info.get("episode")
    if ep_num is None:
        ep_num = info.get("number")
    if ep_num is None:
        return
    from resources.lib.modules.globals import g

    title = g.get_language_string(30529).format(int(ep_num))
    info["title"] = title
    info.setdefault("sorttitle", title)


def ensure_season_title(info: dict[str, Any]) -> None:
    """Default season labels when Simkl has no per-season name (only Specials is named in API)."""
    if not info or info.get("mediatype") != "season" or info.get("title"):
        return
    season_num = info.get("season")
    if season_num is None:
        return
    if int(season_num) == 0:
        info["title"] = "Specials"
        info.setdefault("sorttitle", "Specials")
        return
    from resources.lib.modules.globals import g

    title = g.get_language_string(30528).format(int(season_num))
    info["title"] = title
    info.setdefault("sorttitle", title)


def finalize_playback_info(info: dict[str, Any]) -> None:
    """Last pass before Kodi playback / a4kScrapers — fill gaps Simkl already gave us."""
    if not info:
        return

    if info.get("genres") and not info.get("genre"):
        info["genre"] = info["genres"]
    if info.get("imdb_id") and not info.get("imdbnumber"):
        info["imdbnumber"] = info["imdb_id"]
    if info.get("title") and not info.get("originaltitle") and info.get("mediatype") in ("movie", "episode"):
        info["originaltitle"] = info["title"]
    if info.get("country") and not info.get("country_origin"):
        info["country_origin"] = str(info["country"]).upper()

    ensure_info_duration(info)
    ensure_episode_title(info)
    ensure_season_title(info)
    promote_ratings_for_display(info)
