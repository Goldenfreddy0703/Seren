from __future__ import annotations

import contextlib
import gc
import json
import os
import time
import zipfile
from io import BytesIO

import xbmc
import xbmcgui
import xbmcvfs

from resources.lib.common import tools
from resources.lib.modules.globals import g

BACKUP_FORMAT = "prism-backup"
BACKUP_FORMAT_VERSION = 2
LEGACY_BACKUP_FORMAT = "prism-userdata-backup"
LEGACY_BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "prism-backup-manifest.json"
DEFERRED_MANIFEST = "deferred-restore.json"

BACKUP_FILES = frozenset(
    {
        "settings.xml",
        "simklSync.db",
        "simklSync.db.md5",
        "providers.db",
        "providers.db.md5",
        "skins.db",
        "skins.db.md5",
        "premiumize.db",
        "premiumize.db.md5",
    }
)
BACKUP_FOLDERS = frozenset(
    {
        "providers",
        "providerModules",
        "providerMeta",
        "providerMedia",
        "skins",
    }
)
SKIP_SUFFIXES = (".db-wal", ".db-shm", ".md5", ".temp")
DB_FILENAMES = frozenset({"simklSync.db", "providers.db", "skins.db", "premiumize.db"})


def _deferred_restore_dirname() -> str:
    return f"{g.ADDON_ID}-deferred-restore"


def _normalize_path(path: str) -> str:
    """Normalize a Kodi path without double-translating absolute profile paths."""
    if not path:
        return path
    if path.startswith(("special://", "plugin://")):
        path = tools.translate_path(path)
    return tools.validate_path(path).rstrip("/\\")


def _userdata_path() -> str:
    path = g.ADDON_USERDATA_PATH or tools.translate_path(
        f"special://profile/addon_data/{g.ADDON_ID}/"
    )
    return _normalize_path(path)


def _deferred_restore_path() -> str:
    parent = os.path.dirname(_userdata_path())
    return _normalize_path(_vfs_join(parent, _deferred_restore_dirname()))


def _vfs_join(base: str, name: str) -> str:
    base = _normalize_path(base)
    name = (name or "").replace("\\", "/").strip("/")
    if base.endswith(("/", "\\")):
        return f"{base}{name}"
    if "/" in base:
        return f"{base}/{name}"
    return f"{base}\\{name}"


def _try_listdir(path: str) -> tuple[list[str], list[str]] | None:
    try:
        dirs, files = xbmcvfs.listdir(_normalize_path(path))
        return list(dirs), list(files)
    except (OSError, ValueError):
        return None


def _is_absolute_vfs_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/")
    if normalized.startswith("/"):
        return True
    return len(normalized) > 1 and normalized[1] == ":"


def _listdir_entry_name(entry: str) -> str:
    return os.path.basename(entry.replace("\\", "/").rstrip("/"))


def _listdir_entry_path(parent: str, entry: str) -> str:
    if _is_absolute_vfs_path(entry):
        return _normalize_path(entry)
    return _vfs_join(parent, _listdir_entry_name(entry))


def _vfs_walk_files(root_path: str, *, skip_dirnames: frozenset[str] | None = None):
    root_path = _normalize_path(root_path)
    skip_dirnames = skip_dirnames or frozenset()
    stack: list[tuple[str, str]] = [(root_path, "")]
    while stack:
        current, rel_prefix = stack.pop()
        try:
            dirs, files = xbmcvfs.listdir(current)
        except (OSError, ValueError):
            continue
        for directory in dirs:
            if directory in (".", "..") or directory in skip_dirnames:
                continue
            dir_name = _listdir_entry_name(directory)
            child_path = _listdir_entry_path(current, directory)
            child_rel = f"{rel_prefix}/{dir_name}" if rel_prefix else dir_name
            stack.append((child_path, child_rel))
        for filename in files:
            if filename in (".", ".."):
                continue
            file_name = _listdir_entry_name(filename)
            if file_name.lower().endswith(SKIP_SUFFIXES):
                continue
            full_path = _listdir_entry_path(current, filename)
            rel_path = f"{rel_prefix}/{file_name}" if rel_prefix else file_name
            yield full_path, rel_path.replace("\\", "/")


