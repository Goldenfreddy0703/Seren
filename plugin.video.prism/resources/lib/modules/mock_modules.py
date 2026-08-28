import xbmc

from resources.lib.gui.windows.download_manager import DownloadManager
from resources.lib.gui.windows.get_sources_window import GetSourcesWindow
from resources.lib.gui.windows.resolver_window import ResolverWindow
from resources.lib.modules.globals import g


class Resolver(ResolverWindow):
    def _resolve_source(self):
        pass

    def onAction(self, action):
        self.close()


class GetSources(GetSourcesWindow):
    class MockScraperClass:
        canceled = False

    def onInit(self):
        super().onInit()
        self.set_scraper_class(self.MockScraperClass())
        if self.getProperty("mock_preview") == "true":
            import threading

            threading.Thread(
                target=self._run_mock_preview,
                daemon=True,
                name="prism-mock-get-sources",
            ).start()

    def onAction(self, action):
        if action.getId() in self.action_exitkeys_id:
            self.close()

    def _run_mock_preview(self):
        import time

        from resources.lib.gui.mock_windows import mock_source_statistics

        xbmc.sleep(1500)
        self.setProperty("has_torrent_providers", "true")
        self.setProperty("has_hoster_providers", "true")
        self.setProperty("has_adaptive_providers", "true")
        self.setProperty("has_direct_providers", "true")
        self.setProperty("has_cloud_scrapers", "true")
        start_time = time.time()
        timeout = 15
        self.setProperty("process_started", "true")
        total_providers = len(mock_source_statistics[0]["remainingProviders"])
        for stats in mock_source_statistics:
            runtime = time.time() - start_time
            self.setProperty("runtime", f"{round(runtime, 2)} {g.get_language_string(30554)}")
            timeout_progress = int(100 - float(1 - (runtime / float(timeout))) * 100)
            self.setProperty("timeout_progress", str(timeout_progress))
            self.setProgress(
                int(100 - (len(stats["remainingProviders"]) / float(total_providers) * 100))
            )
            self.update_properties(stats)
            xbmc.sleep(750)
        xbmc.sleep(10000)
        self.close()


class KodiPlayer:
    def __init__(self):
        self.playing_file = "http://testurl.com/Barry.S02E01.1080p.TVShows.mkv"

    def isPlaying(self):
        return True

    def getPlayingFile(self):
        return self.playing_file

    def getTotalTime(self):
        return 2048

    def getTime(self):
        return 2045

    def pause(self):
        """
        Over write normal behaivour
        :return:
        """
        pass

    def seekTime(self, time):
        """
        Over write normal behaivour
        :return:
        """
        pass

    def stop(self):
        """
        Over write normal behaivour
        :return:
        """
        pass


class DownloadManagerWindow(DownloadManager):
    def __init__(self, xml_file, location, item_information=None, mock_downloads=None):
        super().__init__(xml_file, location, item_information)
        self.downloads = mock_downloads or []

    def update_download_info(self):
        pass
