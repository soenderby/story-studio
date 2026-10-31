# Story Studio user guide

## Automatic images and videos

Copy or generate new PNG, JPG/JPEG, WebP, GIF or BMP images, or MP4, M4V, MOV, WebM or OGV videos, directly into the story folder. They appear in the library within a few seconds, without selecting files or refreshing the page.

- Only files directly in the story folder are listed. Subdirectories, symbolic links and ZIP archives are not scanned or opened.
- Previously described images retain their descriptions and scene categories. New images use their filenames until described elsewhere.
- Removed images disappear from the available library. If they are already placed in a chapter, their placements and captions are retained so you do not lose your work; they show as unavailable until the files return.
- Pictures are referenced by their original filenames, not uploaded or embedded.
- Discovery runs while the local server and editor page are running. Reopening the editor also loads the current folder contents.

### Videos

Videos work everywhere pictures do: insert, drag, Replace, sizes (S/M/L/Wide), captions and picture groups (mix videos and pictures freely).

- In the library, videos have a **▶** badge and a thumbnail taken from about one second in. New videos get the scene **Videos**.
- In the editor, videos are silent and **play while you hover over them**. Click one to open it in the viewer, with sound and full controls.
- In **Read**, Print and exported HTML, videos show normal play controls. Exported stories link to the video files the same way they link to images, so keep them beside the HTML.
- **MP4 (H.264) and WebM play in every browser.** MOV files only play if they contain H.264 video. HEVC/H.265 (common on iPhones) may not play in Chrome or Firefox; convert those to MP4 first.
- Video files are streamed, not loaded whole, so large clips are fine.

## Automatic project saving

The server automatically creates **story-project.json** in the story folder on first launch, starting with a blank one-chapter story. It then maintains that same file as you work.

- Edits are saved about half a second after you stop typing, and structural changes are saved the same way.
- Watch for **Automatically saved · story-project.json** before closing the page.
- Reopening Story Studio resumes the file automatically. There is no file picker or permission-reconnection step.
- The file contains the whole editable project: titles, text, chapter/block order, image placements, captions and private notes.
- Each update is written to a temporary file, flushed, then atomically replaces the project file. A partial write should not destroy the last saved JSON.
- **story-project.json.bak** contains the previous saved version. It is a rolling backup, not a complete version history. Download occasional backups for longer-term snapshots.
- A browser recovery copy is also kept. If the connection fails, leave the page open and restart Story Studio. The editor retries pending saves automatically. Unsaved local changes can be recovered after reload when the disk revision is unchanged.
- If another tab, program, Dropbox synchronization or another computer changes the project file, stale writes are rejected and the editor offers a recovery download instead of silently overwriting either version.
- An unreadable existing project is never silently replaced by the starting draft. Check it and its `.bak` file before restarting.

Since the project file lives in a Dropbox folder, Dropbox can sync it along with the images. **Do not edit the same project simultaneously on multiple computers.** Dropbox is not a collaborative database.

## Write and arrange

The middle of the screen looks like the finished page. Controls appear only when you hover over or click into something.

- **Chapters** (left): click to open, **drag to reorder**, and drop an image or block onto a chapter to move it there. **Title page** edits the story title and introduction. **Notes** above each chapter holds private notes. A dot means the chapter has notes.
- **Story outline** (top of the left sidebar, dashed): a private planning page for premise, characters, arc and open questions. **Insert chapter list** adds the current chapter titles; **Download (.md)** saves a copy. The outline is saved in the project but never appears in Read, Print or Export.
- **Disclaimer**: text readers see before the title in Read, Print and exported HTML. Leave it empty for none. Tick **Readers must click Continue** to make the exported story open on the disclaimer alone, with a Continue button.
- **Writing**: text boxes grow as you type. Blank lines separate paragraphs. Text is plain text, not Markdown/rich text.
- **Insert anywhere**: hover between any two blocks to get **+ Text** / **+ Image**. Clicking the thin line between blocks marks that spot for the next image.
- **Images go where you are working.** The library always says where the next picture will land: at the marked spot, at your text cursor (the paragraph is split around the picture), or at the end of the chapter.
- **Image library** (right): pictures load as small cached thumbnails. Search, filter by scene, sort newest first (newly generated images are at the top), show only unused pictures, change the thumbnail size, or drag the panel edge to resize it. **⤢** enlarges the library to fill the screen. Hover a picture for **+ Insert**, tick several for a batch insert, or drag one to any spot in the chapter.
- **Click any picture** (in the library or the story) to see it large. Use ← → to browse. Here you can write a **description** and **scene** for it; these make search useful and are saved in the project, never in the image file. The viewer also lists where the picture is already used.
- **Picture blocks**: hover for size (**S / M / L / Wide**), **Replace** (keeps the caption), move up/down, or **⋯** for Duplicate, Move to chapter and Delete. Click under a picture to write a caption.
- **Picture groups**: 2–12 pictures shown together as a **Row** or a **Grid**, with one shared caption. Make one by:
  - ticking pictures in the library and choosing **Insert as one group**;
  - dragging a picture onto the left or right edge of another picture (it shows *Group side by side*);
  - using **⋯ → Group with the picture above/below**.
  Hover a group for Row/Grid, size, **+ Add** and **⋯ → Ungroup**. Hover a single picture inside the group to move it, **Replace** it or take it out with **×**. A group left with one picture turns back into a normal picture. On phones, rows and grids show two pictures across.
- **Deleting never asks.** Deleted blocks and chapters show an **Undo** button instead, and Ctrl+Z works too. Undo history does not survive a reload. Original image files are never deleted.
- **Focus** hides both side panels. **☰** and **Images** toggle each one.
- **Read** shows the whole draft as a reader will see it, opened at the current chapter. Each chapter has an **Edit** button that jumps back to it.
- The dot in the top bar is green when everything is on disk. Click it for details.

### Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+Enter | In text: split here, or start a new text block |
| Backspace at the start of a text block | Join it with the text above |
| Alt+↑ / Alt+↓ | Move the current block |
| Alt+I | Pick an image to insert at the cursor |
| Alt+PgUp / Alt+PgDn | Previous / next chapter |
| / | Search images |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo (in a text field these undo typing first) |
| Esc | Close the viewer, the enlarged library or a menu |

## Backups, import and finished stories

**Download backup** (in the **⋯** menu) downloads editable project JSON, including private notes and image descriptions. **Import project…** replaces the current draft after confirmation and continues saving automatically. Keep backups before replacing a project or starting a blank story.

**Export story HTML** downloads a read-only illustrated story without editor code or private notes. Save it **beside the original images** so relative picture links work. Alternatively use **Read → Print / PDF**. Image sizes (S/M/L/Wide) are kept in the export.

Projects and exports reference images by filename; they do not embed the image files. Keep the original images together with the project when moving it.

## Privacy and scope

The server listens only on **127.0.0.1**, not on your LAN or the internet. It serves only the editor assets and the top-level images and videos of your story folder. Project writes are restricted to the designated autosave/backup files, use a per-server request token and reject cross-site writes. There is no general filesystem API or directory listing.

**Hide images** hides pictures and videos in the editor and preview, for working in public.

Your images and videos are never modified or deleted by editing.