def _read_bytes(path: str) -> bytes | None:
    handle = None
    try:
        handle = xbmcvfs.File(_normalize_path(path))
        return handle.readBytes()
    except OSError:
        return None
    finally:
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.close()


def _write_bytes(path: str, data: bytes) -> bool:
    path = _normalize_path(path)
    handle = None
    try:
        handle = xbmcvfs.File(path, "wb")
        handle.write(data)
        return True
    except OSError:
        g.log_stacktrace()
        return False
    finally:
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.close()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(_normalize_path(path))
    if parent:
        xbmcvfs.mkdirs(_normalize_path(parent))


def _overwrite_file(dest: str, data: bytes) -> bool:
    dest = _normalize_path(dest)
    _ensure_parent_dir(dest)
    if xbmcvfs.exists(dest) and not xbmcvfs.delete(dest):
        return False
    if not _write_bytes(dest, data):
        return False
    return xbmcvfs.exists(dest)


def _overwrite_file_from_source(src: str, dest: str) -> bool:
    src = _normalize_path(src)
    dest = _normalize_path(dest)
    _ensure_parent_dir(dest)
    if xbmcvfs.exists(dest) and not xbmcvfs.delete(dest):
        return False
    if xbmcvfs.copy(src, dest):
        return xbmcvfs.exists(dest)
    payload = _read_bytes(src)
    if payload is not None:
        return _write_bytes(dest, payload) and xbmcvfs.exists(dest)
    return False


def _remove_tree(path: str) -> None:
    path = _normalize_path(path)
    if xbmcvfs.exists(path):
        xbmcvfs.rmdir(path, True)


def _open_zip_archive(path: str) -> zipfile.ZipFile:
    data = _read_bytes(path)
    if not data:
        raise OSError(f"Unable to read zip archive: {path}")
    return zipfile.ZipFile(BytesIO(data))


def _backup_export_filename() -> str:
    stamp = time.strftime("%Y-%m-%d")
    return f"prism-backup-{stamp}.zip"


def _build_backup_manifest(file_list: list[str]) -> dict:
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "addon_id": g.ADDON_ID,
        "addon_version": g.ADDON.getAddonInfo("version"),
        "kodi_version": getattr(g, "KODI_FULL_VERSION", None) or str(getattr(g, "KODI_VERSION", "")),
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": sorted(file_list),
    }


def _member_is_safe(member: str) -> bool:
    if not member or member.startswith("/"):
        return False
    normalized = member.replace("\\", "/").rstrip("/")
    if not normalized or normalized == MANIFEST_NAME:
        return False
    return ".." not in normalized.split("/")


def _is_allowlisted_member(member: str) -> bool:
    if not _member_is_safe(member):
        return False
    normalized = member.replace("\\", "/").rstrip("/")
    if normalized in BACKUP_FILES:
        return True
    return any(normalized.startswith(f"{folder}/") for folder in BACKUP_FOLDERS)


def _resolve_member_dest(userdata: str, member: str) -> str | None:
    if not _member_is_safe(member):
        return None
    normalized = member.replace("\\", "/").rstrip("/")
    userdata_abs = os.path.abspath(userdata)
    target = os.path.abspath(os.path.join(userdata, normalized.replace("/", os.sep)))
    if target != userdata_abs and not target.startswith(userdata_abs + os.sep):
        return None
    return tools.validate_path(target)


def _is_prism_settings_payload(head: bytes | str) -> bool:
    if not isinstance(head, str):
        try:
            head = head.decode("utf-8", errors="ignore")
        except Exception:
            return False
    return "<settings" in head and 'id="plugin.video.prism"' in head


def _is_prism_settings_file(path: str, *, member_name: str | None = None, archive=None) -> bool:
    if not path and archive is None:
        return False
    try:
        if archive is not None and member_name:
            head = archive.read(member_name)[:4096]
        else:
            payload = _read_bytes(path)
            if payload is None:
                return False
            head = payload[:4096]
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return _is_prism_settings_payload(head)


def _is_prism_backup_zip(path: str) -> bool:
    if not path or not xbmcvfs.exists(path):
        return False
    try:
        with _open_zip_archive(path) as archive:
            names = archive.namelist()
            if MANIFEST_NAME in names:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
                fmt = manifest.get("format")
                if fmt == BACKUP_FORMAT and manifest.get("addon_id") == g.ADDON_ID:
                    return True
                if fmt == LEGACY_BACKUP_FORMAT and manifest.get("addon_id") == g.ADDON_ID:
                    return True
            if "settings.xml" in names:
                return _is_prism_settings_file(path, member_name="settings.xml", archive=archive)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError, ValueError):
        return False
    return False


