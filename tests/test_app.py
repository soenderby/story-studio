"""Starting the app: single instance, /api/quit, port fallback, --story. Run: python -m unittest discover -s tests

Uses free ports picked by the OS, never 8765.
"""
import contextlib
import io
import json
import os
import socket
import sys
import threading
import unittest
from unittest import mock

from helpers import TempHome  # noqa: E402 (helpers puts src/ on sys.path)
from story_studio import app, desktop, registry, server  # noqa: E402

sys.dont_write_bytecode = True


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class AppTest(TempHome):
    def setUp(self):
        super().setUp()
        self.port, self.spare = free_port(), free_port()
        while self.spare == self.port:
            self.spare = free_port()
        (self.home / 'settings.json').write_text(json.dumps({'version': 1, 'port': self.port}))
        self.opened = []
        for patch in [mock.patch.object(desktop, 'open_browser', self.opened.append),
                      mock.patch.object(app, 'PORTS', [self.port, self.spare]),  # Never the real 8765–8775.
                      mock.patch.object(app, 'QUIT_WAIT', 3.0)]:
            patch.start()
            self.addCleanup(patch.stop)

    def start(self, **options):
        handle = app.start(**{'open_browser': False, **options})
        if handle:
            self.addCleanup(handle.shutdown)
        return handle

    def instance(self):
        return json.loads((self.home / 'instance.json').read_text())

    def wait_for(self, handle, seconds=5):
        waiter = threading.Thread(target=handle.wait, daemon=True)
        waiter.start()
        waiter.join(seconds)
        return not waiter.is_alive()

    def test_start_serves_records_the_instance_and_quits_on_request(self):
        handle = self.start()
        self.assertEqual(handle.url, f'http://127.0.0.1:{self.port}/')
        self.assertEqual(app.probe(self.port), {'app': 'story-studio', 'version': server.SERVER_VERSION,
                                                'appVersion': app.__version__})
        instance = self.instance()
        self.assertEqual((instance['port'], instance['api'], instance['appVersion'], instance['token']),
                         (self.port, server.SERVER_VERSION, app.__version__, handle.server.token))
        self.assertTrue((self.home / 'logs' / 'studio.log').is_file())
        with self.assertRaises(ValueError):
            app.request(self.port, '/api/quit', {}, 'not the token')
        # As the installer and phase 3 send it: empty body, no Origin, only the token.
        quit = app.Request(f'http://127.0.0.1:{self.port}/api/quit', data=b'', method='POST',
                           headers={'X-Story-Token': instance['token']})
        with app.LOCAL.open(quit, timeout=2) as response:
            self.assertEqual(json.load(response), {'ok': True})
        self.assertTrue(self.wait_for(handle))
        self.assertFalse((self.home / 'instance.json').exists())
        self.assertIsNone(app.probe(self.port))
        handle.shutdown()  # Safe to call again.
        self.assertEqual(self.opened, [])

    def test_second_start_opens_the_running_instance(self):
        handle = self.start()
        self.assertIsNone(self.start(open_browser=False))
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.start(open_browser=True))
        self.assertEqual(self.opened, [handle.url])
        folder = self.folder('Opened from the command line')
        self.assertIsNone(self.start(open_browser=True, story=str(folder)))
        sid = registry.story_id(folder.resolve())
        self.assertEqual(self.opened[-1], f'{handle.url}s/{sid}/')
        self.assertEqual([s['id'] for s in app.request(self.port, '/api/stories')['stories']], [sid])
        self.assertTrue(handle.server.studio(sid))

    def test_a_different_version_is_asked_to_quit_and_replaced(self):
        old = self.start()
        with mock.patch.object(app, '__version__', '9.9.9'):
            new = self.start()
        self.assertTrue(self.wait_for(old))
        self.assertEqual(new.url, old.url)
        self.assertEqual(self.instance()['token'], new.server.token)
        self.assertEqual(app.request(self.port, '/api/stories')['token'], new.server.token)
        old.shutdown()
        self.assertTrue((self.home / 'instance.json').exists())  # The old instance never removes the new one's file.

    def test_an_instance_that_cannot_be_stopped_is_left_alone(self):
        old = self.start()
        (self.home / 'instance.json').unlink()  # Without its token it cannot be asked to quit.
        with mock.patch.object(app, '__version__', '9.9.9'):
            new = self.start()
        self.assertEqual(new.url, f'http://127.0.0.1:{self.spare}/')
        self.assertEqual(app.probe(self.port)['app'], 'story-studio')
        # Only a temporary move: once the old instance is closed, the next start returns to the usual port.
        self.assertEqual(registry.Settings(self.home).get('port'), self.port)
        old.shutdown()
        new.shutdown()
        self.assertEqual(self.start().url, f'http://127.0.0.1:{self.port}/')

    def test_local_requests_ignore_proxy_settings(self):
        self.start()
        proxy = 'http://127.0.0.1:9'  # Nothing listens there; a request through it would fail.
        with mock.patch.dict(os.environ, {'http_proxy': proxy, 'HTTP_PROXY': proxy, 'no_proxy': '', 'NO_PROXY': ''}):
            self.assertEqual(app.probe(self.port)['app'], 'story-studio')

    def test_port_fallback_is_saved_in_settings(self):
        with socket.socket() as blocker:
            blocker.bind(('127.0.0.1', self.port))  # Bound but not listening: something else holds the port.
            handle = self.start()
            self.assertEqual(handle.url, f'http://127.0.0.1:{self.spare}/')
            self.assertEqual(registry.Settings(self.home).get('port'), self.spare)
            handle.shutdown()
            with self.assertRaises(OSError):
                self.start(port=self.port)  # An explicit --port has no fallback.
        self.assertEqual(self.start(port=self.port).url, f'http://127.0.0.1:{self.port}/')
        self.assertEqual(registry.Settings(self.home).get('port'), self.spare)  # --port is not saved.

    def test_story_option_registers_and_opens_the_story(self):
        folder = self.folder('Direct')
        handle = self.start(open_browser=True, story=str(folder))
        sid = registry.story_id(folder.resolve())
        self.assertEqual(self.opened, [f'{handle.url}s/{sid}/'])
        self.assertTrue((folder / 'story-project.json').is_file())
        self.assertEqual(handle.server.registry.get(sid)['title'], 'Direct')

    def test_bad_story_folder_does_not_leave_a_server_behind(self):
        with self.assertRaises(ValueError):
            self.start(story=str(self.base / 'nowhere'))
        damaged = self.folder('Damaged')
        (damaged / 'story-project.json').write_text('broken')
        with self.assertRaises(ValueError):
            self.start(story=str(damaged))
        self.assertFalse((self.home / 'instance.json').exists())
        self.assertIsNone(app.probe(self.port))
        self.assertEqual((damaged / 'story-project.json').read_text(), 'broken')

    def test_main_runs_until_quit(self):
        output = io.StringIO()
        runner = threading.Thread(target=lambda: app.main(['--no-browser']), daemon=True)
        with contextlib.redirect_stdout(output):
            runner.start()
            for _ in range(100):
                if app.probe(self.port, timeout=0.2):
                    break
                threading.Event().wait(0.05)
            app.request(self.port, '/api/quit', {}, self.instance()['token'])
            runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertIn(f'http://127.0.0.1:{self.port}/', output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            app.main(['--no-browser', '--story', str(self.base / 'nowhere')])
        self.assertEqual(exit.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
