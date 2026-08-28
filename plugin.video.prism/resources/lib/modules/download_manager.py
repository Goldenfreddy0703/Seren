from __future__ import annotations

import abc
import math
import os
import threading
import time
from urllib import parse

import requests
import xbmcgui
import xbmcvfs

from resources.lib.common import source_utils
from resources.lib.common import tools
from resources.lib.common.thread_pool import ThreadPoolExecutor
from resources.lib.debrid.all_debrid import AllDebrid
from resources.lib.debrid.premiumize import Premiumize
from resources.lib.debrid.real_debrid import RealDebrid
from resources.lib.modules.exceptions import FileAlreadyExists
from resources.lib.modules.exceptions import GeneralIOError
from resources.lib.modules.exceptions import InvalidSourceType
from resources.lib.modules.exceptions import InvalidWebPath
from resources.lib.modules.exceptions import SourceNotAvailable
from resources.lib.modules.exceptions import TaskDoesNotExist
from resources.lib.modules.exceptions import UnexpectedResponse
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g
from resources.lib.modules.download_paths import build_download_subdir
from resources.lib.modules.download_paths import join_download_path
from resources.lib.modules.download_paths import move_to_local_library

CLOCK = time.time
VALID_SOURCE_TYPES = ["torrent", "hoster", "cloud", "direct"]

_download_executor = None
_download_executor_workers = None
_download_slots = None
_download_slots_limit = None
_executor_lock = threading.Lock()


def _get_max_download_workers():
    if g.get_int_setting("download.concurrency.mode", 1) == 0:
        return 1
    return max(1, min(8, g.get_int_setting("download.concurrency.limit", 3)))


def _get_download_slots():
    global _download_slots, _download_slots_limit
    workers = _get_max_download_workers()
    with _executor_lock:
        if _download_slots is None or _download_slots_limit != workers:
            _download_slots = threading.Semaphore(workers)
            _download_slots_limit = workers
            g.log(f"Download concurrency slots set to {workers}", "debug")
    return _download_slots


def _get_executor_worker_count():
    limit = _get_max_download_workers()
    return min(8, max(limit + 4, limit))


def _get_download_executor():
    global _download_executor, _download_executor_workers
    workers = _get_executor_worker_count()
    with _executor_lock:
        if _download_executor is None or _download_executor_workers != workers:
            previous = _download_executor
            _download_executor = ThreadPoolExecutor(max_workers=workers)
            _download_executor_workers = workers
            g.log(
                f"Download executor workers set to {workers} "
                f"(stream limit {_get_max_download_workers()})",
                "debug",
            )
            if previous is not None:
                previous.shutdown(wait=False, cancel_futures=False)
        return _download_executor


def _submit_download(downloader, url, overwrite, headers):
    if downloader._is_canceled():
        downloader._handle_failure()
        return
    downloader.download(url, overwrite, headers)


