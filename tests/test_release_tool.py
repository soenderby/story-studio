"""scripts/release_tool.py: versions, CHANGELOG rewriting, latest.json bytes, the public key."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('release_tool', ROOT / 'scripts' / 'release_tool.py')
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

CHANGELOG = """# Changelog

## Unreleased

- New thing.
- Another thing.

## 0.2.0 (2026-11-01)

- Old thing.
"""


class SemverTests(unittest.TestCase):
    def test_valid_and_invalid(self):
        for v in ('0.2.0', '1.10.0', '0.3.0-beta.1', '1.0.0-rc.1', '1.0.0-x-y.7'):
            rt.parse_semver(v)
        for v in ('', '1.2', 'v1.2.3', '01.2.3', '1.2.3-', '1.2.3-01', '1.2.3+build', '1.2.3 '):
            with self.assertRaises(rt.ReleaseError, msg=v):
                rt.parse_semver(v)

    def test_precedence(self):
        ordered = ['0.1.0', '0.2.0-alpha', '0.2.0-alpha.1', '0.2.0-alpha.beta', '0.2.0-beta',
                   '0.2.0-beta.2', '0.2.0-beta.11', '0.2.0-rc.1', '0.2.0', '0.2.1', '0.10.0', '1.0.0']
        self.assertEqual(sorted(reversed(ordered), key=rt.semver_key), ordered)
        self.assertTrue(rt.is_prerelease('0.3.0-beta.1'))
        self.assertFalse(rt.is_prerelease('0.3.0'))


class ChangelogTests(unittest.TestCase):
    def test_stable_release_moves_unreleased(self):
        out = rt.release_changelog(CHANGELOG, '0.3.0', '2026-12-01')
        self.assertEqual(out, """# Changelog

## Unreleased

## 0.3.0 (2026-12-01)

- New thing.
- Another thing.

## 0.2.0 (2026-11-01)

