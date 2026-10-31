# PyInstaller spec for StoryStudio.exe: one-folder, windowed (no console), no UPX.
# Build: packaging/build.ps1, or from the repository root:
#   pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/story_studio.spec
import json
from pathlib import Path
import re
import runpy
import sys

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))  # for collect_submodules('story_studio')
VERSION = runpy.run_path(str(SRC / 'story_studio' / 'version.py'))['__version__']
NUMBERS = tuple(int(part) for part in re.match(r'(\d+)\.(\d+)\.(\d+)', VERSION).groups()) + (0,)

# The tray PNG is drawn at build time (*.png is gitignored) by the same code that drew icon.ico.
TRAY_PNG = Path(workpath) / 'tray.png'
TRAY_PNG.parent.mkdir(parents=True, exist_ok=True)
runpy.run_path(str(Path(SPECPATH) / 'make_icon.py'))['draw_icon'](64).save(TRAY_PNG)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'Story Studio'),
            StringStruct('FileDescription', 'Story Studio'),
            StringStruct('FileVersion', VERSION),
            StringStruct('InternalName', 'StoryStudio'),
            StringStruct('OriginalFilename', 'StoryStudio.exe'),
            StringStruct('ProductName', 'Story Studio'),
            StringStruct('ProductVersion', VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

a = Analysis(
    [str(Path(SPECPATH) / 'launcher.py')],
    pathex=[str(SRC)],
    datas=[
        # Path(story_studio.__file__).parent / 'web' resolves to sys._MEIPASS/story_studio/web.
        (str(SRC / 'story_studio' / 'web'), 'story_studio/web'),
        (str(TRAY_PNG), 'story_studio'),
    ],
    # The whole package, so modules imported lazily (updater, folder picker) are always bundled;
    # pystray picks its backend at runtime; tkinter is the native folder dialog (phase 1).
    hiddenimports=[name for name in collect_submodules('story_studio') if name != 'story_studio.__main__']
                  + ['pystray._win32', 'tkinter', 'tkinter.filedialog'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StoryStudio',
    console=False,
    upx=False,
    icon=str(Path(SPECPATH) / 'icon.ico'),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='StoryStudio')

# PLAN.md: the install dir carries version.json beside the exe.
(Path(DISTPATH) / 'StoryStudio' / 'version.json').write_text(json.dumps({'version': VERSION}) + '\n', encoding='utf-8')
