import base64
import importlib
import json
import os
import sys
from importlib import reload as reload_module

import xbmcvfs

from resources.lib.common import tools
from resources.lib.database.providerCache import ProviderCache
from resources.lib.modules.exceptions import RanOnceAlready
from resources.lib.modules.global_lock import GlobalLock
from resources.lib.modules.globals import g
from resources.lib.modules.providers.settings import SettingsManager

# Below is the contents of the providers/__init__.py base64 encoded
# If you update this init file_path you will need to update this base64 as well to ensure it is deployed on the users machine
# If you change the init file_path without updating this it will be overwritten with the old one!!
INIT_BASE64 = "aW1wb3J0IG9zCgpmcm9tIHJlc291cmNlcy5saWIuZGF0YWJhc2UucHJvdmlkZXJDYWNoZSBpbXBvcnQgUHJvdmlkZXJDYWNoZSwgUFJPVklERVJfQ0FUQUxPR1MKZnJvbSByZXNvdXJjZXMubGliLm1vZHVsZXMuZ2xvYmFscyBpbXBvcnQgZwoKCmRlZiBfaXNfdmFsaWRfcHJvdmlkZXJfZGlyKG5hbWUpOgogICAgZGlyX3BhdGggPSBvcy5wYXRoLmpvaW4oZGF0YV9wYXRoLCBuYW1lKQogICAgdHJ5OgogICAgICAgIGlmIG5vdCBvcy5wYXRoLmlzZGlyKGRpcl9wYXRoKToKICAgICAgICAgICAgcmV0dXJuIEZhbHNlCiAgICAgICAgaWYgbmFtZS5zdGFydHN3aXRoKCdfXycpOgogICAgICAgICAgICByZXR1cm4gRmFsc2UKCiAgICAgICAgcmV0dXJuIFRydWUKCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHJldHVybiBGYWxzZQoKCmRhdGFfcGF0aCA9IG9zLnBhdGguam9pbihnLkFERE9OX1VTRVJEQVRBX1BBVEgsICdwcm92aWRlcnMnKQpwcm92aWRlcl9wYWNrYWdlcyA9IFtuYW1lIGZvciBuYW1lIGluIG9zLmxpc3RkaXIoZGF0YV9wYXRoKSBpZiBfaXNfdmFsaWRfcHJvdmlkZXJfZGlyKG5hbWUpXQpwcm92aWRlckNhY2hlID0gUHJvdmlkZXJDYWNoZSgpCgpwcm92aWRlcl90eXBlcyA9ICgKICAgICgnaG9zdGVycycsICdnZXRfaG9zdGVycycpLAogICAgKCd0b3JyZW50JywgJ2dldF90b3JyZW50JyksCiAgICAoJ2FkYXB0aXZlJywgJ2dldF9hZGFwdGl2ZScpLAogICAgKCdkaXJlY3QnLCAnZ2V0X2RpcmVjdCcpLAopCgoKZGVmIF9ub3JtYWxpemVfY2F0YWxvZyhjYXRhbG9nKToKICAgIHZhbHVlID0gKGNhdGFsb2cgb3IgJ21vdmllJykuc3RyaXAoKS5sb3dlcigpCiAgICBpZiB2YWx1ZSBpbiBQUk9WSURFUl9DQVRBTE9HUzoKICAgICAgICByZXR1cm4gdmFsdWUKICAgIGlmIHZhbHVlIGluICgnbW92aWVzJywgJ21vdmllJyk6CiAgICAgICAgcmV0dXJuICdtb3ZpZScKICAgIGlmIHZhbHVlIGluICgndHZzaG93JywgJ3Nob3dzJywgJ3Nob3cnKToKICAgICAgICByZXR1cm4gJ3R2JwogICAgcmV0dXJuICdtb3ZpZScKCgpkZWYgX3Byb3ZpZGVyX2VuYWJsZWRfZm9yX2NhdGFsb2cocHJvdmlkZXJfcm93LCBjYXRhbG9nKToKICAgIGNvbHVtbiA9ICdzdGF0dXNfJXMnICUgX25vcm1hbGl6ZV9jYXRhbG9nKGNhdGFsb2cpCiAgICBzdGF0ZSA9IHByb3ZpZGVyX3Jvdy5nZXQoY29sdW1uKSBvciBwcm92aWRlcl9yb3cuZ2V0KCdzdGF0dXMnKSBvciAnZGlzYWJsZWQnCiAgICByZXR1cm4gc3RyKHN0YXRlKS5sb3dlcigpID09ICdlbmFibGVkJwoKCmRlZiBfaXNfcHJvdmlkZXJfZW5hYmxlZChwcm92aWRlcl9uYW1lLCBwYWNrYWdlLCBzdGF0dXNlcywgY2F0YWxvZz0nbW92aWUnKToKICAgIGZvciByb3cgaW4gc3RhdHVzZXM6CiAgICAgICAgaWYgcm93Wydwcm92aWRlcl9uYW1lJ10gPT0gcHJvdmlkZXJfbmFtZSBhbmQgcm93WydwYWNrYWdlJ10gPT0gcGFja2FnZToKICAgICAgICAgICAgcmV0dXJuIF9wcm92aWRlcl9lbmFibGVkX2Zvcl9jYXRhbG9nKHJvdywgY2F0YWxvZykKICAgIHJldHVybiBGYWxzZQoKCmRlZiBfZ2V0X3Byb3ZpZGVycyhsYW5ndWFnZSwgc3RhdHVzPUZhbHNlLCBjYXRhbG9nPSdtb3ZpZScpOgogICAgcHJvdmlkZXJfc3RvcmUgPSB7CiAgICAgICAgJ2hvc3RlcnMnOiBbXSwKICAgICAgICAndG9ycmVudCc6IFtdLAogICAgICAgICdhZGFwdGl2ZSc6IFtdLAogICAgICAgICdkaXJlY3QnOiBbXSwKICAgIH0KICAgIGZvciBwYWNrYWdlIGluIHByb3ZpZGVyX3BhY2thZ2VzOgogICAgICAgIHByb3ZpZGVyc19wYXRoID0gJ3Byb3ZpZGVycy4lcy4lcycgJSAocGFja2FnZSwgbGFuZ3VhZ2UpCiAgICAgICAgdHJ5OgogICAgICAgICAgICBwcm92aWRlcl9saXN0ID0gX19pbXBvcnRfXyhwcm92aWRlcnNfcGF0aCwgZnJvbWxpc3Q9WycnXSkKICAgICAgICAgICAgZm9yIHByb3ZpZGVyX3R5cGUgaW4gcHJvdmlkZXJfdHlwZXM6CiAgICAgICAgICAgICAgICBmb3IgaSBpbiBnZXRhdHRyKHByb3ZpZGVyX2xpc3QsIHByb3ZpZGVyX3R5cGVbMV0sIGxhbWJkYTogW10pKCk6CiAgICAgICAgICAgICAgICAgICAgaWYgc3RhdHVzIGlzIG5vdCBGYWxzZSBhbmQgbm90IF9pc19wcm92aWRlcl9lbmFibGVkKGksIHBhY2thZ2UsIHN0YXR1cywgY2F0YWxvZyk6CiAgICAgICAgICAgICAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgICAgICAgICAgICAgIHByb3ZpZGVyX3N0b3JlW3Byb3ZpZGVyX3R5cGVbMF1dLmFwcGVuZCgKICAgICAgICAgICAgICAgICAgICAgICAgKCd7fS57fScuZm9ybWF0KHByb3ZpZGVyc19wYXRoLCBwcm92aWRlcl90eXBlWzBdKSwgaSwgcGFja2FnZSkKICAgICAgICAgICAgICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgICAgICAgICBnLmxvZygnU2tpcHBpbmcgcHJvdmlkZXIgcGFja2FnZSAlcyAtIGZhaWxlZCB0byBpbXBvcnQ6ICVzJyAlIChwYWNrYWdlLCBlKSwgJ2Vycm9yJykKICAgICAgICAgICAgY29udGludWUKCiAgICByZXR1cm4gcHJvdmlkZXJfc3RvcmUKCgpkZWYgZ2V0X3JlbGV2YW50KGxhbmd1YWdlLCBjYXRhbG9nPSdtb3ZpZScpOgogICAgcHJvdmlkZXJfc3RhdHVzID0gW2kgZm9yIGkgaW4gcHJvdmlkZXJDYWNoZS5nZXRfcHJvdmlkZXJzKCkgaWYgaVsnY291bnRyeSddID09IGxhbmd1YWdlXQogICAgcmV0dXJuIF9nZXRfcHJvdmlkZXJzKGxhbmd1YWdlLCBwcm92aWRlcl9zdGF0dXMsIGNhdGFsb2c9X25vcm1hbGl6ZV9jYXRhbG9nKGNhdGFsb2cpKQoKCmRlZiBnZXRfYWxsKGxhbmd1YWdlKToKICAgIHJldHVybiBfZ2V0X3Byb3ZpZGVycyhsYW5ndWFnZSkK"


