# Changelog

## Unreleased

## 0.4.1 (2026-11-03)

### Fixed

- **Export PDF failed** with “msedge did not produce a PDF (exit code 0)” after Story Studio had updated itself. Edge inherited a Windows compatibility setting from the update installer and quit straight away.

## 0.4.0 (2026-11-01)

### New

- **Export PDF** (⋯ menu): downloads the story as an A4 PDF with the pictures, each chapter on a new page. Videos appear as their first frame.

### Fixed

- **Pictures were missing from exported HTML** unless the file was saved into the story's own folder. The exported HTML now contains the pictures and videos, so it works anywhere. Pictures are stored as high-quality WebP, so the file stays much smaller than the originals.

## 0.3.1 (2026-11-01)

### Fixed

- **Checking for updates failed on some Windows computers** with “certificate verify failed: unable to get issuer certificate”. On those computers, 0.2.0 and 0.3.0 cannot update themselves: download and run `StoryStudio-Setup-0.3.1.exe` from the [releases page](https://github.com/soenderby/story-studio/releases) once. Your stories and settings are kept. Later updates install from inside the app again.

## 0.3.0 (2026-11-01)

### New

- **Comments and markup.** Select words in your story and comment on them (**＋ Comment** or Alt+C), or comment on a whole paragraph. Label each comment Note, Change, Question or Keep, suggest new wording and accept it with one click, and resolve comments when they are done. A Comments panel lists them by chapter or for the whole story. Comments stay attached to their words while you edit, and are never part of Print or exported stories. In Read mode you can show them and comment as you read.
- **Settings.** A new Settings dialog (⋯ → Settings in a story, Settings on the story list).
- **Hide all images and videos** is now a setting, and it covers everything: story covers, the library, chapter thumbnails, the story, the viewer and Read mode. Hidden pictures become small labelled placeholders instead of empty space, and videos stop playing.

### Compatibility

- Projects with comments can be opened by 0.2.0, but saving them there would drop the comments. Update every computer that edits the same story.

## 0.2.0 (2026-10-31)

The first installable release of Story Studio.

### New

- **Install it like any Windows program.** No Python or other setup needed. Story Studio runs quietly in the tray (Open · Check for updates · Quit) and opens in your browser.
- **Several stories in one place.** The new *All stories* page lets you start a story in a folder you choose, add folders you already have, and reopen any story. Removing a story from the list never deletes its files.
- **Automatic updates.** Story Studio checks for new versions once a day and shows what's new. *Install and restart* updates it in place; your stories and settings are never touched. Every update is signed and verified before it runs.
- **Videos.** MP4, M4V, MOV, WebM and OGV files work everywhere pictures do: in the library, the story, picture groups, Read mode and exported HTML. In the editor they play silently while you hover over them.

### Moving from the earlier single-folder editor

- Add your existing story folder with *Add existing folder…*. Image descriptions and scenes from the old editor are carried over automatically, after a backup copy (`story-project.pre-migration.json`) is written.
- An unsaved recovery copy kept by the old editor in your browser is picked up by the matching story.
- Close the old editor before starting Story Studio for the first time.
