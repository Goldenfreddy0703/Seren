import xbmcgui

from resources.lib.gui.windows.base_window import BaseWindow
from resources.lib.modules import catalog_profiles, locale_playback, playback_streams
from resources.lib.modules.globals import g


class LocaleSelect(BaseWindow):
    """Per-catalog playback audio/subtitle language profiles."""

    CATALOG_CONTROLS = {6101: "movie", 6102: "tv", 6103: "anime"}
    AUDIO_LIST = 8000
    SUBTITLE_LIST = 9000
    USE_KODI_TOGGLE = 6200
    CLOSE_CONTROL = 6001
    RESET_CONTROL = 6104
    SUB_PRESET_CONTROL = 6105
    DUB_PRESET_CONTROL = 6106
    KEYWORD_CONTROL = 6107
    CUSTOM_KEYWORD_CONTROL = 6109

    PLAYBACK_SETTINGS_SECTION = 7

    def __init__(self, xml_file, xml_location, catalog=None):
        super().__init__(xml_file, xml_location)

        catalog_profiles.ensure_migrated()
        self.catalog = catalog_profiles.normalize_catalog(catalog or catalog_profiles.get_last_catalog())
        self._audio_options = locale_playback.language_options("audio")
        self._subtitle_options = locale_playback.language_options("subtitle")
        self.audio_list = None
        self.subtitle_list = None

    def onInit(self):
        self.audio_list = self.getControlList(self.AUDIO_LIST)
        self.subtitle_list = self.getControlList(self.SUBTITLE_LIST)
        self._ensure_list_populated(self.audio_list, self._audio_options)
        self._ensure_list_populated(self.subtitle_list, self._subtitle_options)
        self._update_catalog_properties()
        self._refresh_catalog_state()
        self.set_default_focus(control_id=self.USE_KODI_TOGGLE)
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

    def _refresh_anime_stream_properties(self):
        if self.catalog != "anime":
            return
        keyword_mode = playback_streams.get_subtitle_keyword_mode()
        self.setProperty("locale.subtitlekeyword", keyword_mode)
        self.setProperty("locale.subtitlekeyword.label", playback_streams.subtitle_keyword_label(keyword_mode))
        self.setProperty("locale.subtitlekeyword.custom", playback_streams.get_custom_subtitle_keyword())

    def _cycle_subtitle_keyword(self):
        if self.catalog != "anime":
            return
        next_mode = playback_streams.cycle_subtitle_keyword_mode()
        if next_mode == playback_streams.KEYWORD_CUSTOM:
            playback_streams.prompt_custom_subtitle_keyword()
        self._refresh_anime_stream_properties()

    def _edit_custom_subtitle_keyword(self):
        if self.catalog != "anime":
            return
        if playback_streams.prompt_custom_subtitle_keyword():
            self._refresh_anime_stream_properties()

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

    def _switch_catalog(self, new_catalog):
        new_catalog = catalog_profiles.normalize_catalog(new_catalog)
        if new_catalog == self.catalog:
            return
        self.catalog = new_catalog
        self._update_catalog_properties()
        self._refresh_catalog_state()

    def _reset_catalog(self):
        locale_playback.reset_catalog_locale(self.catalog)
        if self.catalog == "anime":
            playback_streams.reset_anime_stream_settings()
        self._refresh_catalog_state()

    def _apply_anime_preset(self, preset: str):
        if self.catalog != "anime":
            return
        if not catalog_profiles.apply_anime_preset(preset):
            return
        self._refresh_catalog_state()
        label = g.get_language_string(30942 if preset == "sub" else 30943)
        g.notification(g.ADDON_NAME, label)

    def handle_action(self, action, control_id=None):
        if action != 7:
            return

        if control_id == self.USE_KODI_TOGGLE:
            self._toggle_use_kodi()
        elif control_id == self.AUDIO_LIST:
            self._select_from_list(self.audio_list, self._audio_options, "audio")
        elif control_id == self.SUBTITLE_LIST:
            self._select_from_list(self.subtitle_list, self._subtitle_options, "subtitle")
        elif control_id in self.CATALOG_CONTROLS:
            self._switch_catalog(self.CATALOG_CONTROLS[control_id])
        elif control_id == self.RESET_CONTROL:
            self._reset_catalog()
        elif control_id == self.SUB_PRESET_CONTROL:
            self._apply_anime_preset("sub")
        elif control_id == self.DUB_PRESET_CONTROL:
            self._apply_anime_preset("dub")
        elif control_id == self.KEYWORD_CONTROL:
            self._cycle_subtitle_keyword()
        elif control_id == self.CUSTOM_KEYWORD_CONTROL:
            self._edit_custom_subtitle_keyword()
        elif control_id == self.CLOSE_CONTROL:
            self.close()

    def close(self):
        catalog_profiles.set_last_catalog(self.catalog)
        super().close()
        g.open_addon_settings(self.PLAYBACK_SETTINGS_SECTION)
