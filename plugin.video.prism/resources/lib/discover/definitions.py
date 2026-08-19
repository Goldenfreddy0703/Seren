"""Discover menu registry from simkl database/discover_menu_structure.md."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Catalog = Literal["movie", "tv", "anime"]
Source = Literal["cdn", "db"]  # db = CDN-backed in-memory query lists


@dataclass(frozen=True)
class DiscoverList:
    list_id: str
    label: str
    catalog: Catalog
    source: Source
    cdn_path: str | None = None
    db_query: str | None = None


def _trending(catalog: Catalog, window: str, label: str) -> DiscoverList:
    slug = {"movie": "movies", "tv": "tv", "anime": "anime"}[catalog]
    return DiscoverList(
        list_id=f"{catalog}_{window}",
        label=label,
        catalog=catalog,
        source="cdn",
        cdn_path=f"/discover/trending/{slug}/{window}_500.json",
    )


MOVIE_LISTS: tuple[DiscoverList, ...] = (
    _trending("movie", "today", "Simkl Trending Movies — Last 24 Hours"),
    _trending("movie", "week", "Simkl Trending Movies — Last 7 Days"),
    _trending("movie", "month", "Simkl Trending Movies — Last 30 Days"),
    DiscoverList("movies_dvd", "Popular DVD Releases on Simkl", "movie", "cdn", "/discover/dvd/releases_500.json"),
    DiscoverList("movies_popular", "Popular Movies on Simkl", "movie", "db", db_query="popular"),
    DiscoverList("movies_most_watched", "Most Watched Movies on Simkl", "movie", "db", db_query="most_watched"),
    DiscoverList("movies_anticipated", "Most Anticipated Movies on Simkl", "movie", "db", db_query="anticipated"),
    DiscoverList("movies_top_simkl", "Top Rated Movies on Simkl", "movie", "db", db_query="top_simkl"),
    DiscoverList("movies_top_imdb", "Top Rated Movies on IMDB", "movie", "db", db_query="top_imdb"),
    DiscoverList("movies_new_year", "New This Year", "movie", "db", db_query="new_year"),
    DiscoverList("movies_new", "New Releases", "movie", "db", db_query="new_releases"),
    DiscoverList("movies_awards", "Award Winners", "movie", "db", db_query="awards"),
    DiscoverList("movies_quick_watch", "Quick Watch", "movie", "db", db_query="quick_watch"),
    DiscoverList("movies_low_drop", "Easy to Finish", "movie", "db", db_query="low_drop"),
    DiscoverList("movies_ongoing", "In Theaters", "movie", "db", db_query="ongoing_movies"),
    DiscoverList("movies_ended", "Ended Movies", "movie", "db", db_query="ended"),
    DiscoverList("movies_tba", "Coming Soon", "movie", "cdn", cdn_path="/calendar/v2/movie_release.json"),
)

TV_LISTS: tuple[DiscoverList, ...] = (
    _trending("tv", "today", "Simkl Trending TV — Last 24 Hours"),
    _trending("tv", "week", "Simkl Trending TV — Last 7 Days"),
    _trending("tv", "month", "Simkl Trending TV — Last 30 Days"),
    DiscoverList("tv_popular", "Popular TV on Simkl", "tv", "db", db_query="popular"),
    DiscoverList("tv_most_watched", "Most Watched TV on Simkl", "tv", "db", db_query="most_watched"),
    DiscoverList("tv_anticipated", "Most Anticipated TV on Simkl", "tv", "db", db_query="anticipated"),
    DiscoverList("tv_top_simkl", "Top Rated TV on Simkl", "tv", "db", db_query="top_simkl"),
    DiscoverList("tv_top_imdb", "Top Rated TV on IMDB", "tv", "db", db_query="top_imdb"),
    DiscoverList("tv_completed", "Completed & Worth Watching", "tv", "db", db_query="completed"),
    DiscoverList("tv_binge", "Binge-Worthy", "tv", "db", db_query="binge"),
    DiscoverList("tv_new_year", "New This Year", "tv", "db", db_query="new_year"),
    DiscoverList("tv_new", "New Series", "tv", "db", db_query="new_releases"),
    DiscoverList("tv_awards", "Award Winners", "tv", "db", db_query="awards"),
    DiscoverList("tv_low_drop", "Easy to Finish", "tv", "db", db_query="low_drop"),
    DiscoverList("tv_ongoing", "Ongoing Series", "tv", "db", db_query="ongoing"),
    DiscoverList("tv_ended", "Ended Series", "tv", "db", db_query="ended"),
    DiscoverList("tv_tba", "Coming Soon", "tv", "cdn", cdn_path="/calendar/v2/tv.json"),
)

ANIME_LISTS: tuple[DiscoverList, ...] = (
    _trending("anime", "today", "Simkl Trending Anime — Last 24 Hours"),
    _trending("anime", "week", "Simkl Trending Anime — Last 7 Days"),
    _trending("anime", "month", "Simkl Trending Anime — Last 30 Days"),
    DiscoverList("anime_popular", "Popular Anime on Simkl", "anime", "db", db_query="popular"),
    DiscoverList("anime_most_watched", "Most Watched Anime on Simkl", "anime", "db", db_query="most_watched"),
    DiscoverList("anime_anticipated", "Most Anticipated Anime on Simkl", "anime", "db", db_query="anticipated"),
    DiscoverList("anime_top_simkl", "Top Rated Anime on Simkl", "anime", "db", db_query="top_simkl"),
    DiscoverList("anime_top_mal", "Top Rated Anime on MAL", "anime", "db", db_query="top_mal"),
    DiscoverList("anime_completed", "Completed & Worth Watching", "anime", "db", db_query="completed"),
    DiscoverList("anime_binge", "Binge-Worthy", "anime", "db", db_query="binge"),
    DiscoverList("anime_new_year", "New This Year", "anime", "db", db_query="new_year"),
    DiscoverList("anime_new", "New Anime", "anime", "db", db_query="new_releases"),
    DiscoverList("anime_low_drop", "Easy to Finish", "anime", "db", db_query="low_drop"),
    DiscoverList("anime_short", "Short Series", "anime", "db", db_query="short"),
    DiscoverList("anime_ongoing", "Ongoing Anime", "anime", "db", db_query="ongoing"),
    DiscoverList("anime_ended", "Completed Anime", "anime", "db", db_query="ended"),
    DiscoverList("anime_tba", "Coming Soon", "anime", "cdn", cdn_path="/calendar/v2/anime.json"),
)

CATALOG_LISTS = {
    "movie": MOVIE_LISTS,
    "tv": TV_LISTS,
    "anime": ANIME_LISTS,
}


def get_list(catalog: Catalog, list_id: str) -> DiscoverList | None:
    for item in CATALOG_LISTS.get(catalog, ()):
        if item.list_id == list_id:
            return item
    return None
