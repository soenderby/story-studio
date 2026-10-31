# Story Studio: architecture and API contract

This is the contract that phases 1–5 build against (see [PLAN.md](PLAN.md)). Change it only deliberately, and update every side that depends on what changed.

## Versions

- **App version**: semver string in `src/story_studio/version.py` (`__version__`). It is what releases and the updater compare.
- **API version**: integer `SERVER_VERSION` in `server.py` and `editor.js`. Bump it when an endpoint or the project format changes incompatibly, so a stale running instance is detected. Phase 1 sets it to **6**.
- **Project format**: `story-project.json`, `"format": "illustrated-story-project"`, `"version": 1`. Unchanged by this plan. A future format bump must migrate forward, write `story-project.v<old>.backup.json` first, and an older app opening a newer-format project must refuse to save instead of overwriting it.

## Files and folders

| What | Where (Windows) | Notes |
| --- | --- | --- |
| Install dir | `%LOCALAPPDATA%\Programs\Story Studio\` | Replaced on every update. Never holds user data. |
| App data | `%APPDATA%\Story Studio\` | Survives updates and uninstall. Override with env `STORY_STUDIO_HOME` (tests always do). On non-Windows dev machines: `~/.local/share/story-studio`. |
| Story folder | anywhere the user chooses | `story-project.json`, `story-project.json.bak`, media. Self-describing: it can be re-added on any install. |

App data contents:

```
stories.json     {"version": 1, "stories": [{"id", "path", "title", "added", "lastOpened"}]}   lastOpened is null until first opened
settings.json    {"version": 1, "port": 8765, "checkUpdates": true, "skippedVersion": null, "lastUpdateCheck": null}
instance.json    {"pid", "port", "token", "appVersion", "api"}   written by the running instance, removed on exit
logs/studio.log  rotating, 1 MB × 3
logs/tray.log    StoryStudio.exe's tray and startup errors, rotating, 1 MB × 3 (phase 3)
updates/         the installer being downloaded (<installer>.part) or run, and install-<version>.log (Inno Setup's log);
                 on every start, everything but the newest install log is deleted
```

All app-data JSON is written with the existing `atomic_write`. An unreadable `stories.json` is renamed to `stories.json.corrupt-<timestamp>` and a fresh registry started; if only some entries are malformed, the original is kept the same way and the readable entries are saved. An unreadable `settings.json` is set aside likewise and the defaults used. Story folders are never touched by that.

**Story id** = `"s" + directory_key(path)[:16]`, deterministic. The same folder seen from Windows or WSL gets the same id, and adding a folder twice is a no-op. If a folder is moved, the story shows as *missing* (no `story-project.json` at its path) until it is relocated, which gives it a new id; relocating onto a folder that is already listed merges the two entries.

## Process

- One instance per user. On launch: read `settings.json` port and `GET /api/info` there. If `app == "story-studio"` with the same `api`, open the home page in the browser and exit. If a different `api` or `appVersion` is running, ask it to quit with `POST /api/quit` and the token from `instance.json`, wait up to 5 s, then start.
- Port 8765. If something else holds it, try 8766–8775 and save the chosen port in `settings.json`. Browser storage is per origin, so the port should stay stable. An explicit `--port` is used as is: no fallback, not saved.
- Listens on 127.0.0.1 only. Every request checks `Host`. Every POST checks `Origin` (if present) and the `X-Story-Token` header (per-process token from `GET /api/stories` or `/s/<id>/api/bootstrap`).

## Python entry points

Phase 1 provides these. Phase 3 (tray, PyInstaller) and phase 4 (updater) build on them.

```python
# story_studio/paths.py
def app_home() -> Path            # %APPDATA%\Story Studio, or STORY_STUDIO_HOME; created on first use
WEB_DIR: Path                     # bundled web assets; correct both from source and inside PyInstaller
                                  # (frozen: first existing of _MEIPASS/story_studio/web, _MEIPASS/web, <exe dir>/web).
                                  # Read it as paths.WEB_DIR at call time, so tests can patch it.

# story_studio/app.py
class AppHandle:
    url: str                      # e.g. "http://127.0.0.1:8765/"
    def shutdown(self) -> None    # stop serving, remove instance.json; safe to call twice
    def wait(self) -> None        # block until shut down (by shutdown() or POST /api/quit)

def start(open_browser: bool = True, port: int | None = None, story: str | None = None) -> AppHandle | None
    # Single-instance logic above. Returns None when another instance was found
    # (its home page, or the story, has been opened instead). Serves on a background thread.
    # Raises ValueError for an unusable story folder, OSError when no port is free.

def main() -> None                # CLI: python -m story_studio [--port N] [--no-browser] [--story FOLDER]
                                  # --story registers that folder and opens it directly.
```

`story_studio/__main__.py` calls `app.main()`. The packaged `StoryStudio.exe [--no-browser]` runs `story_studio.tray.main()`, which calls `start(open_browser=not --no-browser)`, shows the tray icon (Open Story Studio · Check for updates · Quit) and calls `shutdown()` on Quit. It also stops when `wait()` returns (`POST /api/quit`), and on logoff or a Restart Manager request. It never lets an exception escape (that would be PyInstaller's error dialog): it logs to `logs/tray.log` and exits with code 1. Without the tray libraries, `tray.main()` falls back to `app.main()`.

## HTTP API

### Global

| Method & path | Body | Response |
| --- | --- | --- |
| `GET /` | | `home.html`, the story list page |
| `GET /<asset>` | | top-level files from `web/` only, with an explicit type: `.html`, `.js`, `.css`, `.svg`, `.ico` (`home.html`, `home.js`, `home.css`, `update.js`, `editor.*`, `editor-data.js`, `icon.svg`). No media types, so a story's media is never shadowed. |
| `GET /api/info` | | `{"app": "story-studio", "version": <api int>, "appVersion": "0.2.0"}` |
| `GET /api/stories` | | `{"token", "stories": [Story]}` sorted by `lastOpened` desc (`added` for stories never opened) |
| `POST /api/stories/pick-folder` | `{"purpose": "new" \| "add"}` | Opens a native folder dialog on the desktop (tkinter, brought to front). `{"path": "C:\\..."}` or `{"cancelled": true}`. `409` while a dialog is already open, `501` when no dialog is available (offer a typed path instead). |
| `POST /api/stories` | `{"path", "title"?}` | Registers the folder. If it has no `story-project.json`, creates a blank project (title from body, else folder name). If a legacy `editor-data.js` is present, migrates it (below). `201 {"story": Story}`, or `200` with the existing entry if already registered (adding again still creates a missing project and repeats the idempotent migration). `400` if not an absolute path to a directory, or if its project or `editor-data.js` cannot be read (nothing is written then). A new blank project gets a unique `id` (`story-<hex>`), so legacy recovery adoption never matches it. |
| `POST /api/stories/<id>/remove` | | Unregisters only. Never deletes files. `{"ok": true}` |
| `POST /api/stories/<id>/relocate` | `{"path"}` | Points a missing story at its new folder (must contain a valid `story-project.json`, else `400`). `{"story": Story}` with the new id |
| `POST /api/stories/<id>/reveal` | | Opens the folder in Explorer. `{"ok": true}`, `404` if the folder is missing |
| `POST /api/quit` | none (`Content-Length: 0`, no `Origin`) | Flushes nothing (saves are synchronous), exits after the response. Used by the updater, the installer and by a newer launching instance. |

`Story` = `{"id", "title", "path", "lastOpened", "missing": bool, "cover": "<media filename>" | null, "chapters": int, "media": int}`. `title` is read from the project when available (cached in `stories.json` for missing folders). `cover` is the first image or video used in the project that exists in the folder, served from `/s/<id>/<cover>`. `media` is the number of top-level image and video files in the folder. `lastOpened` is an ISO 8601 timestamp, or `null` if never opened. `missing` is true when the folder or its `story-project.json` is gone. When the folder exists but its project cannot be read, the Story also has `"error": "<message>"` (and `missing: false`).

### Per story: `/s/<id>/...`

| Method & path | Notes |
| --- | --- |
| `GET /s/<id>` | `301` to `/s/<id>/`, so relative URLs resolve. |
| `GET /s/<id>/` | `editor.html`, for any well-formed id (`s` + 16 hex); the editor learns about an unknown or missing story from `api/bootstrap`. Also sets `lastOpened`. |
| `GET /s/<id>/<asset>` | Same web assets as above, so the editor's relative links work. |
| `GET /s/<id>/<media>` | Top-level media of that story folder only, same rules and Range support as today. |
| `GET /s/<id>/api/bootstrap` | As today, plus `"storyId"` and `"appVersion"`. Also sets `lastOpened`. `500 {"error"}` if the project cannot be read (it is left untouched). |
| `GET /s/<id>/api/images` | As today. |
| `POST /s/<id>/api/project` | As today (revision check, `.bak`, atomic write). |

Unknown or missing story → `404 {"error"}` for the API and media routes. `editor.html` and the other web assets are still served for such an id, so the editor can load, get the 404 from `api/bootstrap` and show "This story's folder is missing" with a link back to the home page.

**Editor rule:** every per-story URL in `editor.js` is *relative* (`api/bootstrap`, not `/api/bootstrap`), so the same file works under `/s/<id>/`. Global endpoints (`/api/info`, `/api/update…`, `/api/stories`) are absolute; `update.js` uses them from both pages.

### Updates (phase 4; phase 2 builds the UI against a stub)

| Method & path | Body | Response |
| --- | --- | --- |
| `GET /api/update` | | `UpdateState` |
| `POST /api/update/check` | | Forces a check now, returns `UpdateState` |
| `POST /api/update/install` | | Starts download → verify → install. Returns immediately, `UpdateState` with `state: "downloading"`. `409 {"error"}` when no update is available, or a check or install is already running. `501 {"error"}` when not running as the installed Windows app (`StoryStudio.exe`): from source it can check, but not install. |
| `POST /api/update/skip` | `{"version"}` | Hides the banner for that version |

`UpdateState` = `{"current": "0.2.0", "latest": "0.3.0" | null, "available": bool, "skipped": bool, "notes": "markdown", "publishedAt", "state": "idle" | "checking" | "downloading" | "verifying" | "installing" | "error", "progress": 0..1, "error": string | null, "lastCheck"}`

`latest` is the newest release the last successful check found (a prerelease only when `current` is one), even when it is not newer; `available` is true only when it is newer than `current`. A failed check sets `state: "error"` with a readable `error`, `latest: null` and `available: false`. A failed install sets `state: "error"` but keeps `available: true`, so the banner offers "Try again". `skipped` does not change `available` (installing a skipped version still works). `lastCheck` is `settings.json` `lastUpdateCheck`, written after every check, failed or not.

Client flow: show a banner when `available && !skipped`. On install, poll `/api/update` every 500 ms. When `state == "installing"`, show "Updating Story Studio…" and poll `/api/info` every second until `appVersion` changes, then `location.reload()`.

## Browser storage (editor)

- `localStorage` recovery key: `illustrated-story-studio:v1:<storyId>`. UI preferences stay global (`illustrated-story-studio:v1:ui`).
- **Legacy adoption:** if the namespaced key is empty and the old un-namespaced key `illustrated-story-studio:v1` holds a project whose `id` equals this project's `id`, use it as this story's recovery copy, then delete the old key.
- IndexedDB thumbnail keys are prefixed `<storyId>|`.

## Legacy migration (adding an old single-story folder)

When `POST /api/stories` registers a folder containing `editor-data.js` (the pre-0.2 layout):

1. Copy `story-project.json` to `story-project.pre-migration.json` (if not already present).
2. For every image in `editor-data.js` `images` with a non-default title or category (title other than the file's stem, or category other than `""`, `New additions`, `Videos`, `Added images`) that the project's `extraImages` does not already describe, add `{file, title, category}` to `extraImages`.
3. Save through the normal validated path. Leave `editor-data.js` and the old `story-server.py`/`editor.*` files in place, untouched.

If the folder has `editor-data.js` but no `story-project.json`, use its `seed` as the project (with the same `extraImages` merge; no pre-migration copy, since there is nothing to back up). A seed that does not validate falls back to the blank project. Migration is idempotent: adding the folder again leaves the project, the pre-migration copy and later edits alone.

## Updates and releases (phases 4–5)

- Every GitHub release `vX.Y.Z` has these assets:
  - `StoryStudio-Setup-X.Y.Z.exe`: Inno Setup installer.
  - `latest.json`: `{"version", "installer": "<asset name>", "sha256", "size", "notes", "publishedAt"}`.
  - `latest.json.sig`: base64 Ed25519 signature of the exact `latest.json` bytes.
- Versions are semver `X.Y.Z` or `X.Y.Z-<prerelease>` (no `+build` metadata), and the tag is always `v` + `version`. A prerelease is published as a GitHub *prerelease*, so `releases/latest` never points at it and installed apps are never offered one.
- `latest.json` must be UTF-8 JSON, at most 64 KiB, an object without duplicate keys. All six fields are required: `version` a semver string (`X.Y.Z`, optional `-prerelease` and `+build`, no leading zeros, at most 64 characters); `installer` a plain file name matching `[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe`; `sha256` 64 *lowercase* hex digits; `size` a JSON integer, 1 byte to 512 MiB; `notes` a string (markdown, may be empty, at most 100 000 characters); `publishedAt` an ISO 8601 time such as `2026-10-31T12:00:00Z`. Other fields are ignored. Anything else fails the check.
- `latest.json` bytes (phase 5, `scripts/release_tool.py manifest`): UTF-8 without BOM, keys in exactly this order, 2-space indent, non-ASCII characters unescaped, LF line ends, one trailing newline. Fields:
  - `version`: string.
  - `installer`: string, always `StoryStudio-Setup-<version>.exe`.
  - `sha256`: 64 lowercase hex digits.
  - `size`: integer, in bytes.
  - `notes`: markdown string, that version's `CHANGELOG.md` section without its heading, LF line ends.
  - `publishedAt`: UTC `YYYY-MM-DDTHH:MM:SSZ`, the time the manifest was signed.

  The updater accepts at most 64 KiB of `latest.json`, 100,000 characters of `notes`, a `size` from 1 byte to 512 MiB, and an `installer` matching `[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe`. The release workflow refuses to publish anything beyond these limits. Verify the signature over the bytes as downloaded, before parsing them.
- `latest.json.sig` is at most 1 KiB: standard base64 (with padding) of the raw 64-byte Ed25519 signature (RFC 8032, pure Ed25519, not Ed25519ph) over the exact bytes of `latest.json` as served. Surrounding whitespace, such as a trailing newline, is ignored.
- `latest.json.sig` as the release workflow writes it: standard base64 (with padding) of the 64-byte signature, 88 characters on one line with no trailing newline. Strip surrounding whitespace before decoding anyway.
- The release workflow signs only with the key that installed apps verify with (`update_key.py` as of the latest published release), and it checks the signature before publishing. See [RELEASING.md](RELEASING.md).
- The app fetches `https://github.com/soenderby/story-studio/releases/latest/download/latest.json` and `.sig`. That address is a plain redirect to the file, not the rate-limited API. It verifies the signature with the public key embedded in `story_studio/update_key.py` *before* parsing the manifest, validates it, compares semver (never a downgrade; a prerelease is only offered to a prerelease), then downloads `https://github.com/soenderby/story-studio/releases/download/v<version>/<installer>` to `updates/<installer>.part`, hashing as it goes. Only when `size` and `sha256` match is it renamed to `updates/<installer>` and run; otherwise it is deleted and nothing is run.
- HTTPS only (redirects too), through the system or environment proxy, 15 s timeout per manifest request, 60 s without data during the download. Env `STORY_STUDIO_UPDATE_URL` (tests, end-to-end runs) replaces the base address: `latest.json`, `latest.json.sig` and the installer are all fetched from `<STORY_STUDIO_UPDATE_URL>/<name>`, plain HTTP allowed only for `127.0.0.1`/`localhost`, which never go through a proxy. The signature is still checked against the embedded key.
- Automatic checks, when `settings.json` `checkUpdates` is true: about 10 s after start, then whenever 24 h have passed since `lastUpdateCheck` (so restarts do not check again), and 1 h after a failed check. They run on a daemon thread that stops with the app. `POST /api/update/check` and the tray's "Check for updates" always check. A check while an update is being installed only reports the state.
- Install: `"updates\<installer>" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH /LOG="updates\install-<version>.log"`, started detached (it outlives the app). Right before starting it, the updater takes every story's save lock, so no save is in flight (saves are synchronous; it gives up with an error if a save takes over 30 s), then sets `state: "installing"`. The installer closes the running app (`/api/quit` with the token from `instance.json` in `STORY_STUDIO_HOME` or `%APPDATA%\Story Studio`, then waits up to 5 s for its `pid`; falling back to the Restart Manager), replaces the install dir and relaunches `StoryStudio.exe --no-browser` (open tabs reload by themselves). If the installer exits with an error while the app still runs (or exits successfully but the app is still running 20 s later), `state` becomes `"error"`, naming the log. See [packaging/README.md](../packaging/README.md).
- Updates never touch app data (other than `settings.json` `lastUpdateCheck`/`skippedVersion` and `updates/`) or any story folder.
- Quitting during the download or verification abandons it and nothing is installed. The partial `updates/<installer>.part` is deleted right away, or at the next start if the app stopped first, and the next check offers the release again. Once `state` is `"installing"` the installer runs on its own; quitting then changes nothing.
- The private signing key exists only as the GitHub Actions secret `UPDATE_SIGNING_KEY` and in the maintainer's offline backup. It is never committed.
