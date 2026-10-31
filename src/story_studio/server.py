#!/usr/bin/env python3
"""Local-only Story studio: live top-level image and video discovery and atomic project saving.

Requires Python 3.9+, standard library only. Run: python -m story_studio --story <folder> --open
"""
import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
# Browsers sniff the container, so QuickTime/M4V files with H.264 inside play when served as MP4.
VIDEO_TYPES = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/mp4', '.webm': 'video/webm', '.ogv': 'video/ogg'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | set(VIDEO_TYPES)
IMAGE_SIZES = {'small', 'medium', 'large', 'wide'}
GROUP_LAYOUTS = {'row', 'grid'}
MAX_GROUP = 12
WEB_DIR = Path(__file__).resolve().parent / 'web'
ASSETS = {'editor.html', 'editor.css', 'editor.js', 'editor-data.js'}
LEGACY_DATA = 'editor-data.js'  # Older single-story installs kept image descriptions here, beside the media.
MAX_BODY = 10_000_000
PROJECT_NAME = 'story-project.json'
SERVER_VERSION = 5  # Bump when the project format or API changes, so stale instances are detected.


def safe_media(name):
    return (isinstance(name, str) and 0 < len(name) <= 500
            and not re.search(r'[\\/\x00-\x1f]', name)
            and Path(name).suffix.lower() in MEDIA_EXTENSIONS)


def directory_key(root):
    """The same folder seen from Windows (C:\\Users\\...) or WSL (/mnt/c/users/...) gets the same key."""
    path = str(root).replace('\\', '/')
    match = re.match(r'^/mnt/([a-zA-Z])(/.*)?$', path)
    if match:
        path = f'{match[1]}:{match[2] or "/"}'
    return hashlib.sha256(path.rstrip('/').lower().encode()).hexdigest()


