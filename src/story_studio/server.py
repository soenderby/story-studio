"""HTTP server: the story list, each story's editor under /s/<id>/, and the update endpoints.

Listens on 127.0.0.1 only. Every request checks Host; every POST also checks Origin and the per-process token.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import threading
from urllib.parse import unquote, urlsplit

from . import desktop, paths
from .registry import Registry, Settings, now
from .story import MAX_BODY, PROJECT_NAME, Studio, byte_range, encode, media_type, prepare_story
from .updates import Conflict, Updater
from .version import __version__

SERVER_VERSION = 6  # Bump when the project format or API changes, so stale instances are detected.
log = logging.getLogger('story_studio')
# Only these types are served from web/. None of them is a media type, so a story's media is never shadowed.
WEB_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
             '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}
STORY_ROUTE = re.compile(r'/s/(s[0-9a-f]{16})(/.*)?')
STORY_ACTION = re.compile(r'/api/stories/(s[0-9a-f]{16})/(remove|relocate|reveal)')


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def folder_argument(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or '\x00' in value:
        raise ValueError('Expected a folder path.')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('Expected a full folder path, such as C:\\Users\\you\\Documents\\My story.')
    return path


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second server bind a port that is in use; see server_bind.
    allow_reuse_address = os.name != 'nt'

    def __init__(self, home, port=0, picker=None, revealer=None, updater=None, on_quit=None):
        self.home = Path(home)
        self.registry = Registry(self.home)
        self.settings = Settings(self.home)
        self.updater = updater or Updater(self.settings)
        self.picker = picker or desktop.pick_folder
        self.revealer = revealer or desktop.reveal_folder
        self.on_quit = on_quit
        self.token = secrets.token_urlsafe(32)
        self.studios = {}  # id -> (path, Studio): one per open story, each with its own lock.
        self.studios_lock = threading.Lock()
        self.pick_lock = threading.Lock()
        super().__init__(('127.0.0.1', port), Handler)

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        log.exception('Request from %s failed', client_address)

    def quit(self):
        (self.on_quit or self.shutdown)()

    def studio(self, sid):
        """The Studio of a registered story whose folder still holds its project; None when unknown or missing."""
        entry = self.registry.get(sid)
        if entry is None or not (Path(entry['path']) / PROJECT_NAME).is_file():
            return None
        with self.studios_lock:
            cached = self.studios.get(sid)
            if cached and cached[0] == entry['path']:
                return cached[1]
            studio = Studio(entry['path'])  # Raises for a corrupt project, which then stays untouched.
            self.studios[sid] = (entry['path'], studio)
            return studio

    def add_story(self, folder, title=None):
        """Register a folder, creating a blank project or migrating a legacy one first. (entry, created)."""
        studio = prepare_story(folder, (title or '').strip() or None)
        with studio.lock:
            project, _ = studio.read_project()
        entry, created = self.registry.add(studio.root, project['title'] or studio.root.name)
        self.forget(entry['id'])  # Re-adding may have migrated legacy descriptions.
        return entry, created

    def forget(self, sid):
        with self.studios_lock:
            self.studios.pop(sid, None)

    def story(self, entry):
        """The API's Story object. Title is cached in stories.json for when the folder is missing."""
        story = {'id': entry['id'], 'title': entry['title'], 'path': entry['path'], 'lastOpened': entry['lastOpened'],
                 'missing': True, 'cover': None, 'chapters': 0, 'media': 0}
        try:
            studio = self.studio(entry['id'])
            if studio:
                summary = studio.summary()
                summary['title'] = summary['title'] or entry['title']
                story.update(summary, missing=False)
                self.registry.update(entry['id'], title=summary['title'])
        except (ValueError, OSError) as error:
            story.update(missing=not Path(entry['path']).is_dir(), error=str(error))
        return story


