"""App data: the list of known story folders (stories.json) and settings.json."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import threading
import time

from .story import atomic_write, directory_key, encode

log = logging.getLogger('story_studio')
STORY_ID = re.compile(r's[0-9a-f]{16}')
DEFAULT_PORT = 8765
DEFAULT_SETTINGS = {'version': 1, 'port': DEFAULT_PORT, 'checkUpdates': True, 'skippedVersion': None, 'lastUpdateCheck': None}


def story_id(path):
    """Deterministic, so the same folder from Windows or WSL, or added twice, is one story."""
    return 's' + directory_key(path)[:16]


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def set_aside(path):
    """Keep an unreadable app-data file for inspection instead of overwriting or deleting it."""
    stamp = time.strftime('%Y%m%d-%H%M%S')
    target, count = path.with_name(f'{path.name}.corrupt-{stamp}'), 1
    while target.exists():
        target, count = path.with_name(f'{path.name}.corrupt-{stamp}-{count}'), count + 1
    try:
        path.replace(target)
        log.warning('Unreadable %s was renamed to %s', path.name, target.name)
    except OSError as error:
        log.warning('Unreadable %s could not be set aside: %s', path.name, error)


def load_json(path):
    """Parsed JSON object; None when the file is absent, or unreadable (it is then set aside)."""
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        set_aside(path)
        return None
    if not isinstance(data, dict) or data.get('version') != 1:
        set_aside(path)
        return None
    return data


def valid_entry(entry):
    return (isinstance(entry, dict) and isinstance(entry.get('id'), str) and STORY_ID.fullmatch(entry['id'])
            and isinstance(entry.get('path'), str) and entry['path']
            and all(entry.get(key) is None or isinstance(entry[key], str) for key in ('title', 'added', 'lastOpened')))


class Registry:
    """stories.json. Entries: {"id", "path", "title", "added", "lastOpened"}. Story folders are never touched."""

    def __init__(self, home):
        self.path = Path(home) / 'stories.json'
        self.lock = threading.RLock()
        data = load_json(self.path)
        stories = data.get('stories') if data else []
        if not isinstance(stories, list):
            set_aside(self.path)
            stories = []
        self.stories, seen = [], set()
        for entry in stories:
            if valid_entry(entry) and entry['id'] not in seen:
                seen.add(entry['id'])
                self.stories.append({'id': entry['id'], 'path': entry['path'], 'title': entry.get('title') or '',
                                     'added': entry.get('added'), 'lastOpened': entry.get('lastOpened')})
        if len(self.stories) != len(stories):
            set_aside(self.path)  # Keep the original, then save the entries that were readable.
            self.save()

    def save(self):
        with self.lock:
            atomic_write(self.path, encode({'version': 1, 'stories': self.stories}))

    def list(self):
        """Copies, most recently opened (or added) first."""
        with self.lock:
            stories = [dict(entry) for entry in self.stories]
        return sorted(stories, key=lambda entry: entry['lastOpened'] or entry['added'] or '', reverse=True)

    def get(self, sid):
        with self.lock:
            entry = next((entry for entry in self.stories if entry['id'] == sid), None)
            return dict(entry) if entry else None

    def add(self, path, title):
        """(entry, created). Adding a folder that is already registered returns the existing entry."""
        sid = story_id(path)
        with self.lock:
            existing = self.get(sid)
            if existing:
                return existing, False
            entry = {'id': sid, 'path': str(path), 'title': title, 'added': now(), 'lastOpened': None}
            self.stories.append(entry)
            self.save()
            return dict(entry), True

    def update(self, sid, **changes):
        with self.lock:
            for entry in self.stories:
                if entry['id'] == sid:
                    if any(entry.get(key) != value for key, value in changes.items()):
                        entry.update(changes)
                        self.save()
                    return dict(entry)
        return None

    def remove(self, sid):
        with self.lock:
            kept = [entry for entry in self.stories if entry['id'] != sid]
            if len(kept) == len(self.stories):
                return False
            self.stories = kept
            self.save()
            return True

    def relocate(self, sid, path):
        """Point an entry at a new folder. The id follows the folder; a folder already listed absorbs the entry."""
        new_id = story_id(path)
        with self.lock:
            entry = self.get(sid)
            if entry is None:
                return None
            if new_id != sid and self.get(new_id):
                self.remove(sid)
                return self.get(new_id)
            self.stories = [dict(item, id=new_id, path=str(path)) if item['id'] == sid else item for item in self.stories]
            self.save()
            return self.get(new_id)


class Settings:
    """settings.json, merged over the defaults. Unknown keys from newer versions are kept."""

    def __init__(self, home):
        self.path = Path(home) / 'settings.json'
        self.lock = threading.Lock()
        self.values = dict(DEFAULT_SETTINGS)
        self.values.update(load_json(self.path) or {})
        port = self.values.get('port')
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            self.values['port'] = DEFAULT_PORT

    def get(self, key):
        with self.lock:
            return self.values.get(key)

    def update(self, **changes):
        with self.lock:
            self.values.update(changes)
            atomic_write(self.path, encode(self.values))