def validate_project(value):
    """Reject malformed imports before touching the last good file."""
    if not isinstance(value, dict) or value.get('format') != 'illustrated-story-project' or value.get('version') != 1:
        raise ValueError('Not a supported Story studio project (version 1).')
    ids = set()

    def text(value, limit=2_000_000):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('Invalid or oversized project text.')
        return value

    def unique_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value) or value in ids:
            raise ValueError('Invalid or duplicate chapter/block ID.')
        ids.add(value)
        return value

    chapters = value.get('chapters')
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 500:
        raise ValueError('A project must contain 1–500 chapters.')
    clean = {'format': 'illustrated-story-project', 'version': 1,
             'id': text(value.get('id', 'story'), 100),
             'title': text(value.get('title'), 10000),
             'subtitle': text(value.get('subtitle'), 100000), 'chapters': []}
    count = 0
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get('blocks'), list):
            raise ValueError('Invalid chapter.')
        result = {'id': unique_id(chapter.get('id')), 'title': text(chapter.get('title'), 10000),
                  'notes': text(chapter.get('notes', ''), 300000), 'blocks': []}
        for block in chapter['blocks']:
            count += 1
            if count > 10000 or not isinstance(block, dict):
                raise ValueError('Invalid or excessive project blocks.')
            item = {'id': unique_id(block.get('id')), 'type': block.get('type')}
            if item['type'] == 'text':
                item['text'] = text(block.get('text'))
            elif item['type'] == 'image' and safe_media(block.get('file')):
                item.update(file=block['file'], caption=text(block.get('caption'), 300000))
            elif item['type'] == 'gallery':
                pictures = block.get('items')
                if not isinstance(pictures, list) or not 2 <= len(pictures) <= MAX_GROUP:
                    raise ValueError(f'A picture group must contain 2–{MAX_GROUP} pictures.')
                if block.get('layout') not in GROUP_LAYOUTS:
                    raise ValueError('Unknown picture group layout.')
                for picture in pictures:
                    if not isinstance(picture, dict) or not safe_media(picture.get('file')):
                        raise ValueError('Image references must be top-level image filenames, not paths or URLs.')
                item.update(layout=block['layout'], items=[{'file': picture['file']} for picture in pictures],
                            caption=text(block.get('caption'), 300000))
            else:
                raise ValueError('Image references must be top-level image filenames, not paths or URLs.')
            if item['type'] != 'text' and 'size' in block:
                if block['size'] not in IMAGE_SIZES:
                    raise ValueError('Unknown image size.')
                item['size'] = block['size']
            result['blocks'].append(item)
        clean['chapters'].append(result)
    if 'extraImages' in value:
        if not isinstance(value['extraImages'], list) or len(value['extraImages']) > 10000:
            raise ValueError('Invalid additional image library.')
        clean['extraImages'] = []
        for image in value['extraImages']:
            if not isinstance(image, dict) or not safe_media(image.get('file')):
                raise ValueError('Invalid additional image reference.')
            clean['extraImages'].append({'file': image['file'], 'title': text(image.get('title'), 10000),
                                         'category': text(image.get('category'), 1000)})
    if 'disclaimer' in value:
        clean['disclaimer'] = text(value['disclaimer'], 100000)
    if 'disclaimerGate' in value:
        if not isinstance(value['disclaimerGate'], bool):
            raise ValueError('Invalid disclaimer setting.')
        clean['disclaimerGate'] = value['disclaimerGate']
    if 'outline' in value:
        clean['outline'] = text(value['outline'])
    if 'updatedAt' in value:
        clean['updatedAt'] = text(value['updatedAt'], 100)
    return clean


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def atomic_write(path, payload):
    """Same-directory replacement, so interruption cannot leave half-written JSON."""
    if path.is_symlink():
        raise ValueError('Refusing to overwrite a symbolic link.')
    descriptor, temporary = tempfile.mkstemp(prefix='.story-save-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_editor_data(path):
    """The JSON inside an editor-data.js file; empty when an optional legacy file is absent."""
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError(f'{path.name} must not be a symbolic link.')
    source = path.read_text(encoding='utf-8').split('window.STORY_EDITOR_DATA = ', 1)[1].strip().removesuffix(';')
    return json.loads(source)


class Studio:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.project_path = self.root / PROJECT_NAME
        self.backup_path = self.root / (PROJECT_NAME + '.bak')
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.directory_id = directory_key(self.root)
        self.known_images = {image['file']: image for image in read_editor_data(self.root / LEGACY_DATA).get('images', [])}
        if not self.project_path.exists():
            atomic_write(self.project_path, encode(validate_project(read_editor_data(WEB_DIR / 'editor-data.js')['seed'])))
        # Never replace a corrupt or unfamiliar existing autosave with the seed.
        self.read_project()

    def read_project(self):
        if self.project_path.is_symlink():
            raise ValueError('The project file must not be a symbolic link.')
        if self.project_path.stat().st_size > MAX_BODY:
            raise ValueError('Project file exceeds 10 MB.')
        payload = self.project_path.read_bytes()
        project = validate_project(json.loads(payload))
        return project, hashlib.sha256(payload).hexdigest()

    def images(self):
        # scandir never enters directories and explicitly excludes symbolic links.
        images = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False) or not safe_media(entry.name):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # A copy/rename may have finished during this scan.
                known = self.known_images.get(entry.name)
                video = Path(entry.name).suffix.lower() in VIDEO_TYPES
                image = dict(known) if known else {'file': entry.name, 'title': Path(entry.name).stem,
                                                  'category': 'Videos' if video else 'New additions'}
                image['stamp'] = f'{stat.st_mtime_ns}-{stat.st_size}'
                images.append(image)
        images.sort(key=lambda image: image['file'])
        signature = hashlib.sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()
        return images, signature

    def save(self, project, expected_revision):
        project = validate_project(project)
        payload = encode(project)
        if len(payload) > MAX_BODY:
            raise ValueError('Project exceeds 10 MB.')
        with self.lock:
            _, current = self.read_project()
            if not isinstance(expected_revision, str) or not secrets.compare_digest(current, expected_revision):
                return None
            if hashlib.sha256(payload).hexdigest() != current:
                # Back up the previous validated version before replacing it.
                atomic_write(self.backup_path, self.project_path.read_bytes())
                atomic_write(self.project_path, payload)
            return hashlib.sha256(payload).hexdigest()