class Manager:
    download_init_status = {
        "speed": "0 B/s",
        "progress": "0",
        "filename": "",
        "eta": "99h",
        "filesize": "0",
        "downloaded": "0",
        "state": "downloading",
    }

    def __init__(self):
        self.download_ids = []
        self.downloads = {}

    def remove_from_index(self, url_hash):
        """
        Removes requested task id from the global index
        :param url_hash:
        :return:
        """
        self.download_ids.remove(url_hash)
        g.set_runtime_setting("SDMIndex", ",".join(self.download_ids))

    def get_all_tasks_info(self):
        """
        Returns all currently active download task information
        :return: list
        """
        self._get_download_index()
        downloads = {url_hash: self.get_task_info(url_hash) for url_hash in self.download_ids}

        return downloads.values()

    def _get_download_index(self):
        """
        Refreshes download IDS from window index
        :return:
        """
        index = g.get_runtime_setting("SDMIndex")
        self.download_ids = [i for i in index.split(",") if i] if index is not None else []

    def _insert_into_index(self):
        """
        Inserts new ID into window index
        :return:
        """
        g.set_runtime_setting("SDMIndex", ",".join(self.download_ids))

    def update_task_info(self, url_hash, download_dict):
        """
        Updates download information stored in window property for download task
        :param url_hash: String
        :param download_dict: dict
        :return:
        """
        g.set_runtime_setting(f"sdm.{url_hash}", tools.construct_action_args(download_dict))

    def get_task_info(self, url_hash):
        """
        Takes a task hash and returns the information stored in the Window property
        :param url_hash: Sting
        :return: dict
        """
        try:
            return tools.deconstruct_action_args(g.get_runtime_setting(f"sdm.{url_hash}"))
        except Exception as e:
            raise TaskDoesNotExist(url_hash) from e

    def cancel_task(self, url_hash):
        """
        Sets status of download to canceled
        :param url_hash: string
        :return: None
        """
        g.log(f"Sending cancellation for task: {url_hash}", "debug")
        self._get_download_index()
        info = self.get_task_info(url_hash)
        info["canceled"] = True
        info["paused"] = False
        self.update_task_info(url_hash, info)

    def pause_task(self, url_hash):
        g.log(f"Pausing download task: {url_hash}", "debug")
        self._get_download_index()
        info = self.get_task_info(url_hash)
        if info.get("state") != "downloading":
            return
        info["paused"] = True
        info["state"] = "paused"
        info["speed"] = "-"
        info["eta"] = g.get_language_string(30934)
        self.update_task_info(url_hash, info)

    def resume_task(self, url_hash):
        g.log(f"Resuming download task: {url_hash}", "debug")
        self._get_download_index()
        info = self.get_task_info(url_hash)
        if info.get("state") != "paused":
            return
        info["paused"] = False
        info["state"] = "downloading"
        info["speed"] = "0 B/s"
        info["eta"] = "99h"
        self.update_task_info(url_hash, info)

    @staticmethod
    def _task_state(download):
        state = download.get("state", "downloading")
        try:
            if int(download.get("progress", 0)) >= 100:
                return "complete"
        except (TypeError, ValueError):
            pass
        return state

    def pause_all(self):
        for download in self.get_all_tasks_info():
            if self._task_state(download) == "downloading":
                self.pause_task(download["hash"])

    def resume_all(self):
        for download in self.get_all_tasks_info():
            if self._task_state(download) == "paused":
                self.resume_task(download["hash"])

    def cancel_all(self):
        for download in self.get_all_tasks_info():
            if self._task_state(download) in ("waiting", "downloading", "paused"):
                self.cancel_task(download["hash"])

    def create_download_task(self, url_hash, filename="", waiting=False):
        """
        Takes a download id and handles window property population
        :param url_hash: string
        :param filename: string
        :param waiting: bool
        :return: bool
        """
        with GlobalLock("PrismDownloaderUpdate"):
            self._get_download_index()
            if url_hash in self.download_ids:
                xbmcgui.Dialog().notification(g.ADDON_NAME, g.get_language_string(30644))
                return False
            self.download_ids.append(url_hash)
            self._insert_into_index()
            task_info = dict(self.download_init_status)
            task_info["hash"] = url_hash
            if filename:
                task_info["filename"] = filename
            if waiting:
                task_info["state"] = "waiting"
                task_info["speed"] = "-"
                task_info["eta"] = g.get_language_string(30933)
            self.downloads[url_hash] = task_info
            self.update_task_info(url_hash, task_info)
            return True

    def remove_download_task(self, url_hash):
        """
        Takes a download id a handles the clearing of download task from the window
        :param url_hash:
        :return: None
        """
        self._get_download_index()
        with GlobalLock("PrismDownloaderUpdate"):
            self._get_download_index()
            g.clear_runtime_setting(f"sdm.{url_hash}")
            if url_hash in self.download_ids:
                self.remove_from_index(url_hash)

    def clear_complete(self):
        for download in self.get_all_tasks_info():
            if int(download.get("progress", 0)) >= 100:
                self.remove_download_task(download["hash"])


