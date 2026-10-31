# Story Studio — distributable app plan

Status: **approved 2026-10-31**. Update the checklist at the bottom as phases land. The detailed contract is in [API.md](API.md).

## Decisions (made 2026-10-31)

| Question | Decision |
| --- | --- |
| Platforms | Windows only (macOS possible later) |
| Update hosting | GitHub Releases on the public `soenderby/story-studio` repo. No server of our own; GitHub hosts the files |
| Story storage | A story = a folder holding `story-project.json` and its media. The app keeps a list of known story folders |
| App shell | Keep the Python local server and open in the browser. Bundled as a standalone .exe with a tray icon, so no Python install is needed |

## Target architecture

```
Install dir (replaced on every update, never holds user data)
  %LOCALAPPDATA%\Programs\Story Studio\
    StoryStudio.exe            PyInstaller one-folder build, no console window
    _internal\...              Python runtime + libraries
    web\                       editor.html/js/css, home.html, icons
    version.json               {"version": "1.2.0"}

User data (survives updates and uninstall)
  %APPDATA%\Story Studio\
    stories.json               registry: [{id, path, title, lastOpened}]
    settings.json              {port, checkUpdates, skippedVersion, lastCheck}
    logs\studio.log            rotating log
    updates\                   downloaded installers (deleted after install)

Story folder (anywhere, e.g. Dropbox)
  story-project.json (+ .bak)  unchanged project format, still self-describing
  *.png / *.mp4 ...            media, referenced by filename
```

Per-user install (no admin prompt), so the app can update itself without UAC.

### Server (story_studio package, split out of story-server.py)

- `GET /`: **Story home page**: list of stories (title, folder, last opened, cover thumbnail), New story, Add existing folder, Remove from list (never deletes files), Reveal in Explorer.
- `GET /s/<storyId>/` serves the editor for one story; `/s/<storyId>/api/bootstrap|images|project` are the current endpoints, scoped per story; `/s/<storyId>/media/<file>` serves media.
- `POST /api/stories/pick-folder` opens a **native Windows folder dialog** (tkinter, runs on the server side; a browser page cannot see folder paths).
- `GET /api/update`, `POST /api/update/install` (see Updates).
- Per-story lock + revision checks as today, so several stories can be open in separate tabs.
- Fixed port 8765 with fallback stored in settings. Browser storage is per origin, so a stable port keeps recovery copies and thumbnails.
- Single instance: launching again opens the home page in the existing instance (existing `/api/info` logic).

### Editor changes

- Browser storage keys (`localStorage`, IndexedDB thumbnails) namespaced per story id, so stories never overwrite each other's recovery copy.
- "← All stories" link; remove story-specific menu links (story.html, catalog.html, premise).
- Update banner: "Version X is available · What's new · Install and restart".
- `editor-data.js` seed removed from the app. New stories start blank. **Migration:** when a folder containing the legacy `editor-data.js` is added, its image descriptions/scenes are copied into the project's `extraImages` (one-time, after writing `story-project.pre-migration.json`).

### Updates

1. Once a day (and on demand from the tray menu or home page) the server asks `api.github.com/repos/<owner>/<repo>/releases/latest`. It is unauthenticated, so the release repo must be **public** (the source can stay private in a separate repo if wanted).
2. Newer semver → banner in the home page and editor, with the release notes.
3. Install: download `StoryStudio-Setup-X.Y.Z.exe` to `%APPDATA%\Story Studio\updates\`, verify its **SHA-256 against a signed manifest** (`latest.json` + Ed25519 signature; the public key is embedded in the app, the private key lives only in a GitHub Actions secret). A compromised download or GitHub account cannot push malware.
4. Flush all pending saves, launch the installer with `/VERYSILENT /SUPPRESSMSGBOXES`, exit. Inno Setup replaces the install dir and relaunches the app. Open tabs show "Updating…" and reload when the new version answers `/api/info`.
5. User data is never in the install dir, so updates cannot touch stories. Project-format migrations always write a backup first, and a newer-format project is opened read-only by an older app instead of being overwritten.

### Packaging and release pipeline

- PyInstaller one-folder build (fast start, fewer antivirus false positives than one-file), `pystray` tray icon: Open Story Studio · Check for updates · Quit.
- Inno Setup installer: per-user, Start-menu + optional desktop shortcut, uninstaller that keeps `%APPDATA%\Story Studio` and all story folders.
- GitHub Actions (`windows-latest`) on tag `v*`: run tests → build → installer → `latest.json` + signature → GitHub Release with assets. Releasing = `scripts/release.ps1 1.2.0` (bumps version, tags, pushes).

### Known caveats

- **Unsigned installer → SmartScreen "Windows protected your PC"** on the *first* manual install ("More info → Run anyway"). In-app updates are downloaded by the app, not the browser, so they do not get that warning. Optional fix later: Azure Trusted Signing (~$10/month) or a code-signing certificate.
- PyInstaller apps are sometimes flagged by antivirus heuristics. One-folder build + signing reduce this.
- GitHub's unauthenticated API allows 60 requests/hour per IP. One check a day is far below that.

## Phases

Each phase is a branch. Independent phases run in parallel in separate git worktrees.

| # | Phase | Depends on | Can run in parallel with |
| --- | --- | --- | --- |
| 0 | **Foundations**: repository, package layout, version file, generic starting draft, API contract ([API.md](API.md)) | — | — |
| 1 | **Multi-story server**: app-data dir, story registry, per-story routes, native folder picker, legacy migration, tests | 0 | 2, 3 |
| 2 | **Front end**: home page, per-story editor URLs, storage namespacing, update banner (against a stubbed `/api/update`) | 0 (API contract) | 1, 3 |
| 3 | **Packaging**: PyInstaller spec, tray icon, no-console launch, single instance, app icon, Inno Setup script | 0 | 1, 2 |
| 4 | **Updater**: GitHub check, download, signature + hash verification, silent install + relaunch, settings, tests with a fake release server | 1, 3 | 5 |
| 5 | **Release pipeline**: GitHub Actions workflow, signing key generation, `release.ps1` | 3 | 4 |
| 6 | **End-to-end on Windows**: install v0.9.0 → publish v0.9.1 → app shows update → installs → all stories intact; uninstall keeps stories; migrate a pre-0.2 single-story folder | all | — |

Each phase ends with its tests passing and a short entry in `CHANGELOG.md`. Every phase branch is reviewed before it is merged into `main`.

## Progress

- [x] Phase 0 — Foundations (0.1.0)
- [x] Phase 1 — Multi-story server
- [x] Phase 2 — Front end
- [x] Phase 3 — Packaging
- [x] Integration: phases 1–3 merged, 69 tests passing, combined app checked in a browser against the real server
- [x] Phase 4 — Updater
- [x] Phase 5 — Release pipeline
- [ ] First release 0.2.0 (`scripts/release.ps1 0.2.0`, see [RELEASING.md](RELEASING.md))
- [ ] Phase 6 — End-to-end verification

## Open items carried forward

- The native folder dialog has only been exercised through a test double. Check on Windows that it appears in front of the browser (phase 6 at the latest).
- Editor top bar is crowded below ~380 px width.
- If a pre-0.2 editor still holds port 8765, the app uses a fallback port for that run only (not saved), so the old browser recovery copy can still be adopted later on 8765.
