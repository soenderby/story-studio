"""Desktop integration: native folder dialog, revealing a folder, opening the browser.

The server takes the picker and revealer as parameters, so tests never open real windows.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import webbrowser

PICK_TITLES = {'new': 'Choose or create a folder for the new story', 'add': 'Choose a story folder'}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class PickerUnavailable(RuntimeError):
    pass


def in_wsl():
    return bool(os.environ.get('WSL_DISTRO_NAME')) and shutil.which('cmd.exe') is not None


def tk_pick(title):
    """tkinter dialog owned by a hidden topmost window, so it opens in front of the browser."""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        root.update()
        return filedialog.askdirectory(parent=root, title=title, initialdir=str(Path.home()), mustexist=True) or None
    finally:
        root.destroy()


POWERSHELL_PICK = r'''
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:STORY_STUDIO_PICK_TITLE
$dialog.ShowNewFolderButton = $true
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
'''


def powershell_pick(title):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-Command', POWERSHELL_PICK],
                            capture_output=True, env=dict(os.environ, STORY_STUDIO_PICK_TITLE=title),
                            creationflags=NO_WINDOW, check=True)
    return result.stdout.decode('utf-8-sig').strip() or None


def pick_folder(purpose):
    """Path chosen in a native folder dialog, or None when cancelled. Blocks until the dialog closes."""
    title = PICK_TITLES.get(purpose, PICK_TITLES['add'])
    outcome = {}

    def run():
        # A dedicated short-lived thread with its own Tk(): tkinter objects must stay on one thread.
        try:
            outcome['path'] = tk_pick(title)
        except Exception as error:  # ImportError without tkinter, TclError without a display.
            outcome['error'] = error

    thread = threading.Thread(target=run, name='folder-picker', daemon=True)
    thread.start()
    thread.join()
    if 'error' not in outcome:
        return outcome['path'] and str(Path(outcome['path']))
    if os.name == 'nt':
        return powershell_pick(title)
    raise PickerUnavailable(f'No folder dialog is available here ({outcome["error"]}). Type the folder path instead.')


def reveal_folder(path):
    """Show a folder in Explorer (Windows), or the platform's file manager."""
    if os.name == 'nt':
        os.startfile(path)
    elif in_wsl():
        windows_path = subprocess.run(['wslpath', '-w', str(path)], capture_output=True, text=True, check=True).stdout.strip()
        subprocess.Popen(['explorer.exe', windows_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_browser(url):
    if in_wsl():
        subprocess.Popen(['cmd.exe', '/c', 'start', '', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)
