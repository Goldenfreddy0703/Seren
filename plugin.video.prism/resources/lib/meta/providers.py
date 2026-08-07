"""Batched provider gap-fill for list enrichment (LIST profile)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from resources.lib.meta.profiles import MetaProfile, profile_scope
from resources.lib.modules.globals import g

if TYPE_CHECKING:
    from resources.lib.database.simkl_sync.database import SimklSyncDatabase


class MetaProviderRouter:
    """Route list enrichment through profile-aware provider updates."""

    @staticmethod
    def enrich_list_refs(
        refs: list[dict],
        media_type: str,
        db: SimklSyncDatabase | None = None,
        *,
        profile: str = MetaProfile.LIST,
        persist: bool = True,
        reason: str = "list_open",
        catalog: str | None = None,
    ) -> list:
        """Fetch missing provider meta for list refs; LIST skips child provider blobs."""
        if not refs:
            return []

        if db is None:
            from resources.lib.database.session import get_sync_database

            db = get_sync_database()

        from resources.lib.meta.registry import filter_pending

        pending_refs = [
            dict(ref)
            for ref in refs
            if ref.get("simkl_id") is not None
        ]
        if not pending_refs:
            return []

        simkl_ids = sorted({int(ref["simkl_id"]) for ref in pending_refs})
        simkl_ids = filter_pending(media_type, simkl_ids)
        if not simkl_ids:
            return []

        id_set = set(simkl_ids)
        pending_refs = [ref for ref in pending_refs if int(ref["simkl_id"]) in id_set]

        with profile_scope(profile):
            handler = db.metadataHandler
            g.log(
                f"MetaProviderRouter enrich profile={profile} reason={reason} "
                f"items={len(pending_refs)} media={media_type}",
                "debug",
            )
            return handler.enrich_list_meta_online(
                pending_refs,
                media_type,
                db=db,
                persist=persist,
                catalog=catalog,
            )

    @staticmethod
    def group_refs_by_provider_type(refs: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split enrichment refs into movie vs tvshow provider batches."""
        movie_refs: list[dict] = []
        show_refs: list[dict] = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("simkl_id") is None:
                continue
            if ref.get("_provider_type") == "movie":
                movie_refs.append(ref)
            else:
                show_refs.append(ref)
        return movie_refs, show_refs
