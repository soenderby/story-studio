"""Starting Story Studio: one instance per user, a stable port, logging, and the command line."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from . import desktop, paths
from .registry import DEFAULT_PORT, Settings, story_id
from .server import SERVER_VERSION, make_server
from .story import atomic_write, encode
from .version import __version__

PORTS = range(DEFAULT_PORT, DEFAULT_PORT + 11)  # 8765, then 8766–8775 when something else holds it.
QUIT_WAIT = 5.0
log = logging.getLogger('story_studio')
# Talk to 127.0.0.1 directly; a configured system proxy must never see the token.
LOCAL = build_opener(ProxyHandler({}))


def request(port, path, body=None, token=None, timeout=2.0):
    """JSON from the local server; POST when a body is given."""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Story-Token'] = token
    data = None if body is None else encode(body)
    try:
        with LOCAL.open(Request(f'http://127.0.0.1:{port}{path}', data=data, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            message = json.load(error).get('error')
        except Exception:
            message = None
        if 400 <= error.code < 500 and message:
            raise ValueError(message) from None  # Such as "Not a folder", reported like a local failure.
        raise


def probe(port, timeout=2.0):
    """/api/info of whatever answers on the port; None when nothing does."""
    try:
        info = request(port, '/api/info', timeout=timeout)
    except Exception:
        return None
    return info if isinstance(info, dict) else {}


def read_instance(home):
    try:
        instance = json.loads((home / 'instance.json').read_bytes())
    except (OSError, ValueError):
        return {}
    return instance if isinstance(instance, dict) else {}


def stop_instance(port, token):
    """Ask another Story Studio to quit (POST /api/quit) and wait until it stops answering."""
    if not token:
        return False
    try:
        request(port, '/api/quit', {}, token)
    except Exception as error:
        log.warning('Story Studio on port %s did not accept quit: %s', port, error)
        return False
    deadline = time.monotonic() + QUIT_WAIT
    while time.monotonic() < deadline:
        if probe(port, timeout=0.5) is None:
            return True
        time.sleep(0.1)
    return False


def bind(home, ports, retry_first=0.0):
    """A server on the first free port. The first port is retried briefly after stopping an old instance."""
    deadline, error = time.monotonic() + retry_first, None
    for index, port in enumerate(ports):
        while True:
            try:
                return make_server(home, port)
            except OSError as failure:
                error = failure
                if index or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
    raise OSError(f'No free port for Story Studio (tried {", ".join(map(str, ports))}): {error}')


class AppHandle:
    """A running Story Studio. shutdown() is safe to call twice and from any thread but the server's own."""

    def __init__(self, server, home, log_handler=None):
        self.server = server
        self.home = Path(home)
        self.url = f'http://127.0.0.1:{server.server_port}/'
        self._log_handler = log_handler
        self._lock = threading.Lock()
        self._closing = False
        self._stopped = threading.Event()
        server.on_quit = self.shutdown
        self.instance = {'pid': os.getpid(), 'port': server.server_port, 'token': server.token,
                         'appVersion': __version__, 'api': SERVER_VERSION}
        atomic_write(self.home / 'instance.json', encode(self.instance))
        self._thread = threading.Thread(target=server.serve_forever, name='story-studio-server', daemon=True)
        self._thread.start()
        log.info('Story Studio %s serving %s (app data %s)', __version__, self.url, self.home)

    def shutdown(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
        self.server.shutdown()
        self.server.server_close()
        if read_instance(self.home).get('token') == self.server.token:  # Never remove a newer instance's file.
            try:
                (self.home / 'instance.json').unlink()
            except OSError:
                pass
        log.info('Story Studio stopped')
        if self._log_handler:
            log.removeHandler(self._log_handler)
            self._log_handler.close()
        self._stopped.set()

    def wait(self):
        while not self._stopped.wait(0.5):  # A timeout keeps Ctrl+C responsive on Windows.
            pass


def configure_logging(home):
    folder = home / 'logs'
    folder.mkdir(exist_ok=True)
    handler = RotatingFileHandler(folder / 'studio.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(threadName)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def start(open_browser=True, port=None, story=None):
    """Serve Story Studio on a background thread, or return None after opening an already running instance.

    port overrides settings.json for this run (no fallback, not saved). story registers that folder
    and opens it directly.
    """
    home = paths.app_home()
    settings = Settings(home)
    preferred = port or settings.get('port')
    folder = Path(story).expanduser().resolve() if story else None
    if folder and not folder.is_dir():
        raise ValueError(f'Story folder not found: {folder}')
    page = f's/{story_id(folder)}/' if folder else ''

    instance, stopped, held_by_studio = read_instance(home), False, False
    for candidate in dict.fromkeys([preferred] + ([instance['port']] if isinstance(instance.get('port'), int) else [])):
        info = probe(candidate)
        if not info or info.get('app') != 'story-studio':
            continue
        if info.get('version') == SERVER_VERSION and info.get('appVersion') == __version__:
            if folder:
                token = request(candidate, '/api/stories')['token']
                request(candidate, '/api/stories', {'path': str(folder)}, token, timeout=30)
            if open_browser:
                desktop.open_browser(f'http://127.0.0.1:{candidate}/{page}')
            return None
        # Another version: ask it to quit (the token proves it is ours), then take over.
        token = instance.get('token') if instance.get('port') == candidate else None
        stopped = stop_instance(candidate, token) or stopped
        if not stopped:
            held_by_studio = held_by_studio or candidate == preferred
            log.warning('Story Studio %s (api %s) holds port %s and could not be stopped',
                        info.get('appVersion'), info.get('version'), candidate)

    handler = configure_logging(home)
    try:
        ports = [preferred] if port else [preferred] + [p for p in PORTS if p != preferred]
        server = bind(home, ports, retry_first=2.0 if stopped else 0.0)
    except OSError:
        log.removeHandler(handler)
        handler.close()
        raise
    # Browser storage is per origin, so keep the port stable. An older Story Studio that cannot be
    # asked to quit (pre-0.2 editors have no /api/quit) only displaces us until it is closed: moving
    # for good would strand the browser recovery copies kept for its port.
    if not port and not held_by_studio and server.server_port != settings.get('port'):
        settings.update(port=server.server_port)
    if folder:
        try:
            server.add_story(folder)
        except (ValueError, OSError):
            server.server_close()
            log.removeHandler(handler)
            handler.close()
            raise
    handle = AppHandle(server, home, handler)
    if open_browser:
        desktop.open_browser(handle.url + page)
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m story_studio', description='Story Studio: a local editor for illustrated stories.')
    parser.add_argument('--port', type=int, help='serve on this port for this run (default: from settings.json, 8765)')
    parser.add_argument('--no-browser', action='store_true', help='do not open the browser')
    parser.add_argument('--story', metavar='FOLDER', help='add this story folder and open it')
    args = parser.parse_args(argv)
    console = logging.StreamHandler() if sys.stderr else None
    if console:
        console.setLevel(logging.WARNING)
        log.addHandler(console)
    try:
        run(parser, args)
    finally:
        if console:
            log.removeHandler(console)


def run(parser, args):
    try:
        handle = start(open_browser=not args.no_browser, port=args.port, story=args.story)
    except ValueError as error:
        parser.exit(1, f'Could not open the story: {error}\n'
                       'Nothing was overwritten. If story-project.json is damaged, story-project.json.bak holds the previous version.\n')
    except OSError as error:
        parser.exit(1, f'Could not start Story Studio: {error}\n')
    if handle is None:
        print('Story Studio is already running' + ('.' if args.no_browser else '; opened it in your browser.'), flush=True)
        return
    print(f'Story Studio {__version__}: {handle.url}\nApp data: {handle.home}\n'
          'Leave this window open. Press Ctrl+C to stop.', flush=True)
    try:
        handle.wait()
    except KeyboardInterrupt:
        pass
    finally:
        handle.shutdown()