class _DownloadTask:
    def __init__(self, filename=None, output_subdir=None):
        self.storage_location = (g.get_setting("download.location") or "").strip()
        self.output_subdir = (output_subdir or "").replace("\\", "/").strip("/")
        self._basename = os.path.basename(parse.unquote(filename or ""))

        if self.storage_location and not xbmcvfs.exists(self.storage_location):
            xbmcvfs.mkdir(self.storage_location)

        self.manager = Manager()
        self.file_size = -1
        self.progress = -1
        self.speed = -1
        self.remaining_seconds = -1
        self._output_path = None
        self._canceled = False
        self._elapsed_time = 0
        self.bytes_consumed = 0
        self._output_file = None
        self.output_filename = self._basename
        self._start_time = CLOCK()
        self.status = "Starting"
        self.url_hash = ""

    def prepare_task(self, url):
        if not self._basename:
            self._basename = parse.unquote(url.split("/")[-1].split("?")[0])
        self._output_path = join_download_path(self.storage_location, self.output_subdir, self._basename)
        self.output_filename = (
            f"{self.output_subdir}/{self._basename}".replace("\\", "/")
            if self.output_subdir
            else self._basename
        )
        self.url_hash = tools.md5_hash(url)
        return self.manager.create_download_task(self.url_hash, filename=self.output_filename, waiting=True)

    def download(self, url, overwrite=False, headers=None):
        """

        :param url: Web Path to file eg:(http://google.com/images/randomimage.jpeg)
        :param overwrite: opt. This will trigger a removal any conflicting files prior to download
        :return: Bool - True = Completed successfully / False = Cancelled
        """
        if self._is_canceled():
            self._handle_failure()
            return False

        g.log(f"Starting download from {url}")
        if not url or not url.startswith("http"):
            raise InvalidWebPath()

        if not self._basename:
            self._basename = parse.unquote(url.split("/")[-1].split("?")[0])
        self._output_path = join_download_path(self.storage_location, self.output_subdir, self._basename)
        self.output_filename = (
            f"{self.output_subdir}/{self._basename}".replace("\\", "/")
            if self.output_subdir
            else self._basename
        )
        g.log(f"Downloading {url} to {self._output_path}")
        output_file = self._create_file(url, overwrite)
        self._output_file = output_file
        g.log(f"Created {self._output_path}")
        head = requests.head(url, headers=headers, allow_redirects=True)

        if not head.ok:
            g.log("Server did not respond correctly to the head request")
            self._handle_failure()
            raise requests.exceptions.ConnectionError(head.status_code)

        self.url_hash = tools.md5_hash(url)
        self.file_size = int(head.headers.get("content-length", None))
        self.file_size_display = self.get_display_size(self.file_size)
        self.progress = 0
        self.speed = 0
        self.status = "downloading"

        slots = _get_download_slots()
        slot_held = False
        slots.acquire()
        slot_held = True
        try:
            if self._is_canceled():
                self._handle_failure()
                return False
            if not self._activate_download_in_dm():
                g.log("Failed to register download manager task", "error")
                self._handle_failure()
                return False

            response = requests.get(url, headers=headers, stream=True)
            for chunk in response.iter_content(1024 * 1024):
                while self._is_paused():
                    if slot_held:
                        slots.release()
                        slot_held = False
                    if g.abort_requested():
                        self._handle_failure()
                        g.log(
                            f"Shutdown requested - Cancelling download: {self.output_filename}",
                            "warning",
                        )
                        self.cancel_download()
                    if self._is_canceled():
                        g.log(
                            f"User cancellation - Cancelling download: {self.output_filename}",
                            "warning",
                        )
                        self.cancel_download()
                        self.status = "canceled"
                        return False
                    time.sleep(0.25)

                if not slot_held:
                    slots.acquire()
                    slot_held = True
                    if self._is_canceled():
                        self._handle_failure()
                        return False

                if g.abort_requested():
                    self._handle_failure()
                    g.log(
                        f"Shutdown requested - Cancelling download: {self.output_filename}",
                        "warning",
                    )
                    self.cancel_download()
                if self._is_canceled():
                    g.log(
                        f"User cancellation - Cancelling download: {self.output_filename}",
                        "warning",
                    )
                    self.cancel_download()
                    self.status = "canceled"
                    return False
                if not chunk:
                    continue
                if result := output_file.write(chunk):
                    self._update_status(len(chunk))

                else:
                    self._handle_failure()
                    self.status = "failed"
                    g.log(
                        f"Failed to fetch chunk from remote server - Cancelling download: {self.output_filename}",
                        "error",
                    )
                    xbmcgui.Dialog().notification(
                        g.ADDON_NAME,
                        g.get_language_string(30643).format(self.output_filename),
                    )
                    raise GeneralIOError(self.output_filename)
            if self._output_file:
                self._output_file.close()
                self._output_file = None
            g.log(f"Download complete: {self._output_path}")
            self._output_path = move_to_local_library(self._output_path)
            local_root = (g.get_setting('local.location') or '').strip()
            if g.get_bool_setting('download.automoveToLocal') and local_root:
                try:
                    self.output_filename = os.path.relpath(
                        self._output_path, tools.validate_path(local_root)
                    ).replace("\\", "/")
                except ValueError:
                    pass
            self.manager.update_task_info(
                self.url_hash,
                {
                    "speed": "-",
                    "progress": 100,
                    "filename": self.output_filename,
                    "eta": "00:00:00",
                    "filesize": self.file_size_display,
                    "downloaded": self.file_size_display,
                    "hash": self.url_hash,
                    "state": "complete",
                },
            )
            xbmcgui.Dialog().notification(
                g.ADDON_NAME, g.get_language_string(30642).format(self.output_filename)
            )
            return True
        finally:
            if slot_held:
                slots.release()

    def _activate_download_in_dm(self):
        """
        :return: bool
        """
        try:
            info = self.manager.get_task_info(self.url_hash)
        except TaskDoesNotExist:
            return self.manager.create_download_task(self.url_hash, filename=self.output_filename)
        info["state"] = "downloading"
        info["filename"] = self.output_filename
        info["speed"] = "0 B/s"
        info["progress"] = 0
        info["eta"] = "99h"
        info.pop("paused", None)
        self.manager.update_task_info(self.url_hash, info)
        return True

    def _is_paused(self):
        try:
            return self.manager.get_task_info(self.url_hash).get("paused", False)
        except TaskDoesNotExist:
            return False

    def _is_canceled(self):
        """
        :return: bool
        """
        try:
            return self.manager.get_task_info(self.url_hash).get("canceled", False)
        except TaskDoesNotExist:
            return True

    def _create_file(self, url, overwrite):

        """
        Confirms the paths and returns a file object
        :param url:
        :return: xbmcvfs.File Object
        """
        output_path = tools.validate_path(self._output_path)

        if xbmcvfs.exists(output_path):
            if not overwrite:
                raise FileAlreadyExists(output_path)
            if not xbmcvfs.delete(output_path):
                raise GeneralIOError(output_path)

        return xbmcvfs.File(output_path, "w")

    def _update_status(self, chunk_size):

        """
        :param chunk_size: int
        :return: None
        """

        self.bytes_consumed += chunk_size
        self.progress = int((float(self.bytes_consumed) / self.file_size) * 100)
        self.speed = self.bytes_consumed / (CLOCK() - self._start_time)
        self.remaining_seconds = float(self.file_size - self.bytes_consumed) / self.speed
        self.manager.update_task_info(
            self.url_hash,
            {
                "speed": self.get_display_speed(),
                "progress": self.progress,
                "filename": self.output_filename,
                "eta": self.get_remaining_time_display(),
                "filesize": self.file_size_display,
                "downloaded": self.get_display_size(self.bytes_consumed),
                "hash": self.url_hash,
                "state": "downloading",
            },
        )

    @staticmethod
    def get_display_size(size_bytes):
        size_names = ("B", "KB", "MB", "GB", "TB")
        size = 0.0
        name_idx = 0

        if size_bytes is not None and size_bytes > 0:
            name_idx = int(math.floor(math.log(size_bytes, 1024)))
            if name_idx > (last_size_value := len(size_names) - 1):
                name_idx = last_size_value
            chunk = math.pow(1024, name_idx)
            size = round(size_bytes / chunk, 2)

        return f"{size} {size_names[name_idx]}"

    def get_display_speed(self):

        """
        Returns a display friendly version of the current speed
        :return: String
        """

        speed = self.speed
        speed_categories = ["B/s", "KB/s", "MB/s"]
        if self.progress >= 100:
            return "-"
        for i in speed_categories:
            if speed < 1024:
                return f"{tools.safe_round(speed, 2)} {i}"
            else:
                speed = speed / 1024

    def get_remaining_time_display(self):
        """
        Returns a display friendly version of the remaining time
        :return: String
        """

        seconds = self.remaining_seconds
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def cancel_download(self):
        """
        Stops download stream and runs cleanup
        :return: None
        """

        self._canceled = True
        self._handle_failure()

    def _handle_failure(self):

        """
        Handle removal of any files in the event of a cancellation or error
        :return: None
        """
        self.manager.remove_download_task(self.url_hash)
        if self._output_file:
            self._output_file.close()

        if self._output_path and xbmcvfs.exists(self._output_path):
            result = xbmcvfs.delete(self._output_path)
            if not result:
                raise GeneralIOError(f"Failed to delete file: {self._output_path}")