def _collect_export_entries(userdata: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for filename in sorted(BACKUP_FILES):
        full_path = _vfs_join(userdata, filename)
        if xbmcvfs.exists(full_path):
            entries.append((full_path, filename))
    for folder in sorted(BACKUP_FOLDERS):
        folder_path = _vfs_join(userdata, folder)
        if _try_listdir(folder_path) is None:
            continue
        for full_path, rel_path in _vfs_walk_files(folder_path):
            arc_name = f"{folder}/{rel_path}".replace("\\", "/")
            entries.append((full_path, arc_name))
    return entries


def _release_userdata_locks() -> None:
    try:
        from resources.lib.database.session import get_sync_database, reset_sync_database

        db = get_sync_database()
        if hasattr(db, "close"):
            db.close()
        reset_sync_database()
    except Exception:
        pass

    try:
        from resources.lib.meta.display_store import reset_display_meta_store

        reset_display_meta_store()
    except Exception:
        pass

    try:
        cache = getattr(g, "CACHE", None)
        if cache is not None and hasattr(cache, "close"):
            cache.close()
    except Exception:
        pass

    gc.collect()


def _reload_settings_after_import() -> None:
    try:
        if getattr(g, "SETTINGS_CACHE", None):
            g.SETTINGS_CACHE.clear_cache()
        from resources.lib.modules.settings_hot_cache import warm_settings_dict

        warm_settings_dict()
    except Exception:
        g.log_stacktrace()


def _prompt_restart_after_import() -> None:
    if xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30904)):
        xbmc.executebuiltin("RestartApp")
        return
    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30905))


def _queue_deferred_file(filename: str, data: bytes, deferred_files: list[str]) -> bool:
    deferred_root = _deferred_restore_path()
    if not xbmcvfs.exists(deferred_root):
        xbmcvfs.mkdirs(deferred_root)
    dest = _vfs_join(deferred_root, filename.replace("/", os.sep))
    if not _overwrite_file(dest, data):
        return False
    if filename not in deferred_files:
        deferred_files.append(filename)
    return True


def _write_deferred_manifest(deferred_files: list[str]) -> None:
    if not deferred_files:
        return
    manifest_path = _vfs_join(_deferred_restore_path(), DEFERRED_MANIFEST)
    payload = json.dumps(
        {"userdata": _userdata_path(), "files": sorted(deferred_files)},
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    _overwrite_file(manifest_path, payload)


def apply_deferred_db_restore() -> None:
    """Swap deferred DB files into userdata before any DB singletons open."""
    deferred_root = tools.validate_path(_deferred_restore_path())
    manifest_path = _vfs_join(deferred_root, DEFERRED_MANIFEST)
    if not xbmcvfs.exists(manifest_path):
        return

    try:
        manifest = json.loads(_read_bytes(manifest_path) or b"{}")
        userdata = manifest.get("userdata") or _userdata_path()
        files = manifest.get("files") or []
        still_failed: list[str] = []
        applied = 0
        for rel in files:
            src = _vfs_join(deferred_root, rel.replace("/", os.sep))
            dest = os.path.join(userdata, rel.replace("/", os.sep))
            if not xbmcvfs.exists(src):
                continue
            try:
                if _overwrite_file_from_source(src, dest):
                    applied += 1
                else:
                    still_failed.append(rel)
            except OSError:
                still_failed.append(rel)
        if still_failed:
            _write_deferred_manifest(still_failed)
            g.log(
                f"Backup import: {len(still_failed)} deferred file(s) still could not be applied on startup",
                "warning",
            )
        else:
            _remove_tree(deferred_root)
        if applied:
            g.log(f"Backup import: applied {applied} deferred file(s) on startup", "info")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        g.log_stacktrace()


def export_backup() -> None:
    export_dir = xbmcgui.Dialog().browse(
        3,
        f"{g.ADDON_NAME}: {g.get_language_string(30892)}",
        "files",
    )
    if not export_dir:
        return

    filename = _backup_export_filename()
    dest = _vfs_join(_normalize_path(export_dir), filename)
    if not xbmcgui.Dialog().yesno(
        g.ADDON_NAME,
        g.get_language_string(30901).format(filename),
    ):
        return

    userdata = _userdata_path()
    if not xbmcvfs.exists(userdata):
        xbmcvfs.mkdirs(userdata)

    entries = _collect_export_entries(userdata)
    if not entries:
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30906))
        return

    try:
        buffer = BytesIO()
        arc_names: list[str] = []
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for full_path, rel_path in entries:
                payload = _read_bytes(full_path)
                if payload is None:
                    continue
                archive.writestr(rel_path, payload)
                arc_names.append(rel_path)
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(_build_backup_manifest(arc_names), indent=2, sort_keys=True),
            )
        if not _write_bytes(dest, buffer.getvalue()):
            xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30898))
            return
    except OSError:
        g.log_stacktrace()
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30898))
        return

    g.log(f"Backup export: wrote {len(arc_names)} file(s) to {dest}", "info")
    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30902).format(dest))


