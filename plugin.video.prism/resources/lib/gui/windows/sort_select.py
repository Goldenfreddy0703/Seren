from resources.lib.gui.windows.base_window import BaseWindow
from resources.lib.modules import catalog_profiles
from resources.lib.modules.globals import g

SORT_OPTIONS = {
    "sortmethod": [30513, 30237, 30252, 30570, 30251, 30571, 30572, 30573, 30575, 30840, 30841],
    # None, Resolution, Source Type, Debrid Provider, Size, Low Cam Sort, HEVC, DV/HDR, Audio Channels, Audio, Subtitles
    "none": [],
    "resolution": [],
    "sourcetypesort": [30581, 30249, 30470, 30057, 30058, 30631],
    # Other, Cloud, Adaptive, Torrents, Hosters, Direct
    "debridsort": [30513, 30134, 30135, 30333, 30718, 31027],
    # None, Premiumize, Real-Debrid, AllDebrid, TorBox, Offcloud
    "size": [],
    "cam": [],
    "hevc": [],
    "hdrsort": [30513, 30590, 30574],
    # None, DV, HDR
    "audiochannels": [],
    "audiosort": [30513, 30842, 30843, 30844, 30845],
    # None, Multi-Audio, Dual-Audio, Sub, Dub
    "subtitlesort": [30513, 30846],
    # None, Multi-Sub
}
SORT_METHODS = [
    "none",
    "resolution",
    "sourcetypesort",
    "debridsort",
    "size",
    "cam",
    "hevc",
    "hdrsort",
    "audiochannels",
    "audiosort",
    "subtitlesort",
]


