"""One story folder: project validation, live media discovery, atomic saving and legacy migration."""
from contextlib import contextmanager
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading

from . import paths

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
# Browsers sniff the container, so QuickTime/M4V files with H.264 inside play when served as MP4.
VIDEO_TYPES = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/mp4', '.webm': 'video/webm', '.ogv': 'video/ogg'}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | set(VIDEO_TYPES)
IMAGE_SIZES = {'small', 'medium', 'large', 'wide'}
GROUP_LAYOUTS = {'row', 'grid'}
MAX_GROUP = 12
COMMENT_LABELS = {'note', 'change', 'question', 'keep'}
MAX_COMMENTS = 500  # per text block
LEGACY_DATA = 'editor-data.js'  # Older single-story installs kept image descriptions here, beside the media.
MAX_BODY = 10_000_000
ELEMENT_KINDS = {'character'}  # A new kind with new fields needs a new format version: older apps drop what they do not know.
MAX_ELEMENTS = 500
MAX_RELATIONS = 5000
MAX_ELEMENT_PICTURES = 2000
# Format 2 = format 1 + story elements (characters) and relations. Apps that only know format 1 refuse to open it,
# rather than dropping the characters when they save.
FORMAT_VERSIONS = (1, 2)
PROJECT_NAME = 'story-project.json'
V1_BACKUP_NAME = 'story-project.v1.backup.json'
PRE_MIGRATION_NAME = 'story-project.pre-migration.json'
DEFAULT_CATEGORIES = {'', 'New additions', 'Videos', 'Added images'}
BLANK_PROJECT = {'format': 'illustrated-story-project', 'version': 1, 'id': 'new-story', 'title': 'Untitled story',
                 'subtitle': '', 'chapters': [{'id': 'chapter-1', 'title': 'Chapter one', 'notes': '', 'blocks': []}]}


def safe_media(name):
    return (isinstance(name, str) and 0 < len(name) <= 500
            and not re.search(r'[\\/\x00-\x1f]', name)
            and Path(name).suffix.lower() in MEDIA_EXTENSIONS)


def directory_key(root):
    """The same folder seen from Windows (C:\\Users\\...) or WSL (/mnt/c/users/...) gets the same key."""
    path = str(root).replace('\\', '/')
    match = re.match(r'^/mnt/([a-zA-Z])(/.*)?$', path)
    if match:
        path = f'{match[1]}:{match[2] or "/"}'
    return hashlib.sha256(path.rstrip('/').lower().encode()).hexdigest()


def utf16_length(value):
    """String length as the editor's JavaScript counts it, so comment offsets mean the same on both sides."""
    return len(value.encode('utf-16-le')) // 2


def clean_comments(value, block_text, text, unique_id):
    """A text block's comments. Offsets that no longer fit the text are clamped, never rejected:
    they only place a highlight, and the comment itself must not be lost."""
    if not isinstance(value, list) or len(value) > MAX_COMMENTS:
        raise ValueError(f'A text block can hold at most {MAX_COMMENTS} comments.')
    length, result = utf16_length(block_text), []
    for comment in value:
        if not isinstance(comment, dict):
            raise ValueError('Invalid comment.')
        offsets = [comment.get('start'), comment.get('end')]
        if any(type(n) is not int for n in offsets):
            raise ValueError('Invalid comment position.')
        end = min(max(offsets[1], 0), length)
        start = min(max(offsets[0], 0), end)
        label = comment.get('label', 'note')
        if not isinstance(label, str) or label not in COMMENT_LABELS:
            raise ValueError('Unknown comment label.')
        item = {'id': unique_id(comment.get('id')), 'start': start, 'end': end, 'label': label,
                'note': text(comment.get('note', ''), 100000), 'quote': text(comment.get('quote', ''), 100000)}
        if 'suggestion' in comment:
            item['suggestion'] = text(comment['suggestion'], 300000)
        if 'resolved' in comment:
            if not isinstance(comment['resolved'], bool):
                raise ValueError('Invalid comment state.')
            if comment['resolved']:
                item['resolved'] = True
        for stamp in ('created', 'updated'):
            if stamp in comment:
                item[stamp] = text(comment[stamp], 100)
        result.append(item)
    return result


