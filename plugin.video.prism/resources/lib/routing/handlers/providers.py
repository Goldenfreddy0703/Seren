"""Router action handlers."""
from __future__ import annotations

from resources.lib.routing.context import DispatchContext
from resources.lib.routing.registry import register_action
import xbmc
import xbmcgui

from resources.lib.modules.globals import g


@register_action("externalProviderInstall")
def handle_externalProviderInstall(ctx: DispatchContext) -> None:
    from resources.lib.modules.providers.install_manager import (
        ProviderInstallManager,
    )

    confirmation = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30166))
    if confirmation == 0:
        return
    ProviderInstallManager().install_package(1, ctx.url=ctx.url)

@register_action("externalProviderUninstall")
def handle_externalProviderUninstall(ctx: DispatchContext) -> None:
    from resources.lib.modules.providers.install_manager import (
        ProviderInstallManager,
    )

    confirmation = xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30168).format(ctx.url))
    if confirmation == 0:
        return
    ProviderInstallManager().uninstall_package(package=ctx.url, silent=False)

@register_action("installProviders")
def handle_installProviders(ctx: DispatchContext) -> None:
    from resources.lib.modules.providers.install_manager import (
        ProviderInstallManager,
    )

    ProviderInstallManager().install_package(ctx.action_args)

@register_action("manageProviders")
def handle_manageProviders(ctx: DispatchContext) -> None:
    g.show_busy_dialog()
    from resources.lib.gui.windows.provider_packages import ProviderPackages
    from resources.lib.database.skinManager import SkinManager

    try:
        window = ProviderPackages(*SkinManager().confirm_skin_path("provider_packages.xml"))
        window.doModal()
    finally:
        del window

@register_action("refreshProviders")
def handle_refreshProviders(ctx: DispatchContext) -> None:
    from resources.lib.modules.providers import CustomProviders

    providers = CustomProviders()
    providers.update_known_providers()
    providers.poll_database()

@register_action("uninstallProviders")
def handle_uninstallProviders(ctx: DispatchContext) -> None:
    from resources.lib.modules.providers.install_manager import (
        ProviderInstallManager,
    )

    ProviderInstallManager().uninstall_package()
