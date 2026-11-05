# Story Studio user guide

## Automatic images and videos

Copy or generate new PNG, JPG/JPEG, WebP, GIF or BMP images, or MP4, M4V, MOV, WebM or OGV videos, into the story folder or any folder inside it. They appear in the library within a few seconds, without selecting files or refreshing the page.

- **Subfolders** show in the library as folders, like in File Explorer. Click a folder to open it; the path above the pictures (**Story folder › Batch one › Day 2**) takes you back up. Each folder shows how many files it holds, counting its own subfolders.
- **Searching and filtering** (scene, character, Unused only) look through the open folder *and all folders inside it*, and each match shows which folder it is in. Search for a folder's name to find everything in it.
- Not scanned: hidden folders (such as `.cache`), folders that hold a story of their own (`story-project.json`), shortcuts, symbolic links, junctions and ZIP archives. Folders more than 8 levels deep are skipped, and a story folder lists at most 20 000 files in 2 000 subfolders.
- Moving or renaming a file or folder counts as removing the picture and adding a new one: placed copies in the story show as unavailable until the file is back at its old path.
- Previously described images retain their descriptions and scene categories. New images use their filenames until described elsewhere.
- Removed images disappear from the available library. If they are already placed in a chapter, their placements and captions are retained so you do not lose your work; they show as unavailable until the files return.
- Pictures are referenced by their original filenames (with the folder, such as `Batch one/forest.png`), not uploaded or embedded.
- The first picture you use from a subfolder changes the story to a newer project format, so older versions of Story Studio cannot open it any more (they could not show those pictures). The file from before is kept as `story-project.v1.backup.json` (or `v2` for a story with characters). Update Story Studio on every computer that edits the same story. Stories that only use pictures at the top of the folder are unchanged.
- Discovery runs while the local server and editor page are running. Reopening the editor also loads the current folder contents.

### Videos

Videos work everywhere pictures do: insert, drag, Replace, sizes (S/M/L/Wide), captions and picture groups (mix videos and pictures freely).

- In the library, videos have a **▶** badge and a thumbnail taken from about one second in. New videos get the scene **Videos**.
- In the editor, videos are silent and **play while you hover over them**. Click one to open it in the viewer, with sound and full controls.
- In **Read**, Print and exported HTML, videos show normal play controls. Exported HTML contains the videos themselves (up to 200 MB each). In an exported PDF, each video is shown as its first frame.
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
- **Story outline** (top of the left sidebar, dashed): a private planning page for the premise, the arc, chapter beats and open questions. **Insert chapter list** adds the current chapter titles; **Download (.md)** saves a copy. The outline is saved in the project but never appears in Read, Print or Export.
- **Disclaimer**: text readers see before the title in Read, Print and exported HTML. Leave it empty for none. Tick **Readers must click Continue** to make the exported story open on the disclaimer alone, with a Continue button.
- **Writing**: text boxes grow as you type. Blank lines separate paragraphs. Text is plain text, not Markdown/rich text.
- **Insert anywhere**: hover between any two blocks to get **+ Text** / **+ Image**. Clicking the thin line between blocks marks that spot for the next image.
- **Images go where you are working.** The library always says where the next picture will land: at the marked spot, at your text cursor (the paragraph is split around the picture), or at the end of the chapter.
- **Image library** (right): pictures load as small cached thumbnails. Search, filter by scene, sort newest first (newly generated images are at the top), show only unused pictures, change the thumbnail size, or drag the panel edge to resize it. **⤢** enlarges the library to fill the screen. Hover a picture for **+ Insert**, tick several for a batch insert, or drag one to any spot in the chapter.
- **Click any picture** (in the library or the story) to see it large. Use ← → to browse. Here you can write a **description** and **scene** for it; these make search useful and are saved in the project, never in the image file. The viewer also lists where the picture is already used.
- **Picture blocks**: hover for size (**S / M / L / Wide**), **Replace** (keeps the caption), move up/down, or **⋯** for Duplicate, Move to chapter (or to a character page) and Delete. Click under a picture to write a caption.
- **Picture groups**: 2–12 pictures shown together as a **Row** or a **Grid**, with one shared caption. Make one by:
  - ticking pictures in the library and choosing **Insert as one group**;
  - dragging a picture onto the left or right edge of another picture (it shows *Group side by side*);
  - using **⋯ → Group with the picture above/below**.
  Hover a group for Row/Grid, size, **+ Add** and **⋯ → Ungroup**. Hover a single picture inside the group to move it, **Replace** it or take it out with **×**. A group left with one picture turns back into a normal picture. On phones, rows and grids show two pictures across.
