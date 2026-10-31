"""The updater: finds a newer release, downloads and verifies its installer, and runs it.

A release publishes latest.json, a manifest signed with the release key (latest.json.sig, see
update_key.py), and the installer it describes. See docs/API.md, "Updates and releases".

UpdateState.state moves like this:

    idle -> checking -> idle (an update is available or not) | error (the check failed)
    idle | error -> downloading -> verifying -> installing   install(), on the update-install thread
    downloading | verifying | installing -> error            a failed step, or the installer failed

"installing" ends with the installer closing this app (POST /api/quit) and starting the new version.

Threads (all daemons, so none keeps the app alive): update-schedule runs the automatic checks
between start() and stop(); update-install downloads, verifies and launches one installer at a
time, then watches it, so an installer that fails while this app still runs is reported.
"""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import ed25519, story
from .update_key import PUBLIC_KEY
from .version import __version__

log = logging.getLogger('story_studio')
REPOSITORY = 'https://github.com/soenderby/story-studio'
LATEST_URL = REPOSITORY + '/releases/latest/download/'  # A plain redirect to the asset, not the rate-limited API.
DOWNLOAD_URL = REPOSITORY + '/releases/download/v{version}/{installer}'
URL_VARIABLE = 'STORY_STUDIO_UPDATE_URL'  # Tests and end-to-end runs serve their own release there.
INSTALLER_ARGS = ('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/RELAUNCH')
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1'}

MAX_MANIFEST = 64 * 1024
MAX_SIGNATURE = 1024
MAX_INSTALLER = 512 * 1024 * 1024
MAX_NOTES = 100_000
TIMEOUT = 15  # Seconds, for each manifest request.
DOWNLOAD_TIMEOUT = 60  # Seconds without data before a download fails.
CHUNK = 1 << 16
SAVE_WAIT = 30  # How long a save in flight may delay the installer.

SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
                    r'(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?'
                    r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')
INSTALLER_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe')
SHA256 = re.compile(r'[0-9a-f]{64}')
BUSY = ('downloading', 'verifying', 'installing')


class UpdateError(Exception):
    """A failed check or install. The message is shown to the user as is."""


class Conflict(Exception):
    """install() cannot start now: nothing to install, or an install or check is already running (HTTP 409)."""


def version_key(text):
    """Semver precedence (semver.org, section 11) as a sortable tuple. Build metadata is ignored."""
    match = SEMVER.fullmatch(text) if isinstance(text, str) and len(text) <= 64 else None
    if not match:
        raise ValueError(f'Not a version: {text!r}')
    pre = match[4]
    # Numeric identifiers sort before alphanumeric ones; more identifiers with equal leading ones sort last.
    identifiers = tuple((0, int(part), '') if part.isdigit() else (1, 0, part) for part in pre.split('.')) if pre else ()
    return int(match[1]), int(match[2]), int(match[3]), 0 if pre else 1, identifiers


def is_prerelease(text):
    match = SEMVER.fullmatch(text) if isinstance(text, str) else None
    return bool(match and match[4])


def offered(latest, current):
    """Whether `latest` is an update for `current`: newer, and a prerelease only for a prerelease."""
    try:
        if is_prerelease(latest) and not is_prerelease(current):
            return False
        return version_key(latest) > version_key(current)
    except ValueError:
        return False


def verify_signature(manifest, signature, public_key):
    """Raise UpdateError unless `signature` (latest.json.sig: base64, surrounding whitespace allowed) signs `manifest`."""
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError):
        raw = b''
    if not ed25519.verify(public_key, manifest, raw):
        raise UpdateError('The update information is not signed by the Story Studio release key, so it was ignored.')


def _reject_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f'{name} is not JSON')