def validate_project(value):
    """Reject malformed imports before touching the last good file."""
    if not isinstance(value, dict) or value.get('format') != 'illustrated-story-project' or type(value.get('version')) is not int:
        raise ValueError('Not a supported Story studio project.')
    if value['version'] not in FORMAT_VERSIONS:
        raise ValueError('This story was saved by a newer version of Story Studio. Update Story Studio to open it.'
                         if value['version'] > max(FORMAT_VERSIONS) else 'Not a supported Story studio project.')
    ids = set()
    count = 0

    def text(value, limit=2_000_000):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('Invalid or oversized project text.')
        return value

    def unique_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value) or value in ids:
            raise ValueError('Invalid or duplicate chapter/block ID.')
        ids.add(value)
        return value

    def blocks(value):
        """A page's blocks: a chapter's or a story element's. Every page counts toward the same block limit."""
        nonlocal count
        if not isinstance(value, list):
            raise ValueError('Invalid page blocks.')
        result = []
        for block in value:
            count += 1
            if count > 10000 or not isinstance(block, dict):
                raise ValueError('Invalid or excessive project blocks.')
            item = {'id': unique_id(block.get('id')), 'type': block.get('type')}
            if item['type'] == 'text':
                item['text'] = text(block.get('text'))
                if 'comments' in block:
                    comments = clean_comments(block['comments'], item['text'], text, unique_id)
                    if comments:
                        item['comments'] = comments
            elif item['type'] == 'image' and safe_media(block.get('file')):
                item.update(file=block['file'], caption=text(block.get('caption'), 300000))
            elif item['type'] == 'gallery':
                pictures = block.get('items')
                if not isinstance(pictures, list) or not 2 <= len(pictures) <= MAX_GROUP:
                    raise ValueError(f'A picture group must contain 2–{MAX_GROUP} pictures.')
                if not isinstance(block.get('layout'), str) or block['layout'] not in GROUP_LAYOUTS:
                    raise ValueError('Unknown picture group layout.')
                for picture in pictures:
                    if not isinstance(picture, dict) or not safe_media(picture.get('file')):
                        raise ValueError('Image references must be top-level image filenames, not paths or URLs.')
                item.update(layout=block['layout'], items=[{'file': picture['file']} for picture in pictures],
                            caption=text(block.get('caption'), 300000))
            else:
                raise ValueError('Image references must be top-level image filenames, not paths or URLs.')
            if item['type'] != 'text' and 'size' in block:
                if not isinstance(block['size'], str) or block['size'] not in IMAGE_SIZES:
                    raise ValueError('Unknown image size.')
                item['size'] = block['size']
            result.append(item)
        return result

    chapters = value.get('chapters')
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 500:
        raise ValueError('A project must contain 1–500 chapters.')
    clean = {'format': 'illustrated-story-project', 'version': value['version'],
             'id': text(value.get('id', 'story'), 100),
             'title': text(value.get('title'), 10000),
             'subtitle': text(value.get('subtitle'), 100000), 'chapters': []}
    for chapter in chapters:
        if not isinstance(chapter, dict) or not isinstance(chapter.get('blocks'), list):
            raise ValueError('Invalid chapter.')
        result = {'id': unique_id(chapter.get('id')), 'title': text(chapter.get('title'), 10000),
                  'notes': text(chapter.get('notes', ''), 300000)}
        result['blocks'] = blocks(chapter['blocks'])
        clean['chapters'].append(result)
    if 'elements' in value:
        clean['elements'] = clean_elements(value['elements'], text, unique_id, blocks)
    if 'relations' in value:
        clean['relations'] = clean_relations(value['relations'], {e['id'] for e in clean.get('elements', [])},
                                             text, unique_id)
    if clean.get('elements') or clean.get('relations'):
        clean['version'] = 2
    if 'extraImages' in value:
        if not isinstance(value['extraImages'], list) or len(value['extraImages']) > 10000:
            raise ValueError('Invalid additional image library.')
        clean['extraImages'] = []
        for image in value['extraImages']:
            if not isinstance(image, dict) or not safe_media(image.get('file')):
                raise ValueError('Invalid additional image reference.')
            clean['extraImages'].append({'file': image['file'], 'title': text(image.get('title'), 10000),
                                         'category': text(image.get('category'), 1000)})
    if 'disclaimer' in value:
        clean['disclaimer'] = text(value['disclaimer'], 100000)
    if 'disclaimerGate' in value:
        if not isinstance(value['disclaimerGate'], bool):
            raise ValueError('Invalid disclaimer setting.')
        clean['disclaimerGate'] = value['disclaimerGate']
    if 'outline' in value:
        clean['outline'] = text(value['outline'])
    if 'updatedAt' in value:
        clean['updatedAt'] = text(value['updatedAt'], 100)
    return clean


