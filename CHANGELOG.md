# Changelog

## 0.1.0 (unreleased)

- Repository created from the single-folder editor. Python package `story_studio` with the web editor under `web/`.
- The server is separated from story data: `--story <folder>` selects the story folder, and new stories start blank.
- Image descriptions from an older single-story folder's `editor-data.js` are still read when present.
- Video support: MP4, M4V, MOV, WebM and OGV, with Range streaming, thumbnails and hover-to-play.
- Multiple stories: an "All stories" home page to create stories, add existing folders (native folder dialog), remove them from the list (files are never deleted), and locate moved folders. Each story opens at `/s/<id>/`.
- App data lives in `%APPDATA%\Story Studio` (story list, settings, logs), separate from the program and from story folders.
- One instance per user, a stable port, and a clean handover when a newer version starts.
- Browser recovery copies and thumbnails are kept per story. A recovery copy from the old single-story editor is adopted by the matching story.
- Image descriptions from a pre-0.2 story folder's `editor-data.js` are migrated into the project, after a `story-project.pre-migration.json` backup.
- Update notifications UI (banner, release notes, install and restart, skip) ready for the updater.
- Windows packaging: `StoryStudio.exe` (no console window, tray icon with Open / Check for updates / Quit) and a per-user Inno Setup installer that never removes app data or stories.
