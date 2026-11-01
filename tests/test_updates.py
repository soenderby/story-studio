"""The updater: Ed25519, manifest checks, semver, the schedule, download, verify and install.

Run: python -m unittest discover -s tests. Every release comes from a fake release server on an
OS-chosen port and is signed with a throwaway key; a fake launcher stands in for the installer.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

from helpers import CLOSED_URL, ServerTest, TempHome  # noqa: E402 (helpers puts src/ on sys.path)
from signing import TestKey, public_key, sign  # noqa: E402
from story_studio import ed25519, registry, story, updates  # noqa: E402
from story_studio.registry import Settings  # noqa: E402

sys.dont_write_bytecode = True
INSTALLER = b'MZ fake installer ' + bytes(range(256)) * 600  # ~150 KB, several download chunks.

# RFC 8032, section 7.1: (secret key, public key, message, signature), hex.
RFC8032 = [
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
    ('833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42',
     'ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf',
     'ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f',
     'dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704'),
]


class FakeRelease:
    """A release server on 127.0.0.1: serves `files` by name, counts requests, can hold a download midway."""

    def __init__(self, test):
        self.files, self.requests, self.redirects = {}, [], {}
        self.hold = None  # (name, Event): send half of that file, then wait for the event.
        self.cut = set()  # Names whose body stops halfway, with the full Content-Length announced.
        self.no_length = set()  # Names sent without Content-Length.
        release = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                name = self.path.lstrip('/')
                release.requests.append(name)
                if name in release.redirects:
                    self.send_response(302)
                    self.send_header('Location', release.redirects[name])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = release.files.get(name)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                if name not in release.no_length:
                    self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                half = len(body) // 2
                try:
                    if release.hold and release.hold[0] == name:
                        self.wfile.write(body[:half])
                        self.wfile.flush()
                        release.hold[1].wait(10)
                        self.wfile.write(body[half:])
                    elif name in release.cut:
                        self.wfile.write(body[:half])
                    else:
                        self.wfile.write(body)
                except ConnectionError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        test.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join()))
        self.url = f'http://127.0.0.1:{self.server.server_port}/'

    def publish(self, key, version='0.3.0', installer=INSTALLER, manifest=None, **fields):
        """A signed release. `manifest` (bytes) replaces the generated latest.json; `fields` override its values."""
        name = f'StoryStudio-Setup-{version}.exe'
        values = {'version': version, 'installer': name, 'sha256': hashlib.sha256(installer).hexdigest(),
                  'size': len(installer), 'notes': '## What is new\n- Faster saving', 'publishedAt': '2026-10-30T12:00:00Z'}
        values.update(fields)
        data = manifest if manifest is not None else json.dumps(values, indent=2).encode() + b'\n'
        self.files.update({'latest.json': data, 'latest.json.sig': key.signature(data).encode(), name: installer})
        return values


class FakeProcess:
    """The launched installer: wait() blocks until finish(code)."""

    def __init__(self):
        self.done = threading.Event()
        self.code = None

    def finish(self, code):
        self.code = code
        self.done.set()

    def wait(self):
        self.done.wait()
        return self.code


class FakeLauncher:
    def __init__(self):
        self.calls, self.processes = [], []
        self.launched = threading.Event()
        self.error = None

    def __call__(self, args):
        if self.error:
            raise self.error
        self.calls.append(args)
        self.processes.append(FakeProcess())
        self.launched.set()
        return self.processes[-1]


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def wait_until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for secret, public, message, signature in RFC8032:
            secret, public, message, signature = map(bytes.fromhex, (secret, public, message, signature))
            self.assertTrue(ed25519.verify(public, message, signature))
            self.assertEqual(public_key(secret), public)  # The test signer agrees with the RFC too.
            self.assertEqual(sign(secret, message), signature)

    def test_rejects_bad_signatures(self):
        _, public, message, signature = map(bytes.fromhex, RFC8032[2])
        self.assertFalse(ed25519.verify(public, message + b'!', signature))
        self.assertFalse(ed25519.verify(public, message[:1], signature))
        self.assertFalse(ed25519.verify(bytes.fromhex(RFC8032[1][1]), message, signature))  # Another key.
        for index in (0, 31, 32, 63):
            flipped = bytearray(signature)
            flipped[index] ^= 1
            self.assertFalse(ed25519.verify(public, message, bytes(flipped)), index)
        s = int.from_bytes(signature[32:], 'little')
        self.assertFalse(ed25519.verify(public, message, signature[:32] + (s + ed25519.L).to_bytes(32, 'little')))  # s >= L.
        self.assertFalse(ed25519.verify(public, message, signature[:63]))
        self.assertFalse(ed25519.verify(public[:31], message, signature))
        self.assertFalse(ed25519.verify(b'\xff' * 32, message, signature))  # y >= p: not a point.
        self.assertFalse(ed25519.verify(public, 'text', signature))

    def test_test_keys_round_trip(self):
        key = TestKey()
        data = b'{"version": "1.0.0"}\n'
        self.assertTrue(ed25519.verify(key.public, data, base64.b64decode(key.signature(data))))
        updates.verify_signature(data, key.signature(data).encode(), key.public)
        with self.assertRaises(updates.UpdateError):
            updates.verify_signature(data, TestKey().signature(data).encode(), key.public)


class SemverTest(unittest.TestCase):
    def test_precedence(self):
        ordered = ['0.9.9', '1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2',
                   '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '1.1.0', '1.10.0', '2.0.0']
        keys = [updates.version_key(version) for version in ordered]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(updates.version_key('1.2.3+build.5'), updates.version_key('1.2.3'))
        for bad in ['1.2', '1.2.3.4', '01.2.3', 'v1.2.3', '1.2.3-', '1.2.3-01', '', None, 3, '1.2.3 ']:
            with self.assertRaises(ValueError, msg=bad):
                updates.version_key(bad)

    def test_offered(self):
        cases = [('0.2.1', '0.2.0', True), ('0.10.0', '0.9.0', True), ('0.2.0', '0.2.0', False),
                 ('0.1.9', '0.2.0', False),  # Never a downgrade.
                 ('0.3.0-beta.1', '0.2.0', False),  # Stable installs ignore prereleases...
                 ('0.3.0-beta.2', '0.3.0-beta.1', True), ('0.3.0', '0.3.0-beta.1', True),  # ...prereleases do not.
                 ('0.4.0-alpha', '0.3.0-beta.1', True), ('0.3.0-alpha', '0.3.0-beta.1', False),
                 ('0.2.0+build.2', '0.2.0', False), ('garbage', '0.2.0', False), ('0.3.0', 'dev', False)]
        for latest, current, expected in cases:
            self.assertEqual(updates.offered(latest, current), expected, (latest, current))


class TlsTest(unittest.TestCase):
    def test_context_verifies_and_accepts_chains_ending_at_a_trusted_intermediate(self):
        context = updates.tls_context()
        self.assertEqual((context.verify_mode, context.check_hostname), (updates.ssl.CERT_REQUIRED, True))
        self.assertTrue(context.verify_flags & updates.ssl.VERIFY_X509_PARTIAL_CHAIN)

    def test_remote_and_local_openers_use_it(self):
        updater = updates.Updater(mock.Mock(path='/nonexistent/settings.json'), home='/nonexistent')
        for opener in (updater.remote, updater.local):
            https = [h for h in opener.handlers if isinstance(h, updates.HTTPSHandler)]
            self.assertEqual(len(https), 1)
            self.assertTrue(https[0]._context.verify_flags & updates.ssl.VERIFY_X509_PARTIAL_CHAIN)


class UpdaterTest(TempHome):
    def setUp(self):
        super().setUp()
        self.key = TestKey()
        self.release = FakeRelease(self)
        self.launcher = FakeLauncher()
        self.clock = Clock()
        self.settings = Settings(self.home)
        self.updater = self.make()
        self.addCleanup(self.finish)

    def make(self, **options):
        options = {'base_url': self.release.url, 'public_key': self.key.public, 'launcher': self.launcher,
                   'packaged': True, 'current': '0.2.0', 'clock': self.clock, **options}
        updater = updates.Updater(self.settings, **options)
        updater.STILL_RUNNING = 0.2
        return updater

    def finish(self):
        self.updater.stop()
        for process in self.launcher.processes:
            process.finish(0)
        if self.release.hold:
            self.release.hold[1].set()
        if self.updater.worker:
            self.updater.worker.join(5)

    def check_fails(self, message):
        state = self.updater.check()
        self.assertEqual((state['available'], state['latest'], state['state']), (False, None, 'error'))
        self.assertIn(message, state['error'])
        return state

    def install(self):
        """Install and wait until the worker is done with download and launch."""
        state = self.updater.install()
        self.assertEqual(state['state'], 'downloading')
        self.assertTrue(wait_until(lambda: self.updater.state()['state'] in ('installing', 'error')))
        return self.updater.state()

    def updates_folder(self):
        folder = self.home / 'updates'
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    # ---------- Check ----------

    def test_check_finds_a_signed_newer_release(self):
        self.release.publish(self.key, '0.3.0')
        self.assertEqual(self.updater.state()['lastCheck'], None)
        state = self.updater.check()
        self.assertEqual(state, {'current': '0.2.0', 'latest': '0.3.0', 'available': True, 'skipped': False,
                                 'notes': '## What is new\n- Faster saving', 'publishedAt': '2026-10-30T12:00:00Z',
                                 'state': 'idle', 'progress': 0, 'error': None, 'lastCheck': '2027-01-15T08:00:00.000Z'})
        self.assertEqual(Settings(self.home).get('lastUpdateCheck'), '2027-01-15T08:00:00.000Z')  # Persisted.
        self.assertEqual(self.release.requests, ['latest.json', 'latest.json.sig'])

    def test_same_or_older_release_is_not_offered(self):
        for version in ['0.2.0', '0.1.5']:
            self.release.publish(self.key, version)
            state = self.updater.check()
            self.assertEqual((state['latest'], state['available'], state['state']), (version, False, 'idle'))

    def test_prereleases_only_for_prereleases(self):
        self.release.publish(self.key, '0.3.0-beta.1')
        state = self.updater.check()
        self.assertEqual((state['latest'], state['available'], state['state']), (None, False, 'idle'))
        self.updater = self.make(current='0.3.0-alpha.2')
        self.assertEqual((self.updater.check()['latest'], self.updater.state()['available']), ('0.3.0-beta.1', True))

    def test_tampered_manifest_is_refused(self):
        self.release.publish(self.key, '0.3.0')
        self.release.files['latest.json'] = self.release.files['latest.json'].replace(b'0.3.0', b'0.3.1')
        self.check_fails('not signed by the Story Studio release key')

    def test_signature_by_another_key_is_refused(self):
        self.release.publish(TestKey(), '0.3.0')
        self.check_fails('not signed')

    def test_missing_or_malformed_signature_is_refused(self):
        self.release.publish(self.key, '0.3.0')
        for signature in [b'not base64!', b'', base64.b64encode(b'\0' * 64), base64.b64encode(b'short')]:
            self.release.files['latest.json.sig'] = signature
            self.check_fails('not signed')
        self.release.files['latest.json.sig'] = b'A' * 2000
        self.check_fails('latest.json.sig is too large')
        del self.release.files['latest.json.sig']
        self.check_fails('HTTP 404')

    def test_oversized_manifest_is_refused(self):
        self.release.publish(self.key, '0.3.0', notes='x' * 70_000)  # Signed, but over 64 KiB.
        self.check_fails('latest.json is too large')

    def test_malformed_manifests_are_refused_even_when_signed(self):
        good = {'version': '0.3.0', 'installer': 'StoryStudio-Setup-0.3.0.exe', 'sha256': 'a' * 64, 'size': 10,
                'notes': '', 'publishedAt': '2026-10-30T12:00:00Z'}
        bad = [b'{"version": "0.3.0"', b'[]', b'"0.3.0"', '{"version": "0.3.0"}'.encode('utf-16'),
               b'{"version": "0.3.0", "version": "0.4.0"}', json.dumps({**good, 'size': 'NaN'}).replace('"NaN"', 'NaN').encode()]
        for change in [{'version': 3}, {'version': '0.3'}, {'version': 'v0.3.0'}, {'installer': '../evil.exe'},
                       {'installer': 'C:\\x.exe'}, {'installer': 'setup.msi'}, {'installer': None},
                       {'sha256': 'A' * 64}, {'sha256': 'a' * 63}, {'size': True}, {'size': 0}, {'size': 1.5},
                       {'size': 2 ** 40}, {'notes': None}, {'publishedAt': 'yesterday'}, {'publishedAt': None}]:
            bad.append(json.dumps({**good, **change}).encode())
        for field in good:
            bad.append(json.dumps({key: value for key, value in good.items() if key != field}).encode())
        for manifest in bad:
            self.release.publish(self.key, manifest=manifest)
            with self.subTest(manifest=manifest[:80]):
                self.check_fails('malformed')
        self.release.publish(self.key, manifest=json.dumps({**good, 'futureField': [1]}).encode())  # Extra fields are fine.
        self.assertEqual(self.updater.check()['state'], 'idle')

    def test_unreachable_server_gives_a_readable_error_and_recovers(self):
        self.release.publish(self.key, '0.3.0')
        self.assertTrue(self.updater.check()['available'])
        offline = self.make(base_url=CLOSED_URL)
        state = offline.check()
        self.assertEqual((state['available'], state['latest'], state['state']), (False, None, 'error'))
        self.assertIn('Could not reach the update server', state['error'])
        self.assertIsNotNone(state['lastCheck'])
        offline.base_url = self.release.url
        self.assertEqual((offline.check()['state'], offline.state()['error']), ('idle', None))

    def test_only_https_away_from_localhost(self):
        state = self.make(base_url='http://updates.example/').check()
        self.assertEqual(state['state'], 'error')
        self.assertIn('HTTPS', state['error'])
        self.release.publish(self.key, '0.3.0')
        self.release.redirects['latest.json'] = 'http://updates.example/latest.json'
        self.assertIn('HTTP 302', self.updater.check()['error'])

    def test_default_urls(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(updates.URL_VARIABLE)
            updater = updates.Updater(self.settings)
        self.assertEqual(updater.base_url, 'https://github.com/soenderby/story-studio/releases/latest/download/')
        self.assertEqual(updater.installer_url({'version': '1.2.0', 'installer': 'StoryStudio-Setup-1.2.0.exe'}),
                         'https://github.com/soenderby/story-studio/releases/download/v1.2.0/StoryStudio-Setup-1.2.0.exe')
        self.assertEqual(updater.public_key, base64.b64decode(updates.PUBLIC_KEY))
        self.assertEqual(len(updater.public_key), 32)
        self.assertEqual(updates.Updater(self.settings).base_url, CLOSED_URL)  # The environment override.
        self.assertEqual(self.updater.installer_url({'version': '1.2.0', 'installer': 'StoryStudio-Setup-1.2.0.exe'}),
                         self.release.url + 'StoryStudio-Setup-1.2.0.exe')

    def test_skip(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        state = self.updater.skip('0.3.0')
        self.assertEqual((state['skipped'], state['available']), (True, True))  # The banner hides; install still works.
        self.assertEqual(Settings(self.home).get('skippedVersion'), '0.3.0')
        self.release.publish(self.key, '0.3.1')
        self.assertEqual(self.updater.check()['skipped'], False)
        for bad in [None, 3, 'latest', '1.2', '1' * 70 + '.0.0']:
            with self.assertRaises(ValueError):
                self.updater.skip(bad)

    # ---------- Automatic checks ----------

    def test_schedule_follows_last_update_check(self):
        self.release.publish(self.key, '0.3.0')
        self.assertEqual(self.updater.next_check(), 0)  # Never checked.
        self.updater.check()
        self.assertEqual(self.updater.next_check(), 24 * 3600)
        self.clock.now += 23 * 3600
        self.assertEqual(self.updater.next_check(), 3600)
        self.assertEqual(self.make().next_check(), 3600)  # A restart does not check again.
        self.clock.now += 3600
        self.assertEqual(self.updater.next_check(), 0)
        self.clock.now -= 48 * 3600  # The clock went back past the last check.
        self.assertEqual(self.updater.next_check(), 0)
        self.settings.update(checkUpdates=False)
        self.assertIsNone(self.updater.next_check())

    def test_failed_check_is_retried_after_an_hour(self):
        self.updater.base_url = CLOSED_URL
        self.updater.check()
        self.assertEqual(self.updater.next_check(), 3600)
        self.updater.base_url = self.release.url
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        self.assertEqual(self.updater.next_check(), 24 * 3600)

    def test_background_thread_checks_when_due_and_stops(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.START_DELAY, self.updater.MIN_SLEEP, self.updater.MAX_SLEEP = 0.05, 0.05, 0.05
        self.updater.start()
        self.assertTrue(wait_until(lambda: self.updater.state()['available']))
        time.sleep(0.3)  # Several more passes of the schedule: not due again.
        self.assertEqual(self.release.requests, ['latest.json', 'latest.json.sig'])
        self.updater.stop()
        self.assertFalse(self.updater.thread.is_alive())

    def test_background_thread_respects_settings(self):
        self.release.publish(self.key, '0.3.0')
        for settings in [{'checkUpdates': False}, {'checkUpdates': True, 'lastUpdateCheck': '2027-01-15T07:00:00Z'}]:
            self.settings.update(**settings)
            updater = self.make()
            updater.START_DELAY = updater.MIN_SLEEP = updater.MAX_SLEEP = 0.02
            updater.start()
            time.sleep(0.2)
            updater.stop()
            self.assertEqual(self.release.requests, [], settings)

    # ---------- Install ----------

    def test_install_downloads_verifies_and_launches(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        gate = threading.Event()
        self.release.hold = ('StoryStudio-Setup-0.3.0.exe', gate)
        self.assertEqual(self.updater.install()['state'], 'downloading')
        self.assertTrue(wait_until(lambda: 0 < self.updater.state()['progress'] < 1))
        state = self.updater.state()
        self.assertEqual((state['state'], state['available']), ('downloading', True))
        self.assertIn('StoryStudio-Setup-0.3.0.exe.part', self.updates_folder())  # Written under a temporary name.
        with self.assertRaises(updates.Conflict):
            self.updater.install()  # One at a time.
        self.assertEqual(self.updater.check()['state'], 'downloading')  # A check meanwhile only reports.
        gate.set()
        self.assertTrue(self.launcher.launched.wait(5))
        state = self.updater.state()
        self.assertEqual((state['state'], state['progress'], state['error']), ('installing', 1, None))
        folder = self.home / 'updates'
        self.assertEqual(self.launcher.calls, [[str(folder / 'StoryStudio-Setup-0.3.0.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                               '/NORESTART', '/RELAUNCH', f'/LOG={folder / "install-0.3.0.log"}']])
        self.assertEqual(self.updates_folder(), ['StoryStudio-Setup-0.3.0.exe'])
        self.assertEqual((folder / 'StoryStudio-Setup-0.3.0.exe').read_bytes(), INSTALLER)
        with self.assertRaises(updates.Conflict):
            self.updater.install()
        self.updater.stop()  # The installer closes the app: no error is reported.
        self.launcher.processes[0].finish(0)
        time.sleep(0.4)
        self.assertEqual(self.updater.state()['state'], 'installing')

    def test_wrong_sha256_is_never_launched(self):
        self.release.publish(self.key, '0.3.0', sha256=hashlib.sha256(b'something else').hexdigest())
        self.updater.check()
        state = self.install()
        self.assertEqual((state['state'], state['available']), ('error', True))  # "Try again" stays possible.
        self.assertIn('does not match the signed checksum', state['error'])
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(self.updates_folder(), [])

    def test_tampered_installer_is_never_launched(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        tampered = bytearray(INSTALLER)
        tampered[1000] ^= 1
        self.release.files['StoryStudio-Setup-0.3.0.exe'] = bytes(tampered)
        self.assertIn('checksum', self.install()['error'])
        self.assertEqual((self.launcher.calls, self.updates_folder()), ([], []))

    def test_wrong_size_is_never_launched(self):
        for served in [INSTALLER + b'x', INSTALLER[:-1]]:
            self.release.publish(self.key, '0.3.0')
            self.updater.check()
            self.release.files['StoryStudio-Setup-0.3.0.exe'] = served
            state = self.install()
            self.assertEqual(state['state'], 'error')
            self.assertIn('wrong size', state['error'])
        self.assertEqual((self.launcher.calls, self.updates_folder()), ([], []))

    def test_size_is_enforced_without_content_length(self):
        name = 'StoryStudio-Setup-0.3.0.exe'
        self.release.no_length.add(name)  # The body then ends when the connection closes.
        for served, message in [(INSTALLER + b'x', 'larger than expected'), (INSTALLER[:-1], 'incomplete')]:
            self.release.publish(self.key, '0.3.0')
            self.updater.check()
            self.release.files[name] = served
            self.assertIn(message, self.install()['error'])
        self.assertEqual((self.launcher.calls, self.updates_folder()), ([], []))

    def test_interrupted_download_is_never_launched(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        self.release.cut.add('StoryStudio-Setup-0.3.0.exe')
        self.assertRegex(self.install()['error'], 'incomplete|download failed')
        self.assertEqual((self.launcher.calls, self.updates_folder()), ([], []))

    def test_quitting_mid_download_leaves_nothing_behind(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        gate = threading.Event()
        self.release.hold = ('StoryStudio-Setup-0.3.0.exe', gate)
        self.updater.install()
        self.assertTrue(wait_until(lambda: self.updater.state()['progress'] > 0))
        self.updater.stop()
        gate.set()
        self.updater.worker.join(5)
        self.assertEqual((self.launcher.calls, self.updates_folder()), ([], []))

    def test_failing_installer_is_reported_with_its_log(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        self.assertEqual(self.install()['state'], 'installing')
        self.launcher.processes[0].finish(4)
        self.assertTrue(wait_until(lambda: self.updater.state()['state'] == 'error'))
        state = self.updater.state()
        self.assertIn('exit code 4', state['error'])
        self.assertIn(str(self.home / 'updates' / 'install-0.3.0.log'), state['error'])
        self.assertTrue(state['available'])
        self.assertEqual(self.install()['state'], 'installing')  # Try again.
        self.assertEqual(len(self.launcher.calls), 2)

    def test_installer_that_leaves_the_app_running_is_reported(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        self.install()
        self.launcher.processes[0].finish(0)
        self.assertTrue(wait_until(lambda: self.updater.state()['state'] == 'error'))
        self.assertIn('not restarted', self.updater.state()['error'])

    def test_installer_that_cannot_start(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        self.launcher.error = OSError('blocked by policy')
        state = self.install()
        self.assertEqual(state['state'], 'error')
        self.assertIn('could not be started: blocked by policy', state['error'])

    def test_installer_waits_for_a_save_in_flight(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        lock = story.folder_lock(self.folder('Alpha'))
        lock.acquire()
        try:
            self.updater.install()
            time.sleep(0.5)
            self.assertEqual(self.launcher.calls, [])  # Downloaded and verified, waiting for the save.
            self.assertEqual(self.updater.state()['state'], 'verifying')
        finally:
            lock.release()
        self.assertTrue(self.launcher.launched.wait(5))
        self.assertEqual(self.updater.state()['state'], 'installing')
        self.assertFalse(lock.locked())  # Released again after the launch.

    def test_a_save_that_never_finishes_stops_the_install(self):
        self.release.publish(self.key, '0.3.0')
        self.updater.check()
        lock = story.folder_lock(self.folder('Beta'))
        with lock, mock.patch.object(updates, 'SAVE_WAIT', 0.2):
            state = self.install()
        self.assertEqual(state['state'], 'error')
        self.assertIn('still being saved', state['error'])
        self.assertEqual(self.launcher.calls, [])

    def test_install_refusals(self):
        with self.assertRaises(updates.Conflict):
            self.updater.install()  # Nothing checked yet.
        self.release.publish(self.key, '0.2.0')
        self.updater.check()
        with self.assertRaises(updates.Conflict):
            self.updater.install()  # Not newer.
        self.release.publish(self.key, '0.3.0')
        unpackaged = self.make(packaged=None)  # Tests never run as the packaged exe.
        unpackaged.check()
        with self.assertRaises(NotImplementedError) as refused:
            unpackaged.install()
        self.assertIn('Windows', str(refused.exception))
        self.assertEqual(self.release.requests.count('StoryStudio-Setup-0.3.0.exe'), 0)

    def test_launch_detached_starts_a_real_process_with_a_fresh_pyinstaller_environment(self):
        script = ('import os, sys; sys.exit(0 if os.environ.get("PYINSTALLER_RESET_ENVIRONMENT") == "1" '
                  'and "_PYI_APPLICATION_HOME_DIR" not in os.environ else 5)')
        with mock.patch.dict(os.environ, {'_PYI_APPLICATION_HOME_DIR': 'C:\\old'}):
            process = updates.launch_detached([sys.executable, '-c', script])
        self.assertEqual(process.wait(timeout=30), 0)

    def test_cleanup_keeps_only_the_newest_install_log(self):
        folder = self.home / 'updates'
        folder.mkdir()
        for name in ['StoryStudio-Setup-0.2.0.exe', 'StoryStudio-Setup-0.3.0.exe.part', 'stray.tmp']:
            (folder / name).write_bytes(b'x')
        for age, name in [(300, 'install-0.1.0.log'), (200, 'install-0.2.0.log'), (100, 'install-0.3.0.log')]:
            (folder / name).write_text('log')
            os.utime(folder / name, (time.time() - age, time.time() - age))
        self.updater.start()
        self.assertEqual(self.updates_folder(), ['install-0.3.0.log'])


class UpdateServerTest(ServerTest):
    """The endpoints with a real updater and a fake release."""

    def test_check_and_install_over_http(self):
        key, release, launcher = TestKey(), FakeRelease(self), FakeLauncher()
        release.publish(key, '99.0.0')
        self.server.updater = updates.Updater(self.server.settings, base_url=release.url, public_key=key.public,
                                              launcher=launcher, packaged=True)
        self.addCleanup(lambda: [process.finish(0) for process in launcher.processes])
        self.addCleanup(self.server.updater.stop)
        self.assertIn('No update', self.post('/api/update/install', status=409)['error'])
        checked = self.post('/api/update/check')
        self.assertEqual((checked['latest'], checked['available'], checked['state']), ('99.0.0', True, 'idle'))
        self.assertEqual(self.get('/api/update'), checked)
        self.assertEqual(registry.Settings(self.home).get('lastUpdateCheck'), checked['lastCheck'])
        self.assertEqual(self.post('/api/update/install')['state'], 'downloading')
        self.assertTrue(launcher.launched.wait(5))
        self.assertEqual(self.get('/api/update')['state'], 'installing')
        self.assertIn('already', self.post('/api/update/install', status=409)['error'])


if __name__ == '__main__':
    unittest.main()