class _DownloadBase:
    def __init__(self, source, item_information=None):
        self.source = source
        self.item_information = item_information
        self.average_speed = "0 B/s"
        self.progress = 0
        self.downloaders = []
        self.valid_source_types = []

    def _confirm_source_downloadable(self):
        if (source_type := self.source.get("type")) not in self.valid_source_types:
            raise InvalidSourceType(source_type)

    def _initiate_download(self, url, output_filename=None, headers=None, inner_path=None):
        """
        Creates Downloader Class and adds it to current download thread pool
        :param url: String
        :param output_filename: String
        :return: None
        """
        basename = os.path.basename(parse.unquote(output_filename or url.split("/")[-1].split("?")[0]))
        subdir = ""
        if self.item_information:
            subdir = build_download_subdir(self.item_information, basename, inner_path=inner_path)
        downloader = _DownloadTask(basename, output_subdir=subdir)
        if not downloader.prepare_task(url):
            return False
        self.downloaders.append(downloader)
        _get_download_executor().submit(_submit_download, downloader, url, True, headers)
        return True

    def _get_single_item_info(self, source):
        """

        :param source:
        :return:
        """
        g.log(source, "debug")
        return source

    @abc.abstractmethod
    def _resolve_file_url(self, file):
        """
        :param file: Dict
        :return: String
        """

    @abc.abstractmethod
    def download(self):
        """
        Begins required download type for provided source
        :return:
        """


