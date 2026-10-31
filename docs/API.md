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
stories.json     {"version": 1, "stories": [{"id", "path", "title", "added", "lastOpened"}]}
settings.json    {"version": 1, "port": 8765, "checkUpdates": true, "skippedVersion": null, "lastUpdateCheck": null}
instance.json    {"pid", "port", "token", "appVersion", "api"}   written by the running instance, removed on exit
logs/studio.log  rotating, 1 MB × 3
updates/         downloaded installers; deleted after a successful install
```

All app-data JSON is written with the existing `atomic_write`. An unreadable `stories.json` is renamed to `stories.json.corrupt-<timestamp>` and a fresh registry started. Story folders are never touched by that.

**Story id** = `"s" + directory_key(path)[:16]`, deterministic. The same folder seen from Windows or WSL gets the same id, and adding a folder twice is a no-op. If a folder is moved, the story shows as *missing* until it is relocated (which gives it a new id).

## Process

- One instance per user. On launch: read `settings.json` port and `GET /api/info` there. If `app == "story-studio"` with the same `api`, open the home page in the browser and exit. If a different `api` or `appVersion` is running, ask it to quit with `POST /api/quit` and the token from `instance.json`, wait up to 5 s, then start.
- Port 8765. If something else holds it, try 8766–8775 and save the chosen port in `settings.json`. Browser storage is per origin, so the port should stay stable.
- Listens on 127.0.0.1 only. Every request checks `Host`. Every POST checks `Origin` (if present) and the `X-Story-Token` header (per-process token from `GET /api/stories` or `/s/<id>/api/bootstrap`).

## HTTP API

### Global

| Method & path | Body | Response |
| --- | --- | --- |
| `GET /` | | `home.html`, the story list page |
| `GET /<asset>` | | files from `web/` only (`home.html`, `home.js`, `home.css`, `editor.*`, `editor-data.js`, `icon.svg`) |
| `GET /api/info` | | `{"app": "story-studio", "version": <api int>, "appVersion": "0.2.0"}` |
| `GET /api/stories` | | `{"token", "stories": [Story]}` sorted by `lastOpened` desc |
| `POST /api/stories/pick-folder` | `{"purpose": "new" \| "add"}` | Opens a native folder dialog on the desktop (tkinter, brought to front). `{"path": "C:\\..."}` or `{"cancelled": true}` |
| `POST /api/stories` | `{"path", "title"?}` | Registers the folder. If it has no `story-project.json`, creates a blank project (title from body, else folder name). If a legacy `editor-data.js` is present, migrates it (below). `201 {"story": Story}`, or `200` with the existing entry if already registered. `400` if not a directory. |
| `POST /api/stories/<id>/remove` | | Unregisters only. Never deletes files. `{"ok": true}` |
| `POST /api/stories/<id>/relocate` | `{"path"}` | Points a missing story at its new folder (must contain a `story-project.json`). `{"story": Story}` |
| `POST /api/stories/<id>/reveal` | | Opens the folder in Explorer. `{"ok": true}` |
| `POST /api/quit` | | Flushes nothing (saves are synchronous), exits after the response. Used by the updater and by a newer launching instance. |

`Story` = `{"id", "title", "path", "lastOpened", "missing": bool, "cover": "<media filename>" | null, "chapters": int, "media": int}`. `title` is read from the project when available (cached in `stories.json` for missing folders). `cover` is the first image or video used in the project, served from `/s/<id>/<cover>`.

### Per story: `/s/<id>/...`

| Method & path | Notes |
| --- | --- |
| `GET /s/<id>/` | `editor.html`. Also sets `lastOpened`. |
| `GET /s/<id>/<asset>` | Same web assets as above, so the editor's relative links work. |
| `GET /s/<id>/<media>` | Top-level media of that story folder only, same rules and Range support as today. |
| `GET /s/<id>/api/bootstrap` | As today, plus `"storyId"` and `"appVersion"`. |
| `GET /s/<id>/api/images` | As today. |
| `POST /s/<id>/api/project` | As today (revision check, `.bak`, atomic write). |

Unknown or missing story → `404 {"error"}`. The editor shows "This story's folder is missing" with a link back to the home page.

**Editor rule:** every URL in `editor.js` is *relative* (`api/bootstrap`, not `/api/bootstrap`), so the same file works under `/s/<id>/`.

### Updates (phase 4; phase 2 builds the UI against a stub)

| Method & path | Body | Response |
| --- | --- | --- |
| `GET /api/update` | | `UpdateState` |
| `POST /api/update/check` | | Forces a check now, returns `UpdateState` |
| `POST /api/update/install` | | Starts download → verify → install. Returns immediately, `UpdateState` with `state: "downloading"`. |
| `POST /api/update/skip` | `{"version"}` | Hides the banner for that version |

`UpdateState` = `{"current": "0.2.0", "latest": "0.3.0" | null, "available": bool, "skipped": bool, "notes": "markdown", "publishedAt", "state": "idle" | "checking" | "downloading" | "verifying" | "installing" | "error", "progress": 0..1, "error": string | null, "lastCheck"}`

Client flow: show a banner when `available && !skipped`. On install, poll `/api/update` every 500 ms. When `state == "installing"`, show "Updating Story Studio…" and poll `/api/info` every second until `appVersion` changes, then `location.reload()`.

## Browser storage (editor)

- `localStorage` recovery key: `illustrated-story-studio:v1:<storyId>`. UI preferences stay global (`illustrated-story-studio:v1:ui`).
- **Legacy adoption:** if the namespaced key is empty and the old un-namespaced key `illustrated-story-studio:v1` holds a project whose `id` equals this project's `id`, use it as this story's recovery copy, then delete the old key.
- IndexedDB thumbnail keys are prefixed `<storyId>|`.

## Legacy migration (adding an old single-story folder)

When `POST /api/stories` registers a folder containing `editor-data.js` (the pre-0.2 layout):

1. Copy `story-project.json` to `story-project.pre-migration.json` (if not already present).
2. For every image in `editor-data.js` `images` with a non-default title or category that the project's `extraImages` does not already describe, add `{file, title, category}` to `extraImages`.
3. Save through the normal validated path. Leave `editor-data.js` and the old `story-server.py`/`editor.*` files in place, untouched.

If the folder has `editor-data.js` but no `story-project.json`, use its `seed` as the project.

## Updates and releases (phases 4–5)

- Every GitHub release `vX.Y.Z` has these assets:
  - `StoryStudio-Setup-X.Y.Z.exe`: Inno Setup installer.
  - `latest.json`: `{"version", "installer": "<asset name>", "sha256", "size", "notes", "publishedAt"}`.
  - `latest.json.sig`: base64 Ed25519 signature of the exact `latest.json` bytes.
- The app fetches `https://github.com/soenderby/story-studio/releases/latest/download/latest.json` and `.sig`. That address is a plain redirect to the file, not the rate-limited API. The app verifies the signature with the public key embedded in `story_studio/update_key.py`, compares semver, then downloads `https://github.com/soenderby/story-studio/releases/download/vX.Y.Z/<installer>` and checks `size` and `sha256` before running it.
- Install: `"<installer>" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH`. The installer closes the running app (`/api/quit`, falling back to the Restart Manager), replaces the install dir and relaunches `StoryStudio.exe`.
- The private signing key exists only as the GitHub Actions secret `UPDATE_SIGNING_KEY`. It is never committed.
