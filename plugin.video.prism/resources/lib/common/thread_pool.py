from __future__ import annotations

import concurrent.futures
import threading
from functools import reduce

from resources.lib.common import tools
from resources.lib.modules.globals import g

_shared_executor = None
_provider_executor = None
_shared_executor_lock = threading.Lock()
_PRISM_PLUGIN_MODE = False
_REAL_THREAD_START = threading.Thread.start


def _guarded_thread_start(self) -> None:
    """Kodi Python is single-threaded for GUI calls — run inline during plugin menus."""
    if _PRISM_PLUGIN_MODE:
        if self._target is None:
            return
        self.run()
        return
    return _REAL_THREAD_START(self)


def enter_prism_plugin_mode() -> None:
    """Set before init_globals — Kodi plugin argv[0] is a URL, not prism.py."""
    global _PRISM_PLUGIN_MODE
    _PRISM_PLUGIN_MODE = True
    threading.Thread.start = _guarded_thread_start  # type: ignore[method-assign]


def exit_prism_plugin_mode() -> None:
    global _PRISM_PLUGIN_MODE
    _PRISM_PLUGIN_MODE = False
    threading.Thread.start = _REAL_THREAD_START  # type: ignore[method-assign]

# Default, Low, Medium, High, Extreme
_SCALED_WORKERS = [20, 10, 20, 40, 80]


class _InlineExecutor:
    """Run pool work on the calling thread — avoids Kodi invoker orphan threads on menu exit."""

    _max_workers = 1
    _shutdown = False

    def submit(self, fn, /, *args, **kwargs):
        future: concurrent.futures.Future = concurrent.futures.Future()
        if self._shutdown:
            future.set_exception(RuntimeError("cannot schedule new futures after shutdown"))
            return future
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def map(self, fn, *iterables):
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        return [fn(*args) for args in zip(*iterables)]

    def shutdown(self, wait=True, *, cancel_futures=False):
        self._shutdown = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Singleton — do not leave inline executor shut down after a with-block.
        return False


_INLINE_EXECUTOR = _InlineExecutor()


def _use_inline_pool() -> bool:
    """Avoid orphan worker threads on Kodi plugin invoker exit (service keeps real pools)."""
    try:
        if _PRISM_PLUGIN_MODE:
            return True
        if g.get_bool_runtime_setting("prism.inline_pool"):
            return True
        import sys

        argv0 = ((sys.argv[0] if sys.argv else "") or "").replace("\\", "/")
        if argv0.endswith("service.py"):
            return False
        if argv0.endswith("prism.py"):
            return True
        return int(getattr(g, "PLUGIN_HANDLE", 0) or 0) > 0
    except Exception:
        return False


def prism_plugin_no_threads() -> bool:
    """Kodi waits for every Python thread before a plugin invoker exits (daemon is ignored)."""
    return _use_inline_pool()


def defer_background(target, /, *args, name: str = "prism-bg", **kwargs) -> None:
    """Spawn a background thread only outside prism.py invocations."""
    if prism_plugin_no_threads():
        return
    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True, name=name).start()


class ThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """
    Support the python 3.9+ option to cancel futures on shutdown.
    (CPython 3.8 ThreadPoolExecutor already uses daemon worker threads.)
    """

    import queue

    def shutdown(self, wait=True, *, cancel_futures=False):
        """
        Clean-up the resources associated with the Executor.

        It is safe to call this method several times. Otherwise, no other methods can be called after this one.

        :param wait: If True then shutdown will not return until all running futures have finished executing and the
                     resources used by the executor have been reclaimed.
        :param cancel_futures: If cancel_futures is True, this method will cancel all pending futures that the executor
                               has not started running. Any futures that are completed or running won’t be cancelled,
                               regardless of the value of cancel_futures
        :return:
        """
        with self._shutdown_lock:
            self._shutdown = True
            if cancel_futures:
                # Drain all work items from the queue, and then cancel their
                # associated futures.
                while True:
                    try:
                        work_item = self._work_queue.get_nowait()
                    except self.queue.Empty:
                        break
                    if work_item is not None:
                        work_item.future.cancel()

            # Send a wake-up to prevent threads calling
            # _work_queue.get(block=True) for permanently blocking.
            self._work_queue.put(None)
        if wait:
            for t in self._threads:
                t.join()


def _max_pool_workers() -> int:
    limiter = g.get_bool_runtime_setting("threadpool.limiter")
    workers = _SCALED_WORKERS[g.get_int_setting("general.threadpoolScale", -1) + 1]
    return 1 if limiter else workers


def get_shared_executor() -> ThreadPoolExecutor:
    """Process-wide executor for flat parallel work (e.g. Simkl detail enrich)."""
    if _use_inline_pool():
        return _INLINE_EXECUTOR  # type: ignore[return-value]
    global _shared_executor
    with _shared_executor_lock:
        if _shared_executor is None or getattr(_shared_executor, "_shutdown", False):
            _shared_executor = ThreadPoolExecutor(max_workers=_max_pool_workers())
        return _shared_executor