def parse_manifest(data):
    """The release described by latest.json, validated strictly. Raises UpdateError."""
    def bad(detail):
        return UpdateError(f'The update information is malformed ({detail}).')

    if len(data) > MAX_MANIFEST:
        raise bad('too large')
    try:
        manifest = json.loads(data.decode('utf-8'), object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise bad('not valid JSON') from None
    if not isinstance(manifest, dict):
        raise bad('not a JSON object')

    def field(name, kind):
        value = manifest.get(name)
        if not isinstance(value, kind) or isinstance(value, bool):
            raise bad(f'"{name}" is missing or not a {"number" if kind is int else "string"}')
        return value

    version, installer, sha256 = field('version', str), field('installer', str), field('sha256', str)
    size, notes, published = field('size', int), field('notes', str), field('publishedAt', str)
    try:
        version_key(version)
    except ValueError:
        raise bad('"version" is not a version such as 1.2.0') from None
    if not INSTALLER_NAME.fullmatch(installer):
        raise bad('"installer" is not a plain .exe file name')
    if not SHA256.fullmatch(sha256):
        raise bad('"sha256" is not 64 lowercase hex digits')
    if not 0 < size <= MAX_INSTALLER:
        raise bad('"size" is out of range')
    if len(notes) > MAX_NOTES:
        raise bad('"notes" is too long')
    if parse_time(published) is None:
        raise bad('"publishedAt" is not an ISO 8601 time')
    return {'version': version, 'installer': installer, 'sha256': sha256, 'size': size, 'notes': notes,
            'publishedAt': published}


def parse_time(value):
    """Seconds since the epoch of an ISO 8601 time (naive means UTC), or None."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def is_local(url):
    return urlsplit(url).hostname in LOCAL_HOSTS


class SafeRedirect(HTTPRedirectHandler):
    """Follows redirects (GitHub sends release assets to its CDN), but only to HTTPS, or within localhost."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https' and not (is_local(newurl) and is_local(req.full_url)):
            raise HTTPError(newurl, code, 'Refused a redirect away from HTTPS', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def describe(error):
    """A sentence for the user about a failed request or step."""
    if isinstance(error, UpdateError):
        return str(error)
    if isinstance(error, HTTPError):
        if error.code == 404:
            return 'The update server has no such file (HTTP 404).'
        return f'The update server answered HTTP {error.code}.'
    reason = getattr(error, 'reason', error)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'The update server did not answer in time.'
    if isinstance(error, URLError):
        return f'Could not reach the update server ({reason}).'
    return str(error) or error.__class__.__name__


def launch_detached(args):
    """Start the installer so that it outlives this app, which it closes. Returns the Popen."""
    # The installer relaunches the new StoryStudio.exe with this environment: it must start afresh, not as
    # a child of this frozen app (PyInstaller's documented way to launch another frozen program).
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('_PYI_', '_MEIPASS'))}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL,
               'close_fds': True, 'cwd': str(Path(args[0]).parent), 'env': env}
    if sys.platform != 'win32':
        return subprocess.Popen(args, start_new_session=True, **options)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:  # Also leave a job object we may run in, unless the job forbids that.
        return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
    except OSError:
        return subprocess.Popen(args, creationflags=flags, **options)


