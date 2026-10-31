#!/usr/bin/env python3
"""Development-only mock of the Story Studio app server, for testing the web front end.

Not shipped and not imported by the package. It implements the global endpoints of docs/API.md
(story registry, a fake folder picker, a scriptable fake updater) and serves the per-story routes
by wrapping the existing Studio class. Phase 1's real server replaces it.

    python3 tools/mock_server.py --port 8792

Everything lives in temporary folders unless --home / --pick-root say otherwise. Dev-only
endpoints, which need no token:
    POST /__mock/pick     {"path": "..."} or {"cancelled": true}: the next folder-dialog answer
    POST /__mock/release  {"version", "notes", "fail": null | "check" | "download" | "verify"} or {}: the fake release
    GET  /__mock/state    registry, settings and update state
"""
import argparse
import json
import re
import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from story_studio import server as core  # noqa: E402

API_VERSION = 6
WEB_ASSETS = {'home.html', 'home.js', 'home.css', 'editor.html', 'editor.css', 'editor.js', 'editor-data.js',
              'update.js', 'icon.svg'}
CONTENT_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                 '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
                 '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif',
                 '.bmp': 'image/bmp', **core.VIDEO_TYPES}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def semver(value):
    return tuple(int(part) for part in re.findall(r'\d+', value or '')[:3])


