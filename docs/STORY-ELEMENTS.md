# Story elements: characters (design)

Status: design agreed in conversation 2026-11-03, not built yet. Characters come first. Places, objects and a story-wide timeline are expected later and use the same structure, as further element *kinds*.

## What it should feel like

Getting to know a character, not filling in a form. The writer starts with a rough idea, then experiments and iterates, so a character page is a free page that grows over time. There are no fields to fill in, no prompts or question decks, and no required headings.

## First version

### Characters in the sidebar

- A **Characters** section in the left sidebar, below Story outline: each character shows its portrait thumbnail and name. Click to open, drag to reorder, **+ Character** to add one.
- Deleting a character shows **Undo**, as for chapters (Ctrl+Z works too). The undo also brings back its relationships.
- A **Relationship map** entry opens the map (below).

### Character page

Opens in the middle, where chapters open.

- **Header**: the portrait (the chosen main picture), the name, and an optional *also called* line for nicknames.
- **Free page**: exactly like a chapter. Text blocks, pictures, picture groups, captions, sizes, drag and drop, insert anywhere. Bio, looks, temperament, history: whatever the writer wants, in their own words and order.
- **Comments** work on its text blocks the same way as in chapters.
- **Looks**: every picture tagged with this character (see below), as a gallery. Click one to view it; **Make portrait** in the viewer sets the main portrait. Pictures placed on the free page are not tagged automatically, so the free page stays free.
- **Earlier looks**: iterating means older pictures stop being right but are still worth keeping. Any picture in Looks can be marked **Earlier look** (and back). Earlier looks stay in the gallery, dimmed and after the current ones, and cannot be the portrait (marking the portrait as earlier clears the portrait).
- **Copy as text**: puts the character on the clipboard as plain text, ready to paste into an image-generation prompt or a note: name, *also called*, the free page's text and captions in order, and the connections ("A ↔ B: sisters — note", "A → B: admires (one-way)"). Picture files and comments are left out.
- **Connections**: this character's relationships, as a small map centred on them. **+ Connection** picks another character, a word and a note.

### Pictures

- The picture viewer gets **In this picture**: tag one or more characters, next to the description and scene.
- The library gets a character filter (any tagged character).
- Concept art and story pictures share the one library. A picture can be in the story, tagged, both or neither.
- The viewer's *Used in* list also shows character pages that place the picture. The library's *Unused* filter and *Used* badge keep counting chapters only: a picture is "used" when it is in the story.

### Relationship map

- One map for the whole cast: portraits with names, joined by lines. Characters without connections are shown too.
- **Automatic layout**: a stress layout, where screen distance follows how many steps apart two characters are (unconnected characters sit just beyond the rest), turned so its longest spread runs across, then nudged so portraits never overlap. It starts from the sidebar order, so the same cast always lays out the same way. No manual positioning in this version.
- On a character page, **Connections** shows that character in the middle with everyone they are connected to around them.
- Each line carries **one word** ("sisters", "rivals"). Clicking a line shows its **note**, editable there.
- A connection is either **mutual** (a plain line) or **one-way** (an arrow: "A secretly admires B"). Two one-way connections between the same pair are drawn as two curved arrows, so B can feel differently about A.
- Click a portrait to open that character's page.

### Private

Characters, tags and relationships are saved in `story-project.json` and in downloaded backups. They never appear in Read, Print, exported HTML or PDF.

### Story outline

The outline stays a single free text page. Its hint and placeholder stop mentioning characters.

## Not in the first version

- **Characters in chapters** ("appears in"), and a character's path through the story. That arrives with the story timeline, which follows chapter order.
- A signature colour per character, a *still to find out* list, a Cast page (line-up and whole-cast notes), colour swatches. Considered and left out for now.
- **Wardrobe** (outfits shared by several characters, current or archived): a later element kind.
- **Relationships changing over time.** The relationship record leaves room for it (a later version can add per-chapter states), so nothing has to be migrated.
- Manual positions on the map, places, objects, the story timeline.

## Project format

New optional top-level fields:

```jsonc
"elements": [
  {
    "id": "el-<hex>",          // shares the project's id namespace (chapters, blocks, comments)
    "kind": "character",       // later: "place", "object", …
    "name": "…",               // may be empty while drafting
    "aka": "…",                // optional, free text
    "portrait": "file.png",    // optional; a top-level media filename
    "pictures": [              // tagged pictures, in the order they were tagged; at most 2000
      {"file": "a.png", "earlier": true}   // "earlier" optional: an earlier look
    "blocks": [ … ]            // same block types and rules as a chapter's blocks, comments included
  }
],
"relations": [
  {
    "id": "rel-<hex>",
    "from": "el-…", "to": "el-…",   // must name existing elements, and differ
    "label": "sisters",             // one word or short phrase, ≤ 100 characters
    "note": "…",                    // ≤ 100 000 characters
    "oneWay": true                  // optional; absent means mutual
  }
]
```

- Limits: at most 500 elements and 5000 relations. Element blocks count toward the existing 10 000-block limit.
- Validation reuses the chapter block validator, so element blocks get the same media and comment rules.
- `project_files` (cover picture) keeps reading chapters only.
- Deleting an element deletes its relations. A relation that names a missing element is dropped on load, not rejected.

### Protecting characters from older versions

0.4.x and the old pre-0.2 editor keep only the fields they know when they save, so they would silently delete every character. Both refuse to open a project whose `version` is not 1. Agreed rule: **the first time a character is created, the project becomes format version 2**, after writing `story-project.v1.backup.json`. Older apps then refuse to open it instead of destroying it. Stories without characters stay version 1 and keep working everywhere. Bump the API version to 9.

## Building it

Progress: steps 1–5 done (2026-11-03).

1. **Format and server**: validation of `elements` and `relations`, format 2 and its backup, API 9, tests.
2. **Pages**: make the block editor work on any page (chapter or element), not only chapters, including comments, undo, the Comments panel ("This page" / "Whole story", with character pages listed after the chapters), drag and drop, and "Move to…".
3. **Characters**: sidebar list, character page header, add, delete and undo, reorder.
4. **Pictures**: *In this picture* tagging in the viewer, the library's character filter, the Looks gallery, earlier looks, the portrait, Copy as text.
5. **Relationships**: the map page, Connections on the character page, adding, editing and deleting connections.
6. **Finish**: outline wording, the user guide, the changelog, and a release.