def clean_elements(value, text, unique_id, blocks):
    """Story elements (characters). Each has a free page of blocks, like a chapter, and its tagged pictures."""
    if not isinstance(value, list) or len(value) > MAX_ELEMENTS:
        raise ValueError(f'A story can hold at most {MAX_ELEMENTS} characters.')
    result = []
    for element in value:
        if not isinstance(element, dict) or element.get('kind') not in ELEMENT_KINDS:
            raise ValueError('Invalid or unknown story element.')
        item = {'id': unique_id(element.get('id')), 'kind': element['kind'], 'name': text(element.get('name', ''), 10000)}
        if 'aka' in element:
            item['aka'] = text(element['aka'], 10000)
        if element.get('portrait') is not None:
            if not safe_media(element['portrait']):
                raise ValueError('A portrait must be a top-level image filename, not a path or URL.')
            item['portrait'] = element['portrait']
        pictures = element.get('pictures', [])
        if not isinstance(pictures, list) or len(pictures) > MAX_ELEMENT_PICTURES:
            raise ValueError(f'A character can have at most {MAX_ELEMENT_PICTURES} tagged pictures.')
        item['pictures'], seen = [], set()
        for picture in pictures:
            if not isinstance(picture, dict) or not safe_media(picture.get('file')):
                raise ValueError('Tagged pictures must be top-level image filenames, not paths or URLs.')
            if 'earlier' in picture and not isinstance(picture['earlier'], bool):
                raise ValueError('Invalid earlier-look setting.')
            if picture['file'] in seen:
                continue  # Tagging the same picture twice is harmless; keep the first.
            seen.add(picture['file'])
            item['pictures'].append({'file': picture['file'], 'earlier': True} if picture.get('earlier')
                                    else {'file': picture['file']})
        item['blocks'] = blocks(element.get('blocks', []))
        result.append(item)
    return result


