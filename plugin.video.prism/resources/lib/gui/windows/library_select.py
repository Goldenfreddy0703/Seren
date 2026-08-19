from __future__ import annotations

import xbmcgui

from resources.lib.gui.windows.base_window import BaseWindow
from resources.lib.modules.globals import g
from resources.lib.simkl import library_sort

_LABEL_NEXTUP = 30088
_LABEL_SORT_BY = 30653
_LABEL_SORT_ORDER = 30865
_LABEL_SORTING_FOR = 31043


class LibrarySelect(BaseWindow):
    """Configure watchlist and Next Up sort settings."""

    GLOBAL_CONTROL = 9200
    CATALOG_CONTROLS = {9201: "movie", 9202: "tv", 9203: "anime"}
    SETTINGS_LIST = 6150
    RESET_CONTROL = 9204
    CLOSE_CONTROL = 9001

    GENERAL_SETTINGS_SECTION = 0
    LIBRARY_SETTINGS_OFFSET = 18

    def __init__(self, xml_file, xml_location):
        super().__init__(xml_file, xml_location)
        self.catalog = library_sort.get_last_catalog()
        self.settings_list = None
        self._nextup_label = g.get_language_string(_LABEL_NEXTUP)
        self._sort_by_label = g.get_language_string(_LABEL_SORT_BY)
        self._sort_order_label = g.get_language_string(_LABEL_SORT_ORDER)
        self._sorting_for_label = g.get_language_string(_LABEL_SORTING_FOR)

    def onInit(self):
        self.settings_list = self.getControlList(self.SETTINGS_LIST)
        self._update_catalog_properties()
        self._populate_settings_list()
        self.set_default_focus(control_list=self.settings_list, control_list_reset=True)
        super().onInit()

    def _is_global(self) -> bool:
        return library_sort.is_global_catalog(self.catalog)

    def _update_catalog_properties(self):
        self.setProperty("library.catalog", self.catalog)
        self.setProperty("library.global.active", str(self._is_global()))
        for catalog in library_sort.CATALOG_LABEL_IDS:
            self.setProperty(
                f"profile.catalog.{catalog}.active",
                str(catalog == self.catalog),
            )

    @staticmethod
    def _add_setting_row(list_control, label: str, action: str, value: str, section: str = ""):
        item = xbmcgui.ListItem(label=label)
        item.setProperty("action", action)
        item.setProperty("value", value)
        item.setProperty("section", section)
        list_control.addItem(item)

    def _section_header(self, selection_label: str) -> str:
        return f"[UPPERCASE]{self._sorting_for_label}: {selection_label}[/UPPERCASE]"

    def _populate_settings_list(self, preserve_position: bool = False):
        selected = self.settings_list.getSelectedPosition() if preserve_position else 0
        self.settings_list.reset()

        if self.catalog != "movie":
            self._add_setting_row(
                self.settings_list,
                self._nextup_label,
                "nextup",
                library_sort.get_nextup_sort_label(),
            )

        if self._is_global():
            section = self._section_header(library_sort.global_selection_label())
            self._add_setting_row(
                self.settings_list,
                self._sort_by_label,
                "global:sortfield",
                library_sort.get_watchlist_sortfield_label(),
                section=section,
            )
            self._add_setting_row(
                self.settings_list,
                self._sort_order_label,
                "global:order",
                library_sort.get_watchlist_order_label(),
            )
        else:
            for status in library_sort.statuses_for_catalog(self.catalog):
                section = self._section_header(library_sort.selection_label(self.catalog, status))
                self._add_setting_row(
                    self.settings_list,
                    self._sort_by_label,
                    f"{self.catalog}:{status}:sortfield",
                    library_sort.get_status_sortfield_label(self.catalog, status),
                    section=section,
                )
                self._add_setting_row(
                    self.settings_list,
                    self._sort_order_label,
                    f"{self.catalog}:{status}:order",
                    library_sort.get_status_order_label(self.catalog, status),
                )

        if self.settings_list.size():
            selected = max(0, min(selected, self.settings_list.size() - 1))
            self.settings_list.selectItem(selected)

    def _apply_list_action(self, action_key: str):
        if action_key == "nextup":
            library_sort.cycle_nextup_sort()
        elif action_key == "global:sortfield":
            library_sort.cycle_watchlist_sortfield()
        elif action_key == "global:order":
            library_sort.toggle_watchlist_order()
        else:
            catalog, status, kind = action_key.split(":", 2)
            if kind == "sortfield":
                library_sort.cycle_status_sortfield(catalog, status)
            else:
                library_sort.toggle_status_order(catalog, status)

    def _handle_list_click(self):
        item = self.settings_list.getSelectedItem()
        if item is None:
            return

        action_key = item.getProperty("action")
        if not action_key:
            return

        self._apply_list_action(action_key)
        self._populate_settings_list(preserve_position=True)

    def _switch_catalog(self, new_catalog: str):
        new_catalog = library_sort.normalize_selection_catalog(new_catalog)
        if new_catalog == self.catalog:
            return
        self.catalog = new_catalog
        self._update_catalog_properties()
        self._populate_settings_list()

    def handle_action(self, action, control_id=None):
        if action != 7:
            return

        if control_id == self.SETTINGS_LIST:
            self._handle_list_click()
        elif control_id == self.GLOBAL_CONTROL:
            self._switch_catalog(library_sort.GLOBAL_CATALOG)
        elif control_id in self.CATALOG_CONTROLS:
            self._switch_catalog(self.CATALOG_CONTROLS[control_id])
        elif control_id == self.RESET_CONTROL:
            if self._is_global():
                library_sort.reset_global_sort_defaults()
            else:
                library_sort.reset_catalog_sort_to_global(self.catalog)
            self._populate_settings_list()
        elif control_id == self.CLOSE_CONTROL:
            self.close()

    def close(self):
        library_sort.set_last_selection(self.catalog, "watching")
        super().close()
        g.open_addon_settings(self.GENERAL_SETTINGS_SECTION, self.LIBRARY_SETTINGS_OFFSET)