class SortSelect(BaseWindow):
    """
    Dialog to provide filter settings
    """

    CATALOG_CONTROLS = {9201: "movie", 9202: "tv", 9203: "anime"}
    RESET_CONTROL = 9204
    SUB_PRESET_CONTROL = 9205
    DUB_PRESET_CONTROL = 9206

    def __init__(self, xml_file, xml_location, catalog=None):
        super().__init__(xml_file, xml_location)

        catalog_profiles.ensure_migrated()
        self.catalog = catalog_profiles.normalize_catalog(catalog or catalog_profiles.get_last_catalog())

        self.sort_lists = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000]
        self.sort_options = catalog_profiles.load_sort_options(self.catalog)
        self.max_level = 8

    def onInit(self):
        self._update_catalog_properties()
        self._populate_all_lists()
        self.set_default_focus(control_id=1001)
        super().onInit()

    def _update_catalog_properties(self):
        self.setProperty("profile.catalog", self.catalog)
        for catalog in catalog_profiles.CATALOGS:
            self.setProperty(f"profile.catalog.{catalog}.active", str(catalog == self.catalog))

    def _sortmethod_setting(self, level, reverse=False):
        return catalog_profiles.sortmethod_key(self.catalog, level, reverse=reverse)

    def _sub_sort_setting(self, category, level):
        return catalog_profiles.sub_sort_key(self.catalog, category, level)

    def _populate_all_lists(self):
        self.max_level = 8
        self._reset_properties()
        for control_id in self.sort_lists:
            self._populate_list(int(control_id / 1000))
        self.setProperty("max_level", str(self.max_level))

    def _reset_properties(self):
        for i in range(len(SORT_OPTIONS['sortmethod'])):
            for j in range(6):
                self.clearProperty(f'general.sortmethod.{i}.label.{j}')
                self.clearProperty(f'general.sortmethod.{i}.label.{j}.last')

    @staticmethod
    def _set_setting_item_properties(menu_item, setting):
        for label in setting:
            if label == "label":
                menu_item.setLabel(g.get_language_string(setting[label]))
            else:
                menu_item.setProperty(label, str(setting[label]))

    def _populate_list(self, level):
        sort_method = self._sortmethod_setting(level)
        method = SORT_METHODS[self.sort_options[sort_method]]
        options = SORT_OPTIONS[method]
        loops = len(options) if options else 1

        last_lang_code = None
        for idx in range(loops):
            if last_lang_code in [30513, 30581] or self.max_level < level:
                continue

            if idx == 0:
                lang_code = SORT_OPTIONS['sortmethod'][self.sort_options[sort_method]]
                self.setProperty(
                    f'general.sortmethod.{level}.label.{idx}',
                    str(g.get_language_string(lang_code)),
                )
                if lang_code == 30513:
                    self.max_level = level
            else:
                if not options:
                    continue

                sub_setting = self._sub_sort_setting(method, idx)
                lang_code = options[self.sort_options[sub_setting]]
                self.setProperty(
                    f'general.sortmethod.{level}.label.{idx}',
                    str(g.get_language_string(lang_code)),
                )

            if lang_code in [30513, 30581] or loops == 1 or idx == loops - 1:
                self.setProperty(
                    f'general.sortmethod.{level}.label.{idx}.last',
                    str(True),
                )

            last_lang_code = lang_code
        self.setProperty(
            f"general.sortmethod.{level}.reverse",
            str(self.sort_options[self._sortmethod_setting(level, reverse=True)]),
        )
        self.setProperty(
            f"general.sortmethod.{level}",
            method,
        )

    def _cycle_info(self, level, idx):
        sort_method = self._sortmethod_setting(level)
        method = SORT_METHODS[self.sort_options[sort_method]]
        setting = sort_method if idx == 0 else self._sub_sort_setting(method, idx)

        current = self.sort_options[setting]
        category = setting.split('.')[1]
        new = (current + 1) % len(SORT_OPTIONS[category])

        self.sort_options[setting] = new
        return new

    def _handle_reverse(self, level):
        setting = self._sortmethod_setting(level, reverse=True)
        self.sort_options[setting] = not self.sort_options[setting]
        self.setProperty(f"general.sortmethod.{level}.reverse", str(self.sort_options[setting]))

    def _save_settings(self):
        for setting in self.sort_options:
            if setting.endswith(".reverse"):
                base_key = setting[: -len(".reverse")]
                method_index = self.sort_options.get(base_key)
                if method_index is not None and SORT_METHODS[method_index] == "debridsort":
                    g.set_setting(setting, False)
                    continue
            g.set_setting(setting, self.sort_options[setting])

    def _switch_catalog(self, new_catalog):
        new_catalog = catalog_profiles.normalize_catalog(new_catalog)
        if new_catalog == self.catalog:
            return
        self._save_settings()
        self.catalog = new_catalog
        self.sort_options = catalog_profiles.load_sort_options(self.catalog)
        self._update_catalog_properties()
        self._populate_all_lists()

    def _reset_catalog(self):
        catalog_profiles.reset_sort_profile(self.catalog)
        self.sort_options = catalog_profiles.load_sort_options(self.catalog)
        self._populate_all_lists()

    def _apply_anime_preset(self, preset: str):
        if self.catalog != "anime":
            return
        if not catalog_profiles.apply_anime_sort_preset(preset):
            return
        self.sort_options = catalog_profiles.load_sort_options(self.catalog)
        self._populate_all_lists()
        label = g.get_language_string(30942 if preset == "sub" else 30943)
        g.notification(g.ADDON_NAME, label)

    def handle_action(self, action, control_id=None):
        if action == 7:
            if control_id in [1111, 2222, 3333, 4444, 5555, 6666, 7777, 8888]:
                self._handle_reverse(int(control_id / 1111))
            elif control_id in self.CATALOG_CONTROLS:
                self._switch_catalog(self.CATALOG_CONTROLS[control_id])
            elif control_id == self.RESET_CONTROL:
                self._reset_catalog()
            elif control_id == self.SUB_PRESET_CONTROL:
                self._apply_anime_preset("sub")
            elif control_id == self.DUB_PRESET_CONTROL:
                self._apply_anime_preset("dub")
            elif control_id == 9001:
                self.close()
            else:
                self._cycle_info(int(control_id / 1000), (control_id % 1000) - 1)
                self._populate_all_lists()
                self.setFocusId(control_id)

    def close(self):
        super().close()
        self._save_settings()
        catalog_profiles.set_last_catalog(self.catalog)
        g.open_addon_settings(6, 11)  # Open settings back where we were launched from
