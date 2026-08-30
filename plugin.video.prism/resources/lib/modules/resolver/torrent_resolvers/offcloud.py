"""
Offcloud torrent resolver for Prism.
"""

from resources.lib.debrid.offcloud import OffCloud
from resources.lib.modules.globals import g
from resources.lib.modules.resolver.torrent_resolvers.base_resolver import (
    TorrentResolverBase,
)


class OffCloudResolver(TorrentResolverBase):
    """Resolver for Offcloud."""

    def __init__(self):
        super().__init__()
        self.debrid_module = OffCloud()
        self.request_id = None
        self._from_cloud_queue = False
        self._source_normalization = (
            ("name", "path", None),
            ("short_name", "release_title", None),
            ("size", "size", lambda k: k / 1024 / 1024 if k > 1024 * 1024 else k),
            ("id", "id", None),
            ("url", "url", None),
            ("link", "link", None),
        )

    def _get_folder_details(self, torrent, item_information):
        cached_id = torrent.get("_prism_request_id")
        if cached_id:
            self.request_id = cached_id
        return super()._get_folder_details(torrent, item_information)

    def _fetch_source_files(self, torrent, item_information):
        try:
            magnet = torrent.get("magnet")
            if not magnet:
                magnet = f"magnet:?xt=urn:btih:{torrent['hash']}"

            torrent_data = self.debrid_module.get_torrent_files(magnet=magnet)
            if not torrent_data:
                g.log("Offcloud: get_torrent_files returned empty", "warning")
                return []

            self.request_id = torrent_data.get("request_id")
            if self.request_id is not None:
                torrent["_prism_request_id"] = self.request_id
            self._from_cloud_queue = not torrent_data.get("cached", False)
            files = torrent_data.get("files", [])

            if not files:
                g.log("Offcloud: No files found in torrent", "warning")
                return []
            return files
        except Exception as e:
            g.log(f"Offcloud _fetch_source_files error: {e}", "error")
            return []

    def resolve_stream_url(self, file_info):
        if file_info is None:
            return None

        if file_info.get("url"):
            return file_info["url"]

        if file_info.get("link"):
            return self.debrid_module.resolve_hoster(file_info["link"])

        file_id = file_info.get("id")
        if file_id is None:
            return None

        return self.debrid_module.resolve_torrent_file(self.request_id, file_id, file_info)

    def _do_post_processing(self, item_information, torrent, identified_file):
        if not g.get_bool_setting("oc.addToCloud") or identified_file is None:
            return
        if self._from_cloud_queue:
            return

        magnet = torrent.get("magnet") or f"magnet:?xt=urn:btih:{torrent.get('hash', '')}"
        if magnet:
            self.debrid_module.add_to_cloud(magnet)
