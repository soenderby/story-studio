"""Shared test fixtures. Every test uses temporary folders and a temporary STORY_STUDIO_HOME."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.dont_write_bytecode = True
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
from story_studio import paths, server  # noqa: E402

REAL_WEB = SRC / 'story_studio' / 'web'
LOCAL = build_opener(ProxyHandler({}))
CLOSED_URL = 'http://127.0.0.1:1/'  # Nothing listens on port 1.


def make_web(folder):
    """Stand-in web assets, so these tests do not depend on the front-end files being built."""
    folder.mkdir(parents=True)
    (folder / 'home.html').write_text('<!doctype html><title>Stand-in home</title><script src="home.js"></script>')
    (folder / 'home.js').write_text('// stand-in home')
    (folder / 'editor.html').write_text('<!doctype html><title>Stand-in editor</title><script src="editor.js"></script>')
    (folder / 'editor.js').write_text('// stand-in editor')
    (folder / 'editor.css').write_text('/* stand-in */')
    (folder / 'home.css').write_text('/* stand-in home */')
    (folder / 'update.js').write_text('// stand-in update banner')
    (folder / 'icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    shutil.copy(REAL_WEB / 'editor-data.js', folder / 'editor-data.js')
    return folder


def legacy_js(data):
    return 'window.STORY_EDITOR_DATA = ' + json.dumps(data) + ';'


class TempHome(unittest.TestCase):
    """A temporary app-data folder (STORY_STUDIO_HOME), stand-in web assets and a folder for stories."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Resolved, as the server resolves story folders: CI's %TEMP% is an 8.3 short path (RUNNER~1).
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.stories = self.base / 'stories'
        self.stories.mkdir()
        self.web = make_web(self.base / 'web')
        # The update URL points at a closed local port, so no test ever contacts GitHub.
        patches = [mock.patch.dict(os.environ, {'STORY_STUDIO_HOME': str(self.home), 'STORY_STUDIO_UPDATE_URL': CLOSED_URL}),
                   mock.patch.object(paths, 'WEB_DIR', self.web)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def folder(self, name):
        path = self.stories / name
        path.mkdir()
        return path


class ServerTest(TempHome):
    """A running server on a free port, with a fake folder picker and revealer."""

    def setUp(self):
        super().setUp()
        self.picks = []
        self.revealed = []
        self.quits = []
        self.server = self.start_server()
        self.token = self.get('/api/stories')['token']

    def start_server(self):
        def picker(purpose):
            result = self.picks.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        instance = server.make_server(self.home, 0, picker=picker, revealer=self.revealed.append,
                                      on_quit=lambda: self.quits.append(True))
        thread = threading.Thread(target=instance.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()

        def stop():
            instance.shutdown()
            instance.server_close()
            thread.join()
        self.addCleanup(stop)
        self.url = f'http://127.0.0.1:{instance.server_port}'
        return instance

    def open(self, path, data=None, headers=None, method=None):
        """(status, headers, body bytes); HTTP errors are returned, not raised."""
        request = Request(self.url + path, data=data, headers=headers or {}, method=method)
        try:
            with LOCAL.open(request) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def get(self, path, status=200, **headers):
        code, _, body = self.open(path, headers=headers)
        self.assertEqual(code, status, body)
        return json.loads(body)

    def post(self, path, body=None, status=200, token=None, origin=None, host=None):
        headers = {'Content-Type': 'application/json', 'X-Story-Token': self.token if token is None else token}
        if origin:
            headers['Origin'] = origin
        if host:
            headers['Host'] = host
        data = b'' if body is None else json.dumps(body).encode()
        code, _, payload = self.open(path, data=data, headers=headers, method='POST')
        self.assertEqual(code, status, payload)
        return json.loads(payload)

    def add(self, folder, status=201, **body):
        return self.post('/api/stories', {'path': str(folder), **body}, status=status).get('story')

    def bootstrap(self, story):
        return self.get(f'/s/{story["id"]}/api/bootstrap')

    def save(self, story, project, revision, status=200):
        return self.post(f'/s/{story["id"]}/api/project', {'project': project, 'revision': revision}, status=status)
