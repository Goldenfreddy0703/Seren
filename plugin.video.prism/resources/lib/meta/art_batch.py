"""Deduped parallel art fetch for list paint (movie/show browse only)."""
from __future__ import annotations

from typing import Any

from resources.lib.modules.globals import g


class ArtBatchCoordinator:
    """One parallel wave per provider with deduped external IDs."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.stats: dict[str, int] = {"art_fetch": 0, "art_deduped": 0}

    def apply_art_gaps(
        self,
        pending: list[tuple[int, dict, str]],
        *,
        db,
        provider_cache: dict[int, dict] | None = None,
    ) -> dict[int, dict]:
        """Fetch art for pending db_objects; return simkl_id -> art dict."""
        if not pending:
            return {}

        art_by_id: dict[int, dict] = {}
        provider_type_by_id: dict[int, str] = {}
        unique_objects: dict[str, dict] = {}
        simkl_by_key: dict[str, list[tuple[int, dict, str]]] = {}

        for simkl_id, db_object, provider_type in pending:
            key = self._dedupe_key(db_object, provider_type)
            if not key:
                unique_objects[f"row:{simkl_id}"] = db_object
                simkl_by_key.setdefault(f"row:{simkl_id}", []).append((simkl_id, db_object, provider_type))
                continue
            simkl_by_key.setdefault(key, []).append((simkl_id, db_object, provider_type))
            if key not in unique_objects:
                unique_objects[key] = db_object
            else:
                self.stats["art_deduped"] += 1

        from resources.lib.common.thread_pool import ThreadPool

        pool = ThreadPool()
        for key, db_object in unique_objects.items():
            db_object["_art_batch_key"] = key
            pool.put(self._handler._fetch_art_patch, db_object)

        fetched: dict[str, dict] = {}
        if pool.tasks:
            import concurrent.futures

            for task in concurrent.futures.as_completed(pool.tasks):
                try:
                    db_object = task.result()
                except Exception:
                    g.log_stacktrace()
                    continue
                if not isinstance(db_object, dict):
                    continue
                key = db_object.get("_art_batch_key")
                if not key:
                    continue
                formatted = self._handler.format_meta(db_object)
                art = formatted.get("art")
                if isinstance(art, dict) and art:
                    fetched[key] = art
                    self.stats["art_fetch"] += 1
                    try:
                        profile = db_object.get("_art_profile")
                        from resources.lib.meta.artwork import provider_media_type

                        provider_type = (
                            "movie"
                            if db_object.get("_entity") == "movie"
                            else provider_media_type(profile) if profile else "tvshow"
                        )
                        self._handler._persist_list_provider_blobs(db_object, db, provider_type)
                    except Exception:
                        g.log_stacktrace()
            pool.tasks.clear()

        for key, art in fetched.items():
            for simkl_id, db_object, provider_type in simkl_by_key.get(key, []):
                art_by_id[int(simkl_id)] = art
                provider_type_by_id[int(simkl_id)] = provider_type

        self._provider_type_by_id = provider_type_by_id
        return art_by_id

    @staticmethod
    def _dedupe_key(db_object: dict, provider_type: str) -> str | None:
        from resources.lib.meta.provider_settings import external_ids_from_row

        ids = external_ids_from_row(db_object)
        if provider_type == "movie":
            tmdb_id = ids.get("tmdb_id")
            if tmdb_id:
                return f"tmdb:movie:{int(tmdb_id)}"
            tvdb_id = ids.get("tvdb_id")
            if tvdb_id:
                return f"tvdb:movie:{int(tvdb_id)}"
        else:
            tmdb_id = ids.get("tmdb_id")
            if tmdb_id:
                return f"tmdb:show:{int(tmdb_id)}"
            tvdb_id = ids.get("tvdb_id")
            if tvdb_id:
                return f"tvdb:show:{int(tvdb_id)}"
        imdb_id = ids.get("imdb_id")
        if imdb_id:
            return f"imdb:{provider_type}:{imdb_id}"
        return None
