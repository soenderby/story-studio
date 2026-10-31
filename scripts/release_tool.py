#!/usr/bin/env python3
"""Release helper shared by scripts/release.ps1, scripts/release.sh and .github/workflows/release.yml.

Standard library only (Python 3.9+). See docs/RELEASING.md.

  current                          print __version__ from src/story_studio/version.py
  check VERSION                    exit 1 unless VERSION is valid semver (X.Y.Z or X.Y.Z-pre)
  prepare VERSION [--date D] [--dry-run]
                                   check VERSION > current and CHANGELOG "## Unreleased" has entries,
                                   then set __version__ and add the "## VERSION (D)" section.
                                   --dry-run prints the diff and changes nothing
  notes VERSION [--out FILE]       that version's CHANGELOG section (markdown, no heading)
  manifest --installer EXE --out latest.json [--version V] [--notes-file F] [--published-at T]
                                   write latest.json (docs/API.md, "Updates and releases")
  verify-manifest latest.json [--installer EXE] [--version V]
                                   check bytes, key order, field types, size and sha256
  public-key [--format pem|der|raw] [--key-file F] [--out FILE]
                                   update_key.PUBLIC_KEY as an Ed25519 SubjectPublicKeyInfo (pem, der)
                                   or base64 of the raw 32 bytes (raw). --key-file reads another copy
                                   of update_key.py, e.g. the one in the last release
"""
import argparse
import base64
import datetime
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = Path('src/story_studio/version.py')
KEY_FILE = Path('src/story_studio/update_key.py')
CHANGELOG = Path('CHANGELOG.md')

# Semver 2.0.0 without build metadata ("+..." would end up in tags and file names).
_ID = r'(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)'
SEMVER_RE = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(' + _ID + r'(?:\.' + _ID + r')*))?$')
VERSION_LINE_RE = re.compile(r"^__version__ = '([^']*)'", re.M)
HEADING_RE = re.compile(r'^## ')
UNRELEASED_RE = re.compile(r'^## Unreleased\s*$')

MANIFEST_KEYS = ('version', 'installer', 'sha256', 'size', 'notes', 'publishedAt')
# Limits of the app's updater (src/story_studio/updates.py): a release beyond them would be rejected.
MAX_MANIFEST = 64 * 1024
MAX_NOTES = 100_000
MAX_INSTALLER = 512 * 1024 * 1024
INSTALLER_NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe')
PUBLISHED_AT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
# DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410); the raw 32-byte key follows.
ED25519_SPKI_PREFIX = bytes.fromhex('302a300506032b6570032100')


class ReleaseError(Exception):
    pass


# --- semver -------------------------------------------------------------------------------------

def parse_semver(version):
    m = SEMVER_RE.match(version or '')
    if not m:
        raise ReleaseError(f"'{version}' is not a valid version (expected X.Y.Z or X.Y.Z-prerelease)")
    major, minor, patch, pre = m.groups()
    return int(major), int(minor), int(patch), (pre.split('.') if pre else [])


def semver_key(version):
    """Sort key with semver precedence: 1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-beta < 1.0.0."""
    major, minor, patch, pre = parse_semver(version)
    if not pre:
        return (major, minor, patch, (1,))
    ids = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre)
    return (major, minor, patch, (0,) + ids)


def is_prerelease(version):
    return bool(parse_semver(version)[3])


def installer_name(version):
    return f'StoryStudio-Setup-{version}.exe'


# --- files --------------------------------------------------------------------------------------