def clean_relations(value, element_ids, text, unique_id):
    """Connections between elements. One naming a missing element (or itself) is dropped, never rejected."""
    if not isinstance(value, list) or len(value) > MAX_RELATIONS:
        raise ValueError(f'A story can hold at most {MAX_RELATIONS} connections.')
    result = []
    for relation in value:
        if not isinstance(relation, dict):
            raise ValueError('Invalid connection.')
        ends = relation.get('from'), relation.get('to')
        if ends[0] == ends[1] or not all(isinstance(end, str) and end in element_ids for end in ends):
            continue
        item = {'id': unique_id(relation.get('id')), 'from': ends[0], 'to': ends[1],
                'label': text(relation.get('label', ''), 100), 'note': text(relation.get('note', ''), 100000)}
        if 'oneWay' in relation:
            if not isinstance(relation['oneWay'], bool):
                raise ValueError('Invalid connection direction.')
            if relation['oneWay']:
                item['oneWay'] = True
        result.append(item)
    return result


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def atomic_write(path, payload):
    """Same-directory replacement, so interruption cannot leave half-written JSON."""
    if path.is_symlink():
        raise ValueError('Refusing to overwrite a symbolic link.')
    descriptor, temporary = tempfile.mkstemp(prefix='.story-save-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_editor_data(path):
    """The JSON inside an editor-data.js file; empty when an optional legacy file is absent."""
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError(f'{path.name} must not be a symbolic link.')
    try:
        source = path.read_text(encoding='utf-8').split('window.STORY_EDITOR_DATA = ', 1)[1].strip().removesuffix(';')
        data = json.loads(source)
    except (IndexError, ValueError) as error:
        raise ValueError(f'{path.name} could not be read: {error}') from None
    if not isinstance(data, dict):
        raise ValueError(f'{path.name} does not hold editor data.')
    return data


def media_type(name):
    return VIDEO_TYPES.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or 'application/octet-stream'


def byte_range(header, size):
    """(start, end, partial) for an HTTP Range header, or None when it cannot be satisfied (416)."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', (header or '').strip())
    if not match or not (match[1] or match[2]):
        return 0, size - 1, False
    if match[1]:
        start = int(match[1])
        end = min(int(match[2]), size - 1) if match[2] else size - 1
    else:
        start, end = max(0, size - int(match[2])), size - 1
    if start >= size or start > end:
        return None
    return start, end, True


def project_files(project):
    """Media filenames in the order the story uses them."""
    for chapter in project['chapters']:
        for block in chapter['blocks']:
            if block['type'] == 'image':
                yield block['file']
            elif block['type'] == 'gallery':
                yield from (item['file'] for item in block['items'])


def legacy_descriptions(project, legacy):
    """Descriptions from a legacy editor-data.js for images the project's extraImages does not describe yet."""
    described = {image['file'] for image in project.get('extraImages', [])}
    additions = []
    images = legacy.get('images')
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, dict) or not safe_media(image.get('file')) or image['file'] in described:
            continue
        stem = Path(image['file']).stem
        title = image['title'] if isinstance(image.get('title'), str) else stem
        category = image['category'] if isinstance(image.get('category'), str) else ''
        if title == stem and category in DEFAULT_CATEGORIES:
            continue  # Nothing beyond what discovery shows anyway.
        additions.append({'file': image['file'], 'title': title, 'category': category})
        described.add(image['file'])
    return additions


def blank_project(title):
    try:
        project = validate_project(read_editor_data(paths.WEB_DIR / LEGACY_DATA).get('seed'))
    except (ValueError, OSError):
        project = validate_project(BLANK_PROJECT)
    # A unique id keeps the editor's legacy recovery-copy adoption from matching an unrelated story.
    project.update(id='story-' + secrets.token_hex(6), title=title)
    return project


LOCKS = {}
LOCKS_GUARD = threading.Lock()


def folder_lock(root):
    """One lock per folder, shared by every Studio for it, so a re-add or migration never races a save."""
    with LOCKS_GUARD:
        return LOCKS.setdefault(directory_key(root), threading.Lock())


@contextmanager
def saves_paused(timeout=30.0):
    """Hold every story folder's lock, so no save is in flight inside the block (saves are synchronous).

    Used by the updater right before it starts the installer. Raises TimeoutError if a save does
    not finish within `timeout` seconds.
    """
    with LOCKS_GUARD:
        locks = list(LOCKS.values())
    taken = []
    try:
        for lock in locks:
            if not lock.acquire(timeout=timeout):
                raise TimeoutError('A story is still being saved.')
            taken.append(lock)
        yield
    finally:
        for lock in reversed(taken):
            lock.release()