def get_provider_executor() -> ThreadPoolExecutor:
    """Separate pool for nested TMDB/TVDB/Fanart fetches inside milling workers.

    Must not share the list-milling executor — workers that block waiting for
    sub-tasks on the same pool will deadlock when the pool is saturated.
    """
    if _use_inline_pool():
        return _INLINE_EXECUTOR  # type: ignore[return-value]
    global _provider_executor
    with _shared_executor_lock:
        if _provider_executor is None or getattr(_provider_executor, "_shutdown", False):
            workers = max(6, min(24, _max_pool_workers() * 2))
            _provider_executor = ThreadPoolExecutor(max_workers=workers)
        return _provider_executor


def release_global_executors(*, wait: bool = False, cancel_futures: bool = True) -> None:
    """Stop shared/provider pools and drop singletons (plugin invoker teardown)."""
    global _shared_executor, _provider_executor
    with _shared_executor_lock:
        for executor in (_shared_executor, _provider_executor):
            if executor is None or getattr(executor, "_shutdown", False):
                continue
            try:
                executor.shutdown(wait=wait, cancel_futures=cancel_futures)
            except Exception:
                pass
        _shared_executor = None
        _provider_executor = None


def shutdown_all_executors(*, wait: bool = False) -> None:
    """Service exit hook — same as release without cancelling running browse futures."""
    release_global_executors(wait=wait, cancel_futures=False)


class ThreadPool:
    """
    Helper class to simplify raising worker_pool
    """

    scaled_workers = _SCALED_WORKERS

    def __init__(self, *, shared: bool = False):
        self._shared = shared
        self._inline = False
        if shared:
            self.executor = get_shared_executor()
            self.max_workers = getattr(self.executor, "_max_workers", 1)
            self._inline = _use_inline_pool()
        elif _use_inline_pool():
            self.executor = _INLINE_EXECUTOR
            self.max_workers = 1
            self._inline = True
        else:
            self.max_workers = _max_pool_workers()
            self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.tasks = []

    def __del__(self):
        if self._shared:
            return
        executor = getattr(self, "executor", None)
        if executor is not None:
            try:
                self.force_stop()
            except Exception:
                pass

    @staticmethod
    def _handle_results(results):
        result_iter = iter(results)

        for result in result_iter:
            if result is not None:
                break
        else:
            return None

        if isinstance(result, dict):
            return reduce(tools.smart_merge_dictionary, result_iter, result)
        elif isinstance(result, (list, set)):
            result_list = list(result)
            for result in result_iter:
                if result is not None:
                    if isinstance(result, list):
                        result_list.extend(result)
                    else:
                        result_list.append(result)
            return result_list
        else:
            return [result for result in result_iter if result is not None]

    def put(self, func, *args, **kwargs):
        """
        Adds task to executor and starts it running
        :param func: method to run in task
        :type func: object
        :param args: arguments to assign to method
        :type args: any
        :param kwargs: kwargs to assign to method
        :type kwargs: any
        :return:
        :rtype:
        """
        self.tasks.append(self.executor.submit(func, *args, **kwargs))

    def force_stop(self) -> None:
        """Cancel pending futures and stop a dedicated executor (Kodi exit / abort)."""
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        if self._shared or self._inline:
            return
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

    def wait_completion(self):
        """
        Joins threads and waits for their completion, raises any exceptions if any present and returns results if
        present
        :return: The results
        :raises: The first exception identified if an exception is raised
        """
        try:
            if self._inline:
                for task in self.tasks:
                    if exception := task.exception():
                        if not self._shared:
                            self.force_stop()
                        raise exception
                results = self._handle_results(task.result() for task in self.tasks if task)
                self.tasks.clear()
                return results

            pending = set(self.tasks)
            while pending:
                if g.abort_requested():
                    self.force_stop()
                    return None
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.5,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for task in done:
                    if exception := task.exception():
                        if not self._shared:
                            self.force_stop()
                        raise exception

            results = self._handle_results(task.result() for task in self.tasks if task)
            self.tasks.clear()
            return results
        except Exception:
            g.log_stacktrace()
            if not self._shared:
                self.force_stop()
            raise

    def map_results(self, func, args_iterable=None, kwargs_iterable=None):
        """
        Takes iterables for args and kwargs and runs func with them, gathers the results and returns in order
        :param func: The function to execute
        :param args_iterable: An iterable of args tuples
        :param kwargs_iterable: An iterable of kwargs dicts
        :return: The results
        """
        try:
            return self._handle_results(
                self.executor.map(lambda args, kwargs: func(*args, **kwargs), args_iterable, kwargs_iterable)
                if args_iterable and kwargs_iterable
                else self.executor.map(lambda kwargs: func(**kwargs), kwargs_iterable)
                if kwargs_iterable
                else self.executor.map(lambda args: func(*args), args_iterable)
            )
        except Exception:
            if not self._shared:
                self.executor.shutdown(wait=False, cancel_futures=True)
            g.log_stacktrace()
            raise
