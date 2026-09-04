import xbmcgui

from resources.lib.gui.windows.base_window import BaseWindow
from resources.lib.modules import catalog_profiles, locale_playback, playback_streams
from resources.lib.modules.globals import g


class LocaleSelect(BaseWindow):
    """Per-catalog playback audio/subtitle language profiles."""

    CATALOG_CONTROLS = {6101: "movie", 6102: "tv", 6103: "anime"}
    AUDIO_LIST = 8000
    SUBTITLE_LIST = 9000
    KEYWORD_LIST = 10000
    USE_KODI_TOGGLE = 6200
    KEYWORD_TOGGLE = 6210
    CLOSE_CONTROL = 6001
    RESET_CONTROL = 6104
    SUB_PRESET_CONTROL = 6105
    DUB_PRESET_CONTROL = 6106

    PLAYBACK_SETTINGS_SECTION = 7

    def __init__(self, xml_file, xml_location, catalog=None):
        super().__init__(xml_file, xml_location)

        catalog_profiles.ensure_migrated()
        self.catalog = catalog_profiles.normalize_catalog(catalog or catalog_profiles.get_last_catalog())
        self._audio_options = locale_playback.language_options("audio")
        self._subtitle_options = locale_playback.language_options("subtitle")
        self._keyword_options = playback_streams.keyword_filter_options()
        self.audio_list = None
        self.subtitle_list = None
        self.keyword_list = None

    def onInit(self):
        self.audio_list = self.getControlList(self.AUDIO_LIST)
        self.subtitle_list = self.getControlList(self.SUBTITLE_LIST)
        self.keyword_list = self.getControlList(self.KEYWORD_LIST)
        self._ensure_list_populated(self.audio_list, self._audio_options)
        self._ensure_list_populated(self.subtitle_list, self._subtitle_options)
        self._ensure_list_populated(self.keyword_list, self._keyword_options)
        self._update_catalog_properties()
        self._refresh_catalog_state()
        self.set_default_focus(control_id=self.USE_KODI_TOGGLE)
        self._update_track_filter_navigation()
        super().onInit()

    def _update_catalog_properties(self):
        self.setProperty("profile.catalog", self.catalog)
        for catalog in catalog_profiles.CATALOGS:
            self.setProperty(f"profile.catalog.{catalog}.active", str(catalog == self.catalog))

    def _refresh_catalog_state(self):
        use_kodi = locale_playback.uses_kodi_defaults(self.catalog)
        self.setProperty("locale.usekodi", str(use_kodi))
        self._mark_selected(self.audio_list, locale_playback.get_catalog_audio(self.catalog))
        self._mark_selected(self.subtitle_list, locale_playback.get_catalog_subtitle(self.catalog))
        self._refresh_anime_stream_properties()
        self._update_track_filter_navigation()

    def _track_filter_enabled(self) -> bool:
        return (
            self.catalog == "anime"
            and self.getProperty("locale.trackfilter.enabled") == "True"
        )

    def _update_track_filter_navigation(self):
        """Wire close / filter list / Kodi toggle when the filter panel is shown."""
        try:
            close_btn = self.getControl(self.CLOSE_CONTROL)
            kodi_toggle = self.getControl(self.USE_KODI_TOGGLE)
            keyword_list = self.getControl(self.KEYWORD_LIST)
            subtitle_list = self.getControl(self.SUBTITLE_LIST)
            sidebar_movie = self.getControl(6101)
        except RuntimeError:
            return

        if self._track_filter_enabled():
            close_btn.controlDown(keyword_list)
            keyword_list.setNavigation(close_btn, kodi_toggle, subtitle_list, sidebar_movie)
            kodi_toggle.controlUp(keyword_list)
        else:
            close_btn.controlDown(kodi_toggle)
            kodi_toggle.controlUp(close_btn)

    def _refresh_anime_stream_properties(self):
        if self.catalog != "anime":
            self.setProperty("locale.trackfilter.enabled", "False")
            return

        keyword_mode = playback_streams.get_subtitle_keyword_mode()
        enabled = playback_streams.keyword_mode_active(keyword_mode)
        self.setProperty("locale.trackfilter.enabled", str(enabled))
        if enabled and self.keyword_list is not None:
            self._mark_selected(self.keyword_list, keyword_mode)

    @staticmethod
    def _ensure_list_populated(list_control, options):
        if list_control.size() == len(options):
            return

        list_control.reset()
        for label, value in options:
            item = xbmcgui.ListItem(label=label)
            item.setProperty("label", label)
            item.setProperty("stored_value", value)
            item.setProperty("value", "False")
            list_control.addItem(item)

    @staticmethod
    def _mark_selected(list_control, selected_value):
        selected_index = 0
        for index in range(list_control.size()):
            item = list_control.getListItem(index)
            is_selected = item.getProperty("stored_value") == selected_value
            item.setProperty("value", str(is_selected))
            if is_selected:
                selected_index = index
        if list_control.size():
            list_control.selectItem(selected_index)

    def _toggle_use_kodi(self):
        use_kodi = not locale_playback.uses_kodi_defaults(self.catalog)
        locale_playback.set_use_kodi_defaults(self.catalog, use_kodi)
        self.setProperty("locale.usekodi", str(use_kodi))

    def _toggle_track_filter(self):
        if self.catalog != "anime":
            return
        keyword_mode = playback_streams.get_subtitle_keyword_mode()
        if playback_streams.keyword_mode_active(keyword_mode):
            playback_streams.set_subtitle_keyword_mode(playback_streams.KEYWORD_OFF)
        else:
            playback_streams.set_subtitle_keyword_mode(playback_streams.KEYWORD_SIGNS_SONGS)
        self._refresh_anime_stream_properties()
        self._update_track_filter_navigation()

    def _select_from_list(self, list_control, options, kind):
        index = list_control.getSelectedPosition()
        if index < 0 or index >= len(options):
            return

        _label, value = options[index]
        if kind == "audio":
            locale_playback.set_catalog_audio(self.catalog, value)
        else:
            locale_playback.set_catalog_subtitle(self.catalog, value)
        self._mark_selected(list_control, value)

    def _select_keyword_mode(self):
        if self.catalog != "anime":
            return
        if not playback_streams.keyword_mode_active():
            return

        index = self.keyword_list.getSelectedPosition()
        if index < 0 or index >= len(self._keyword_options):
            return

        _label, value = self._keyword_options[index]
        if value == playback_streams.KEYWORD_CUSTOM:
            if not playback_streams.prompt_custom_subtitle_keyword():
                self._refresh_anime_stream_properties()
                return
            if not playback_streams.get_custom_subtitle_keyword():
                self._refresh_anime_stream_properties()
                return
            self._keyword_options = playback_streams.keyword_filter_options()
            self._ensure_list_populated(self.keyword_list, self._keyword_options)

        playback_streams.set_subtitle_keyword_mode(value)
        self._mark_selected(self.keyword_list, value)

    def _switch_catalog(self, new_catalog):
        new_catalog = catalog_profiles.normalize_catalog(new_catalog)
        if new_catalog == self.catalog:
            return
        self.catalog = new_catalog
        self._update_catalog_properties()
        self._refresh_catalog_state()
        self._update_track_filter_navigation()

    def _reset_catalog(self):
        locale_playback.reset_catalog_locale(self.catalog)
        if self.catalog == "anime":
            playback_streams.reset_anime_stream_settings()
        self._refresh_catalog_state()

    def _apply_anime_preset(self, preset: str):
        if self.catalog != "anime":
            return
        if not catalog_profiles.apply_anime_playback_preset(preset):
            return
        self._refresh_catalog_state()
        label = g.get_language_string(30942 if preset == "sub" else 30943)
        g.notification(g.ADDON_NAME, label)

    def handle_action(self, action, control_id=None):
        if action != 7:
            return

        if control_id == self.USE_KODI_TOGGLE:
            self._toggle_use_kodi()
        elif control_id == self.KEYWORD_TOGGLE:
            self._toggle_track_filter()
        elif control_id == self.AUDIO_LIST:
            self._select_from_list(self.audio_list, self._audio_options, "audio")
        elif control_id == self.SUBTITLE_LIST:
            self._select_from_list(self.subtitle_list, self._subtitle_options, "subtitle")
        elif control_id == self.KEYWORD_LIST:
            self._select_keyword_mode()
        elif control_id in self.CATALOG_CONTROLS:
            self._switch_catalog(self.CATALOG_CONTROLS[control_id])
        elif control_id == self.RESET_CONTROL:
            self._reset_catalog()
        elif control_id == self.SUB_PRESET_CONTROL:
            self._apply_anime_preset("sub")
        elif control_id == self.DUB_PRESET_CONTROL:
            self._apply_anime_preset("dub")
        elif control_id == self.CLOSE_CONTROL:
            self.close()

    def close(self):
        catalog_profiles.set_last_catalog(self.catalog)
        super().close()
        g.open_addon_settings(self.PLAYBACK_SETTINGS_SECTION)
