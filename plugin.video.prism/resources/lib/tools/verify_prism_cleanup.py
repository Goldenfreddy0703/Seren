"""Static verification for Prism addon architecture cleanup (Phase 8)."""
from __future__ import annotations

import compileall
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _lib_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _grep_count(root: Path, pattern: str) -> int:
    rx = re.compile(pattern)
    count = 0
    for path in root.rglob("*.py"):
        if path.name == "verify_prism_cleanup.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        count += len(rx.findall(text))
    return count


def _bootstrap_kodi_stubs() -> None:
    for name in ("xbmc", "xbmcgui", "xbmcplugin", "xbmcvfs", "xbmcaddon"):
        sys.modules.setdefault(name, MagicMock())


def main() -> int:
    lib = _lib_root()
    addon_root = lib.parents[1]
    if str(addon_root) not in sys.path:
        sys.path.insert(0, str(addon_root))

    errors: list[str] = []

    if not compileall.compile_dir(str(lib), quiet=1):
        errors.append("compileall failed")

    deleted = (
        "library_detail_sync.py",
        "show_metadata.py",
        "meta_storage.py",
        "metadata_providers.py",
        "artwork_profile.py",
        "modules/database.py",
    )
    for name in deleted:
        if list(lib.rglob(name)):
            errors.append(f"deleted file still present: {name}")

    singleton_calls = _grep_count(lib, r"SimklSyncDatabase\(\)")
    if singleton_calls != 1:
        errors.append(f"expected 1 SimklSyncDatabase() (session singleton), found {singleton_calls}")

    dead_symbols = ("gapfill_list_meta", "hybrid_apply_list_meta", "_enrich_discover_simkl_detail")
    for symbol in dead_symbols:
        if _grep_count(lib, re.escape(symbol)):
            errors.append(f"dead symbol still referenced: {symbol}")

    _bootstrap_kodi_stubs()
    try:
        from resources.lib.meta.menu_paint_profile import verify_menu_paint_coverage

        missing = verify_menu_paint_coverage()
        if missing:
            errors.append(f"MenuPaintProfile missing for: {', '.join(missing)}")
    except Exception as exc:
        errors.append(f"profile coverage check failed: {exc}")

    if errors:
        print("VERIFY FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("VERIFY OK: compile, deletions, singleton, dead symbols, profile coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
