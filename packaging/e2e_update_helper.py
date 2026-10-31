"""Helpers for packaging/e2e-update.ps1: a throwaway signing key, a fake signed release, a test story.

    python e2e_update_helper.py keygen  <seed file> <update_key.py to rewrite>
    python e2e_update_helper.py publish <seed file> <installer> <release folder> <version>
    python e2e_update_helper.py story   <port> <story folder>      registers it, saves an edit, prints its id

The seed (the private key) is generated fresh for every run, lives only in the e2e work folder,
and is only ever trusted by builds made from a temporary copy of the source. Release manifests
are signed with tests/signing.py, the same test signer the unit tests use.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
from urllib.request import ProxyHandler, Request, build_opener
import zlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
from signing import TestKey  # noqa: E402

LOCAL = build_opener(ProxyHandler({}))


def keygen(seed_file, key_module):
    key = TestKey()
    Path(seed_file).write_text(key.seed.hex())
    module = Path(key_module)
    text = module.read_text(encoding='utf-8')
    start = text.index("PUBLIC_KEY = '")
    end = text.index("'", start + len("PUBLIC_KEY = '"))
    module.write_text(text[:start] + f"PUBLIC_KEY = '{key.public_base64}" + text[end:], encoding='utf-8')
    print(key.public_base64)


def publish(seed_file, installer, folder, version):
    """latest.json and latest.json.sig as the release pipeline publishes them, plus the installer."""
    key = TestKey(bytes.fromhex(Path(seed_file).read_text().strip()))
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = Path(installer).read_bytes()
    name = Path(installer).name
    shutil.copyfile(installer, folder / name)
    manifest = {'version': version, 'installer': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                'notes': f'## Story Studio {version}\n- End-to-end test release',
                'publishedAt': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}
    payload = (json.dumps(manifest, indent=2) + '\n').encode()
    (folder / 'latest.json').write_bytes(payload)
    (folder / 'latest.json.sig').write_text(key.signature(payload), encoding='ascii')
    print(json.dumps(manifest))


def png(width, height, rgb):
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    rows = b''.join(b'\0' + bytes(rgb) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def call(port, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Story-Token'] = token
    data = None if body is None else json.dumps(body).encode()
    with LOCAL.open(Request(f'http://127.0.0.1:{port}{path}', data=data, headers=headers), timeout=30) as response:
        return json.load(response)


def make_story(port, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'scene-one.png').write_bytes(png(64, 48, (200, 120, 40)))
    (folder / 'scene-two.png').write_bytes(png(48, 64, (40, 120, 200)))
    token = call(port, '/api/stories')['token']
    story = call(port, '/api/stories', {'path': str(folder), 'title': 'Alpha'}, token)['story']
    boot = call(port, f'/s/{story["id"]}/api/bootstrap')
    project = boot['project']
    project['chapters'][0]['blocks'] += [
        {'id': 'e2e-text', 'type': 'text', 'text': 'Alpha walked through the gate.'},
        {'id': 'e2e-image', 'type': 'image', 'file': 'scene-one.png', 'caption': 'The gate'}]
    saved = call(port, f'/s/{story["id"]}/api/project', {'project': project, 'revision': boot['revision']}, token)
    assert saved['revision'] != boot['revision'], 'the edit was not saved'
    print(story['id'])


if __name__ == '__main__':
    command, args = sys.argv[1], sys.argv[2:]
    {'keygen': keygen, 'publish': publish, 'story': make_story}[command](*args)
