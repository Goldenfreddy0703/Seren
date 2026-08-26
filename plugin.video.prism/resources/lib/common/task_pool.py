"""Work-queue thread pool for parallel list building (POV-style TaskPool)."""
from __future__ import annotations

from queue import Empty, Queue
from threading import Thread

from resources.lib.modules.globals import g


class TaskPool:
    """Feed a queue to N worker threads; each worker drains until empty."""

    _WORKER_SCALES = [10, 20, 40, 80]

    def __init__(self, maxsize: int | None = None):
        if maxsize is None:
            if g.get_bool_runtime_setting("threadpool.limiter"):
                maxsize = 1
            else:
                idx = g.get_int_setting("general.threadpoolScale", 1)
                idx = max(0, min(idx, len(self._WORKER_SCALES) - 1))
                maxsize = self._WORKER_SCALES[idx]
        self.maxsize = max(1, int(maxsize))
        self._queue: Queue = Queue()

    @staticmethod
    def _start_threads(threads: list[Thread]) -> list[Thread]:
        started: list[Thread] = []
        for thread in threads:
            try:
                thread.start()
                started.append(thread)
            except Exception:
                g.log_stacktrace()
        return started

    def _thread_target(self, target) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            try:
                if isinstance(item, tuple):
                    target(*item)
                else:
                    target(item)
            except Exception:
                g.log_stacktrace()

    def tasks_enumerate(self, target, items, thread_cls=Thread) -> list[Thread]:
        if not items:
            return []
        from resources.lib.common.thread_pool import prism_plugin_no_threads

        if prism_plugin_no_threads():
            for position, tag in enumerate(items, 1):
                try:
                    target(position, tag)
                except Exception:
                    g.log_stacktrace()
            return []

        workers = min(len(items), self.maxsize)
        for position, tag in enumerate(items, 1):
            self._queue.put((position, tag))
        threads = [thread_cls(target=self._thread_target, args=(target,)) for _ in range(workers)]
        return self._start_threads(threads)
