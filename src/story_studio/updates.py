"""Update state for the home page and editor banner. A stub until phase 4 replaces it with the real updater."""
import re

from .registry import now
from .version import __version__

VERSION = re.compile(r'\d{1,5}\.\d{1,5}\.\d{1,5}([-+][0-9A-Za-z.-]{1,40})?')


class Updater:
    """Never finds a release. The interface (state, check, skip, install) is what the server calls."""

    def __init__(self, settings):
        self.settings = settings
        self.latest = None
        self.notes = ''
        self.published_at = None
        self.status = 'idle'
        self.error = None

    def state(self):
        skipped = self.latest is not None and self.latest == self.settings.get('skippedVersion')
        return {'current': __version__, 'latest': self.latest, 'available': False, 'skipped': skipped,
                'notes': self.notes, 'publishedAt': self.published_at, 'state': self.status, 'progress': 0,
                'error': self.error, 'lastCheck': self.settings.get('lastUpdateCheck')}

    def fetch(self):
        """The latest release as {"version", "notes", "publishedAt"}, or None. The stub knows of none."""
        return None

    def check(self):
        """A failed check reports state "error" with its message, and never offers an update."""
        try:
            release = self.fetch()
        except Exception as error:
            self.status, self.error = 'error', str(error) or error.__class__.__name__
        else:
            self.status, self.error = 'idle', None
            if release:
                self.latest, self.notes, self.published_at = release['version'], release.get('notes', ''), release.get('publishedAt')
        self.settings.update(lastUpdateCheck=now())
        return self.state()

    def skip(self, version):
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError('Expected a version such as "1.2.0".')
        self.settings.update(skippedVersion=version)
        return self.state()

    def install(self):
        raise NotImplementedError('Installing updates is not available in this version yet.')