class App:
    def __init__(self, args):
        self.args = args
        self.home = Path(args.home or tempfile.mkdtemp(prefix='story-studio-home-')).resolve()
        self.pick_root = Path(args.pick_root or tempfile.mkdtemp(prefix='story-studio-picks-')).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.pick_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.app_version = args.app_version
        self.studios = {}
        self.next_pick = None
        self.release = {'version': args.latest, 'notes': args.notes, 'fail': None} if args.latest else None
        self.update = {'state': 'idle', 'progress': 0, 'error': None, 'lastCheck': None, 'checkFailed': False}
        self.restarting = False
        self.httpd = None
        self.registry = self.read_json('stories.json', {'version': 1, 'stories': []})
        self.settings = self.read_json('settings.json', {'version': 1, 'port': args.port, 'checkUpdates': True,
                                                         'skippedVersion': None, 'lastUpdateCheck': None})

    # ---------- App data ----------
    def read_json(self, name, default):
        try:
            return json.loads((self.home / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def write_json(self, name, value):
        core.atomic_write(self.home / name, core.encode(value))

    def save_registry(self):
        self.write_json('stories.json', self.registry)

    # ---------- Stories ----------
    @staticmethod
    def story_id(path):
        return 's' + core.directory_key(path)[:16]

    def entry(self, story_id):
        return next((s for s in self.registry['stories'] if s['id'] == story_id), None)

    def studio(self, story_id):
        """The Studio for a registered story whose folder still holds a project, else None."""
        entry = self.entry(story_id)
        if not entry or not (Path(entry['path']) / core.PROJECT_NAME).is_file():
            self.studios.pop(story_id, None)
            return None
        if story_id not in self.studios:
            self.studios[story_id] = core.Studio(entry['path'])
        return self.studios[story_id]

    def summary(self, entry):
        story = {'id': entry['id'], 'title': entry.get('title') or Path(entry['path']).name, 'path': entry['path'],
                 'lastOpened': entry.get('lastOpened'), 'missing': True, 'cover': None, 'chapters': 0, 'media': 0}
        try:
            studio = self.studio(entry['id'])
            if studio:
                project, _ = studio.read_project()
                images, _ = studio.images()
                present = {image['file'] for image in images}
                files = [f for c in project['chapters'] for b in c['blocks']
                         for f in ([b['file']] if b['type'] == 'image' else [x['file'] for x in b.get('items', [])])]
                story.update(missing=False, title=project['title'] or story['title'], chapters=len(project['chapters']),
                             media=len(images), cover=next((f for f in files if f in present), None))
                entry['title'] = story['title']
        except (OSError, ValueError, KeyError):
            pass
        return story

    def stories(self):
        with self.lock:
            stories = [self.summary(entry) for entry in self.registry['stories']]
            self.save_registry()
        return sorted(stories, key=lambda s: s['lastOpened'] or '', reverse=True)

    def add(self, path, title=None):
        root = Path(path).expanduser()
        if not root.is_dir():
            raise ValueError('That folder does not exist.')
        root = root.resolve()
        story_id = self.story_id(root)
        with self.lock:
            existing = self.entry(story_id)
            if existing:
                return 200, self.summary(existing)
            if not (root / core.PROJECT_NAME).exists():
                project = dict(core.read_editor_data(core.WEB_DIR / 'editor-data.js')['seed'])
                project.update(id='story-' + secrets.token_hex(6), title=(title or '').strip() or root.name)
                core.atomic_write(root / core.PROJECT_NAME, core.encode(core.validate_project(project)))
            self.registry['stories'].append({'id': story_id, 'path': str(root), 'title': title or root.name,
                                             'added': now(), 'lastOpened': None})
            story = self.summary(self.entry(story_id))
            self.save_registry()
        return 201, story

    def remove(self, story_id):
        with self.lock:
            self.registry['stories'] = [s for s in self.registry['stories'] if s['id'] != story_id]
            self.studios.pop(story_id, None)
            self.save_registry()

    def relocate(self, story_id, path):
        root = Path(path).expanduser()
        if not (root / core.PROJECT_NAME).is_file():
            raise ValueError(f'That folder has no {core.PROJECT_NAME}. Choose the folder this story was moved to.')
        root = root.resolve()
        new_id = self.story_id(root)
        with self.lock:
            entry = self.entry(story_id)
            if not entry:
                raise KeyError('Unknown story.')
            if new_id != story_id and self.entry(new_id):
                raise ValueError('That folder is already in your list as another story.')
            self.studios.pop(story_id, None)
            entry.update(id=new_id, path=str(root))
            story = self.summary(entry)
            self.save_registry()
        return story

    def opened(self, story_id):
        with self.lock:
            entry = self.entry(story_id)
            if entry and self.studio(story_id):
                entry['lastOpened'] = now()
                self.save_registry()

    # ---------- Folder dialog ----------
    def pick(self, purpose):
        time.sleep(self.args.pick_delay)  # A real dialog takes a while.
        with self.lock:
            answer, self.next_pick = self.next_pick, None
        if answer:
            return answer
        if purpose == 'new':
            for n in range(1, 1000):
                folder = self.pick_root / f'New story {n}'
                if not folder.exists():
                    folder.mkdir()
                    return {'path': str(folder)}
        if self.args.add_folder:
            return {'path': self.args.add_folder}
        return {'cancelled': True}

    # ---------- Fake updater ----------
    def update_state(self):
        release = self.release or {}
        latest = release.get('version')
        available = bool(latest) and semver(latest) > semver(self.app_version) and not self.update.get('checkFailed')
        return {'current': self.app_version, 'latest': latest if available else None, 'available': available,
                'skipped': available and self.settings.get('skippedVersion') == latest,
                'notes': release.get('notes', '') if available else '',
                'publishedAt': release.get('publishedAt', '2026-10-30T12:00:00Z') if available else None,
                **{k: v for k, v in self.update.items() if k != 'checkFailed'}}

    def check(self):
        self.update.update(state='checking', error=None)
        time.sleep(0.6)
        if (self.release or {}).get('fail') == 'check':
            self.update.update(state='error', error='Could not reach GitHub (mock failure).', checkFailed=True)
        else:
            self.update.update(state='idle', error=None, checkFailed=False)
        self.update['lastCheck'] = self.settings['lastUpdateCheck'] = now()
        self.write_json('settings.json', self.settings)
        return self.update_state()

    def install(self):
        if not self.update_state()['available'] or self.update['state'] in {'downloading', 'verifying', 'installing'}:
            return self.update_state()
        self.update.update(state='downloading', progress=0, error=None)
        threading.Thread(target=self.run_install, daemon=True).start()
        return self.update_state()

    def run_install(self):
        fail = (self.release or {}).get('fail')
        steps = 20
        for step in range(1, steps + 1):
            time.sleep(self.args.download_seconds / steps)
            self.update['progress'] = step / steps
            if fail == 'download' and step == steps // 2:
                self.update.update(state='error', error='The download was interrupted (mock failure).')
                return
        self.update.update(state='verifying')
        time.sleep(0.8)
        if fail == 'verify':
            self.update.update(state='error', error='The download did not match its signature, so it was deleted (mock failure).')
            return
        self.update.update(state='installing')
        time.sleep(1.5)
        # "Restart": stop listening for a while, then come back as the new version with a new token.
        self.restarting = True
        self.httpd.shutdown()

    def restarted(self):
        self.app_version = self.release['version']
        self.token = secrets.token_urlsafe(32)
        self.update = {'state': 'idle', 'progress': 0, 'error': None, 'lastCheck': self.update['lastCheck'], 'checkFailed': False}
        self.restarting = False


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'StoryStudioMock/1.0'

        def trusted_host(self):
            port = self.server.server_port
            return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

        def send_data(self, status, payload, content_type='application/json; charset=utf-8', extra=()):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def json(self, status, value):
            self.send_data(status, core.encode(value))

        def send_file(self, file, versioned):
            size = file.stat().st_size
            start, end, status = 0, size - 1, 200
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', '').strip())
            if match and (match[1] or match[2]):
                if match[1]:
                    start, end = int(match[1]), min(int(match[2]), size - 1) if match[2] else size - 1
                else:
                    start = max(0, size - int(match[2]))
                if start >= size or start > end:
                    self.send_data(416, b'', extra=[('Content-Range', f'bytes */{size}')])
                    return
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', CONTENT_TYPES.get(file.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'private, max-age=31536000, immutable' if versioned else 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command == 'HEAD':
                return
            with file.open('rb') as stream:
                stream.seek(start)
                self.wfile.write(stream.read(end - start + 1))

        def asset(self, name):
            self.send_data(200, (core.WEB_DIR / name).read_bytes(), CONTENT_TYPES[Path(name).suffix])

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.trusted_host():
                return self.json(403, {'error': 'Localhost access only.'})
            url = urlsplit(self.path)
            path = unquote(url.path)
            try:
                if path == '/':
                    return self.asset('home.html')
                if path == '/api/info':
                    return self.json(200, {'app': 'story-studio', 'version': API_VERSION, 'appVersion': app.app_version})
                if path == '/api/stories':
                    return self.json(200, {'token': app.token, 'stories': app.stories()})
                if path == '/api/update':
                    return self.json(200, app.update_state())
                if path == '/__mock/state':
                    return self.json(200, {'registry': app.registry, 'settings': app.settings, 'update': app.update_state(),
                                           'home': str(app.home), 'pickRoot': str(app.pick_root)})
                match = re.fullmatch(r'/s/([^/]+)(/.*)?', path)
                if match:
                    return self.story_get(match[1], match[2], url)
                name = path.removeprefix('/')
                if name in WEB_ASSETS and (core.WEB_DIR / name).is_file():
                    return self.asset(name)
                self.json(404, {'error': 'File not available.'})
            except ConnectionError:
                pass
            except (ValueError, OSError, KeyError) as error:
                self.json(500, {'error': str(error)})

        def story_get(self, story_id, rest, url):
            if rest is None:  # Relative URLs in the editor need the trailing slash.
                return self.send_data(301, b'', extra=[('Location', f'/s/{quote(story_id)}/')])
            name = rest.removeprefix('/') or 'editor.html'
            if name in WEB_ASSETS:
                # The editor page loads even for an unknown or missing story, so it can say so.
                if name == 'editor.html':
                    app.opened(story_id)
                if (core.WEB_DIR / name).is_file():
                    return self.asset(name)
                return self.json(404, {'error': 'File not available.'})
            studio = app.studio(story_id)
            if not studio:
                return self.json(404, {'error': "This story's folder is missing, or the story is not in the list."})
            if name == 'api/bootstrap':
                with studio.lock:
                    project, revision = studio.read_project()
                images, signature = studio.images()
                return self.json(200, {'project': project, 'revision': revision, 'token': app.token, 'images': images,
                                       'signature': signature, 'projectFile': core.PROJECT_NAME, 'version': API_VERSION,
                                       'storyId': story_id, 'appVersion': app.app_version})
            if name == 'api/images':
                images, signature = studio.images()
                return self.json(200, {'images': images, 'signature': signature})
            file = studio.root / name
            if '/' in name or '\\' in name or not core.safe_media(name) or file.is_symlink() or not file.is_file():
                return self.json(404, {'error': 'File not available.'})
            self.send_file(file, url.query.startswith('v='))

        def do_POST(self):
            path = unquote(urlsplit(self.path).path)
            port = self.server.server_port
            origin = self.headers.get('Origin')
            if not self.trusted_host() or (origin and origin not in {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}):
                return self.json(403, {'error': 'Request not authorized by Story Studio.'})
            if not path.startswith('/__mock/') and not secrets.compare_digest(self.headers.get('X-Story-Token', ''), app.token):
                return self.json(403, {'error': 'Request not authorized by Story Studio.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > core.MAX_BODY:
                    return self.json(413, {'error': 'Request too large.'})
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    raise ValueError('Invalid request.')
                self.route_post(path, body)
            except ConnectionError:
                pass
            except KeyError as error:
                self.json(404, {'error': str(error.args[0]) if error.args else 'Not found.'})
            except (ValueError, TypeError) as error:
                self.json(400, {'error': str(error)})
            except OSError as error:
                self.json(500, {'error': str(error)})

        def route_post(self, path, body):
            if path == '/__mock/pick':
                app.next_pick = {'cancelled': True} if body.get('cancelled') else {'path': body['path']}
                return self.json(200, {'ok': True})
            if path == '/__mock/release':
                app.release = body if body.get('version') else None
                app.update.update(state='idle', progress=0, error=None, checkFailed=False)
                return self.json(200, app.update_state())
            if path == '/api/quit':
                self.json(200, {'ok': True})
                return threading.Thread(target=app.httpd.shutdown, daemon=True).start()
            if path == '/api/stories/pick-folder':
                if body.get('purpose') not in {'new', 'add'}:
                    raise ValueError('Unknown purpose.')
                return self.json(200, app.pick(body['purpose']))
            if path == '/api/stories':
                if not isinstance(body.get('path'), str) or not body['path'].strip():
                    raise ValueError('A folder path is required.')
                status, story = app.add(body['path'], body.get('title'))
                return self.json(status, {'story': story})
            if path == '/api/update/check':
                return self.json(200, app.check())
            if path == '/api/update/install':
                return self.json(200, app.install())
            if path == '/api/update/skip':
                app.settings['skippedVersion'] = body.get('version')
                app.write_json('settings.json', app.settings)
                return self.json(200, app.update_state())
            match = re.fullmatch(r'/api/stories/([^/]+)/(remove|relocate|reveal)', path)
            if match:
                story_id, action = match[1], match[2]
                if not app.entry(story_id):
                    raise KeyError('Unknown story.')
                if action == 'remove':
                    app.remove(story_id)
                    return self.json(200, {'ok': True})
                if action == 'relocate':
                    return self.json(200, {'story': app.relocate(story_id, body.get('path', ''))})
                if not Path(app.entry(story_id)['path']).is_dir():
                    raise ValueError('The folder is missing.')
                print('reveal (mock, nothing opened):', app.entry(story_id)['path'], flush=True)
                return self.json(200, {'ok': True})
            match = re.fullmatch(r'/s/([^/]+)/api/project', path)
            if match:
                studio = app.studio(match[1])
                if not studio:
                    raise KeyError("This story's folder is missing.")
                revision = studio.save(body.get('project'), body.get('revision'))
                if revision is None:
                    return self.json(409, {'error': 'The project file changed elsewhere. Reload before saving to avoid overwriting it.'})
                return self.json(200, {'revision': revision, 'projectFile': core.PROJECT_NAME})
            raise KeyError('Unknown endpoint.')

        def log_message(self, format, *args):
            if len(args) > 1 and str(args[1]).startswith(('4', '5')):
                super().log_message(format, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8792)
    parser.add_argument('--home', help='App-data folder (default: a new temporary folder)')
    parser.add_argument('--pick-root', help='Where the fake dialog creates folders for "new" (default: temporary)')
    parser.add_argument('--add-folder', help='What the fake dialog answers for "add" (default: cancelled)')
    parser.add_argument('--pick-delay', type=float, default=0.4)
    parser.add_argument('--app-version', default='0.2.0')
    parser.add_argument('--latest', help='Offer this version as a fake update from the start')
    parser.add_argument('--notes', default='## What\'s new\n\n- A **mock** release for testing.\n', help='Fake release notes (markdown)')
    parser.add_argument('--download-seconds', type=float, default=3)
    parser.add_argument('--restart-seconds', type=float, default=2)
    args = parser.parse_args()
    if args.port == 8765:
        parser.exit(1, 'Port 8765 belongs to the real Story Studio; use 8792 or above.\n')
    app = App(args)
    print(f'Story Studio mock: http://127.0.0.1:{args.port}/\n  app data: {app.home}\n  new-story folders: {app.pick_root}', flush=True)
    while True:
        app.httpd = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
        app.httpd.daemon_threads = True
        try:
            app.httpd.serve_forever()
        except KeyboardInterrupt:
            break
        finally:
            app.httpd.server_close()
        if not app.restarting:
            break
        print('mock update: restarting…', flush=True)
        time.sleep(args.restart_seconds)
        app.restarted()
        print(f'mock update: now version {app.app_version}', flush=True)


if __name__ == '__main__':
    main()