class Studio:
    """One story folder. Each folder has its own lock, so several stories can be edited at once."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.project_path = self.root / PROJECT_NAME
        self.backup_path = self.root / (PROJECT_NAME + '.bak')
        self.lock = folder_lock(self.root)
        legacy = read_editor_data(self.root / LEGACY_DATA).get('images')
        self.known_images = {image['file']: {'file': image['file'], 'title': str(image.get('title', '')),
                                             'category': str(image.get('category', ''))}
                             for image in (legacy if isinstance(legacy, list) else [])
                             if isinstance(image, dict) and safe_media(image.get('file'))}
        # Never replace a corrupt or unfamiliar existing autosave: refuse to open it instead.
        self.read_project()

    def read_project(self):
        if self.project_path.is_symlink():
            raise ValueError('The project file must not be a symbolic link.')
        if self.project_path.stat().st_size > MAX_BODY:
            raise ValueError('Project file exceeds 10 MB.')
        payload = self.project_path.read_bytes()
        project = validate_project(json.loads(payload))
        return project, hashlib.sha256(payload).hexdigest()

    def images(self):
        # scandir never enters directories and explicitly excludes symbolic links.
        images = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False) or not safe_media(entry.name):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # A copy/rename may have finished during this scan.
                known = self.known_images.get(entry.name)
                video = Path(entry.name).suffix.lower() in VIDEO_TYPES
                image = dict(known) if known else {'file': entry.name, 'title': Path(entry.name).stem,
                                                  'category': 'Videos' if video else 'New additions'}
                image['stamp'] = f'{stat.st_mtime_ns}-{stat.st_size}'
                images.append(image)
        images.sort(key=lambda image: image['file'])
        signature = hashlib.sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()
        return images, signature

    def media_file(self, name):
        """A top-level media file of this folder, never a symbolic link or anything in a subfolder."""
        if not safe_media(name):
            return None
        file = self.root / name
        return file if not file.is_symlink() and file.is_file() else None

    def summary(self):
        with self.lock:
            project, _ = self.read_project()
        images, _ = self.images()
        present = {image['file'] for image in images}
        cover = next((file for file in project_files(project) if file in present), None)
        return {'title': project['title'], 'cover': cover, 'chapters': len(project['chapters']), 'media': len(images)}

    def save(self, project, expected_revision):
        project = validate_project(project)
        payload = encode(project)
        if len(payload) > MAX_BODY:
            raise ValueError('Project exceeds 10 MB.')
        with self.lock:
            on_disk, current = self.read_project()
            if not isinstance(expected_revision, str) or not secrets.compare_digest(current, expected_revision):
                return None
            if on_disk['version'] > project['version']:
                # Never step a story back to a format that older apps would open and strip.
                project['version'] = on_disk['version']
                payload = encode(project)
            if hashlib.sha256(payload).hexdigest() != current:
                if on_disk['version'] == 1 and project['version'] > 1 and not (self.root / V1_BACKUP_NAME).exists():
                    # The last format-1 project, kept once, before older apps stop opening this story.
                    atomic_write(self.root / V1_BACKUP_NAME, self.project_path.read_bytes())
                # Back up the previous validated version before replacing it.
                atomic_write(self.backup_path, self.project_path.read_bytes())
                atomic_write(self.project_path, payload)
            return hashlib.sha256(payload).hexdigest()

    def migrate(self, legacy):
        """Copy legacy image descriptions into extraImages, after keeping the untouched project. Idempotent."""
        with self.lock:
            project, revision = self.read_project()
            backup = self.root / PRE_MIGRATION_NAME
            if not backup.exists():
                atomic_write(backup, self.project_path.read_bytes())
        additions = legacy_descriptions(project, legacy)
        if not additions:
            return False
        project['extraImages'] = project.get('extraImages', []) + additions
        if self.save(project, revision) is None:
            raise ValueError('The project changed during migration. Add the folder again.')
        return True


def prepare_story(root, title=None):
    """Make a folder ready to register: create a blank project, or migrate a legacy one. Safe to repeat."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f'Not a folder: {root}')
    legacy = read_editor_data(root / LEGACY_DATA)
    project_path = root / PROJECT_NAME
    if project_path.exists() or project_path.is_symlink():
        studio = Studio(root)  # Refuses a corrupt or linked project rather than replacing it.
        if legacy:
            studio.migrate(legacy)
        return studio
    try:
        project = validate_project(legacy.get('seed'))  # A legacy folder without a project starts from its seed.
    except ValueError:
        project = blank_project(title or root.name or BLANK_PROJECT['title'])
    additions = legacy_descriptions(project, legacy)
    if additions:
        project['extraImages'] = project.get('extraImages', []) + additions
    atomic_write(project_path, encode(validate_project(project)))
    return Studio(root)
