"""Where the app keeps its own files: bundled web assets and per-user app data."""
import os
from pathlib import Path
import sys


def web_dir():
    """The bundled web/ folder, from a source checkout or from inside a PyInstaller build."""
    if getattr(sys, 'frozen', False):
        base = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        candidates = [base / 'story_studio' / 'web', base / 'web', Path(sys.executable).parent / 'web']
        return next((path for path in candidates if path.is_dir()), candidates[0])
    return Path(__file__).resolve().parent / 'web'


# Other modules read paths.WEB_DIR at call time, so tests can point it at stand-in files.
WEB_DIR = web_dir()


def app_home():
    """Per-user app data (registry, settings, logs). Survives updates; STORY_STUDIO_HOME overrides it."""
    if os.environ.get('STORY_STUDIO_HOME'):
        home = Path(os.environ['STORY_STUDIO_HOME'])
    elif os.name == 'nt':
        home = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'Story Studio'
    else:
        home = Path.home() / '.local' / 'share' / 'story-studio'
    home.mkdir(parents=True, exist_ok=True)
    return home
