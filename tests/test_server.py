"""Standard-library tests. Run: python3 -B test-story-server.py

Run: python -m unittest discover -s tests   (from the repository root)

All test data lives in a temporary directory, never in a real story folder.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'src'))
from story_studio import server as module  # noqa: E402


class StudioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = module.make_server(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.initial = self.get('/api/bootstrap')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path):
        with urlopen(self.url + path) as response:
            return json.load(response)

    def post(self, project, revision=None, token=None, origin=None):
        headers = {'Content-Type': 'application/json', 'X-Story-Token': token or self.initial['token']}
        if origin:
            headers['Origin'] = origin
        request = Request(self.url + '/api/project', data=json.dumps({
            'project': project, 'revision': revision or self.initial['revision']}).encode(), headers=headers)
        with urlopen(request) as response:
            return json.load(response)

    def test_creates_project_and_resumes_it_without_overwriting(self):
        path = self.root / module.PROJECT_NAME
        self.assertTrue(path.is_file())
        project = self.initial['project']
        project['title'] = 'Persisted title'
        self.post(project)
        reopened = module.Studio(self.root)
        self.assertEqual(reopened.read_project()[0]['title'], 'Persisted title')
        self.assertEqual(self.get('/api/bootstrap')['project']['title'], 'Persisted title')

    def test_atomic_save_keeps_previous_version(self):
        previous = self.initial['project']
        project = json.loads(json.dumps(previous))
        project['title'] = 'Updated'
        result = self.post(project)
        self.assertNotEqual(result['revision'], self.initial['revision'])
        self.assertEqual(json.loads((self.root / module.PROJECT_NAME).read_text())['title'], 'Updated')
        self.assertEqual(json.loads((self.root / (module.PROJECT_NAME + '.bak')).read_text()), previous)
        self.assertFalse(list(self.root.glob('.story-save-*.tmp')))

    def test_discovers_additions_and_removals_without_scanning_subdirectories(self):
        self.assertEqual(self.get('/api/images')['images'], [])
        (self.root / 'a new image.PNG').write_bytes(b'image')
        nested = self.root / 'private'
        nested.mkdir()
        (nested / 'secret.png').write_bytes(b'not scanned')
        (self.root / 'archive.zip').write_bytes(b'not opened')
        try:
            (self.root / 'linked.png').symlink_to(nested / 'secret.png')
        except OSError:
            pass
        result = self.get('/api/images')
        self.assertEqual([image['file'] for image in result['images']], ['a new image.PNG'])
        (self.root / 'a new image.PNG').unlink()
        self.assertEqual(self.get('/api/images')['images'], [])

    def test_videos_are_discovered_saved_and_streamed_in_ranges(self):
        (self.root / 'clip.MP4').write_bytes(b'0123456789')
        (self.root / 'notes.txt').write_bytes(b'not media')
        images = self.get('/api/images')['images']
        self.assertEqual([(i['file'], i['category']) for i in images], [('clip.MP4', 'Videos')])
        with urlopen(self.url + '/clip.MP4') as response:
            self.assertEqual((response.status, response.headers['Content-Type'], response.headers['Accept-Ranges']),
                             (200, 'video/mp4', 'bytes'))
            self.assertEqual(response.read(), b'0123456789')
        for header, body, content_range in [('bytes=2-5', b'2345', 'bytes 2-5/10'), ('bytes=7-', b'789', 'bytes 7-9/10'),
                                            ('bytes=-3', b'789', 'bytes 7-9/10'), ('bytes=8-99', b'89', 'bytes 8-9/10')]:
            with urlopen(Request(self.url + '/clip.MP4', headers={'Range': header})) as response:
                self.assertEqual((response.status, response.headers['Content-Range'], response.read()), (206, content_range, body))
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + '/clip.MP4', headers={'Range': 'bytes=10-'}))
        self.assertEqual(error.exception.code, 416)
        project = self.initial['project']
        project['chapters'][0]['blocks'].append({'id': 'video-1', 'type': 'image', 'file': 'clip.MP4', 'caption': 'Moving'})
        self.post(project)
        self.assertEqual(self.get('/api/bootstrap')['project']['chapters'][0]['blocks'][-1]['file'], 'clip.MP4')

    def test_concurrent_and_external_changes_do_not_get_overwritten(self):
        project = self.initial['project']
        project['title'] = 'First writer'
        self.post(project)
        project['title'] = 'Stale second writer'
        with self.assertRaises(HTTPError) as error:
            self.post(project)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.get('/api/bootstrap')['project']['title'], 'First writer')

    def test_rejects_untrusted_writes_and_invalid_projects(self):
        for kwargs in [{'token': 'wrong'}, {'origin': 'https://example.com'}]:
            with self.assertRaises(HTTPError) as error:
                self.post(self.initial['project'], **kwargs)
            self.assertEqual(error.exception.code, 403)
        bad = self.initial['project']
        bad['chapters'][0]['blocks'].append({'id': 'unsafe-image', 'type': 'image', 'file': '../secret.png', 'caption': ''})
        with self.assertRaises(HTTPError) as error:
            self.post(bad)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.get('/api/bootstrap')['revision'], self.initial['revision'])

    def test_blocks_private_files_and_paths(self):
        for path in ['/story-project.json', '/story-project.json.bak', '/story-server.py', '/private/secret.png', '/%2e%2e%2fsecret.png']:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)
        request = Request(self.url + '/api/bootstrap', headers={'Host': 'evil.example'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_same_folder_from_windows_and_wsl_is_recognised(self):
        self.assertEqual(module.directory_key('/mnt/c/Users/Me/Story'), module.directory_key('C:\\Users\\me\\Story\\'))
        self.assertNotEqual(module.directory_key('/mnt/c/Users/Me/Story'), module.directory_key('C:\\Users\\Me\\Other'))
        info = self.get('/api/info')
        self.assertEqual((info['app'], info['version']), ('story-studio', module.SERVER_VERSION))

    def test_outline_and_disclaimer_are_saved_and_validated(self):
        project = self.initial['project']
        project.update(outline='Act one\n- the gate', disclaimer='For adult readers.', disclaimerGate=True)
        self.post(project)
        reopened = self.get('/api/bootstrap')
        self.assertEqual(reopened['version'], module.SERVER_VERSION)
        saved = reopened['project']
        self.assertEqual((saved['outline'], saved['disclaimer'], saved['disclaimerGate']),
                         ('Act one\n- the gate', 'For adult readers.', True))
        project['disclaimerGate'] = 'yes'
        with self.assertRaises(ValueError):
            module.validate_project(project)

    def test_picture_groups_are_saved_and_validated(self):
        project = self.initial['project']
        files = [image['file'] for image in self.initial['images']][:3] or ['a.png', 'b.png', 'c.png']
        group = {'id': 'group-1', 'type': 'gallery', 'layout': 'grid', 'size': 'wide',
                 'items': [{'file': f} for f in files], 'caption': 'One caption for all.'}
        project['chapters'][0]['blocks'].append(group)
        self.post(project)
        saved = self.get('/api/bootstrap')['project']['chapters'][0]['blocks'][-1]
        self.assertEqual(saved, group)
        for bad in ({'layout': 'circle'}, {'items': [{'file': files[0]}]}, {'items': [{'file': '../secret.png'}] * 2}):
            broken = json.loads(json.dumps(project))
            broken['chapters'][0]['blocks'][-1].update(bad)
            with self.assertRaises(ValueError):
                module.validate_project(broken)

    def test_image_size_is_kept_and_unknown_sizes_are_rejected(self):
        project = self.initial['project']
        image = {'id': 'sized-image', 'type': 'image', 'file': 'a.png', 'caption': '', 'size': 'wide'}
        project['chapters'][0]['blocks'].append(image)
        self.post(project)
        saved = json.loads((self.root / module.PROJECT_NAME).read_text())
        sizes = [b.get('size') for c in saved['chapters'] for b in c['blocks'] if b['id'] == image['id']]
        self.assertEqual(sizes, ['wide'])
        image['size'] = 'gigantic'
        with self.assertRaises(ValueError):
            module.validate_project(project)

    def test_new_story_starts_blank_and_editor_is_served_from_the_app(self):
        project = self.initial['project']
        self.assertEqual((project['title'], len(project['chapters']), project['chapters'][0]['blocks']),
                         ('Untitled story', 1, []))
        with urlopen(self.url + '/') as response:
            self.assertIn(b'editor.js', response.read())
        (self.root / 'editor.js').write_text('story folder copy must not be served')
        with urlopen(self.url + '/editor.js') as response:
            self.assertNotIn(b'story folder copy', response.read())

    def test_legacy_editor_data_in_story_folder_supplies_descriptions(self):
        (self.root / 'legacy.png').write_bytes(b'image')
        (self.root / 'editor-data.js').write_text('window.STORY_EDITOR_DATA = ' + json.dumps(
            {'images': [{'file': 'legacy.png', 'title': 'A described picture', 'category': 'Garden'}], 'seed': {}}) + ';')
        studio = module.Studio(self.root)
        self.assertEqual([(i['title'], i['category']) for i in studio.images()[0]], [('A described picture', 'Garden')])

    def test_corrupt_project_is_not_replaced_or_backed_up_as_good(self):
        path = self.root / module.PROJECT_NAME
        path.write_text('broken project')
        with self.assertRaises(ValueError):
            module.Studio(self.root)
        self.assertEqual(path.read_text(), 'broken project')
        self.assertFalse((self.root / (module.PROJECT_NAME + '.bak')).exists())


if __name__ == '__main__':
    unittest.main()