def _download_file_size_label(file_dict: dict) -> str:
    for key in ("bytes", "filesize", "size"):
        value = file_dict.get(key)
        if value is None:
            continue
        try:
            size_bytes = int(value)
        except (TypeError, ValueError):
            continue
        if size_bytes > 0:
            return tools.bytes_size_display(size_bytes)
    return "-"


def _build_download_picker_entries(available_files: list[tuple]) -> list[dict]:
    entries = []
    for file_dict, filename in available_files:
        path = (file_dict.get("path") or filename or "").replace("\\", "/")
        entries.append(
            {
                "file": file_dict,
                "filename": filename,
                "path": path,
                "size_label": _download_file_size_label(file_dict),
                "selected": False,
            }
        )
    return entries


class _DebridDownloadBase(_DownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        self.debrid_module = None
        self.valid_source_types = ["torrent", "hoster", "cloud"]
        self._confirm_source_downloadable()

    @abc.abstractmethod
    def _fetch_available_files(self):
        """
        Fetches available files in source and returns a list of (path, filename) tuples
        :return: List
        """

    def _get_selected_files(self):
        """
        :return:
        """
        if self.source.get("type") in ["hoster", "cloud"]:
            return self.source
        available_files = self._fetch_available_files()
        available_files = [
            (i, i["path"].split("/")[-1]) for i in available_files if source_utils.is_file_ext_valid(i["path"])
        ]
        if len(available_files) == 1:
            return [available_files[0]]
        available_files = sorted(available_files, key=lambda k: k[1])
        from resources.lib.gui.windows.download_file_picker import pick_download_files

        selection = pick_download_files(_build_download_picker_entries(available_files))
        if selection is None:
            return []
        return selection

    def _resolver_setup(self, selected_files):
        """

        :param selected_files:
        :return:
        """
        return selected_files

    def _handle_potential_multi(self):
        """
        Requests selection of files from user and begins download tasks
        :return: True if at least one download was started
        """
        selected_files = self._get_selected_files()
        selected_files = self._resolver_setup(selected_files)
        if not selected_files:
            return False

        started = False
        for i in selected_files:
            inner_path = i[0].get("path") if isinstance(i[0], dict) else None
            if self._initiate_download(self._resolve_file_url(i), i[1], inner_path=inner_path):
                started = True
        return started

    def download(self):
        """
        Begins required download type for provided source
        :return: True if at least one download was started
        """
        if self.source["type"] not in ["hoster", "cloud"]:
            return self._handle_potential_multi()

        source_info = self._get_single_item_info(self.source)
        return self._initiate_download(
            self._resolve_file_url([source_info]),
            self.source["release_title"],
        )


class _PremiumizeDownloader(_DebridDownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        self.debrid_module = Premiumize()
        self.available_files = []

    def _fetch_available_files(self):
        if self.source["type"] in ["hoster", "cloud"]:
            return self.source
        self.available_files = self.debrid_module.direct_download(self.source["magnet"])["content"]
        return self.available_files

    def _get_single_item_info(self, source):
        source = super()._get_single_item_info(source)
        return self.debrid_module.item_details(source["url"])

    def _resolve_file_url(self, file):
        return file[0]["link"]


class _RealDebridDownloader(_DebridDownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        self.debrid_module = RealDebrid()
        self.available_files = []
        self.torrent_info = []

    def _fetch_available_files(self):
        try:
            availability = self.debrid_module.check_hash(self.source["hash"])[self.source["hash"]]
            self.torrent_info = availability["torrent_info"]
            availability = sorted(availability["rd"], key=lambda k: len(k.values()))
        except Exception as e:
            raise SourceNotAvailable from e
        self.available_files = [
            {
                "path": value["filename"],
                "index": key,
                "bytes": value.get("filesize"),
            }
            for rd_item in availability
            for key, value in rd_item.items()
        ]

        return self.available_files

    def _resolve_file_url(self, file):
        item = file[0]
        if isinstance(item, str):
            link = item
        elif isinstance(item, dict):
            link = item.get("url") or item.get("link")
        else:
            link = None
        if not link or not isinstance(link, str):
            raise SourceNotAvailable()
        if link.startswith("http") and ".download.real-debrid.com/" in link:
            return link
        return self.debrid_module.resolve_hoster(link)

    def _resolver_setup(self, selected_files):
        if self.source.get("type") in ["hoster", "cloud"]:
            return [(self.source.get("url", ""), self.source.get("release_title"))]
        
        info = self.torrent_info
        remote_files = {str(i["id"]): idx for idx, i in enumerate(info["files"])}
        selected_files = [(remote_files[i[0]["index"]], i[1]) for i in selected_files]
        return [(info["links"][i[0]], i[1]) for i in selected_files]

    def _get_single_item_info(self, source):
        source = super()._get_single_item_info(source)
        return source


class _AllDebridDownloader(_DebridDownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        self.debrid_module = AllDebrid()
        self.available_files = []

    def _fetch_available_files(self):
        self.magnet_id = self.debrid_module.upload_magnet(self.source['hash'])["magnets"][0]["id"]
        status = self.debrid_module.magnet_status(self.magnet_id)['magnets']
        if status["status"] != "Ready":
            raise UnexpectedResponse(status)
        return [{'path': i['filename'], 'url': i['link']} for i in status['links']]

    def _get_single_item_info(self, source):
        source = super()._get_single_item_info(source)
        return source

    def _resolve_file_url(self, file):
        return self.debrid_module.resolve_hoster(file[0]["url"])


class _TorBoxDownloader(_DebridDownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        from resources.lib.debrid.torbox import TorBox

        self.debrid_module = TorBox()
        self.torrent_id = None

    def _fetch_available_files(self):
        magnet = self.source.get("magnet")
        if not magnet:
            magnet = f"magnet:?xt=urn:btih:{self.source['hash']}"
        torrent_data = self.debrid_module.get_torrent_files(magnet=magnet)
        if not torrent_data or not torrent_data.get("files"):
            raise UnexpectedResponse(torrent_data)
        self.torrent_id = torrent_data.get("torrent_id")
        return [
            {
                "path": f.get("short_name") or f.get("name", ""),
                "id": f.get("id"),
                "torrent_id": self.torrent_id,
                "bytes": f.get("size") or f.get("bytes"),
            }
            for f in torrent_data["files"]
        ]

    def _get_single_item_info(self, source):
        return super()._get_single_item_info(source)

    def _resolve_file_url(self, file):
        item = file[0]
        url = item.get("url", "")

        if url.startswith("http"):
            stream_url = self.debrid_module.resolve_hoster(url)
        elif item.get("is_usenet"):
            stream_url = self.debrid_module.resolve_usenet(url)
        elif "," in url:
            parent_id, file_id = url.split(",", 1)
            stream_url = self.debrid_module.resolve_torrent_file(parent_id, file_id)
        else:
            torrent_id = item.get("torrent_id") or self.torrent_id
            file_id = item.get("id")
            if torrent_id is None or file_id is None:
                raise SourceNotAvailable()
            stream_url = self.debrid_module.resolve_torrent_file(torrent_id, file_id)

        if not stream_url or not str(stream_url).startswith("http"):
            raise SourceNotAvailable()
        return stream_url


class _OffCloudDownloader(_DebridDownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        from resources.lib.debrid.offcloud import OffCloud

        self.debrid_module = OffCloud()
        self.request_id = None

    def _fetch_available_files(self):
        magnet = self.source.get("magnet")
        if not magnet:
            magnet = f"magnet:?xt=urn:btih:{self.source['hash']}"
        torrent_data = self.debrid_module.get_torrent_files(magnet=magnet)
        if not torrent_data or not torrent_data.get("files"):
            raise UnexpectedResponse(torrent_data)
        self.request_id = torrent_data.get("request_id")
        return [
            {
                "path": f.get("path") or f.get("name", ""),
                "id": f.get("id"),
                "request_id": self.request_id,
                "url": f.get("url"),
                "bytes": f.get("size", 0),
            }
            for f in torrent_data["files"]
        ]

    def _resolve_file_url(self, file):
        item = file[0]
        if item.get("url"):
            return item["url"]
        return self.debrid_module.resolve_torrent_file(
            item.get("request_id") or self.request_id,
            item.get("id"),
            item,
        )


class _DirectDownloader(_DownloadBase):
    def __init__(self, source, item_information=None):
        super().__init__(source, item_information)
        self.valid_source_types = ["direct"]
        self._confirm_source_downloadable()

    def _get_single_item_info(self, source):
        source = super()._get_single_item_info(source)
        return source

    def _resolve_file_url(self, file):
        return file[0]['url']

    def download(self):
        source_info = self._get_single_item_info(self.source)
        return self._initiate_download(
            self._resolve_file_url([source_info]),
            f"{source_info['release_title']}{source_info.get('filetype', '')}",
            headers=source_info.get("headers"),
        )


def _get_debrid_downloader_class(source, item_information=None):
    """
    Takes source and returns the relevant debrid class for source
    :param source: dict
    :return: object
    """
    debrid_providers = {
        "premiumize": _PremiumizeDownloader,
        "real_debrid": _RealDebridDownloader,
        "all_debrid": _AllDebridDownloader,
        "torbox": _TorBoxDownloader,
        "offcloud": _OffCloudDownloader,
    }
    return debrid_providers[source["debrid_provider"]](source, item_information)


def create_task(source, item_information=None):
    """
    Takes source and auto fires of download process
    :param source: dict
    :param item_information: dict - metadata for the item being downloaded
    :return: True if at least one download was started
    """
    if (source_type := source.get("type")) not in VALID_SOURCE_TYPES:
        raise InvalidSourceType(source_type)

    if source_type == "direct":
        downloader_class = _DirectDownloader(source, item_information)
    else:
        downloader_class = _get_debrid_downloader_class(source, item_information)

    return bool(downloader_class.download())
