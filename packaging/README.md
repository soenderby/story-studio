# Packaging (Windows)

Builds `StoryStudio.exe` (PyInstaller, one folder, no console, tray icon) and the per-user
Inno Setup installer `StoryStudio-Setup-<version>.exe`.

## Build

Requirements: Windows x64, Python 3.12, and Inno Setup 6
(`winget install --id JRSoftware.InnoSetup -e --scope user`).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
```

It works from a clean checkout. It creates or updates `.venv-build` with the pinned tools in
`requirements-build.txt`, regenerates `icon.ico`, runs PyInstaller (`dist\StoryStudio\`) and
ISCC, then prints the installer path (`packaging\Output\`), its size and its SHA-256.
Options: `-Python <python.exe>` (or `$env:STORY_STUDIO_PYTHON`) and `-Iscc <ISCC.exe>` (or `$env:ISCC`).
The version comes from `src/story_studio/version.py` and must start with `X.Y.Z`.

| File | Purpose |
| --- | --- |
| `launcher.py` | PyInstaller entry script: `story_studio.tray.main()` |
| `story_studio.spec` | One-folder build `StoryStudio`, `console=False`, no UPX, icon, version resource, bundles `story_studio/web/**` and the tray PNG |
| `make_icon.py` | Draws the icon (same design as `src/story_studio/web/icon.svg`) into `icon.ico`, 16–256 px. The spec calls its `draw_icon()` for the tray PNG |
| `icon.ico` | Generated, committed |
| `installer.iss` | Inno Setup script. Needs `/DAppVersion=X.Y.Z` (and optionally `/DAppNumericVersion=`) |
| `smoke-test.ps1` | Installs the newest `Output\` installer into a temp folder with its own `STORY_STUDIO_HOME` (port 8793), checks serving, no window, every shutdown path (no error dialog, clean log), `/RELAUNCH`, the Restart Manager fallback and uninstall, then cleans up. Exit code 1 on any failure |
| `e2e-update.ps1` | End-to-end test of the in-app updater: builds two versions (default 0.2.0 and 0.2.1) from a temporary copy of the source that trusts a throwaway test key, serves a fake signed release on 127.0.0.1 (a free port), installs the older one into a temp folder (own `STORY_STUDIO_HOME`, port 8796), saves an edit to a test story, checks that a tampered installer is refused, then updates from the app and checks that it restarts as the new version with the story and app data untouched. Uninstalls and cleans up; `-KeepBuild` keeps the builds for the next run. Exit code 1 on any failure |
| `e2e_update_helper.py` | Its helpers: test key, signed `latest.json`, test story (uses `tests/signing.py`) |

## The app

`StoryStudio.exe [--no-browser]` starts the server and shows a tray icon: **Open Story Studio**
(also a click on the icon), **Check for updates**, **Quit**. Launching it again while it runs
opens the home page of the running instance and exits. Errors go to `logs\tray.log` in the
app-data folder; a windowed build never shows an error dialog (it exits with code 1 instead).

Ways it stops cleanly: Quit, `POST /api/quit` (token from `instance.json`), the Restart Manager
(installer fallback) and logoff. `taskkill /PID` without `/F` has no effect (the app has no
visible window); `taskkill /F` kills it and leaves a stale `instance.json`, which the next launch
ignores.

## Installer

| | |
| --- | --- |
| Install dir | `%LOCALAPPDATA%\Programs\Story Studio\` (per user, no admin prompt). `_internal\` is wiped before files are copied, so no stale modules survive an update |
| App data | `%APPDATA%\Story Studio\` (or `STORY_STUDIO_HOME`). Never touched by the installer or uninstaller |
| Shortcuts | Start menu; desktop optional (unchecked task `desktopicon`) |
| AppId | `{15941046-8201-491A-A5E0-2A057BDD1F20}` (never change it: updates and uninstall depend on it) |

Command line: Inno's standard switches (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=... /LOG=...
/TASKS=desktopicon`) plus:

- `/RELAUNCH`: start `StoryStudio.exe --no-browser` after the install, also when silent. The
  updater runs `StoryStudio-Setup-X.Y.Z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH
  /LOG=<app data>\updates\install-X.Y.Z.log` (see docs/API.md, "Updates and releases").
  Without it, a silent install does not start the app; an interactive one offers "Launch Story Studio".

Closing the running app before files are replaced (`[Code]`, `PrepareToInstall`, and before
uninstalling): read `instance.json` from the app-data folder (`STORY_STUDIO_HOME` if set, else
`%APPDATA%\Story Studio`), `POST http://127.0.0.1:<port>/api/quit` with header
`X-Story-Token: <token>`, then wait up to 5 s for that `pid` to exit. If that fails,
`CloseApplications=yes` lets the Restart Manager close it (the tray accepts its session-end
messages). The setup log (`/LOG=`) records each step as `Story Studio: ...`.

Uninstall: `"%LOCALAPPDATA%\Programs\Story Studio\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES`.
It removes the install dir, shortcuts and the uninstall registry entry only.

## Unsigned builds

The installer and exe are not code-signed, so the first manual install shows SmartScreen's
"Windows protected your PC" (More info → Run anyway). See docs/PLAN.md, "Known caveats".
