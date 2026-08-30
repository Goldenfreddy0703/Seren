from resources.lib.common import source_utils
from resources.lib.debrid.real_debrid import RealDebrid
from resources.lib.modules.globals import g
from resources.lib.modules.resolver.torrent_resolvers.base_resolver import (
    TorrentResolverBase,
)


class RealDebridResolver(TorrentResolverBase):
    """
    Resolver for Real Debrid
    """

    def __init__(self):
        super().__init__()
        self.debrid_module = RealDebrid()
        self.torrent_id = None
        self._source_normalization = (
            ("path", "path", None),
            ("bytes", "size", lambda k: (k / 1024) / 1024),
            ("size", "size", None),
            ("filename", "release_title", None),
            ("id", "id", None),
            ("link", "link", None),
            ("selected", "selected", None),
        )

    def _files_for_picker(self, torrent_info):
        all_files = torrent_info.get("files") or []
        links = torrent_info.get("links") or []
        result = []
        for idx, file in enumerate(all_files):
            path = file.get("path") or ""
            if "sample" in path.lower() or not source_utils.is_file_ext_valid(path):
                continue
            if not self.pack_select and not file.get("selected"):
                continue
            item = dict(file)
            if idx < len(links) and links[idx]:
                item["link"] = links[idx]
            result.append(item)
        return result

    def _fetch_source_files(self, torrent, item_information):
        hash_check = self.debrid_module.check_hash(torrent["hash"])
        entry = hash_check.get(torrent["hash"])
        if not entry:
            return []

        self.torrent_id = entry["torrent_id"]
        torrent_info = entry["torrent_info"]
        if self.pack_select:
            self.debrid_module.torrent_select_all(self.torrent_id)
            torrent_info = self.debrid_module.torrent_info(self.torrent_id) or torrent_info
            if "files" in torrent_info:
                torrent_info["files"] = [
                    file
                    for file in torrent_info["files"]
                    if "sample" not in file.get("path", "").lower()
                    and source_utils.is_file_ext_valid(file.get("path", ""))
                ]

        return self._files_for_picker(torrent_info)

    def resolve_stream_url(self, file_info):
        """
        Convert provided source file into a link playable through debrid service
        :param file_info: Normalised information on source file
        :return: streamable link
        """
        return self.debrid_module.resolve_hoster(file_info["link"])

    def _do_post_processing(self, item_information, torrent, identified_file):
        if identified_file is None and not g.get_bool_setting("rd.autodelete"):
            self.debrid_module.delete_torrent(self.torrent_id)
