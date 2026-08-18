"""Background metadata enrichment after Kodi menu paint (Seren/POV-style)."""
from __future__ import annotations

import threading
import time

from resources.lib.modules.globals import g

_PENDING_KEY = "meta_enrich.pending"
_IN_FLIGHT_KEY = "meta_enrich.in_flight"
_DEFER_KEY = "meta_enrich.defer_until"
_DEFER_MS = 250
_worker_thread = None
_worker_lock = threading.Lock()


def _empty_pending() -> dict:
    return {
        "batches": {
            "movie": {"simkl_ids": [], "catalog": None, "reasons": []},
            "tvshow": {"simkl_ids": [], "catalog": None, "reasons": []},
        },
        "child_jobs": [],
    }


class MetaEnrichmentQueue:
    @staticmethod
    def _load_pending() -> dict:
        pending = g.get_runtime_setting(_PENDING_KEY)
        if not isinstance(pending, dict):
            return _empty_pending()
        pending.setdefault("batches", _empty_pending()["batches"])
        pending.setdefault("child_jobs", [])
        for media_type in ("movie", "tvshow"):
            pending["batches"].setdefault(media_type, {"simkl_ids": [], "catalog": None, "reasons": []})
        return pending

    @staticmethod
    def _save_pending(pending: dict) -> None:
        g.set_runtime_setting(_PENDING_KEY, pending)

    @staticmethod
    def _has_work(pending: dict | None = None) -> bool:
        pending = pending or MetaEnrichmentQueue._load_pending()
        for batch in (pending.get("batches") or {}).values():
            if batch.get("simkl_ids"):
                return True
        return bool(pending.get("child_jobs"))

    @staticmethod
    def _touch_defer() -> None:
        g.set_runtime_setting(_DEFER_KEY, int(time.time() * 1000) + _DEFER_MS)

    @classmethod
    def _merge_batch(
        cls,
        media_type: str,
        simkl_ids: list[int],
        *,
        reason: str,
        catalog: str | None,
    ) -> None:
        if not simkl_ids:
            return
        from resources.lib.meta.registry import filter_pending

        simkl_ids = filter_pending(media_type, simkl_ids)
        if not simkl_ids:
            return
        pending = cls._load_pending()
        batch = pending["batches"].setdefault(
            media_type,
            {"simkl_ids": [], "catalog": None, "reasons": []},
        )
        batch["simkl_ids"] = sorted({int(simkl_id) for simkl_id in batch["simkl_ids"]} | set(simkl_ids))
        reasons = batch.setdefault("reasons", [])
        if reason and reason not in reasons:
            reasons.append(reason)
        if catalog:
            batch["catalog"] = catalog
        cls._save_pending(pending)

    @classmethod
    def _merge_from_action_args(cls, action_args: dict | None) -> None:
        if not isinstance(action_args, dict):
            return
        media_type = action_args.get("media_type") or "tvshow"
        simkl_ids = [int(simkl_id) for simkl_id in (action_args.get("simkl_ids") or []) if simkl_id is not None]
        if simkl_ids:
            cls._merge_batch(
                media_type,
                simkl_ids,
                reason=str(action_args.get("reason") or "list_open"),
                catalog=action_args.get("catalog"),
            )

    @classmethod
    def _enqueue_child_job(cls, job: dict) -> None:
        pending = cls._load_pending()
        jobs = pending.setdefault("child_jobs", [])
        key = (
            job.get("kind"),
            job.get("simkl_show_id"),
            job.get("season_row_id"),
            tuple(job.get("episode_ids") or ()),
        )
        for existing in jobs:
            existing_key = (
                existing.get("kind"),
                existing.get("simkl_show_id"),
                existing.get("season_row_id"),
                tuple(existing.get("episode_ids") or ()),
            )
            if existing_key == key:
                return
        jobs.append(job)
        cls._save_pending(pending)

    @classmethod
    def _enrich_lock_name(cls, media_type: str) -> str:
        return f"meta.enrich.{media_type}"

    @classmethod
    def _kick_worker(cls) -> None:
        if g.get_bool_runtime_setting(_IN_FLIGHT_KEY):
            return
        if not cls._has_work():
            return

        # In-process worker when not building a directory (service / maintenance / queue action).
        if g.PLUGIN_HANDLE <= 0:
            from resources.lib.modules.cache_maintenance import service_background_idle_ready

            if not service_background_idle_ready():
                return
            cls._start_worker_thread()
            return

        import xbmc

        url = g.create_url(g.BASE_URL, {"action": "processMetaEnrichmentQueue"})
        xbmc.executebuiltin(f'RunPlugin("{url}")')

    @classmethod
    def _start_worker_thread(cls) -> None:
        global _worker_thread
        with _worker_lock:
            if _worker_thread is not None and _worker_thread.is_alive():
                return

            def _run() -> None:
                try:
                    cls.process_request(None)
                except Exception:
                    g.log_stacktrace()

            _worker_thread = threading.Thread(target=_run, daemon=True, name="prism-meta-enrich")
            _worker_thread.start()

    @classmethod
    def process_idle(cls) -> bool:
        """Service hook: drain pending enrichment when browse/prefetch is idle."""
        if g.get_bool_runtime_setting(_IN_FLIGHT_KEY) or not cls._has_work():
            return False
        from resources.lib.modules.cache_maintenance import service_background_idle_ready

        if not service_background_idle_ready():
            return False
        cls._start_worker_thread()
        return True

    @classmethod
    def enrich_simkl_ids_blocking(
        cls,
        simkl_ids: list[int],
        media_type: str,
        *,
        reason: str = "prefetch",
        catalog: str | None = None,
    ) -> int:
        """Run list enrichment synchronously (used by next-page prefetch)."""
        from resources.lib.meta.registry import filter_pending, mark_enriched

        simkl_ids = filter_pending(media_type, simkl_ids)
        if not simkl_ids:
            return 0
        from resources.lib.meta.profiles import MetaProfile, profile_scope
        from resources.lib.modules.global_lock import GlobalLock

        with profile_scope(MetaProfile.LIST):
            with GlobalLock(cls._enrich_lock_name(media_type)):
                processed = cls._process_entity_batch(simkl_ids, media_type, reason=reason, catalog=catalog)
        if processed:
            mark_enriched(media_type, simkl_ids, reason=reason)
        return processed

    @classmethod
    def schedule_run_plugin(
        cls,
        refs: list[dict],
        media_type: str,
        *,
        reason: str = "list_open",
        catalog: str | None = None,
    ) -> None:
        if not refs:
            return

        from resources.lib.meta.registry import filter_pending

        simkl_ids = sorted({int(ref["simkl_id"]) for ref in refs if ref.get("simkl_id") is not None})
        simkl_ids = filter_pending(media_type, simkl_ids)
        if not simkl_ids:
            return

        cls._merge_batch(media_type, simkl_ids, reason=reason, catalog=catalog)
        cls._touch_defer()
        g.log(f"Scheduling meta enrichment ({reason}) for {len(simkl_ids)} items", "debug")
        cls._kick_worker()

    @classmethod
    def schedule_show_children(
        cls,
        simkl_show_id: int,
        *,
        kind: str = "season_list",
        season_row_id: int | None = None,
        episode_ids: list[int] | None = None,
    ) -> None:
        if not simkl_show_id:
            return
        job = {"kind": kind, "simkl_show_id": int(simkl_show_id)}
        if season_row_id is not None:
            job["season_row_id"] = int(season_row_id)
        if episode_ids:
            job["episode_ids"] = sorted({int(episode_id) for episode_id in episode_ids})
        cls._enqueue_child_job(job)
        cls._touch_defer()
        g.log(f"Scheduling {kind} enrichment for show {simkl_show_id}", "debug")
        cls._kick_worker()

    @classmethod
    def process_request(cls, action_args: dict | None) -> None:
        try:
            cls._merge_from_action_args(action_args)
            if g.get_bool_runtime_setting(_IN_FLIGHT_KEY):
                return

            g.set_runtime_setting(_IN_FLIGHT_KEY, True)
            enriched_count = 0
            while cls._has_work():
                defer_until = g.get_int_runtime_setting(_DEFER_KEY, 0)
                wait_ms = defer_until - int(time.time() * 1000)
                if wait_ms > 0:
                    g.wait_for_abort(wait_ms / 1000.0)

                pending = cls._load_pending()
                if not cls._has_work(pending):
                    break

                if g.PLUGIN_HANDLE <= 0:
                    from resources.lib.modules.cache_maintenance import service_background_idle_ready

                    if not service_background_idle_ready():
                        break

                child_jobs = list(pending.get("child_jobs") or [])
                batches = {
                    media_type: dict(batch)
                    for media_type, batch in (pending.get("batches") or {}).items()
                }
                cls._save_pending(_empty_pending())

                from resources.lib.meta.registry import mark_enriched
                from resources.lib.modules.global_lock import GlobalLock

                for media_type, batch in batches.items():
                    simkl_ids = batch.get("simkl_ids") or []
                    if not simkl_ids:
                        continue
                    reason = (batch.get("reasons") or ["list_open"])[0]
                    catalog = batch.get("catalog")
                    with GlobalLock(cls._enrich_lock_name(media_type)):
                        enriched_count += cls._process_entity_batch(
                            simkl_ids,
                            media_type,
                            reason=reason,
                            catalog=catalog,
                        )
                    mark_enriched(media_type, simkl_ids, reason=reason)

                if child_jobs:
                    with GlobalLock(cls._enrich_lock_name("tvshow")):
                        enriched_count += cls._process_child_jobs(child_jobs)

                if not cls._has_work():
                    break

            if enriched_count:
                g.trigger_widget_refresh(if_playing=False)
        except Exception:
            g.log_stacktrace()
        finally:
            g.set_runtime_setting(_IN_FLIGHT_KEY, False)
            if cls._has_work():
                cls._kick_worker()

    @classmethod
    def _process_child_jobs(cls, jobs: list[dict]) -> int:
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        processed = 0
        seen: set[tuple] = set()
        for job in jobs:
            if not isinstance(job, dict):
                continue
            key = (
                job.get("kind"),
                job.get("simkl_show_id"),
                job.get("season_row_id"),
                tuple(job.get("episode_ids") or ()),
            )
            if key in seen:
                continue
            seen.add(key)
            show_id = job.get("simkl_show_id")
            if show_id is None:
                continue
            start = time.time()
            if job.get("kind") == "season_list":
                db._try_update_seasons(int(show_id), job.get("season_row_id"))
            elif job.get("kind") == "episode_list":
                episode_ids = job.get("episode_ids") or []
                if len(episode_ids) == 1:
                    db._try_update_episodes(int(show_id), job.get("season_row_id"), episode_ids[0])
                else:
                    db._try_update_episodes(int(show_id), job.get("season_row_id"))
            else:
                continue
            processed += 1
            g.log(
                f"child_enrich_ms={(time.time() - start) * 1000:.0f} kind={job.get('kind')} show={show_id}",
                "debug",
            )
        return processed

    @classmethod
    def _process_entity_batch(
        cls,
        simkl_ids: list[int],
        media_type: str,
        *,
        reason: str,
        catalog: str | None,
    ) -> int:
        start = time.time()
        g.ensure_addon()
        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        handler = db.metadataHandler

        if catalog and (
            reason
            in ("discover", "search", "calendar", "library", "genre", "actor", "browse")
            or str(reason).startswith("prefetch_")
        ):
            from resources.lib.simkl.enrich import enrich_sync_ids_persisted

            enrich_sync_ids_persisted(catalog, simkl_ids, db=db)

        rows = cls._load_entity_rows(db, simkl_ids, media_type)
        if catalog:
            by_id = {
                int(row["simkl_id"]): row
                for row in rows
                if isinstance(row, dict) and row.get("simkl_id") is not None
            }
            for paint_row in cls._load_catalog_paint_rows(catalog, simkl_ids, media_type):
                sid = int(paint_row["simkl_id"])
                if sid not in by_id:
                    by_id[sid] = paint_row
            rows = list(by_id.values())

        from resources.lib.meta.paint_complete import partition_paint_rows

        _complete, incomplete_rows = partition_paint_rows(rows, media_type, gap_scope="full")
        if not incomplete_rows:
            return len(simkl_ids)
        incomplete_ids = {
            int(row["simkl_id"])
            for row in incomplete_rows
            if isinstance(row, dict) and row.get("simkl_id") is not None
        }
        rows = incomplete_rows
        simkl_ids = [sid for sid in simkl_ids if sid in incomplete_ids]

        _, enrichment_refs = handler.merge_list_meta_local(rows, media_type, db=db)
        if enrichment_refs:
            targets = enrichment_refs
        else:
            from resources.lib.meta.artwork import artwork_profile_for_row, provider_media_type

            targets = []
            for row in rows:
                if not isinstance(row, dict) or row.get("simkl_id") is None:
                    continue
                provider_type = provider_media_type(artwork_profile_for_row(row, default_media_type=media_type))
                targets.append(
                    {
                        "simkl_id": int(row["simkl_id"]),
                        "needs_update": True,
                        "_provider_type": provider_type,
                    }
                )
        if targets:
            from resources.lib.meta.profiles import MetaProfile, profile_scope
            from resources.lib.meta.providers import MetaProviderRouter

            with profile_scope(MetaProfile.LIST):
                MetaProviderRouter.enrich_list_refs(
                    targets,
                    media_type,
                    db=db,
                    profile=MetaProfile.LIST,
                    persist=True,
                    reason=reason,
                    catalog=catalog,
                )

        cls._gapfill_anime_titles(db, simkl_ids, media_type)

        depth = sum(len(batch.get("simkl_ids") or []) for batch in cls._load_pending().get("batches", {}).values())
        g.log(
            f"enrich_queue_depth={depth} reason={reason} enrich_ms={(time.time() - start) * 1000:.0f} "
            f"items={len(simkl_ids)}",
            "debug",
        )
        return len(simkl_ids)

    @staticmethod
    def _load_entity_rows(db, simkl_ids: list[int], media_type: str) -> list[dict]:
        ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
        if media_type == "movie":
            query = f"""
                SELECT m.simkl_id, m.info, m.art, m.[cast], m.args, m.last_updated,
                       m.tmdb_id, m.tvdb_id, m.imdb_id
                FROM movies AS m
                WHERE m.simkl_id IN ({ids_sql})
            """
        else:
            query = f"""
                SELECT s.simkl_id, s.info, s.[cast], s.art, s.args, s.last_updated,
                       s.tmdb_id, s.tvdb_id, s.imdb_id
                FROM shows AS s
                WHERE s.simkl_id IN ({ids_sql})
            """
        return db.fetchall(query) or []

    @staticmethod
    def _load_catalog_paint_rows(catalog: str | None, simkl_ids: list[int], media_type: str) -> list[dict]:
        if not catalog or not simkl_ids:
            return []
        from resources.lib.discover.catalog_store import sync_items_for_refs
        from resources.lib.meta.paint_cache import sync_row_to_paint_row

        page_refs = [{"simkl_id": int(sid), "catalog": catalog} for sid in simkl_ids]
        items = sync_items_for_refs(catalog, page_refs)
        rows: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            painted = sync_row_to_paint_row(item)
            if not painted or painted.get("simkl_id") is None:
                continue
            info = painted.get("info") if isinstance(painted.get("info"), dict) else {}
            rows.append(
                {
                    "simkl_id": int(painted["simkl_id"]),
                    "info": info,
                    "art": painted.get("art") or {},
                    "cast": painted.get("cast") or [],
                    "tmdb_id": painted.get("tmdb_id") or info.get("tmdb_id"),
                    "tvdb_id": painted.get("tvdb_id") or info.get("tvdb_id"),
                    "imdb_id": painted.get("imdb_id") or info.get("imdb_id"),
                }
            )
        return rows

    @staticmethod
    def _gapfill_anime_titles(db, simkl_ids: list[int], media_type: str) -> None:
        ids_sql = ",".join(str(int(simkl_id)) for simkl_id in simkl_ids)
        table = "movies" if media_type == "movie" else "shows"
        rows = db.fetchall(
            f"""
            SELECT simkl_id, info, art, [cast]
            FROM {table}
            WHERE simkl_id IN ({ids_sql})
            """
        )
        if not rows:
            return
        from resources.lib.simkl.enrich import gapfill_anime_title_rows

        updated = gapfill_anime_title_rows(rows)
        db.metadataHandler._persist_list_rows(
            updated,
            "movie" if media_type == "movie" else "tvshow",
            db=db,
        )

    @classmethod
    def schedule_needs_update(cls, limit: int = 50) -> int:
        """Queue rows flagged needs_update after sync/maintenance."""
        if limit <= 0:
            return 0

        from resources.lib.database.session import get_sync_database

        db = get_sync_database()
        movie_rows = db.fetchall(
            f"SELECT simkl_id FROM movies WHERE needs_update=1 LIMIT {int(limit // 2 or 1)}"
        )
        show_rows = db.fetchall(
            f"SELECT simkl_id FROM shows WHERE needs_update=1 LIMIT {int(limit - len(movie_rows or []))}"
        )
        scheduled = 0
        if movie_rows:
            movie_ids = [int(row["simkl_id"]) for row in movie_rows if row.get("simkl_id") is not None]
            if movie_ids:
                cls._merge_batch("movie", movie_ids, reason="sync", catalog="movie")
                scheduled += len(movie_ids)
        if show_rows:
            show_ids = [int(row["simkl_id"]) for row in show_rows if row.get("simkl_id") is not None]
            if show_ids:
                cls._merge_batch("tvshow", show_ids, reason="sync", catalog=None)
                scheduled += len(show_ids)
        if scheduled:
            cls._touch_defer()
        return scheduled


def enrich_simkl_sync_items(items: list[dict], **kwargs) -> list[dict]:
    """Simkl CDN/detail gap-fill for search, discover, and prefetch paths."""
    from resources.lib.simkl.enrich import enrich_sync_items

    return enrich_sync_items(items, **kwargs)
