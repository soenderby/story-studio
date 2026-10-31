"""Tray entry point of the packaged StoryStudio.exe (see docs/API.md, "Python entry points").

main() starts the app with app.start(), shows a tray icon (Open Story Studio, Check for updates,
Quit) and shuts the app down on Quit. Without pystray/Pillow it falls back to app.main().

Command line: StoryStudio.exe [--no-browser]
  --no-browser  do not open the home page on start (the installer's /RELAUNCH uses it, because
                open editor tabs reload by themselves after an update)
"""
import json
import logging
import logging.handlers
import os
from pathlib import Path
import sys
import threading
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

log = logging.getLogger('story_studio.tray')
WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION = 0x0010, 0x0011, 0x0016
ERROR_INVALID_WINDOW_HANDLE = 1400
# Never send localhost requests through a system proxy (urllib reads the Windows proxy settings).
_local = build_opener(ProxyHandler({}))


def _app_home():
    try:
        from .paths import app_home
        return app_home()
    except ImportError:  # before phase 1's paths.py exists: same rules as the contract
        if os.environ.get('STORY_STUDIO_HOME'):
            home = Path(os.environ['STORY_STUDIO_HOME'])
        elif sys.platform == 'win32':
            home = Path(os.environ.get('APPDATA', Path.home())) / 'Story Studio'
        else:
            home = Path.home() / '.local' / 'share' / 'story-studio'
        home.mkdir(parents=True, exist_ok=True)
        return home


class _LogStream:
    """Stands in for sys.stdout/stderr in the windowed exe, where they are None."""

    def __init__(self, level):
        self.level = level

    def write(self, text):
        text = text.rstrip()
        if text:
            log.log(self.level, '%s', text)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def _setup_logging():
    """logs/tray.log (rotating, 1 MB x 3). Phase 1 owns logs/studio.log; a second handler on the
    same file would break its rotation on Windows."""
    try:
        folder = _app_home() / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / 'tray.log', maxBytes=1_000_000, backupCount=3,
                                                       encoding='utf-8', delay=True)
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    if sys.stdout is None:
        sys.stdout = _LogStream(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _LogStream(logging.WARNING)
    sys.excepthook = lambda kind, value, trace: log.critical('Unhandled exception', exc_info=(kind, value, trace))
    threading.excepthook = lambda args: log.error('Unhandled exception in thread %s', getattr(args.thread, 'name', '?'),
                                                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _icon_image():
    """The tray PNG bundled next to this module (packaging/make_icon.py draws it at build time).
    From a source checkout, draw it with make_icon.py directly."""
    from PIL import Image
    png = Path(__file__).resolve().parent / 'tray.png'  # also right inside PyInstaller (sys._MEIPASS)
    if png.is_file():
        return Image.open(png)
    maker = Path(__file__).resolve().parents[2] / 'packaging' / 'make_icon.py'
    if maker.is_file():
        import runpy
        return runpy.run_path(str(maker))['draw_icon'](64)
    return Image.new('RGBA', (64, 64), (61, 58, 158, 255))


def _token():
    try:
        return json.loads((_app_home() / 'instance.json').read_text(encoding='utf-8')).get('token', '')
    except (OSError, ValueError):
        return ''


def _check_for_updates(url):
    try:
        request = Request(url + 'api/update/check', data=b'{}', method='POST',
                          headers={'X-Story-Token': _token(), 'Content-Type': 'application/json'})
        with _local.open(request, timeout=60) as response:  # a check makes two requests to GitHub
            response.read()
    except Exception as error:  # the home page shows the result or the error itself
        log.warning('Update check failed: %s', error)
    webbrowser.open(url)


def _run_tray(handle, pystray):
    """Runs the tray icon on this (the main) thread until Quit, POST /api/quit, logoff or the
    Restart Manager stops it. Every path ends in stop(), which only posts WM_STOP: the window is
    always torn down by pystray on this thread."""
    stopping = threading.Event()
    stop_lock = threading.Lock()

    def stop():
        with stop_lock:
            if stopping.is_set():
                return
            stopping.set()
        handle.shutdown()
        icon.stop()

    def open_home(icon=None, item=None):
        webbrowser.open(handle.url)

    def check(icon, item):
        threading.Thread(target=_check_for_updates, args=(handle.url,), name='update-check', daemon=True).start()

    icon = pystray.Icon('story-studio', _icon_image(), 'Story Studio', menu=pystray.Menu(
        pystray.MenuItem('Open Story Studio', open_home, default=True),
        pystray.MenuItem('Check for updates', check),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Quit', lambda icon, item: stop()),
    ))

    # pystray answers unhandled window messages with 0, which would veto logoff and ignore the
    # Restart Manager (the installer's fallback when /api/quit fails) and taskkill without /F.
    # Accept them and shut down instead.
    handlers = getattr(icon, '_message_handlers', None)
    if isinstance(handlers, dict):
        def end_session(wparam, lparam):
            if wparam:
                stop()
            return 0

        def close(wparam, lparam):
            stop()
            return 0

        handlers[WM_QUERYENDSESSION] = lambda wparam, lparam: 1
        handlers[WM_ENDSESSION] = end_session
        handlers[WM_CLOSE] = close

    def watch():
        # POST /api/quit (updater, newer instance, installer) shuts the server down: remove the icon too.
        handle.wait()
        stop()

    def setup(icon):
        icon.visible = True
        threading.Thread(target=watch, name='tray-watch', daemon=True).start()

    try:
        icon.run(setup=setup)
    except OSError as error:
        # The Restart Manager and taskkill also send WM_CLOSE to pystray's second hidden (menu)
        # window, which DefWindowProc destroys; pystray's cleanup then fails on DestroyWindow with
        # ERROR_INVALID_WINDOW_HANDLE. That is harmless only while we are shutting down anyway.
        if stopping.is_set() and getattr(error, 'winerror', None) == ERROR_INVALID_WINDOW_HANDLE:
            log.info('Tray window was already destroyed during shutdown')
        else:
            raise


def main():
    """Never lets an exception escape: in the windowed exe that would be PyInstaller's
    "Unhandled exception" dialog. Errors go to logs/tray.log and the exit code is 1."""
    _setup_logging()
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        log.exception('Story Studio stopped after an unexpected error')
        raise SystemExit(1)


def _main():
    try:
        import pystray  # noqa: F401  (also pulls in Pillow)
        from PIL import Image  # noqa: F401
    except ImportError:
        from . import app
        app.main()
        return
    from . import app
    handle = app.start(open_browser='--no-browser' not in sys.argv[1:])
    if handle is None:
        log.info('Another instance is running; handed over to it')
        return
    log.info('Serving %s (pid %s)', handle.url, os.getpid())
    try:
        _run_tray(handle, pystray)
    finally:
        handle.shutdown()
        log.info('Stopped')


if __name__ == '__main__':
    main()