def read_text(path):
    # newline='' keeps CRLF if a checkout has it, so rewriting a file does not churn line endings.
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def write_text(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def current_version(root=ROOT):
    m = VERSION_LINE_RE.search(read_text(Path(root) / VERSION_FILE))
    if not m:
        raise ReleaseError(f'No __version__ line in {VERSION_FILE}')
    return m.group(1)


def split_lines(text):
    nl = '\r\n' if '\r\n' in text else '\n'
    return text.replace('\r\n', '\n').split('\n'), nl


def trim(lines):
    lines = list(lines)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def find_section(lines, heading_re):
    """(heading index, end index) of the first "## " section whose heading matches, or None."""
    for i, line in enumerate(lines):
        if heading_re.match(line):
            end = next((j for j in range(i + 1, len(lines)) if HEADING_RE.match(lines[j])), len(lines))
            return i, end
    return None


def version_heading_re(version):
    return re.compile(r'^## v?' + re.escape(version) + r'(?:\s|$)')


def changelog_notes(text, version):
    lines, _ = split_lines(text)
    found = find_section(lines, version_heading_re(version))
    if not found:
        raise ReleaseError(f'CHANGELOG.md has no "## {version}" section')
    body = trim(lines[found[0] + 1:found[1]])
    if not body:
        raise ReleaseError(f'The CHANGELOG.md section for {version} is empty')
    return '\n'.join(body) + '\n'


def release_changelog(text, version, date):
    """CHANGELOG text with the release section added. A stable release moves the "## Unreleased"
    entries into "## VERSION (DATE)" and leaves a fresh, empty "## Unreleased". A prerelease copies
    them, so "## Unreleased" keeps collecting everything for the next stable release."""
    lines, nl = split_lines(text)
    found = find_section(lines, UNRELEASED_RE)
    if not found:
        raise ReleaseError('CHANGELOG.md has no "## Unreleased" section')
    if find_section(lines, version_heading_re(version)):
        raise ReleaseError(f'CHANGELOG.md already has a section for {version}')
    start, end = found
    body = trim(lines[start + 1:end])
    if not body:
        raise ReleaseError('"## Unreleased" in CHANGELOG.md has no entries')
    head, rest = lines[:start], trim(lines[end:])
    section = [f'## {version} ({date})', ''] + body
    unreleased = ['## Unreleased', ''] + (body + [''] if is_prerelease(version) else [])
    out = head + unreleased + section + ([''] + rest if rest else []) + ['']
    return nl.join(out)


def release_version_file(text, version):
    return VERSION_LINE_RE.sub(f"__version__ = '{version}'", text, count=1)


def prepare(version, date=None, dry_run=False, root=ROOT, out=sys.stdout):
    """Check that VERSION can be released, then edit version.py and CHANGELOG.md (unless dry_run).
    Returns the list of problems; nothing is written when there is one."""
    root = Path(root)
    date = date or datetime.date.today().isoformat()
    problems = []
    try:
        parse_semver(version)
        current = current_version(root)
        if semver_key(version) <= semver_key(current):
            problems.append(f'{version} is not greater than the current version {current}')
    except ReleaseError as e:
        problems.append(str(e))
        current = None
    changes = {}
    if not problems:
        try:
            changes[VERSION_FILE] = release_version_file(read_text(root / VERSION_FILE), version)
            changes[CHANGELOG] = release_changelog(read_text(root / CHANGELOG), version, date)
        except ReleaseError as e:
            problems.append(str(e))
    if problems:
        for p in problems:
            print(f'  FAIL  {p}', file=out)
        return problems
    kind = 'prerelease' if is_prerelease(version) else 'release'
    print(f'  ok    {current} -> {version} ({kind}), CHANGELOG "## Unreleased" has entries', file=out)
    for rel, new in changes.items():
        old = read_text(root / rel)
        if dry_run:
            diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f'a/{rel.as_posix()}', f'b/{rel.as_posix()}')
            out.write(''.join(diff))
        else:
            write_text(root / rel, new)
    if not dry_run:
        print(f'  wrote {VERSION_FILE.as_posix()} and {CHANGELOG.as_posix()}', file=out)
    return []


# --- manifest -----------------------------------------------------------------------------------