class CustomProviders(ProviderCache):
    def __init__(self):
        super().__init__()
        self.deploy_init()
        self.providers_module = self._try_add_providers_path()
        self.pre_update_collection = []
        self.language = "en"
        self.known_packages = None
        self.known_providers = None

        self.providers_path = os.path.join(g.ADDON_USERDATA_PATH, "providers")
        self.modules_path = os.path.join(g.ADDON_USERDATA_PATH, "providerModules")
        self.meta_path = os.path.join(g.ADDON_USERDATA_PATH, "providerMeta")
        self.media_path = os.path.join(g.ADDON_USERDATA_PATH, "providerMedia")
        self.provider_types = ["torrent", "hosters", "adaptive", "direct"]

        try:
            with GlobalLock(self.__class__.__name__, True):
                self._init_providers()
        except RanOnceAlready:
            pass
        self.poll_database()
        self.provider_settings = SettingsManager()

    def _init_providers(self):
        g.log("Init provider packages")
        self.update_known_packages()
        self.update_known_providers()

    def _try_add_providers_path(self):
        try:
            if g.ADDON_USERDATA_PATH in sys.path:
                return reload_module(importlib.import_module("providers"))

            sys.path.append(g.ADDON_USERDATA_PATH)
            return importlib.import_module("providers")
        except ImportError:
            g.log("Providers folder appears to be missing")

    def poll_database(self):
        self.known_providers = self.get_providers()
        self.known_packages = self.get_provider_packages()

    def update_known_packages(self):
        packages = []
        for root, _, files in os.walk(self.meta_path):
            for filename in files:
                if filename.endswith(".json"):
                    with open(os.path.join(root, filename)) as f:
                        meta = json.load(f)
                        try:
                            packages.append(
                                (
                                    meta["name"],
                                    meta["author"],
                                    meta["remote_meta"],
                                    meta["version"],
                                    "|".join(meta.get("services", [])),
                                )
                            )
                        except KeyError:
                            continue

        predicate = "','".join(p[0] for p in packages)
        self.execute_sql(self.package_insert_query, packages)
        self.execute_sql(f"DELETE FROM providers where package not in ('{predicate}')")
        self.known_packages = self.get_provider_packages()

    def update_known_providers(self):
        providers = self._try_add_providers_path()
        if providers is None:
            g.log("Providers folder unavailable; skipping provider refresh", "error")
            return
        try:
            all_providers = providers.get_all(self.language)
        except Exception as e:
            # A broken/half-installed package must not crash provider management,
            # otherwise the user can no longer install or repair packages. Preserve
            # the existing provider DB rows and bail out of the refresh.
            g.log(f"Failed to enumerate providers, keeping existing list: {e}", "error")
            g.log_stacktrace()
            return
        providers = [
            (provider[1], provider[2], "enabled", self.language, provider_type, "enabled", "enabled", "enabled")
            for provider_type in self.provider_types
            for provider in all_providers.get(provider_type, [])
            if any(provider[2] == package['pack_name'] for package in self.known_packages)
        ]

        self.execute_sql(self.provider_insert_query, providers)
        providers = {
            provider[1] for provider_type in self.provider_types for provider in all_providers.get(provider_type, [])
        }
        packages = {
            provider[2] for provider_type in self.provider_types for provider in all_providers.get(provider_type, [])
        }
        self.execute_sql(
            f"""
            DELETE FROM providers
            WHERE (NOT package IN ('{"','".join(packages)}') OR NOT provider_name IN ('{"','".join(providers)}'))
            """
        )

    def flip_provider_status(self, package_name, provider_name, status_override=None, catalog=None):
        if catalog:
            return self.flip_provider_catalog_status(package_name, provider_name, catalog, status_override)

        current_status = self.get_single_provider(provider_name, package_name)["status"]

        if status_override:
            new_status = status_override
        else:
            new_status = "disabled" if current_status == "enabled" else "enabled"
        self.adjust_provider_status(provider_name, package_name, new_status)
        return new_status.title() if isinstance(new_status, str) else new_status

    def get_icon(self, provider_imports):
        if not provider_imports or len(provider_imports) != 3:
            return None

        # provider_imports = ("providers.PACKAGE_NAME.LANGUAGE.PROVIDER_TYPE",
        #                     "PROVIDER_NAME",
        #                     "PACKAGE_NAME")
        package_name = provider_imports[2]
        package_split = provider_imports[0].split(".")
        language = package_split[2] if len(package_split) >= 3 else None
        provider_type = package_split[3] if len(package_split) >= 4 else None
        provider_name = provider_imports[1]

        package_path = None
        provider_path = None

        if None in [language, provider_type, provider_name]:
            package_path = os.path.join(
                g.ADDON_USERDATA_PATH,
                "providerMedia",
                package_name,
                f"{package_name}.png",
            )
        elif provider_type == "cloud":
            provider_path = os.path.join(
                g.IMAGES_PATH,
                "providerMedia",
                f"{provider_name}.png",
            )
        else:
            provider_path = os.path.join(
                g.ADDON_USERDATA_PATH,
                "providerMedia",
                package_name,
                language,
                provider_type,
                f"{provider_name}.png",
            )

        if provider_path is not None and xbmcvfs.exists(provider_path):
            return provider_path
        elif package_path is not None and xbmcvfs.exists(package_path):
            return package_path
        else:
            return None

    @staticmethod
    def deploy_init():
        folders = ["providerModules/", "providers/", "providerMedia/"]
        root_init_path = os.path.join(g.ADDON_USERDATA_PATH, "__init__.py")

        if not xbmcvfs.exists(g.ADDON_USERDATA_PATH):
            tools.makedirs(g.ADDON_USERDATA_PATH, exist_ok=True)
        if not xbmcvfs.exists(root_init_path):
            xbmcvfs.File(root_init_path, "a").close()
        for i in folders:
            folder_path = os.path.join(g.ADDON_USERDATA_PATH, i)
            tools.makedirs(folder_path, exist_ok=True)
            xbmcvfs.File(os.path.join(folder_path, "__init__.py"), "a").close()
        provider_init = xbmcvfs.File(os.path.join(g.ADDON_USERDATA_PATH, "providers", "__init__.py"), "w+")
        provider_init.write(str(base64.b64decode(INIT_BASE64).decode("utf-8")))
        provider_init.close()
