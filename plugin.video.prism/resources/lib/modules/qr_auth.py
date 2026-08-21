"""QR code helpers for device / PIN account authentication."""
from __future__ import annotations

import glob
import os
import time

from resources.lib.common import tools
from resources.lib.modules.globals import g

QR_AUTH_PREFIX = "qr_auth_"
LEGACY_QR_NAME = "qr_code.png"


def qr_png_path() -> str:
    """Legacy fixed path — prefer :func:`generate_qr_png` (unique file per session)."""
    return os.path.join(g.ADDON_USERDATA_PATH, LEGACY_QR_NAME)


def _qr_auth_glob() -> list[str]:
    pattern = os.path.join(g.ADDON_USERDATA_PATH, f"{QR_AUTH_PREFIX}*.png")
    return sorted(glob.glob(pattern))


def delete_qr_png(path: str | None = None) -> None:
    """Remove transient auth QR image(s) from addon userdata."""
    targets: list[str] = []
    if path:
        targets.append(path.replace("/", os.sep))
    else:
        legacy = qr_png_path()
        if os.path.isfile(legacy):
            targets.append(legacy)
        targets.extend(_qr_auth_glob())

    seen: set[str] = set()
    for target in targets:
        normalized = os.path.normpath(target)
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            if os.path.isfile(normalized):
                os.remove(normalized)
        except OSError:
            g.log_stacktrace()


def generate_qr_png(url: str) -> str:
    import pyqrcode

    # Kodi caches local textures by path — always use a fresh filename per auth session.
    delete_qr_png()

    userdata = g.ADDON_USERDATA_PATH
    os.makedirs(userdata, exist_ok=True)
    path = os.path.join(userdata, f"{QR_AUTH_PREFIX}{int(time.time() * 1000)}.png")
    pyqrcode.create(url).png(path, scale=20)
    return path.replace("\\", "/")


AUTH_TIMEOUT_MAX = 300


def capped_auth_timeout(seconds: int, maximum: int = AUTH_TIMEOUT_MAX) -> int:
    """Limit device-code auth wait time so expired sessions fail faster."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = maximum
    if value <= 0:
        value = maximum
    return min(value, maximum)


def show_auth_failed(provider: str) -> None:
    import xbmcgui

    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30065).format(provider))


def show_auth_timeout(provider: str) -> None:
    import xbmcgui

    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(31047).format(provider))


def build_auth_message(verification_url: str, user_code: str | None = None) -> str:
    parts = [g.get_language_string(30018).format(g.color_string(verification_url))]
    if user_code:
        parts.append(g.get_language_string(30019).format(g.color_string(user_code)))
        if tools.copy2clip(user_code):
            parts.append(g.get_language_string(30047))
    return "[CR]".join(parts)


def auth_progress_percent(remaining: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int(float(remaining * 100) / total)))


def wait_auth_interval(seconds: int, progress) -> bool:
    """Sleep without blocking Kodi input; return False if the auth dialog was cancelled."""
    import xbmc

    remaining_ms = max(0, int(seconds * 1000))
    while remaining_ms > 0:
        if progress.iscanceled():
            return False
        step = min(100, remaining_ms)
        xbmc.sleep(step)
        remaining_ms -= step
    return not progress.iscanceled()


def open_auth_dialog(heading: str, verification_url: str, user_code: str | None = None, percent: int = 100):
    from resources.lib.database.skinManager import SkinManager
    from resources.lib.gui.windows.auth_progress import AuthProgressDialog

    qr_path = generate_qr_png(verification_url)
    dialog = AuthProgressDialog(
        *SkinManager().confirm_skin_path("auth_progress.xml"),
        heading=heading,
        text=build_auth_message(verification_url, user_code),
        qr_code=qr_path,
        percent=percent,
    )
    dialog.show()
    return dialog
