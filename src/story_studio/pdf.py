"""PDF export: prints a self-contained story page with a headless Edge or Chrome.

Edge ships with Windows, so no PDF library is bundled. The browser gets a throwaway profile, so it never attaches to
the user's open browser window, and it only ever loads the page written to a private temporary folder.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .desktop import NO_WINDOW

TIMEOUT = 300  # Seconds. A long illustrated story renders in a few seconds; this only stops a hung browser.


class PdfUnavailable(RuntimeError):
    pass


def browser_candidates():
    if os.name == 'nt':
        roots = [os.environ.get(name) for name in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')]
        for root in filter(None, roots):
            yield Path(root, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
            yield Path(root, 'Google', 'Chrome', 'Application', 'chrome.exe')
    else:
        for name in ('microsoft-edge', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
            if found := shutil.which(name):
                yield Path(found)


def find_browser():
    return next((path for path in browser_candidates() if path.is_file()), None)


def browser_environment():
    """This app's environment without Windows compatibility shims. An app started by the update installer inherits
    __COMPAT_LAYER (e.g. DetectorsAppHealth), and Edge started with it exits at once, with code 0, without a PDF."""
    return {key: value for key, value in os.environ.items() if key.upper() != '__COMPAT_LAYER'}


def render_pdf(html):
    """PDF bytes for `html` (bytes of a complete, self-contained page). Raises PdfUnavailable without a browser."""
    browser = find_browser()
    if browser is None:
        raise PdfUnavailable('PDF export needs Microsoft Edge or Google Chrome, and neither was found.')
    with tempfile.TemporaryDirectory(prefix='story-studio-pdf-', ignore_cleanup_errors=True) as folder:
        folder = Path(folder)
        page, output = folder / 'story.html', folder / 'story.pdf'
        page.write_bytes(html)
        result = subprocess.run(
            [str(browser), '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
             '--disable-extensions', '--disable-sync', '--user-data-dir=' + str(folder / 'profile'),
             '--no-pdf-header-footer', '--print-to-pdf=' + str(output), page.as_uri()],
            capture_output=True, timeout=TIMEOUT, creationflags=NO_WINDOW, env=browser_environment())
        if not output.is_file() or output.stat().st_size == 0:
            detail = result.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or [f'exit code {result.returncode}']
            raise OSError(f'{browser.stem} did not produce a PDF ({detail[0]}).')
        return output.read_bytes()