class Handler(BaseHTTPRequestHandler):
    server_version = 'StoryStudio/' + __version__

    def hosts(self):
        return {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def trusted_host(self):
        return self.headers.get('Host') in self.hosts()

    def authorized(self):
        origin = self.headers.get('Origin')
        token = self.headers.get('X-Story-Token', '').encode('utf-8', 'replace')
        return (self.trusted_host() and (not origin or origin in {'http://' + host for host in self.hosts()})
                and secrets.compare_digest(token, self.server.token.encode()))

    def send_data(self, status, payload, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def json(self, status, value):
        self.send_data(status, encode(value))

    def redirect(self, location):
        self.send_response(301)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def send_asset(self, name):
        """A file from the app's own web/ folder, never from a story folder."""
        suffix = Path(name).suffix.lower()
        file = paths.WEB_DIR / name
        if '/' in name or '\\' in name or suffix not in WEB_TYPES or file.is_symlink() or not file.is_file():
            raise Refused(404, 'File not available.')
        self.send_data(200, file.read_bytes(), WEB_TYPES[suffix])

    def send_file(self, file, content_type, versioned):
        """Streams the file and honours byte ranges, which video seeking and thumbnails rely on."""
        size = file.stat().st_size
        span = byte_range(self.headers.get('Range'), size)
        if span is None:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        start, end, partial = span
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        # ?v= carries the file's mtime and size, so a versioned URL never changes content.
        self.send_header('Cache-Control', 'private, max-age=31536000, immutable' if versioned else 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            with file.open('rb') as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except ConnectionError:
            pass  # Browsers routinely abandon video requests while seeking.

    def require_studio(self, sid):
        if self.server.registry.get(sid) is None:
            raise Refused(404, 'Unknown story.')
        studio = self.server.studio(sid)
        if studio is None:
            raise Refused(404, "This story's folder is missing.")
        return studio

    def read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length < 0 or length > MAX_BODY:
            raise Refused(413, 'Request must be at most 10 MB.')
        body = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        return body

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.trusted_host():
            self.json(403, {'error': 'Localhost access only.'})
            return
        url = urlsplit(self.path)
        try:
            self.route_get(unquote(url.path), url.query)
        except Refused as error:
            self.json(error.status, {'error': str(error)})
        except ConnectionError:
            pass
        except (ValueError, OSError, KeyError) as error:
            log.warning('GET %s failed: %s', url.path, error)
            self.json(500, {'error': str(error)})

    def route_get(self, path, query):
        server = self.server
        if path == '/api/info':
            self.json(200, {'app': 'story-studio', 'version': SERVER_VERSION, 'appVersion': __version__})
        elif path == '/api/stories':
            self.json(200, {'token': server.token, 'stories': [server.story(entry) for entry in server.registry.list()]})
        elif path == '/api/update':
            self.json(200, server.updater.state())
        elif path == '/':
            self.send_asset('home.html')
        elif match := STORY_ROUTE.fullmatch(path):
            self.story_get(match[1], match[2], query)
        else:
            self.send_asset(path.removeprefix('/'))

    def story_get(self, sid, rest, query):
        server = self.server
        if rest is None:
            self.redirect(f'/s/{sid}/')  # The editor's relative URLs need the trailing slash.
        elif rest == '/':
            # Served for any well-formed id: the editor itself reports an unknown or missing story.
            if server.registry.get(sid) and server.studio(sid):
                server.registry.update(sid, lastOpened=now())
            self.send_asset('editor.html')
        elif rest == '/api/bootstrap':
            studio = self.require_studio(sid)
            with studio.lock:
                project, revision = studio.read_project()
            images, signature = studio.images()
            server.registry.update(sid, lastOpened=now(), title=project['title'] or server.registry.get(sid)['title'])
            self.json(200, {'project': project, 'revision': revision, 'token': server.token, 'images': images,
                            'signature': signature, 'projectFile': PROJECT_NAME, 'version': SERVER_VERSION,
                            'storyId': sid, 'appVersion': __version__})
        elif rest == '/api/images':
            images, signature = self.require_studio(sid).images()
            self.json(200, {'images': images, 'signature': signature})
        elif Path(rest).suffix.lower() in WEB_TYPES:
            self.send_asset(rest[1:])
        else:
            file = self.require_studio(sid).media_file(rest[1:])
            if file is None:
                raise Refused(404, 'File not available.')
            self.send_file(file, media_type(file.name), query.startswith('v='))

    def do_POST(self):
        if not self.authorized():
            self.json(403, {'error': 'Request not authorized by this local editor.'})
            return
        path = unquote(urlsplit(self.path).path)
        try:
            self.route_post(path, self.read_body())
        except Refused as error:
            self.json(error.status, {'error': str(error)})
        except NotImplementedError as error:
            self.json(501, {'error': str(error)})
        except desktop.PickerUnavailable as error:
            self.json(501, {'error': str(error)})
        except (ValueError, TypeError, KeyError) as error:
            self.json(400, {'error': str(error)})
        except (OSError, subprocess.SubprocessError) as error:
            log.warning('POST %s failed: %s', path, error)
            self.json(500, {'error': 'The operation failed: ' + str(error)})

    def route_post(self, path, body):
        server = self.server
        if path == '/api/stories/pick-folder':
            purpose = body.get('purpose', 'add')
            if purpose not in ('new', 'add'):
                raise ValueError('purpose must be "new" or "add".')
            if not server.pick_lock.acquire(blocking=False):
                raise Refused(409, 'A folder dialog is already open.')
            try:
                chosen = server.picker(purpose)
            finally:
                server.pick_lock.release()
            self.json(200, {'path': str(chosen)} if chosen else {'cancelled': True})
        elif path == '/api/stories':
            self.add_story(body)
        elif match := STORY_ACTION.fullmatch(path):
            self.story_action(match[1], match[2], body)
        elif path == '/api/quit':
            self.json(200, {'ok': True})
            self.wfile.flush()
            threading.Thread(target=server.quit, name='quit', daemon=True).start()
        elif path == '/api/update/check':
            self.json(200, server.updater.check())
        elif path == '/api/update/install':
            try:
                self.json(200, server.updater.install())
            except Conflict as error:
                raise Refused(409, str(error)) from None
        elif path == '/api/update/skip':
            self.json(200, server.updater.skip(body.get('version')))
        elif (match := STORY_ROUTE.fullmatch(path)) and match[2] == '/api/project':
            studio = self.require_studio(match[1])
            revision = studio.save(body.get('project'), body.get('revision'))
            if revision is None:
                raise Refused(409, 'The project file changed elsewhere. Reload before saving to avoid overwriting it.')
            server.registry.update(match[1], title=body['project']['title'] or server.registry.get(match[1])['title'])
            self.json(200, {'revision': revision, 'projectFile': PROJECT_NAME})
        else:
            raise Refused(404, 'Unknown endpoint.')

    def add_story(self, body):
        title = body.get('title')
        if title is not None and (not isinstance(title, str) or len(title) > 10000):
            raise ValueError('Invalid title.')
        entry, created = self.server.add_story(folder_argument(body.get('path')), title)
        self.json(201 if created else 200, {'story': self.server.story(entry)})

    def story_action(self, sid, action, body):
        server = self.server
        entry = server.registry.get(sid)
        if entry is None:
            raise Refused(404, 'Unknown story.')
        if action == 'remove':
            server.registry.remove(sid)  # Unregisters only. Files are never deleted.
            server.forget(sid)
            self.json(200, {'ok': True})
        elif action == 'relocate':
            folder = folder_argument(body.get('path')).resolve()
            if not (folder / PROJECT_NAME).is_file():
                raise ValueError(f'That folder has no {PROJECT_NAME}.')
            studio = Studio(folder)  # Validates the project before the registry points at it.
            server.forget(sid)
            moved = server.registry.relocate(sid, studio.root)
            self.json(200, {'story': server.story(moved)})
        else:
            if not Path(entry['path']).is_dir():
                raise Refused(404, "This story's folder is missing.")
            server.revealer(Path(entry['path']))
            self.json(200, {'ok': True})

    def log_request(self, code='-', size='-'):
        # Image polling and thumbnails would otherwise swamp the log; only failures are interesting.
        if str(getattr(code, 'value', code)).startswith(('4', '5')):
            log.info('%s "%s" %s', self.address_string(), self.requestline, getattr(code, 'value', code))

    def log_message(self, format, *args):
        log.warning('%s %s', self.address_string(), format % args)


def make_server(home, port=0, **options):
    """A server for the app data in `home`. Options: picker, revealer, updater, on_quit (see StudioServer)."""
    return StudioServer(home, port, **options)