def _import_settings_xml(source: str) -> None:
    if not _is_prism_settings_file(source):
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30893))
        return

    if not xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30894)):
        return

    userdata_path = tools.validate_path(g.ADDON_USERDATA_PATH)
    if not xbmcvfs.exists(userdata_path):
        xbmcvfs.mkdir(userdata_path)

    backup_path = f"{g.SETTINGS_PATH}.bak"
    if xbmcvfs.exists(g.SETTINGS_PATH):
        if xbmcvfs.exists(backup_path):
            xbmcvfs.delete(backup_path)
        xbmcvfs.copy(g.SETTINGS_PATH, backup_path)

    if xbmcvfs.exists(g.SETTINGS_PATH):
        xbmcvfs.delete(g.SETTINGS_PATH)

    if xbmcvfs.copy(source, g.SETTINGS_PATH):
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30897))
        return

    if xbmcvfs.exists(backup_path):
        xbmcvfs.copy(backup_path, g.SETTINGS_PATH)
    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30898))


def _import_backup_zip(source: str) -> None:
    source = tools.validate_path(tools.translate_path(source))
    if not _is_prism_backup_zip(source):
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30903))
        return

    if not xbmcgui.Dialog().yesno(g.ADDON_NAME, g.get_language_string(30900)):
        return

    userdata = _userdata_path()
    if not xbmcvfs.exists(userdata):
        xbmcvfs.mkdirs(userdata)

    _release_userdata_locks()

    restored = 0
    deferred_files: list[str] = []
    try:
        with _open_zip_archive(source) as archive:
            for member in archive.namelist():
                if not _is_allowlisted_member(member):
                    continue
                if member.endswith("/"):
                    target_dir = _resolve_member_dest(userdata, member)
                    if target_dir:
                        xbmcvfs.mkdirs(_normalize_path(target_dir))
                    continue
                dest = _resolve_member_dest(userdata, member)
                if not dest:
                    continue
                payload = archive.read(member)
                basename = os.path.basename(member.replace("\\", "/"))
                if _overwrite_file(dest, payload):
                    restored += 1
                    continue
                if basename in DB_FILENAMES and _queue_deferred_file(basename, payload, deferred_files):
                    g.log(f"Backup import: deferred locked DB file {basename}", "warning")
                    restored += 1
                else:
                    g.log(f"Backup import: failed to restore {member}", "warning")
    except (OSError, zipfile.BadZipFile, ValueError):
        g.log_stacktrace()
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30898))
        return

    if deferred_files:
        _write_deferred_manifest(deferred_files)

    if restored == 0:
        xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30898))
        return

    g.log(f"Backup import: restored {restored} file(s)", "info")
    _reload_settings_after_import()
    _prompt_restart_after_import()


def import_backup() -> None:
    source = xbmcgui.Dialog().browse(
        1,
        f"{g.ADDON_NAME}: {g.get_language_string(30891)}",
        "files",
        "",
        False,
        False,
    )
    if not source:
        return

    source = tools.validate_path(source)
    lowered = source.lower()
    if lowered.endswith(".zip"):
        _import_backup_zip(source)
        return
    if lowered.endswith(".xml"):
        _import_settings_xml(source)
        return
    xbmcgui.Dialog().ok(g.ADDON_NAME, g.get_language_string(30903))
