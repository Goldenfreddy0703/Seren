"""Persistent cache for debrid hash check results."""

import collections
import time
from threading import Thread

from resources.lib.database import Database
from resources.lib.modules.globals import g

CACHE_TTL_HOURS = 24
CACHE_TTL_HOURS_UNCACHED = 4

schema = {
    "debrid_data": {
        "columns": collections.OrderedDict(
            [
                ("hash", ["TEXT", "NOT NULL"]),
                ("debrid", ["TEXT", "NOT NULL"]),
                ("cached", ["TEXT", "NOT NULL"]),
                ("expires", ["INTEGER", "NOT NULL"]),
            ]
        ),
        "table_constraints": ["PRIMARY KEY (hash, debrid)"],
        "indices": [
            ("idx_debrid_data_debrid", ["debrid"]),
            ("idx_debrid_data_expires", ["expires"]),
        ],
        "default_seed": [],
    }
}


class DebridCache(Database):
    def __init__(self):
        super().__init__(g.DEBRID_CACHE_DB_PATH, schema)
        self.table_name = next(iter(schema))
        self.enabled = g.get_bool_setting("general.torrentCache")

    def get_many(self, hash_list):
        if not self.enabled or not hash_list:
            return []
        try:
            current_time = int(time.time())
            results = []
            batch_size = 500
            hash_list = list(hash_list)
            for index in range(0, len(hash_list), batch_size):
                batch = hash_list[index:index + batch_size]
                placeholders = ",".join("?" for _ in batch)
                rows = self.fetchall(
                    f"SELECT hash, debrid, cached, expires FROM debrid_data "
                    f"WHERE hash IN ({placeholders}) AND expires > ?",
                    (*batch, current_time),
                )
                if rows:
                    results.extend(rows)
            return results
        except Exception as exc:
            g.log(f"DebridCache.get_many error: {exc}", "warning")
            return []

    def set_many(self, hash_results, debrid):
        if not self.enabled or not hash_results:
            return
        try:
            now = int(time.time())
            for info_hash, cached in hash_results:
                ttl_hours = CACHE_TTL_HOURS if cached == "True" else CACHE_TTL_HOURS_UNCACHED
                expires = now + (ttl_hours * 3600)
                self.execute_sql(
                    "INSERT OR REPLACE INTO debrid_data (hash, debrid, cached, expires) "
                    "VALUES (?, ?, ?, ?)",
                    (info_hash, debrid, cached, expires),
                )
        except Exception as exc:
            g.log(f"DebridCache.set_many error: {exc}", "warning")

    def set_many_background(self, hash_results, debrid):
        if self.enabled and hash_results:
            Thread(target=self.set_many, args=(hash_results, debrid), daemon=True).start()

    def get_cached_hashes_for_service(self, all_cached_rows, debrid):
        return {
            row["hash"]
            for row in all_cached_rows
            if row["debrid"] == debrid and row["cached"] == "True"
        }

    def get_known_hashes_for_service(self, all_cached_rows, debrid):
        return {row["hash"] for row in all_cached_rows if row["debrid"] == debrid}

    def cleanup(self):
        try:
            self.execute_sql(
                "DELETE FROM debrid_data WHERE expires <= ?",
                (int(time.time()),),
            )
        except Exception as exc:
            g.log(f"DebridCache.cleanup error: {exc}", "warning")

    def clear_all(self):
        try:
            self.execute_sql("DELETE FROM debrid_data")
            g.log("DebridCache: Cleared all entries", "info")
        except Exception as exc:
            g.log(f"DebridCache.clear_all error: {exc}", "warning")
