import xbmcgui

from resources.lib.common.source_utils import INFO_STRUCT
from resources.lib.gui.windows.base_window import BaseWindow
from resources.lib.modules import catalog_profiles
from resources.lib.modules.globals import g

# Explicit display order for tags that should not be alphabetised (audio first,
# broadest to narrowest, with the subtitle tag last). Tags absent from this map
# fall back to alphabetical ordering, leaving the other filter cards unchanged.
FILTER_ORDER = {
    "MULTI-AUDIO": 0,
    "DUAL-AUDIO": 1,
    "DUB": 3,
    "SUB": 2,
    "MULTI-SUB": 4,
}


class FilterSelect(BaseWindow):
    """
    Dialog to provide filter settings
    """

    CATALOG_CONTROLS = {6101: "movie", 6102: "tv", 6103: "anime"}
    SUB_PRESET_CONTROL = 6105
    DUB_PRESET_CONTROL = 6106

    def __init__(self, xml_file, xml_location, catalog=None):
        super().__init__(xml_file, xml_location)

        catalog_profiles.ensure_migrated()
        self.catalog = catalog_profiles.normalize_catalog(catalog or catalog_profiles.get_last_catalog())

        self.videocodec_list = None
        self.hdrcodec_list = None
        self.audiocodec_list = None
        self.audiochannels_list = None
        self.misc_list = None
        self.audiosublang_list = None

        self.current_filters = catalog_profiles.get_filters(self.catalog)

    def onInit(self):
        self.videocodec_list = self.getControlList(1000)
        self.hdrcodec_list = self.getControlList(2000)
        self.audiocodec_list = self.getControlList(3000)
        self.audiochannels_list = self.getControlList(4000)
        self.misc_list = self.getControlList(5000)
        self.audiosublang_list = self.getControlList(7000)

        self._update_catalog_properties()
        self._refresh_lists()

        super().onInit()

    def _update_catalog_properties(self):
        self.setProperty("profile.catalog", self.catalog)
        for catalog in catalog_profiles.CATALOGS:
            self.setProperty(f"profile.catalog.{catalog}.active", str(catalog == self.catalog))

    def _refresh_lists(self):
        self._populate_list(self.videocodec_list, "videocodec")
        self._populate_list(self.hdrcodec_list, "hdrcodec")
        self._populate_list(self.audiocodec_list, "audiocodec")
        self._populate_list(self.audiochannels_list, "audiochannels")
        self._populate_list(self.misc_list, "misc")
        self._populate_list(self.audiosublang_list, ("audiolang", "subtitlelang"))

    @staticmethod
    def _set_setting_item_properties(menu_item, setting):
        value = str(setting["value"])
        menu_item.setProperty("label", setting["label"])
        menu_item.setProperty("value", value)

    def _populate_list(self, codec_list, key):
        def _create_menu_item(setting):
            new_item = xbmcgui.ListItem(label=f"{setting['label']}")
            self._set_setting_item_properties(new_item, setting)
            return new_item

        keys = key if isinstance(key, (tuple, list)) else (key,)
        codecs = set().union(*(INFO_STRUCT[category] for category in keys))

        for idx, codec in enumerate(
            sorted(
                [
                    i
                    for i in codecs
                    if i
                    not in {
                        "SDR",
                    }
                ],
                key=lambda i: (FILTER_ORDER.get(i, len(FILTER_ORDER)), i),
            )
        ):
            info_item = {"label": codec, "value": codec in self.current_filters}
            if idx < codec_list.size():
                menu_item = codec_list.getListItem(idx)
                self._set_setting_item_properties(
                    menu_item,
                    info_item,
                )
            else:
                menu_item = _create_menu_item(info_item)
                codec_list.addItem(menu_item)

    def _flip_info(self, list_item):
        label = list_item.getLabel()
        if label in self.current_filters:
            self.current_filters.remove(label)
            list_item.setProperty("value", str(False))
        else:
            self.current_filters.add(label)
            list_item.setProperty("value", str(True))

    def _save_filters(self):
        catalog_profiles.save_filters(self.catalog, self.current_filters)

    def _switch_catalog(self, new_catalog):
        new_catalog = catalog_profiles.normalize_catalog(new_catalog)
        if new_catalog == self.catalog:
            return
        self._save_filters()
        self.catalog = new_catalog
        self.current_filters = catalog_profiles.get_filters(self.catalog)
        self._update_catalog_properties()
        self._refresh_lists()

    def _reset_catalog(self):
        catalog_profiles.reset_filters(self.catalog)
        self.current_filters = catalog_profiles.get_filters(self.catalog)
        self._refresh_lists()

    def _apply_anime_preset(self, preset: str):
        if self.catalog != "anime":
            return
        if not catalog_profiles.apply_anime_filter_preset(preset):
            return
        self.current_filters = catalog_profiles.get_filters(self.catalog)
        self._refresh_lists()
        label = g.get_language_string(30942 if preset == "sub" else 30943)
        g.notification(g.ADDON_NAME, label)

    def handle_action(self, action, control_id=None):
        if action == 7:
            if control_id in [1000, 2000, 3000, 4000, 5000, 7000]:
                lists = {
                    1000: self.videocodec_list,
                    2000: self.hdrcodec_list,
                    3000: self.audiocodec_list,
                    4000: self.audiochannels_list,
                    5000: self.misc_list,
                    7000: self.audiosublang_list,
                }

                li = lists.get(control_id).getSelectedItem()

                self._flip_info(li)
            elif control_id in self.CATALOG_CONTROLS:
                self._switch_catalog(self.CATALOG_CONTROLS[control_id])
            elif control_id == 6104:
                self._reset_catalog()
            elif control_id == self.SUB_PRESET_CONTROL:
                self._apply_anime_preset("sub")
            elif control_id == self.DUB_PRESET_CONTROL:
                self._apply_anime_preset("dub")
            elif control_id == 6001:
                self.close()

    def close(self):
        self._save_filters()
        catalog_profiles.set_last_catalog(self.catalog)
        super().close()
        g.open_addon_settings(6, 1)  # Open settings back where we were launched from