def make_server(root, port=8765):
    studio = Studio(root)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'StoryStudio/1.0'

        def trusted_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

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

        def send_file(self, file, content_type, versioned):
            """Streams the file and honours byte ranges, which video seeking and thumbnails rely on."""
            size = file.stat().st_size
            start, end, status = 0, size - 1, 200
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', '').strip())
            if match and (match[1] or match[2]):
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), size - 1) if match[2] else size - 1
                else:
                    start = max(0, size - int(match[2]))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            if status == 206:
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

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.trusted_host():
                self.json(403, {'error': 'Localhost access only.'})
                return
            url = urlsplit(self.path)
            path = unquote(url.path)
            try:
                if path == '/api/info':
                    self.json(200, {'app': 'story-studio', 'directory': studio.directory_id, 'version': SERVER_VERSION})
                elif path == '/api/bootstrap':
                    with studio.lock:
                        project, revision = studio.read_project()
                    images, signature = studio.images()
                    self.json(200, {'project': project, 'revision': revision, 'token': studio.token,
                                    'images': images, 'signature': signature, 'projectFile': PROJECT_NAME,
                                    'version': SERVER_VERSION})
                elif path == '/api/images':
                    images, signature = studio.images()
                    self.json(200, {'images': images, 'signature': signature})
                else:
                    name = 'editor.html' if path == '/' else path.removeprefix('/')
                    if '/' in name or '\\' in name or not (name in ASSETS or safe_media(name)):
                        self.json(404, {'error': 'File not available.'})
                        return
                    file = (WEB_DIR if name in ASSETS else studio.root) / name
                    if file.is_symlink() or not file.is_file():
                        self.json(404, {'error': 'File not available.'})
                        return
                    suffix = Path(name).suffix.lower()
                    content_type = VIDEO_TYPES.get(suffix) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
                    if name in ASSETS:
                        self.send_data(200, file.read_bytes(), content_type)
                    else:
                        self.send_file(file, content_type, url.query.startswith('v='))
            except ConnectionError:
                pass
            except (ValueError, OSError, KeyError) as error:
                self.json(500, {'error': str(error)})

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed_origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
            if (not self.trusted_host() or (origin and origin not in allowed_origins)
                    or not secrets.compare_digest(self.headers.get('X-Story-Token', ''), studio.token)):
                self.json(403, {'error': 'Request not authorized by this local editor.'})
                return
            if self.path != '/api/project':
                self.json(404, {'error': 'Unknown endpoint.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > MAX_BODY:
                    self.json(413, {'error': 'Project request must be between 1 byte and 10 MB.'})
                    return
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Invalid save request.')
                revision = studio.save(body.get('project'), body.get('revision'))
                if revision is None:
                    self.json(409, {'error': 'The project file changed elsewhere. Reload before saving to avoid overwriting it.'})
                else:
                    self.json(200, {'revision': revision, 'projectFile': PROJECT_NAME})
            except (ValueError, TypeError, KeyError) as error:
                self.json(400, {'error': str(error)})
            except OSError as error:
                self.json(500, {'error': 'File save failed: ' + str(error)})

        def log_message(self, format, *args):
            # Image polling and thumbnails otherwise swamp the launcher's console.
            if args and str(args[1] if len(args) > 1 else '').startswith(('4', '5')):
                super().log_message(format, *args)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.studio = studio
    return server


def open_browser(url):
    if os.environ.get('WSL_DISTRO_NAME') and shutil.which('cmd.exe'):
        subprocess.Popen(['cmd.exe', '/c', 'start', '', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='Open the editor in your browser')
    parser.add_argument('--story', default='.', help='Story folder holding story-project.json and its media (default: current folder)')
    args = parser.parse_args()
    root = Path(args.story).resolve()
    if not root.is_dir():
        parser.exit(1, f'Story folder not found: {root}\n')
    url = f'http://127.0.0.1:{args.port}/'
    try:
        server = make_server(root, args.port)
    except OSError as error:
        # Reopening the launcher should reopen the existing instance, not create a second writer.
        try:
            with urlopen(url + 'api/info', timeout=2) as response:
                info = json.load(response)
        except Exception:
            info = {}
        if info.get('app') != 'story-studio':
            parser.exit(1, f'Could not start Story studio: port {args.port} is used by another program ({error}).\n'
                           f'Close that program, or try another port with --port {args.port + 1}.\n')
        if info.get('version') != SERVER_VERSION:
            parser.exit(1, 'An older Story studio is still running and holding port '
                           f'{args.port}. It may be in another window or in WSL.\n'
                           'Close that window (or press Ctrl+C in it), then start Story studio again.\n')
        if info.get('directory') != directory_key(root):
            parser.exit(1, f'Story studio for a different folder is already running on port {args.port}.\n'
                           f'Close it, or use another port with --port {args.port + 1}.\n')
        print('Story studio is already running: ' + url, flush=True)
        if args.open:
            open_browser(url)
        return
    except (ValueError, KeyError, IndexError) as error:
        parser.exit(1, f'Project was not overwritten: {error}\nCheck story-project.json; the previous version is story-project.json.bak.\n')
    print(f'Story studio: {url}\nAutomatic project file: {root / PROJECT_NAME}\n'
          'New top-level images and videos appear automatically. No subdirectories or ZIPs are scanned.\n'
          'Leave this window running. Press Ctrl+C to stop.', flush=True)
    if args.open:
        threading.Timer(0.3, open_browser, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
