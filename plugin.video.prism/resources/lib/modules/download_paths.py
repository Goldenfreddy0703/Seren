import os
import re
from urllib import parse

import xbmcvfs

from resources.lib.common import tools
from resources.lib.modules.globals import g

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')
_SEASON_EPISODE_RE = re.compile(r'(?i)s(\d{1,2})e(\d{1,2})')


def sanitize_path_component(name):
    if not name:
        return 'Unknown'
    name = str(name).strip().strip('.')
    name = _INVALID_PATH_CHARS.sub('', name)
    return name.strip() or 'Unknown'


def is_organize_enabled():
    return g.get_bool_setting('download.organize.enabled')


def is_anime_catalog(item_information):
    if not item_information:
        return False
    info = item_information.get('info') or {}
    if info.get('catalog') == 'anime':
        return True
    return bool(info.get('mal_id') or info.get('mal_show_id'))


def _item_info(item_information):
    return item_information.get('info') or {}


def resolve_show_title(item_information):
    info = _item_info(item_information)
    title = info.get('tvshowtitle') or info.get('title') or 'Unknown'
    if is_anime_catalog(item_information):
        from resources.lib.simkl.field_map import ensure_anime_title_slots, format_anime_display_name

        ensure_anime_title_slots(info)
        title = format_anime_display_name(info, fallback=title) or title
    return sanitize_path_component(title)


def resolve_movie_title(item_information):
    info = _item_info(item_information)
    title = info.get('title') or 'Unknown'
    if is_anime_catalog(item_information):
        from resources.lib.simkl.field_map import ensure_anime_title_slots, format_anime_display_name

        ensure_anime_title_slots(info)
        title = format_anime_display_name(info, fallback=title) or title
    return sanitize_path_component(title)


def resolve_library_root(item_information):
    if not g.get_bool_setting('download.organize.splitLibrary'):
        return ''
    info = _item_info(item_information)
    mediatype = info.get('mediatype')
    if mediatype == g.MEDIA_EPISODE:
        return 'Anime' if is_anime_catalog(item_information) else 'TV Shows'
    if mediatype == g.MEDIA_MOVIE:
        return 'Anime' if is_anime_catalog(item_information) else 'Movies'
    return ''


def parse_season_from_name(name):
    if not name:
        return None
    match = _SEASON_EPISODE_RE.search(str(name))
    if match:
        return int(match.group(1))
    return None


def resolve_season_folder(item_information, filename=None, inner_path=None):
    if not g.get_bool_setting('download.organize.tvSeasonFolders'):
        return ''
    season = None
    if g.get_int_setting('download.organize.multiselect') == 1:
        season = parse_season_from_name(inner_path or filename)
    if season is None:
        season_raw = _item_info(item_information).get('season')
        if season_raw is not None and str(season_raw).isdigit():
            season = int(season_raw)
    if season is None:
        return ''
    return f'Season {season:02d}'


def build_download_subdir(item_information, filename, inner_path=None):
    if not is_organize_enabled() or not item_information:
        return ''

    parts = []
    library_root = resolve_library_root(item_information)
    if library_root:
        parts.append(library_root)

    info = _item_info(item_information)
    mediatype = info.get('mediatype')

    if mediatype == g.MEDIA_EPISODE:
        parts.append(resolve_show_title(item_information))
        season_folder = resolve_season_folder(item_information, filename, inner_path)
        if season_folder:
            parts.append(season_folder)
    elif mediatype == g.MEDIA_MOVIE:
        title = resolve_movie_title(item_information)
        if g.get_bool_setting('download.organize.movieYear'):
            year = info.get('year')
            if year:
                title = f'{title} ({year})'
        parts.append(title)
    else:
        return ''

    return os.path.join(*parts) if parts else ''


def ensure_directory(path):
    if path and not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(tools.validate_path(path))


def join_download_path(storage_root, subdir, filename):
    storage_root = tools.validate_path(storage_root.rstrip('/\\'))
    filename = os.path.basename(parse.unquote(filename or ''))
    if subdir:
        dest_dir = os.path.join(storage_root, subdir.replace('/', os.sep))
        ensure_directory(dest_dir)
        return tools.validate_path(os.path.join(dest_dir, filename))
    ensure_directory(storage_root)
    return tools.validate_path(os.path.join(storage_root, filename))


def _normalize_path(path):
    return os.path.normpath(tools.validate_path(path))


def _move_file(source, dest):
    if xbmcvfs.rename(source, dest):
        return True
    g.log(f'Auto-move: rename failed, trying copy {source} -> {dest}', 'debug')
    if xbmcvfs.copy(source, dest):
        if xbmcvfs.delete(source):
            return True
        g.log(f'Auto-move: copied but failed to delete source: {source}', 'warning')
        return True
    return False


def move_to_local_library(completed_file_path):
    if not g.get_bool_setting('download.automoveToLocal'):
        return completed_file_path

    local_root = (g.get_setting('local.location') or '').strip()
    download_root = (g.get_setting('download.location') or '').strip()
    if not local_root or not download_root:
        g.log('Auto-move: download or local directory not configured', 'warning')
        return completed_file_path

    completed_file_path = _normalize_path(completed_file_path)
    download_root = _normalize_path(download_root.rstrip('/\\'))
    local_root = _normalize_path(local_root.rstrip('/\\'))

    if not xbmcvfs.exists(completed_file_path):
        g.log(f'Auto-move: completed file not found: {completed_file_path}', 'error')
        return completed_file_path

    if not xbmcvfs.exists(local_root):
        xbmcvfs.mkdir(local_root)

    try:
        relative = os.path.relpath(completed_file_path, download_root)
    except ValueError:
        g.log(f'Auto-move: cannot compute relative path for {completed_file_path}', 'warning')
        return completed_file_path

    if relative.startswith('..'):
        g.log(f'Auto-move: file outside download root: {completed_file_path}', 'warning')
        return completed_file_path

    dest = _normalize_path(os.path.join(local_root, relative))
    dest_dir = os.path.dirname(dest)
    if dest_dir and not xbmcvfs.exists(dest_dir):
        xbmcvfs.mkdirs(dest_dir)

    if xbmcvfs.exists(dest):
        g.log(f'Auto-move: destination already exists: {dest}', 'warning')
        return completed_file_path

    if not _move_file(completed_file_path, dest):
        g.log(f'Auto-move: move failed {completed_file_path} -> {dest}', 'error')
        return completed_file_path

    g.log(f'Auto-move: moved to {dest}', 'info')
    _cleanup_empty_dirs(os.path.dirname(completed_file_path), download_root)
    return dest


def _cleanup_empty_dirs(start_dir, stop_at):
    current = tools.validate_path(start_dir)
    stop_at = tools.validate_path(stop_at.rstrip('/\\'))
    while current and current.lower() != stop_at.lower():
        try:
            listing = xbmcvfs.listdir(current)
            if listing[0] or listing[1]:
                break
            if not xbmcvfs.rmdir(current):
                break
        except (OSError, ValueError):
            break
        current = os.path.dirname(current)
