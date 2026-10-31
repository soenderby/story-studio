# Story Studio

A local editor for illustrated stories. Write chapters, place images and videos between paragraphs, group them into rows and grids, and export a finished story as a single HTML page.

Everything runs on your own computer. A story is an ordinary folder holding your images, videos and a `story-project.json` file. Nothing is uploaded anywhere, and the media files are never modified.

> **Status:** under active development toward a Windows installer with multiple stories and automatic updates. See [docs/PLAN.md](docs/PLAN.md).

## Run from source

Requires Python 3.9 or newer, standard library only.

```sh
python -m story_studio [--story "path/to/a/story folder"] [--no-browser] [--port N]
```

Run it from the `src` folder, or set `PYTHONPATH=src`. The browser opens http://127.0.0.1:8765/ (or the story directly with `--story`, which also adds it to the list). Starting it again while it runs just opens the running instance. New images and videos copied into a story folder appear in the library within a few seconds, and edits are saved automatically to `story-project.json` in that folder.

The list of stories, settings and a log live in `%APPDATA%\Story Studio` (`~/.local/share/story-studio` elsewhere). Set `STORY_STUDIO_HOME` to use another folder.

## Tests

```sh
python -B -m unittest discover -s tests
```

Tests only use temporary folders and free ports.

## Documentation

- [User guide](docs/USER-GUIDE.md)
- [Plan](docs/PLAN.md) and [architecture / API contract](docs/API.md)
- [Changelog](CHANGELOG.md)