- **Deleting never asks.** Deleted blocks and chapters show an **Undo** button instead, and Ctrl+Z works too. Undo history does not survive a reload. Original image files are never deleted.
- **Focus** hides all side panels. **☰**, **Images** and **Comments** toggle each one. Images and Comments share the right-hand side, so opening one closes the other.
- **Read** shows the whole draft as a reader will see it, opened at the current chapter. Each chapter has an **Edit** button that jumps back to it.
- The dot in the top bar is green when everything is on disk. Click it for details.

## Characters

Get to know your characters on pages of their own. There are no forms or fields to fill in: a character page is a free page, like a chapter, that you build up as the character takes shape.

- **Characters** (left sidebar, above the chapters): **+** adds a character, click one to open it, drag to reorder. Each shows its portrait and name.
- **The character page** has a name, an optional *also called* line for nicknames, and a free page below it. Write, insert pictures and picture groups, add captions and comments exactly as in a chapter: bio, looks, temperament, history, in your own words and order. Deleting a character shows **Undo**, which also brings back its connections.
- **Looks**: tag pictures with the characters in them. In the picture viewer, click names under **In this picture**, or drag a picture from the library onto a character's Looks. Every tagged picture appears in that character's **Looks** gallery. Concept art and story pictures share the one library, so a picture can be in the story, tagged, both or neither.
- **Portrait**: open a look and click **Make portrait**. Without a chosen portrait, the first look is used.
- **Earlier looks**: when a character's look moves on, tick **Earlier look** in the viewer instead of deleting the old pictures. Earlier looks stay in the gallery, dimmed and after the current ones.
- **Library filter**: the library's character menu shows the pictures of one character, or those not tagged with anyone.
- **Copy as text** puts the character on the clipboard as plain text: name, *also called*, the page's text and captions, and the connections. Paste it into an image generator's prompt or a note. Pictures and comments are left out.

### Relationship map

- **Relationship map** (under the characters) shows the whole cast as portraits joined by lines. It lays itself out: closely connected characters sit near each other.
- **+ Connection**: pick two characters, mutual (**↔**) or one-way (**→**), and a word such as *sisters* or *admires*. One-way connections are dashed arrows, so A can feel differently about B than B does about A.
- **Click a line or its word** to write a note about the connection, change the word or the direction, or delete it (with **Undo**).
- **Click a portrait** to open that character's page. Each character page also has a **Connections** section showing that character with everyone they are connected to.

### Private, and newer stories

Characters, tags and connections are saved in `story-project.json` and in downloaded backups. They never appear in Read, Print, exported HTML or PDF.

The first character changes the story to a newer project format, so older versions of Story Studio cannot delete the characters by accident. The story file from before is kept as `story-project.v1.backup.json`. Older versions refuse to open the story instead, so **update Story Studio on every computer that edits the same story**. Stories without characters are unchanged.

## Comments and markup

Leave notes on your own text while you write or reread, and mark passages to come back to.

