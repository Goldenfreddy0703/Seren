import re

import xbmcvfs

from resources.lib.common import source_utils
from resources.lib.common import tools
from resources.lib.modules.cloud_scrapers import CloudScraper
from resources.lib.modules.globals import g

_SEASON_EPISODE_RE = re.compile(r'(?i)s(\d{1,2})e(\d{1,2})')
_SEASON_EPISODE_X_RE = re.compile(r'(?i)(?:^|[.\s_-])(\d{1,2})x(\d{1,2})(?:[.\s_-]|$)')
_SEASON_FOLDER_RE = re.compile(r'(?i)^season\s*0*(\d+)$')
_TITLE_TOKEN_RE = re.compile(r'[a-z0-9]+')


def _join_vfs_path(base, name):
    if base.endswith(('/', '\\')):
        return f"{base}{name}"
    if '/' in base:
        return f"{base}/{name}"
    return f"{base}\\{name}"


def _normalize_root(path):
    return (path or '').strip().rstrip('/\\').lower()


def local_scraping_enabled():
    return g.local_playback_available()


class LocalFileScraper(CloudScraper):
    def __init__(self, terminate_check):
        super().__init__(terminate_check)
        self.provider_name = 'Local'
        self.debrid_provider = 'local'

    def _is_enabled(self):
        return local_scraping_enabled()

    def _normalize_item(self, item):
        return item

    def _is_valid_pack(self, item):
        # Local library files often use release-group prefixes in the filename.
        # Path-aware matching happens later in _identify_items.
        return True

    def get_sources(self, item_information, simple_info=None):
        # CloudScraper only binds simple_info when show_title is present (episodes).
        if isinstance(simple_info, dict) and simple_info.get('title') and not simple_info.get('show_title'):
            self.simple_info = simple_info
        return super().get_sources(item_information, simple_info)

    def _resolve_root_path(self, path):
        """Return the first VFS path variant Kodi can access for a configured folder."""
        stripped = (path or '').strip()
        if not stripped or stripped.lower() == 'userdata':
            return None

        candidates = []
        for candidate in (stripped, stripped.rstrip('/\\')):
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        try:
            validated = tools.validate_path(stripped.rstrip('/\\'))
            if validated and validated not in candidates:
                candidates.append(validated)
        except Exception:
            pass

        for candidate in candidates:
            if xbmcvfs.exists(candidate):
                return candidate.rstrip('/\\')

        g.log(f"Local scraper: root missing or inaccessible: {stripped}", 'info')
        return None

    def _local_roots(self):
        roots = []
        seen = set()
        for setting_id in ('local.location', 'download.location'):
            root = self._resolve_root_path(g.get_setting(setting_id))
            if not root:
                continue
            normalized = _normalize_root(root)
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(root)
        return roots

    def _fetch_cloud_items(self):
        roots = self._local_roots()
        items = []
        for root in roots:
            items.extend(self._walk_folder(root))

        g.log(
            f"Local scraper: indexed {len(items)} video files across {len(roots)} root(s)",
            'info' if not items else 'debug',
        )
        return source_utils.filter_files_for_resolving(items, self.item_information)

    @staticmethod
    def _parent_folder_name(path):
        parts = [part for part in (path or '').replace('\\', '/').split('/') if part]
        return parts[-2] if len(parts) > 1 else ''

    def _walk_folder(self, root):
        items = []
        stack = [root]

        while stack:
            if self.terminate_check and self.terminate_check():
                break

            current = stack.pop()
            try:
                dirs, files = xbmcvfs.listdir(current)
            except Exception as exc:
                g.log(f"Local scraper: unable to list {current} ({exc})", 'info')
                continue

            for directory in dirs:
                if directory in ('.', '..'):
                    continue
                stack.append(_join_vfs_path(current, directory))

            for filename in files:
                if not source_utils.is_file_ext_valid(filename):
                    continue

                full_path = _join_vfs_path(current, filename)
                try:
                    size_bytes = xbmcvfs.Stat(full_path).st_size()
                except Exception:
                    size_bytes = 0

                path_normalized = full_path.replace('\\', '/')
                items.append(
                    {
                        'release_title': filename,
                        'name': filename,
                        'folder_name': self._parent_folder_name(path_normalized),
                        'url': full_path,
                        'path': path_normalized,
                        'size': (size_bytes / 1024) / 1024 if size_bytes else 0,
                    }
                )

        return items

    def _episode_title_candidates(self):
        if not self.simple_info:
            return []

        titles = self._titles_from_simple_info(self.simple_info, 'show_title')
        info = (self.item_information or {}).get('info') or {}
        for key in ('tvshowtitle', 'title', 'title_en', 'title_romaji', 'originaltitle'):
            value = (info.get(key) or '').strip()
            if value and value not in titles:
                titles.append(value)
        for key in ('title_en', 'title_romaji', 'title'):
            value = (info.get(f'tvshow.{key}') or info.get(key) or '').strip()
            if value and value not in titles:
                titles.append(value)
        for alias in info.get('aliases', []) or []:
            if alias and alias not in titles:
                titles.append(alias)
        return titles

    @staticmethod
    def _titles_from_simple_info(simple_info, title_key, alias_key='show_aliases'):
        titles = []
        primary = (simple_info.get(title_key) or '').strip()
        if primary:
            titles.append(primary)
        for alias in simple_info.get(alias_key, []) or []:
            if alias and alias not in titles:
                titles.append(alias)
        return titles

    @staticmethod
    def _parse_episode_numbers(text):
        match = _SEASON_EPISODE_RE.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = _SEASON_EPISODE_X_RE.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None

    def _local_search_text(self, item):
        path = item.get('path', '')
        filename = item.get('release_title', '')
        return source_utils.clean_title(f"{path} {filename}")

    @staticmethod
    def _title_tokens(title):
        clean = source_utils.clean_title(title)
        return [token for token in _TITLE_TOKEN_RE.findall(clean) if len(token) >= 3]

    def _title_in_local_item(self, item, titles):
        haystack = self._local_search_text(item)
        for title in titles:
            clean_title = source_utils.clean_title(title)
            if clean_title and clean_title in haystack:
                return True

        show_folder = self._show_folder_from_path(item.get('path', ''))
        if show_folder:
            folder_clean = source_utils.clean_title(show_folder)
            for title in titles:
                if folder_clean and folder_clean in source_utils.clean_title(title):
                    return True
                title_tokens = self._title_tokens(title)
                folder_tokens = self._title_tokens(show_folder)
                if len(title_tokens) >= 2 and len(set(title_tokens) & set(folder_tokens)) >= 2:
                    return True
                if title_tokens and folder_tokens and title_tokens[0] in folder_tokens:
                    return True

        return False

    @staticmethod
    def _show_folder_from_path(path):
        if not path:
            return None
        parts = [part for part in path.replace('\\', '/').split('/') if part]
        for index, part in enumerate(parts):
            if _SEASON_FOLDER_RE.match(part) and index > 0:
                return parts[index - 1]
        return None

    def _target_episode_numbers(self):
        if not self.simple_info:
            return None

        season = self.simple_info.get('season_number')
        episode = self.simple_info.get('episode_number')
        if season in (None, '') or episode in (None, ''):
            return None

        try:
            return int(season), int(episode)
        except (TypeError, ValueError):
            return None

    def _matches_local_episode(self, item):
        target = self._target_episode_numbers()
        if not target:
            return False

        parsed = self._parse_episode_numbers(item.get('release_title', ''))
        if not parsed:
            parsed = self._parse_episode_numbers(item.get('path', ''))
        if not parsed or parsed != target:
            return False

        return self._title_in_local_item(item, self._episode_title_candidates())

    def _episode_match_title(self, item):
        match_title = source_utils.build_cloud_match_title(item)
        if not match_title:
            match_title = self._local_search_text(item)
        return match_title

    def _matches_shared_episode(self, item):
        if not self.simple_info or not self.episode_regex:
            return False
        return source_utils.cloud_episode_item_matches(
            self._episode_match_title(item),
            episode_regex=self.episode_regex,
            season_regex=self.season_regex,
            simple_info=self.simple_info,
        )

    def _matches_local_movie(self, item):
        if not self.simple_info:
            return False

        haystack = self._local_search_text(item)
        if 'sample' in haystack:
            return False

        titles = self._titles_from_simple_info(self.simple_info, 'title', alias_key='aliases')
        year = (self.simple_info.get('year') or '').strip()

        for title in titles:
            clean_title = source_utils.clean_title(title)
            if not clean_title or clean_title not in haystack:
                continue
            if year and year not in haystack:
                continue
            return True
        return False

    def _identify_items(self, cloud_items):
        sources = []

        if self.media_type == g.MEDIA_EPISODE:
            for item in cloud_items:
                search_text = self._local_search_text(item)
                if (
                    self._matches_shared_episode(item)
                    or (self.show_regex and self.show_regex(search_text))
                    or self._matches_local_episode(item)
                ):
                    sources.append(item)
        else:
            for item in cloud_items:
                if self._matches_local_movie(item):
                    sources.append(item)
                elif source_utils.filter_movie_title(
                    None,
                    self._local_search_text(item),
                    self.item_information['info']['title'],
                    {
                        'year': self.item_information.get('info', {}).get('year'),
                        'title': self.item_information.get('info').get('title'),
                    },
                ):
                    sources.append(item)

        if not sources and cloud_items:
            if self.media_type == g.MEDIA_EPISODE:
                show_title = (self.simple_info or {}).get('show_title', '')
                g.log(
                    f"Local scraper: no episode match for {show_title} "
                    f"S{self.simple_info.get('season_number')}E{self.simple_info.get('episode_number')} "
                    f"simkl_ep={self.simple_info.get('simkl_episode_number')} "
                    f"tvdb={self.simple_info.get('tvdb_season_number')}x{self.simple_info.get('tvdb_episode_number')} "
                    f"from {len(cloud_items)} indexed file(s)",
                    'info',
                )
            elif self.media_type == g.MEDIA_MOVIE:
                title = (self.simple_info or {}).get('title', '')
                year = (self.simple_info or {}).get('year', '')
                g.log(
                    f"Local scraper: no movie match for {title} ({year}) "
                    f"from {len(cloud_items)} indexed file(s)",
                    'info',
                )

        return sources

    def _finalise_identified_items(self, items):
        for item in items:
            item.update(
                {
                    'quality': source_utils.get_quality(item['release_title']),
                    'language': self.language,
                    'provider': self.provider_name,
                    'type': 'direct',
                    'info': source_utils.get_info(item['release_title']),
                    'size': item.get('size', 0),
                }
            )

        return items