class Updater:
    """state, check, skip and install are what the server calls; start and stop are the app's.

    home: app-data folder (default: the one holding settings.json); installers go to home/updates.
    base_url: where latest.json, latest.json.sig and, only when given, the installer are served
    (default: env STORY_STUDIO_UPDATE_URL, else GitHub). public_key, launcher, packaged, current and
    clock stand in for the release key, process launch, packaged-exe test, app version and time in tests.
    """
    START_DELAY = 10  # The first automatic check, after start.
    INTERVAL = 24 * 3600  # Between automatic checks, measured from lastUpdateCheck in settings.json.
    RETRY = 3600  # After a failed check.
    MAX_SLEEP = 3600  # The schedule is re-evaluated at least this often (sleep, clock changes, settings).
    MIN_SLEEP = 60
    STILL_RUNNING = 20  # An installer that succeeded should have closed this app by then.

    def __init__(self, settings, home=None, base_url=None, public_key=None, launcher=None, packaged=None,
                 current=__version__, clock=time.time):
        self.settings = settings
        self.home = Path(home) if home else Path(settings.path).parent
        self.folder = self.home / 'updates'
        configured = base_url or os.environ.get(URL_VARIABLE)
        self.custom_url = bool(configured)
        self.base_url = (configured or LATEST_URL).rstrip('/') + '/'
        self.public_key = public_key or base64.b64decode(PUBLIC_KEY)
        self.launcher = launcher or launch_detached
        self.packaged = (getattr(sys, 'frozen', False) and sys.platform == 'win32') if packaged is None else packaged
        self.current = current
        self.clock = clock
        self.lock = threading.RLock()  # Guards release, status, progress, error, worker and retry_at.
        self.check_lock = threading.Lock()  # One check at a time.
        self.release = None  # The verified manifest of the newest release considered.
        self.status, self.progress, self.error = 'idle', 0, None
        self.worker = None
        self.retry_at = None
        self.stopping = threading.Event()
        self.thread = None
        self.remote = build_opener(SafeRedirect)  # Uses the system or environment proxy settings.
        self.local = build_opener(ProxyHandler({}), SafeRedirect)  # Localhost never goes through a proxy.

    # ---------- State ----------

    def available(self):
        with self.lock:
            return bool(self.release) and offered(self.release['version'], self.current)

    def state(self):
        with self.lock:
            release = self.release
            latest = release['version'] if release else None
            return {'current': self.current, 'latest': latest, 'available': self.available(),
                    'skipped': latest is not None and latest == self.settings.get('skippedVersion'),
                    'notes': release['notes'] if release else '', 'publishedAt': release['publishedAt'] if release else None,
                    'state': self.status, 'progress': round(self.progress, 4), 'error': self.error,
                    'lastCheck': self.settings.get('lastUpdateCheck')}

    def stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    # ---------- Network ----------

    def open(self, url, timeout):
        parts = urlsplit(url)
        local = parts.hostname in LOCAL_HOSTS
        if parts.scheme != 'https' and not (local and parts.scheme == 'http'):
            raise UpdateError('Updates are only downloaded over HTTPS.')
        request = Request(url, headers={'User-Agent': f'StoryStudio/{self.current}', 'Accept': '*/*'})
        return (self.local if local else self.remote).open(request, timeout=timeout)

    def download(self, url, limit):
        """At most `limit` bytes from url; more is an error."""
        with self.open(url, TIMEOUT) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            name = urlsplit(url).path.rsplit('/', 1)[-1]
            raise UpdateError(f'The update information is malformed ({name} is too large).')
        return data

    def installer_url(self, release):
        name = quote(release['installer'])
        if self.custom_url:
            return self.base_url + name
        return DOWNLOAD_URL.format(version=quote(release['version']), installer=name)

    def fetch(self):
        """The latest release: its manifest, signature-checked, then validated. Raises on any failure."""
        try:
            manifest = self.download(self.base_url + 'latest.json', MAX_MANIFEST)
            signature = self.download(self.base_url + 'latest.json.sig', MAX_SIGNATURE)
        except (OSError, ValueError, HTTPException) as error:  # URLError, HTTPError and timeouts are OSErrors.
            raise UpdateError(describe(error)) from None
        verify_signature(manifest, signature, self.public_key)
        release = parse_manifest(manifest)
        release['url'] = self.installer_url(release)
        return release

    # ---------- Check and skip ----------

    def check(self):
        """Check now. A failed check reports state "error" with its message, and never offers an update.
        While an update is being installed, only reports the state."""
        with self.check_lock:
            with self.lock:
                if self.status in BUSY:
                    return self.state()
                self.status, self.error, self.progress = 'checking', None, 0
            try:
                release = self.fetch()
                if release and is_prerelease(release['version']) and not is_prerelease(self.current):
                    release = None  # Stable installs ignore prereleases.
            except Exception as error:
                log.warning('Update check failed: %s', describe(error))
                with self.lock:
                    self.release, self.status, self.error = None, 'error', describe(error)
                    self.retry_at = self.clock() + self.RETRY
            else:
                with self.lock:
                    self.release, self.status, self.retry_at = release, 'idle', None
                if release:
                    log.info('Update check: latest release %s, this is %s%s', release['version'], self.current,
                             ' (update available)' if self.available() else '')
            self.settings.update(lastUpdateCheck=self.stamp())
            return self.state()

    def skip(self, version):
        if not isinstance(version, str) or len(version) > 64 or not SEMVER.fullmatch(version):
            raise ValueError('Expected a version such as "1.2.0".')
        self.settings.update(skippedVersion=version)
        return self.state()

    # ---------- Automatic checks ----------

    def next_check(self):
        """Seconds until the next automatic check is due (0: now), or None when automatic checks are off."""
        if not self.settings.get('checkUpdates'):
            return None
        now = self.clock()
        with self.lock:
            if self.retry_at is not None:
                return max(0.0, self.retry_at - now)
        last = parse_time(self.settings.get('lastUpdateCheck'))
        if last is None or last > now:  # Never checked, or the clock went back.
            return 0.0
        return max(0.0, last + self.INTERVAL - now)

    def start(self):
        """At app start: tidy updates/, then run the automatic checks until stop()."""
        self.cleanup()
        if self.thread is None:
            self.thread = threading.Thread(target=self.schedule, name='update-schedule', daemon=True)
            self.thread.start()

    def stop(self):
        """At app exit. Also abandons a download in progress (its partial file is deleted)."""
        self.stopping.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)

    def schedule(self):
        delay = self.START_DELAY
        while not self.stopping.wait(delay):
            try:
                self.cleanup()  # Again: at start, the installer that started this version may still hold its file.
                wait = self.next_check()
                if wait == 0:
                    self.check()
                    wait = self.next_check()
            except Exception:
                log.exception('Automatic update check failed')
                wait = self.RETRY
            delay = self.MAX_SLEEP if wait is None else min(max(wait, self.MIN_SLEEP), self.MAX_SLEEP)

    def cleanup(self):
        """Delete leftover installers and partial downloads in updates/, keeping only the newest install log."""
        with self.lock:  # install() waits, so a fresh download is never deleted.
            if self.status in BUSY or not self.folder.is_dir():
                return
            logs = []
            for entry in self.folder.iterdir():
                if entry.is_file() and not entry.is_symlink() and entry.name.startswith('install-') and entry.suffix == '.log':
                    logs.append(entry)
                elif entry.is_file() or entry.is_symlink():
                    self.remove(entry)
            logs.sort(key=lambda entry: entry.stat().st_mtime)
            for entry in logs[:-1]:
                self.remove(entry)

    @staticmethod
    def remove(path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:  # Such as the installer still running from it.
            log.info('Could not delete %s yet: %s', path.name, error)

    # ---------- Install ----------

    def install(self):
        """Start download -> verify -> install on a worker thread; returns the state ("downloading")."""
        if not self.packaged:
            raise NotImplementedError('Only the installed Story Studio app for Windows can install updates. '
                                      f'Download the new version from {REPOSITORY}/releases instead.')
        with self.lock:
            if self.status in BUSY:
                raise Conflict('An update is already being installed.')
            if self.status == 'checking':
                raise Conflict('Story Studio is checking for updates. Try again in a moment.')
            if not self.available():
                raise Conflict('No update is available. Check for updates first.')
            self.status, self.progress, self.error = 'downloading', 0, None
            self.worker = threading.Thread(target=self.run_install, args=(dict(self.release),), name='update-install',
                                           daemon=True)
            self.worker.start()
            return self.state()

    def fail(self, message):
        log.warning('Update failed: %s', message)
        with self.lock:
            self.status, self.error, self.progress = 'error', message, 0

    def run_install(self, release):
        log_path = self.folder / f'install-{release["version"]}.log'
        try:
            installer = self.fetch_installer(release)
            try:
                with story.saves_paused(SAVE_WAIT):  # No save is in flight when the installer starts.
                    with self.lock:
                        self.status, self.progress = 'installing', 1
                    process = self.launcher([str(installer), *INSTALLER_ARGS, f'/LOG={log_path}'])
            except TimeoutError:
                raise UpdateError('A story is still being saved. Try again in a moment.') from None
            except OSError as error:
                raise UpdateError(f'The installer could not be started: {error}') from None
            log.info('Started the installer for Story Studio %s (log: %s)', release['version'], log_path)
        except Exception as error:
            if not self.stopping.is_set():
                self.fail(describe(error))
            return
        self.watch(process, log_path)

    def watch(self, process, log_path):
        """The installer normally closes this app before it exits. If it exits while we still run, say so."""
        try:
            code = process.wait()
        except Exception as error:
            log.warning('Lost track of the installer: %s', error)
            return
        if code == 0:
            if self.stopping.wait(self.STILL_RUNNING):
                return
            self.fail(f'The installer finished, but Story Studio was not restarted. Details are in {log_path}')
        elif not self.stopping.is_set():
            self.fail(f'The installer stopped with an error (exit code {code}). Details are in {log_path}')

    def fetch_installer(self, release):
        """Download the installer to updates/<name>.part while hashing it, and rename it only once its
        size and SHA-256 match the signed manifest. Returns its path."""
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / release['installer']
        partial = self.folder / (release['installer'] + '.part')
        size, digest, received = release['size'], hashlib.sha256(), 0
        try:
            try:
                with self.open(release['url'], DOWNLOAD_TIMEOUT) as response, open(partial, 'wb') as out:
                    length = (response.headers.get('Content-Length') or '').strip()
                    if length.isdigit() and int(length) != size:
                        raise UpdateError(f'The download has the wrong size ({int(length)} bytes, expected {size}), '
                                          'so it was not installed.')
                    while chunk := response.read(CHUNK):
                        if self.stopping.is_set():
                            raise UpdateError('Story Studio is closing.')
                        received += len(chunk)
                        if received > size:
                            raise UpdateError(f'The download is larger than expected ({size} bytes), so it was not installed.')
                        digest.update(chunk)
                        out.write(chunk)
                        with self.lock:
                            self.progress = received / size
                    out.flush()
                    os.fsync(out.fileno())
            except (OSError, ValueError, HTTPException) as error:
                raise UpdateError('The download failed: ' + describe(error)) from None
            with self.lock:
                self.status = 'verifying'
            if received != size:
                raise UpdateError(f'The download is incomplete ({received} of {size} bytes), so it was not installed.')
            if digest.hexdigest() != release['sha256']:
                raise UpdateError('The download does not match the signed checksum, so it was not installed.')
            os.replace(partial, target)
        finally:
            self.remove(partial)
        return target
