from __future__ import annotations

from resources.lib.database.simkl_sync import database


class SimklSyncDatabase(database.SimklSyncDatabase):
    def get_hidden_items(self, section, media_type=None):

        if media_type is None:
            return self.fetchall("SELECT simkl_id FROM hidden WHERE section=?", (section,))
        else:
            return self.fetchall(
                "SELECT simkl_id FROM hidden WHERE section=? and mediatype=?",
                (section, media_type),
            )

    def get_hidden_simkl_ids(self, section, media_type=None) -> set[int]:
        rows = self.get_hidden_items(section, media_type)
        return {int(row["simkl_id"]) for row in rows if row.get("simkl_id") is not None}