def manifest_bytes(manifest):
    """The exact bytes of latest.json: UTF-8 (no BOM), keys in MANIFEST_KEYS order, 2-space indent,
    non-ASCII characters as is, LF line ends, one trailing newline."""
    ordered = {k: manifest[k] for k in MANIFEST_KEYS}
    return (json.dumps(ordered, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def file_digest(path):
    h = hashlib.sha256()
    size = 0
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def build_manifest(version, installer, notes, published_at=None):
    parse_semver(version)
    installer = Path(installer)
    if installer.name != installer_name(version):
        raise ReleaseError(f'Installer is named {installer.name}, expected {installer_name(version)}')
    sha256, size = file_digest(installer)
    published_at = published_at or utc_now()
    if not PUBLISHED_AT_RE.match(published_at):
        raise ReleaseError(f'publishedAt {published_at!r} is not YYYY-MM-DDTHH:MM:SSZ')
    if not notes.strip():
        raise ReleaseError('Release notes are empty')
    return {'version': version, 'installer': installer.name, 'sha256': sha256, 'size': size,
            'notes': notes, 'publishedAt': published_at}


def verify_manifest(data, installer=None, version=None):
    """Raise ReleaseError unless DATA (bytes of latest.json) is a well-formed manifest."""
    if data.startswith(b'\xef\xbb\xbf'):
        raise ReleaseError('latest.json starts with a UTF-8 BOM')
    if len(data) > MAX_MANIFEST:
        raise ReleaseError(f'latest.json is {len(data)} bytes; the app accepts at most {MAX_MANIFEST}')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ReleaseError(f'latest.json is not UTF-8: {e}')
    pairs = json.loads(text, object_pairs_hook=lambda p: p)
    if not isinstance(pairs, list) or tuple(k for k, _ in pairs) != MANIFEST_KEYS:
        raise ReleaseError(f'latest.json keys must be exactly {list(MANIFEST_KEYS)} in that order')
    m = dict(pairs)
    for key in ('version', 'installer', 'sha256', 'notes', 'publishedAt'):
        if not isinstance(m[key], str):
            raise ReleaseError(f'latest.json "{key}" must be a string')
    parse_semver(m['version'])
    if m['installer'] != installer_name(m['version']):
        raise ReleaseError(f'"installer" is {m["installer"]!r}, expected {installer_name(m["version"])!r}')
    if not INSTALLER_NAME_RE.fullmatch(m['installer']):
        raise ReleaseError(f'"installer" {m["installer"]!r} is not a file name the app accepts')
    if not re.fullmatch(r'[0-9a-f]{64}', m['sha256']):
        raise ReleaseError('"sha256" must be 64 lowercase hex digits')
    if not isinstance(m['size'], int) or isinstance(m['size'], bool) or not 0 < m['size'] <= MAX_INSTALLER:
        raise ReleaseError(f'"size" must be an integer from 1 to {MAX_INSTALLER}')
    if not m['notes'].strip():
        raise ReleaseError('"notes" is empty')
    if len(m['notes']) > MAX_NOTES:
        raise ReleaseError(f'"notes" has {len(m["notes"])} characters; the app accepts at most {MAX_NOTES}')
    if not PUBLISHED_AT_RE.match(m['publishedAt']):
        raise ReleaseError('"publishedAt" must be YYYY-MM-DDTHH:MM:SSZ (UTC)')
    if manifest_bytes(m) != data:
        raise ReleaseError('latest.json is not in the canonical format (indent, key order, newline)')
    if version is not None and m['version'] != version:
        raise ReleaseError(f'"version" is {m["version"]!r}, expected {version!r}')
    if installer is not None:
        if Path(installer).name != m['installer']:
            raise ReleaseError(f'Installer file is {Path(installer).name}, manifest says {m["installer"]}')
        sha256, size = file_digest(installer)
        if size != m['size']:
            raise ReleaseError(f'Installer size {size} != manifest size {m["size"]}')
        if sha256 != m['sha256']:
            raise ReleaseError(f'Installer sha256 {sha256} != manifest sha256 {m["sha256"]}')
    return m


# --- public key ---------------------------------------------------------------------------------

def public_key_raw(root=ROOT, key_file=None):
    key_file = Path(key_file) if key_file else Path(root) / KEY_FILE
    namespace = {}
    exec(compile(read_text(key_file), str(key_file), 'exec'), namespace)
    raw = base64.b64decode(namespace['PUBLIC_KEY'], validate=True)
    if len(raw) != 32:
        raise ReleaseError(f'update_key.PUBLIC_KEY is {len(raw)} bytes, expected 32')
    return raw


def public_key_der(root=ROOT, key_file=None):
    return ED25519_SPKI_PREFIX + public_key_raw(root, key_file)


def public_key_pem(root=ROOT, key_file=None):
    b64 = base64.b64encode(public_key_der(root, key_file)).decode('ascii')
    body = '\n'.join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f'-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----\n'


# --- command line -------------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')  # a cp1252 console must not crash on a diff
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('current')
    s = sub.add_parser('check')
    s.add_argument('version')
    s = sub.add_parser('prepare')
    s.add_argument('version')
    s.add_argument('--date', help='release date, YYYY-MM-DD (default: today)')
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('notes')
    s.add_argument('version')
    s.add_argument('--out')
    s = sub.add_parser('manifest')
    s.add_argument('--installer', required=True)
    s.add_argument('--out', required=True)
    s.add_argument('--version')
    s.add_argument('--notes-file', help='markdown notes (default: the CHANGELOG section)')
    s.add_argument('--published-at', help='YYYY-MM-DDTHH:MM:SSZ (default: now, UTC)')
    s = sub.add_parser('verify-manifest')
    s.add_argument('manifest')
    s.add_argument('--installer')
    s.add_argument('--version')
    s = sub.add_parser('public-key')
    s.add_argument('--format', choices=('pem', 'der', 'raw'), default='pem')
    s.add_argument('--key-file')
    s.add_argument('--out')
    a = p.parse_args(argv)
    try:
        if a.cmd == 'current':
            print(current_version())
        elif a.cmd == 'check':
            parse_semver(a.version)
            print(f'{a.version} is a valid version' + (' (prerelease)' if is_prerelease(a.version) else ''))
        elif a.cmd == 'prepare':
            return 1 if prepare(a.version, a.date, a.dry_run) else 0
        elif a.cmd == 'notes':
            notes = changelog_notes(read_text(ROOT / CHANGELOG), a.version)
            if a.out:
                Path(a.out).write_bytes(notes.encode('utf-8'))
            else:
                sys.stdout.write(notes)
        elif a.cmd == 'manifest':
            version = a.version or current_version()
            if a.notes_file:
                notes = Path(a.notes_file).read_bytes().decode('utf-8').replace('\r\n', '\n')
            else:
                notes = changelog_notes(read_text(ROOT / CHANGELOG), version)
            data = manifest_bytes(build_manifest(version, a.installer, notes, a.published_at))
            verify_manifest(data, a.installer, version)
            Path(a.out).write_bytes(data)
            print(f'Wrote {a.out} ({len(data)} bytes)')
        elif a.cmd == 'verify-manifest':
            m = verify_manifest(Path(a.manifest).read_bytes(), a.installer, a.version)
            checked = ', installer size and sha256 match' if a.installer else ''
            print(f'{a.manifest}: OK (version {m["version"]}, {m["installer"]}, {m["size"]} bytes{checked})')
        elif a.cmd == 'public-key':
            if a.format == 'der':
                data = public_key_der(key_file=a.key_file)
            elif a.format == 'raw':
                data = base64.b64encode(public_key_raw(key_file=a.key_file)) + b'\n'
            else:
                data = public_key_pem(key_file=a.key_file).encode('ascii')
            if a.out:
                Path(a.out).write_bytes(data)
            else:
                sys.stdout.buffer.write(data)
    except (ReleaseError, OSError, ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