- **Comment on words**: select them in a text block and click the **＋ Comment** button that appears above the selection, or press **Alt+C**. With nothing selected, the whole paragraph at the cursor gets the comment. Every text block also has **Comment** in its hover toolbar.
- **Labels**: each comment is a **Note**, **Change**, **Question** or **Keep**, shown as the highlight colour. The text of a comment is optional, so a label alone works as a coloured marker.
- **Suggest wording**: propose a rewrite of the commented words. **Accept change** puts it into the story (Undo works); **Discard suggestion** drops it.
- **Resolve** a comment when it is dealt with. Resolved comments lose their highlight and are hidden until you tick **Show resolved**. **Delete** removes a comment completely.
- **The Comments panel** lists the comments of **This chapter** or the **Whole story**, in reading order. Click the quoted words on a card to jump to them in the text. Putting the cursor inside highlighted words highlights their card. The chapter list shows how many open comments each chapter has.
- **Comments follow the words.** Edit freely: highlights move as you type, splitting and joining text blocks keeps them with their words, and moving a block to another chapter takes its comments along. If you delete all the commented words, the comment stays and shows the original words struck through. Duplicating a block does not copy its comments.
- **Commenting while reading**: in **Read**, tick **Show comments** to see the highlights. Select words to comment on them, or click a highlight to open its comment. **Show this in the editor** jumps back to the spot.
- Comments are **private**. They are saved in `story-project.json` and in downloaded backups, but never appear in Print or exported HTML, and appear in Read only when you turn them on. Comments work on character pages too; the panel lists them after the chapters.
- ⋯ → **Settings** → **Highlight commented text** turns the highlights off while writing, if you prefer a clean page. The Comments panel still works.

### Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+Enter | In text: split here, or start a new text block |
| Backspace at the start of a text block | Join it with the text above |
| Alt+↑ / Alt+↓ | Move the current block |
| Alt+I | Pick an image to insert at the cursor |
| Alt+C | Comment on the selected words (or the paragraph at the cursor). Elsewhere: show / hide comments |
| Alt+PgUp / Alt+PgDn | Previous / next chapter |
| / | Search images |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo (in a text field these undo typing first) |
| Esc | Close the viewer, the enlarged library, a menu or a dialog |

## Backups, import and finished stories

**Download backup** (in the **⋯** menu) downloads editable project JSON, including private notes and image descriptions. **Import project…** replaces the current draft after confirmation and continues saving automatically. Keep backups before replacing a project or starting a blank story.

**Export story HTML** (in the **⋯** menu) downloads a read-only illustrated story as **one file with the pictures inside**, so it works wherever you open or send it. Pictures are saved as high-quality WebP at most 2048 pixels on their longest side, which keeps the file much smaller than the originals.

**Export PDF** downloads the same story as an A4 PDF, each chapter starting on a new page. Story Studio prints it in the background with Microsoft Edge (or Google Chrome), which comes with Windows. If neither is found, use **Read → Print / PDF** and choose *Save as PDF*.

Neither export includes private notes, the outline or comments. Image sizes (S/M/L/Wide) and picture groups are kept. Exporting takes a few seconds for a long story; a message shows the progress.

Projects reference images by filename; they do not embed the image files. Keep the original images together with the project when moving it. (Exports are different: they contain the pictures.)

## Privacy and scope

The server listens only on **127.0.0.1**, not on your LAN or the internet. It serves only the editor assets and the images and videos of your story folder and its subfolders, never through a link or junction to somewhere else. Project writes are restricted to the designated autosave/backup files, use a per-server request token and reject cross-site writes. There is no general filesystem API or directory listing.

**Settings → Hide all images and videos** (the **⋯** menu in a story, or **Settings** on the story list) hides every picture and video in Story Studio: story covers, the image library, chapter thumbnails, the story itself, the viewer and Read mode. Each one is replaced by a small placeholder with its description, so you can still write and arrange the story, and videos stop playing. The setting is kept in the browser and applies to all stories. Exported HTML always includes the pictures.

Your images and videos are never modified or deleted by editing.