- Old thing.
""")
        self.assertEqual(rt.changelog_notes(out, '0.3.0'), '- New thing.\n- Another thing.\n')
        self.assertEqual(rt.changelog_notes(out, '0.2.0'), '- Old thing.\n')

    def test_prerelease_copies_unreleased(self):
        out = rt.release_changelog(CHANGELOG, '0.3.0-beta.1', '2026-12-01')
        self.assertIn('## Unreleased\n\n- New thing.\n- Another thing.\n\n## 0.3.0-beta.1 (2026-12-01)\n\n'
                      '- New thing.\n- Another thing.\n\n## 0.2.0', out)
        self.assertEqual(rt.changelog_notes(out, '0.3.0-beta.1'), '- New thing.\n- Another thing.\n')
        # The final release then collects the same entries.
        final = rt.release_changelog(out, '0.3.0', '2026-12-08')
        self.assertEqual(rt.changelog_notes(final, '0.3.0'), '- New thing.\n- Another thing.\n')
        self.assertNotIn('0.3.0-beta', rt.changelog_notes(final, '0.3.0'))

    def test_keeps_crlf(self):
        out = rt.release_changelog(CHANGELOG.replace('\n', '\r\n'), '0.3.0', '2026-12-01')
        self.assertNotIn('\n', out.replace('\r\n', ''))

    def test_errors(self):
        with self.assertRaisesRegex(rt.ReleaseError, 'no entries'):
            rt.release_changelog('# Changelog\n\n## Unreleased\n\n## 0.2.0 (x)\n\n- a\n', '0.3.0', 'd')
        with self.assertRaisesRegex(rt.ReleaseError, 'no "## Unreleased"'):
            rt.release_changelog('# Changelog\n\n## 0.2.0 (x)\n\n- a\n', '0.3.0', 'd')
        with self.assertRaisesRegex(rt.ReleaseError, 'already has'):
            rt.release_changelog(CHANGELOG, '0.2.0', 'd')
        with self.assertRaisesRegex(rt.ReleaseError, 'no "## 0.4.0"'):
            rt.changelog_notes(CHANGELOG, '0.4.0')
        # "## 0.2.0" must not match "## 0.2.0-beta.1".
        with self.assertRaises(rt.ReleaseError):
            rt.changelog_notes('## 0.2.0-beta.1 (x)\n\n- a\n', '0.2.0')


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / 'src' / 'story_studio').mkdir(parents=True)
        (self.root / rt.VERSION_FILE).write_text("# comment\n__version__ = '0.2.0'\n")
        (self.root / rt.CHANGELOG).write_text(CHANGELOG)

    def test_dry_run_writes_nothing(self):
        out = io.StringIO()
        self.assertEqual(rt.prepare('0.3.0', '2026-12-01', dry_run=True, root=self.root, out=out), [])
        self.assertIn("+__version__ = '0.3.0'", out.getvalue())
        self.assertIn('+## 0.3.0 (2026-12-01)', out.getvalue())
        self.assertEqual(rt.current_version(self.root), '0.2.0')
        self.assertEqual((self.root / rt.CHANGELOG).read_text(), CHANGELOG)

    def test_writes(self):
        self.assertEqual(rt.prepare('0.3.0', '2026-12-01', root=self.root, out=io.StringIO()), [])
        self.assertEqual(rt.current_version(self.root), '0.3.0')
        self.assertEqual((self.root / rt.VERSION_FILE).read_text(), "# comment\n__version__ = '0.3.0'\n")
        self.assertIn('## 0.3.0 (2026-12-01)', (self.root / rt.CHANGELOG).read_text())

    def test_refuses_lower_or_equal_or_invalid(self):
        for v, why in (('0.2.0', 'not greater'), ('0.2.0-rc.1', 'not greater'), ('0.1.9', 'not greater'),
                       ('v0.3.0', 'not a valid version')):
            out = io.StringIO()
            problems = rt.prepare(v, root=self.root, out=out)
            self.assertTrue(problems and why in problems[0], (v, problems))
            self.assertEqual(rt.current_version(self.root), '0.2.0')


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.exe = self.dir / 'StoryStudio-Setup-0.3.0.exe'
        self.exe.write_bytes(b'MZ' + bytes(range(256)) * 10)

    def test_bytes_and_verify(self):
        m = rt.build_manifest('0.3.0', self.exe, '- Café → done.\n', '2026-12-01T10:00:00Z')
        data = rt.manifest_bytes(m)
        self.assertFalse(data.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(data.endswith(b'}\n'))
        self.assertIn('Café → done'.encode('utf-8'), data)
        parsed = json.loads(data.decode('utf-8'))
        self.assertEqual(list(parsed), list(rt.MANIFEST_KEYS))
        self.assertEqual(parsed['installer'], 'StoryStudio-Setup-0.3.0.exe')
        self.assertEqual(parsed['size'], 2 + 2560)
        self.assertEqual(len(parsed['sha256']), 64)
        self.assertEqual(rt.verify_manifest(data, self.exe, '0.3.0')['version'], '0.3.0')

    def test_verify_rejects(self):
        m = rt.build_manifest('0.3.0', self.exe, '- x\n', '2026-12-01T10:00:00Z')
        good = rt.manifest_bytes(m)
        bad = {
            'BOM': b'\xef\xbb\xbf' + good,
            'key order': json.dumps(dict(reversed(list(m.items()))), indent=2).encode() + b'\n',
            'not canonical': json.dumps(m).encode(),
            'size as string': rt.manifest_bytes(dict(m, size=str(m['size']))),
            'local time': rt.manifest_bytes(dict(m, publishedAt='2026-12-01T10:00:00+01:00')),
            'uppercase hex': rt.manifest_bytes(dict(m, sha256=m['sha256'].upper())),
            'notes too long for the app': rt.manifest_bytes(dict(m, notes='x' * (rt.MAX_NOTES + 1))),
            'manifest too large for the app': rt.manifest_bytes(dict(m, notes='é' * 40000)),
            'installer too large for the app': rt.manifest_bytes(dict(m, size=rt.MAX_INSTALLER + 1)),
            'size zero': rt.manifest_bytes(dict(m, size=0)),
        }
        for why, data in bad.items():
            with self.assertRaises(rt.ReleaseError, msg=why):
                rt.verify_manifest(data)
        with self.assertRaisesRegex(rt.ReleaseError, 'sha256'):
            rt.verify_manifest(rt.manifest_bytes(dict(m, sha256='0' * 64)), self.exe)
        with self.assertRaisesRegex(rt.ReleaseError, 'size'):
            rt.verify_manifest(rt.manifest_bytes(dict(m, size=1)), self.exe)
        with self.assertRaisesRegex(rt.ReleaseError, 'expected'):
            rt.verify_manifest(good, version='0.3.1')
        with self.assertRaisesRegex(rt.ReleaseError, 'named'):
            rt.build_manifest('0.3.1', self.exe, '- x\n')


class PublicKeyTests(unittest.TestCase):
    def test_der_and_pem_from_update_key(self):
        raw = rt.public_key_raw()
        self.assertEqual(len(raw), 32)
        der = rt.public_key_der()
        self.assertEqual(der, bytes.fromhex('302a300506032b6570032100') + raw)
        pem = rt.public_key_pem()
        self.assertTrue(pem.startswith('-----BEGIN PUBLIC KEY-----\n'))
        self.assertEqual(base64.b64decode(''.join(pem.splitlines()[1:-1])), der)

    @unittest.skipUnless(shutil.which('openssl'), 'openssl not on PATH')
    def test_openssl_sign_and_verify_roundtrip(self):
        """The workflow's signing commands, with a throwaway key in a temp folder."""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)

        def ssl(*args):
            return subprocess.run(['openssl', *args], cwd=d, capture_output=True, text=True)

        if ssl('genpkey', '-algorithm', 'ed25519', '-out', 'test-key.pem').returncode:
            self.skipTest('openssl without Ed25519')
        self.assertEqual(ssl('pkey', '-in', 'test-key.pem', '-pubout', '-outform', 'DER', '-out', 'pub.der')
                         .returncode, 0)
        raw = (d / 'pub.der').read_bytes()[-32:]
        key_dir = d / 'src' / 'story_studio'
        key_dir.mkdir(parents=True)
        (key_dir / 'update_key.py').write_text(f"PUBLIC_KEY = '{base64.b64encode(raw).decode()}'\n")
        self.assertEqual(rt.public_key_der(d), (d / 'pub.der').read_bytes())
        (d / 'pub.pem').write_text(rt.public_key_pem(d))
        (d / 'latest.json').write_bytes(b'{"version": "0.3.0"}\n')
        r = ssl('pkeyutl', '-sign', '-inkey', 'test-key.pem', '-rawin', '-in', 'latest.json', '-out', 'sig.bin')
        if r.returncode and '-rawin' in r.stderr:
            self.skipTest('openssl without pkeyutl -rawin')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len((d / 'sig.bin').read_bytes()), 64)
        verify = ('pkeyutl', '-verify', '-pubin', '-inkey', 'pub.pem', '-rawin', '-in', 'latest.json',
                  '-sigfile', 'sig.bin')
        self.assertEqual(ssl(*verify).returncode, 0)
        (d / 'latest.json').write_bytes(b'{"version": "0.3.1"}\n')
        self.assertNotEqual(ssl(*verify).returncode, 0)


if __name__ == '__main__':
    unittest.main()
