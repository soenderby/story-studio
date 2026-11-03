"""HTTP API tests against a server on a free port. Run: python -m unittest discover -s tests

All test data lives in temporary directories, never in a real story folder.
"""
import http.client
import json
import os
import sys
import time
import unittest

from helpers import ServerTest, legacy_js  # noqa: E402 (helpers puts src/ on sys.path)
from story_studio import desktop, pdf, registry, server as module, story  # noqa: E402
from story_studio.version import __version__  # noqa: E402

sys.dont_write_bytecode = True


class OneStoryTest(ServerTest):
    """The single-story behaviour of earlier versions, now under /s/<id>/."""

    def setUp(self):
        super().setUp()
        self.root = self.folder('Story')
        self.story = self.add(self.root)
        self.prefix = f'/s/{self.story["id"]}'
        self.initial = self.bootstrap(self.story)

    def post_project(self, project, revision=None, status=200, **kwargs):
        return self.post(self.prefix + '/api/project', {'project': project, 'revision': revision or self.initial['revision']},
                         status=status, **kwargs)

    def test_bootstrap_reports_story_and_versions(self):
        self.assertEqual((self.initial['storyId'], self.initial['appVersion'], self.initial['version']),
                         (self.story['id'], __version__, module.SERVER_VERSION))
        self.assertEqual(self.initial['token'], self.token)
        self.assertEqual(self.initial['projectFile'], story.PROJECT_NAME)

    def test_creates_project_and_resumes_it_without_overwriting(self):
        self.assertTrue((self.root / story.PROJECT_NAME).is_file())
        project = self.initial['project']
        project['title'] = 'Persisted title'
        self.post_project(project)
        self.assertEqual(story.Studio(self.root).read_project()[0]['title'], 'Persisted title')
        self.assertEqual(self.bootstrap(self.story)['project']['title'], 'Persisted title')
        self.assertEqual(self.get('/api/stories')['stories'][0]['title'], 'Persisted title')

    def test_atomic_save_keeps_previous_version(self):
        previous = self.initial['project']
        project = json.loads(json.dumps(previous))
        project['title'] = 'Updated'
        result = self.post_project(project)
        self.assertNotEqual(result['revision'], self.initial['revision'])
        self.assertEqual(json.loads((self.root / story.PROJECT_NAME).read_text())['title'], 'Updated')
        self.assertEqual(json.loads((self.root / (story.PROJECT_NAME + '.bak')).read_text()), previous)
        self.assertFalse(list(self.root.glob('.story-save-*.tmp')))

    def test_discovers_additions_and_removals_without_scanning_subdirectories(self):
        images = self.prefix + '/api/images'
        self.assertEqual(self.get(images)['images'], [])
        (self.root / 'a new image.PNG').write_bytes(b'image')
        nested = self.root / 'private'
        nested.mkdir()
        (nested / 'secret.png').write_bytes(b'not scanned')
        (self.root / 'archive.zip').write_bytes(b'not opened')
        try:
            (self.root / 'linked.png').symlink_to(nested / 'secret.png')
        except OSError:
            pass
        self.assertEqual([image['file'] for image in self.get(images)['images']], ['a new image.PNG'])
        self.assertEqual(self.open(self.prefix + '/linked.png')[0], 404)
        (self.root / 'a new image.PNG').unlink()
        self.assertEqual(self.get(images)['images'], [])

    def test_videos_are_discovered_saved_and_streamed_in_ranges(self):
        (self.root / 'clip.MP4').write_bytes(b'0123456789')
        (self.root / 'notes.txt').write_bytes(b'not media')
        images = self.get(self.prefix + '/api/images')['images']
        self.assertEqual([(i['file'], i['category']) for i in images], [('clip.MP4', 'Videos')])
        status, headers, body = self.open(self.prefix + '/clip.MP4')
        self.assertEqual((status, headers['Content-Type'], headers['Accept-Ranges'], body), (200, 'video/mp4', 'bytes', b'0123456789'))
        for header, expected, content_range in [('bytes=2-5', b'2345', 'bytes 2-5/10'), ('bytes=7-', b'789', 'bytes 7-9/10'),
                                                ('bytes=-3', b'789', 'bytes 7-9/10'), ('bytes=8-99', b'89', 'bytes 8-9/10')]:
            status, headers, body = self.open(self.prefix + '/clip.MP4', headers={'Range': header})
            self.assertEqual((status, headers['Content-Range'], body), (206, content_range, expected))
        status, headers, _ = self.open(self.prefix + '/clip.MP4', headers={'Range': 'bytes=10-'})
        self.assertEqual((status, headers['Content-Range']), (416, 'bytes */10'))
        _, headers, _ = self.open(self.prefix + '/clip.MP4?v=1-10')
        self.assertIn('immutable', headers['Cache-Control'])
        status, _, body = self.open(self.prefix + '/clip.MP4', method='HEAD')
        self.assertEqual((status, body), (200, b''))
        project = self.initial['project']
        project['chapters'][0]['blocks'].append({'id': 'video-1', 'type': 'image', 'file': 'clip.MP4', 'caption': 'Moving'})
        self.post_project(project)
        self.assertEqual(self.bootstrap(self.story)['project']['chapters'][0]['blocks'][-1]['file'], 'clip.MP4')

    def test_concurrent_and_external_changes_do_not_get_overwritten(self):
        project = self.initial['project']
        project['title'] = 'First writer'
        self.post_project(project)
        project['title'] = 'Stale second writer'
        self.post_project(project, status=409)
        self.assertEqual(self.bootstrap(self.story)['project']['title'], 'First writer')

    def test_rejects_untrusted_writes_and_invalid_projects(self):
        for kwargs in [{'token': 'wrong'}, {'token': ''}, {'token': 'wrøng'}, {'origin': 'https://example.com'},
                       {'origin': 'http://localhost:1'}, {'host': 'evil.example'}]:
            self.post_project(self.initial['project'], status=403, **kwargs)
        bad = self.initial['project']
        bad['chapters'][0]['blocks'].append({'id': 'unsafe-image', 'type': 'image', 'file': '../secret.png', 'caption': ''})
        self.post_project(bad, status=400)
        self.post(self.prefix + '/api/project', ['not', 'an', 'object'], status=400)
        status, _, _ = self.open(self.prefix + '/api/project', data=b'{', method='POST',
                                 headers={'X-Story-Token': self.token, 'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertEqual(self.bootstrap(self.story)['revision'], self.initial['revision'])

    def test_same_origin_writes_are_accepted(self):
        self.post_project(self.initial['project'], origin=self.url)
        self.post_project(self.initial['project'], origin=self.url.replace('127.0.0.1', 'localhost'),
                          host=self.url.replace('http://127.0.0.1', 'localhost'))

    def test_blocks_private_files_and_paths(self):
        (self.root / 'editor-data.js').write_text('story folder copy')
        for path in ['/story-project.json', '/story-project.json.bak', '/story-server.py', '/private/secret.png',
                     '/%2e%2e%2fsecret.png', '/..%2fstory-project.json', '/%2e%2e/web/editor.js', '/api/../story-project.json']:
            status, _, body = self.open(self.prefix + path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b'story folder copy', body)
        self.assertNotIn(b'story folder copy', self.open(self.prefix + '/editor-data.js')[2])
        for path in ['/story-project.json', '/../stories/Story/story-project.json', '/s/../story-project.json']:
            self.assertEqual(self.open(path)[0], 404, path)
        self.get(self.prefix + '/api/bootstrap', status=403, Host='evil.example')
        self.get('/api/stories', status=403, Host='evil.example')

    def test_outline_and_disclaimer_are_saved_and_validated(self):
        project = self.initial['project']
        project.update(outline='Act one\n- the gate', disclaimer='For adult readers.', disclaimerGate=True)
        self.post_project(project)
        saved = self.bootstrap(self.story)['project']
        self.assertEqual((saved['outline'], saved['disclaimer'], saved['disclaimerGate']),
                         ('Act one\n- the gate', 'For adult readers.', True))
        project['disclaimerGate'] = 'yes'
        with self.assertRaises(ValueError):
            story.validate_project(project)

    def test_picture_groups_are_saved_and_validated(self):
        project = self.initial['project']
        files = ['a.png', 'b.png', 'c.png']
        group = {'id': 'group-1', 'type': 'gallery', 'layout': 'grid', 'size': 'wide',
                 'items': [{'file': f} for f in files], 'caption': 'One caption for all.'}
        project['chapters'][0]['blocks'].append(group)
        self.post_project(project)
        self.assertEqual(self.bootstrap(self.story)['project']['chapters'][0]['blocks'][-1], group)
        for bad in ({'layout': 'circle'}, {'items': [{'file': files[0]}]}, {'items': [{'file': '../secret.png'}] * 2}):
            broken = json.loads(json.dumps(project))
            broken['chapters'][0]['blocks'][-1].update(bad)
            with self.assertRaises(ValueError):
                story.validate_project(broken)

    def test_image_size_is_kept_and_unknown_sizes_are_rejected(self):
        project = self.initial['project']
        image = {'id': 'sized-image', 'type': 'image', 'file': 'a.png', 'caption': '', 'size': 'wide'}
        project['chapters'][0]['blocks'].append(image)
        self.post_project(project)
        saved = json.loads((self.root / story.PROJECT_NAME).read_text())
        self.assertEqual([b.get('size') for c in saved['chapters'] for b in c['blocks'] if b['id'] == image['id']], ['wide'])
        image['size'] = 'gigantic'
        with self.assertRaises(ValueError):
            story.validate_project(project)

    def test_editor_and_assets_come_from_the_app_not_the_story_folder(self):
        (self.root / 'editor.js').write_text('story folder copy must not be served')
        status, headers, body = self.open(self.prefix + '/')
        self.assertEqual((status, b'Stand-in editor' in body), (200, True))
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        status, headers, body = self.open(self.prefix + '/editor.js')
        self.assertEqual((status, body, headers['Content-Type']), (200, b'// stand-in editor', 'text/javascript; charset=utf-8'))
        self.assertIn(b'Untitled story', self.open(self.prefix + '/editor-data.js')[2])
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request('GET', self.prefix)
        response = connection.getresponse()
        self.assertEqual((response.status, response.headers['Location']), (301, self.prefix + '/'))
        connection.close()

    def test_cover_and_counts_in_the_story_list(self):
        for name in ['b.png', 'a.png', 'clip.mp4']:
            (self.root / name).write_bytes(b'media')
        listed = self.get('/api/stories')['stories'][0]
        self.assertEqual((listed['cover'], listed['chapters'], listed['media'], listed['missing']), (None, 1, 3, False))
        project = self.initial['project']
        project['chapters'][0]['blocks'] = [{'id': 'gone', 'type': 'image', 'file': 'deleted.png', 'caption': ''},
                                           {'id': 'g', 'type': 'gallery', 'layout': 'row',
                                            'items': [{'file': 'b.png'}, {'file': 'a.png'}], 'caption': ''}]
        project['chapters'].append({'id': 'chapter-2', 'title': 'Two', 'notes': '', 'blocks': []})
        self.post_project(project)
        listed = self.get('/api/stories')['stories'][0]
        self.assertEqual((listed['cover'], listed['chapters']), ('b.png', 2))
        self.assertEqual(self.open(f'/s/{listed["id"]}/{listed["cover"]}')[2], b'media')


class StoryListTest(ServerTest):
    def test_info_and_empty_list(self):
        self.assertEqual(self.get('/api/info'), {'app': 'story-studio', 'version': 9, 'appVersion': __version__})
        listed = self.get('/api/stories')
        self.assertEqual((listed['stories'], listed['token']), ([], self.server.token))
        status, _, body = self.open('/')
        self.assertEqual((status, b'Stand-in home' in body), (200, True))
        for name in ['home.js', 'icon.svg']:
            self.assertEqual(self.open('/' + name)[0], 200, name)
        for name in ['/missing.js', '/helpers.py', '/editor-data.json', '/../web/home.js']:
            self.assertEqual(self.open(name)[0], 404, name)

    def test_web_assets_are_served_at_the_root_and_under_any_story_id(self):
        known = self.add(self.folder('Known'))['id']
        names = ['home.html', 'home.js', 'home.css', 'update.js', 'editor.html', 'editor.js', 'editor.css',
                 'editor-data.js', 'icon.svg']
        for prefix in ['', f'/s/{known}', '/s/s0000000000000000']:  # Unknown ids too: the editor explains.
            for name in names:
                status, headers, body = self.open(f'{prefix}/{name}')
                self.assertEqual((status, body), (200, (self.web / name).read_bytes()), (prefix, name))
            status, _, body = self.open(prefix + '/')
            self.assertEqual(body, (self.web / ('home.html' if not prefix else 'editor.html')).read_bytes())
        self.assertEqual(self.open('/s/s0000000000000000/api/bootstrap')[0], 404)

    def test_adding_a_new_folder_creates_a_blank_project_once(self):
        folder = self.folder('Fresh')
        added = self.add(folder, title='  A brand new tale ')
        self.assertEqual((added['title'], added['chapters'], added['media'], added['missing']), ('A brand new tale', 1, 0, False))
        self.assertEqual(added['id'], registry.story_id(folder))
        project = json.loads((folder / story.PROJECT_NAME).read_text())
        self.assertEqual((project['title'], project['chapters'][0]['blocks']), ('A brand new tale', []))
        again = self.add(self.stories / 'Fresh' / '.', status=200)
        self.assertEqual(again['id'], added['id'])
        self.assertEqual(len(self.get('/api/stories')['stories']), 1)
        self.assertEqual(self.add(self.folder('Named after folder'))['title'], 'Named after folder')

    def test_adding_refuses_what_is_not_a_folder(self):
        (self.stories / 'file.txt').write_text('x')
        for path in [str(self.stories / 'file.txt'), str(self.stories / 'nowhere'), 'relative/folder', '', 42, None]:
            self.post('/api/stories', {'path': path}, status=400)
        self.post('/api/stories', {'path': str(self.folder('Fine')), 'title': 7}, status=400)
        self.assertEqual(self.get('/api/stories')['stories'], [])
        self.assertFalse((self.stories / 'Fine' / story.PROJECT_NAME).exists())

    def test_adding_a_corrupt_project_reports_it_and_leaves_it_alone(self):
        folder = self.folder('Damaged')
        (folder / story.PROJECT_NAME).write_text('{"broken": ')
        self.assertIn('error', self.post('/api/stories', {'path': str(folder)}, status=400))
        self.assertEqual((folder / story.PROJECT_NAME).read_text(), '{"broken": ')
        self.assertEqual(self.get('/api/stories')['stories'], [])

    def test_existing_story_folder_is_registered_as_is(self):
        folder = self.folder('Existing')
        project = json.loads(story.encode(story.BLANK_PROJECT))
        project.update(title='Written elsewhere', id='kept-id')
        (folder / story.PROJECT_NAME).write_bytes(story.encode(project))
        added = self.add(folder, title='Ignored')
        self.assertEqual(added['title'], 'Written elsewhere')
        self.assertEqual(self.bootstrap(added)['project']['id'], 'kept-id')
        self.assertFalse((folder / story.PRE_MIGRATION_NAME).exists())

    def test_list_is_sorted_by_last_opened(self):
        a = self.add(self.folder('A'))
        b = self.add(self.folder('B'))
        self.bootstrap(a)
        self.bootstrap(b)
        self.assertEqual([s['title'] for s in self.get('/api/stories')['stories']], ['B', 'A'])
        self.open(f'/s/{a["id"]}/')
        stories = self.get('/api/stories')['stories']
        self.assertEqual([s['title'] for s in stories], ['A', 'B'])
        self.assertIsNotNone(stories[0]['lastOpened'])

    def test_remove_unregisters_but_keeps_files(self):
        folder = self.folder('Keep me')
        added = self.add(folder)
        (folder / 'pic.png').write_bytes(b'image')
        self.assertEqual(self.post(f'/api/stories/{added["id"]}/remove'), {'ok': True})
        self.assertEqual(self.get('/api/stories')['stories'], [])
        self.assertTrue((folder / story.PROJECT_NAME).is_file() and (folder / 'pic.png').is_file())
        self.get(f'/s/{added["id"]}/api/bootstrap', status=404)
        self.assertEqual(self.open(f'/s/{added["id"]}/pic.png')[0], 404)
        self.post(f'/api/stories/{added["id"]}/remove', status=404)
        self.assertEqual(self.add(folder)['id'], added['id'])

    def test_moved_folder_shows_missing_until_relocated(self):
        folder = self.folder('Wanderer')
        added = self.add(folder, title='The wanderer')
        (folder / 'pic.png').write_bytes(b'image')
        moved = self.stories / 'Moved'
        folder.rename(moved)
        listed = self.get('/api/stories')['stories'][0]
        self.assertEqual((listed['missing'], listed['title'], listed['media'], listed['cover']), (True, 'The wanderer', 0, None))
        self.assertIn('missing', self.get(f'/s/{added["id"]}/api/bootstrap', status=404)['error'])
        self.get(f'/s/{added["id"]}/api/images', status=404)
        self.assertEqual(self.open(f'/s/{added["id"]}/pic.png')[0], 404)
        self.assertEqual(self.open(f'/s/{added["id"]}/')[0], 200)  # The editor itself says the folder is missing.
        self.post(f'/api/stories/{added["id"]}/reveal', status=404)
        self.post(f'/api/stories/{added["id"]}/relocate', {'path': str(self.folder('Empty'))}, status=400)
        self.post(f'/api/stories/{added["id"]}/relocate', {'path': 'not/absolute'}, status=400)
        relocated = self.post(f'/api/stories/{added["id"]}/relocate', {'path': str(moved)})['story']
        self.assertEqual((relocated['id'], relocated['missing'], relocated['title'], relocated['media']),
                         (registry.story_id(moved), False, 'The wanderer', 1))
        self.assertEqual([s['id'] for s in self.get('/api/stories')['stories']], [relocated['id']])
        self.assertEqual(self.open(f'/s/{relocated["id"]}/pic.png')[2], b'image')
        self.get(f'/s/{added["id"]}/api/bootstrap', status=404)
        self.post('/api/stories/s0000000000000000/relocate', {'path': str(moved)}, status=404)

    def test_relocate_refuses_a_corrupt_project(self):
        added = self.add(self.folder('Good'))
        bad = self.folder('Bad')
        (bad / story.PROJECT_NAME).write_text('nope')
        self.post(f'/api/stories/{added["id"]}/relocate', {'path': str(bad)}, status=400)
        self.assertEqual(self.get('/api/stories')['stories'][0]['id'], added['id'])

    def test_unreadable_project_is_listed_with_its_error(self):
        folder = self.folder('Later damaged')
        added = self.add(folder)
        (folder / story.PROJECT_NAME).write_text('damaged later')
        self.server.forget(added['id'])
        listed = self.get('/api/stories')['stories'][0]
        self.assertEqual((listed['missing'], listed['title']), (False, 'Later damaged'))
        self.assertIn('error', listed)
        self.assertEqual(self.get(f'/s/{added["id"]}/api/bootstrap', status=500).keys(), {'error'})
        self.assertEqual((folder / story.PROJECT_NAME).read_text(), 'damaged later')

    def test_reveal_opens_the_folder(self):
        folder = self.folder('Show me')
        added = self.add(folder)
        self.assertEqual(self.post(f'/api/stories/{added["id"]}/reveal'), {'ok': True})
        self.assertEqual(self.revealed, [folder.resolve()])
        self.post('/api/stories/s0000000000000000/reveal', status=404)

    def test_unknown_and_malformed_story_ids(self):
        for path in ['/s/s0000000000000000/api/bootstrap', '/s/s0000000000000000/api/images', '/s/s0000000000000000/a.png']:
            self.assertEqual(self.get(path, status=404).keys(), {'error'})
        for path in ['/s/nope/', '/s/S0000000000000000/', '/s/s000/api/bootstrap']:
            self.assertEqual(self.open(path)[0], 404, path)
        self.post('/s/s0000000000000000/api/project', {'project': {}, 'revision': 'x'}, status=404)
        self.post('/api/stories/nope/remove', status=404)
        self.post('/api/unknown', status=404)

    def test_pick_folder_uses_the_native_dialog(self):
        self.picks = ['C:\\Users\\me\\Stories\\New', None, desktop.PickerUnavailable('no dialog'), OSError('crashed')]
        self.assertEqual(self.post('/api/stories/pick-folder', {'purpose': 'new'}), {'path': 'C:\\Users\\me\\Stories\\New'})
        self.assertEqual(self.post('/api/stories/pick-folder', {'purpose': 'add'}), {'cancelled': True})
        self.post('/api/stories/pick-folder', {}, status=501)
        self.post('/api/stories/pick-folder', {}, status=500)
        self.post('/api/stories/pick-folder', {'purpose': 'delete'}, status=400)
        self.assertEqual(self.picks, [])

    def export_pdf(self, sid, html, status=200, token=None):
        headers = {'Content-Type': 'text/html; charset=utf-8', 'X-Story-Token': self.token if token is None else token}
        return self.open(f'/s/{sid}/api/export-pdf', data=html, headers=headers, method='POST')

    def test_export_pdf_returns_the_rendered_document(self):
        added = self.add(self.folder('Printable'))
        page = '<!doctype html><h1>Æ story</h1><img src="data:image/png;base64,AAAA">'.encode()
        self.pdf_results = [b'%PDF-1.7 fake', pdf.PdfUnavailable('no browser'), OSError('crashed')]
        code, headers, body = self.export_pdf(added['id'], page)
        self.assertEqual((code, headers['Content-Type'], body), (200, 'application/pdf', b'%PDF-1.7 fake'))
        self.assertEqual(self.pdfs, [page])
        self.assertEqual(self.export_pdf(added['id'], page)[0], 501)
        self.assertEqual(self.export_pdf(added['id'], page)[0], 500)
        self.assertEqual(self.export_pdf(added['id'], b'')[0], 413)
        self.assertEqual(self.export_pdf('s0000000000000000', page)[0], 404)
        self.assertEqual(self.export_pdf(added['id'], page, token='wrong')[0], 403)
        self.assertEqual(len(self.pdfs), 3)

    def test_only_one_pdf_export_at_a_time(self):
        added = self.add(self.folder('Busy'))
        self.server.pdf_lock.acquire()
        try:
            self.assertEqual(self.export_pdf(added['id'], b'<p>x</p>')[0], 409)
        finally:
            self.server.pdf_lock.release()
        self.assertEqual(self.pdfs, [])

    def test_only_one_folder_dialog_at_a_time(self):
        self.server.pick_lock.acquire()
        try:
            self.post('/api/stories/pick-folder', {'purpose': 'add'}, status=409)
        finally:
            self.server.pick_lock.release()

    def test_every_post_route_rejects_untrusted_requests(self):
        folder = self.folder('Target')
        added = self.add(folder)
        sid = added['id']
        routes = {'/api/stories/pick-folder': {'purpose': 'add'}, '/api/stories': {'path': str(self.folder('Other'))},
                  f'/api/stories/{sid}/remove': None, f'/api/stories/{sid}/relocate': {'path': str(folder)},
                  f'/api/stories/{sid}/reveal': None, '/api/quit': None, '/api/update/check': None,
                  '/api/update/install': None, '/api/update/skip': {'version': '9.9.9'},
                  f'/s/{sid}/api/project': {'project': self.bootstrap(added)['project'], 'revision': 'x'}}
        attacks = [{'token': ''}, {'token': 'wrong'}, {'origin': 'https://evil.example'}, {'origin': 'null'},
                   {'host': 'evil.example'}, {'host': f'127.0.0.1:{self.server.server_port + 1}'}]
        for route, body in routes.items():
            for attack in attacks:
                self.assertIn('error', self.post(route, body, status=403, **attack), (route, attack))
        self.assertEqual((self.picks, self.revealed, self.quits), ([], [], []))
        self.assertEqual([s['id'] for s in self.get('/api/stories')['stories']], [sid])
        self.assertFalse((self.stories / 'Other' / story.PROJECT_NAME).exists())
        self.assertIsNone(registry.Settings(self.home).get('skippedVersion'))
        self.assertIsNone(registry.Settings(self.home).get('lastUpdateCheck'))

    def test_quit_responds_then_stops(self):
        self.assertEqual(self.post('/api/quit'), {'ok': True})
        deadline = time.monotonic() + 2
        while not self.quits and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.quits, [True])


class IsolationTest(ServerTest):
    def test_stories_are_separate(self):
        a_root, b_root = self.folder('A'), self.folder('B')
        (a_root / 'only-a.png').write_bytes(b'a')
        (b_root / 'only-b.png').write_bytes(b'b')
        a, b = self.add(a_root), self.add(b_root)
        self.assertEqual(self.open(f'/s/{a["id"]}/only-a.png')[2], b'a')
        self.assertEqual(self.open(f'/s/{b["id"]}/only-a.png')[0], 404)
        self.assertEqual(self.open(f'/s/{a["id"]}/only-b.png')[0], 404)
        self.assertEqual([i['file'] for i in self.get(f'/s/{b["id"]}/api/images')['images']], ['only-b.png'])
        a_boot, b_boot = self.bootstrap(a), self.bootstrap(b)
        a_project = a_boot['project']
        a_project['title'] = 'Changed A'
        self.save(a, a_project, a_boot['revision'])
        self.assertEqual(self.bootstrap(b)['revision'], b_boot['revision'])
        self.assertEqual(json.loads((b_root / story.PROJECT_NAME).read_text())['title'], 'B')
        # A revision from one story is never accepted by another.
        self.save(b, b_boot['project'], a_boot['revision'], status=409)
        self.assertIsNot(self.server.studio(a['id']), self.server.studio(b['id']))
        self.assertIsNot(self.server.studio(a['id']).lock, self.server.studio(b['id']).lock)
        self.assertIs(story.Studio(a_root).lock, self.server.studio(a['id']).lock)  # Shared per folder.

    def test_story_inside_another_story_does_not_expose_its_parent(self):
        outer = self.folder('Outer')
        (outer / 'outer.png').write_bytes(b'outer')
        inner = outer / 'inner'
        inner.mkdir()
        o, i = self.add(outer), self.add(inner)
        self.assertEqual(self.open(f'/s/{i["id"]}/outer.png')[0], 404)
        self.assertEqual(self.open(f'/s/{i["id"]}/..%2fouter.png')[0], 404)
        self.assertEqual([x['file'] for x in self.get(f'/s/{o["id"]}/api/images')['images']], ['outer.png'])

    @unittest.skipIf(os.name == 'nt', 'symbolic links need extra rights on Windows')
    def test_linked_story_folder_resolves_to_its_target(self):
        real = self.folder('Real')
        link = self.stories / 'Link'
        link.symlink_to(real, target_is_directory=True)
        self.assertEqual(self.add(link)['id'], registry.story_id(real.resolve()))


class LegacyMigrationTest(ServerTest):
    def legacy_folder(self, with_project=True):
        folder = self.folder('Old story')
        for name in ['garden.png', 'plain.png', 'clip.mp4']:
            (folder / name).write_bytes(b'media')
        (folder / 'story-server.py').write_text('# the old server')
        (folder / 'editor.html').write_text('<!-- the old editor -->')
        seed = json.loads(story.encode(story.BLANK_PROJECT))
        seed.update(id='old-story', title='The legacy seed')
        (folder / 'editor-data.js').write_text(legacy_js({'images': [
            {'file': 'garden.png', 'title': 'Two figures in the garden', 'category': 'Garden'},
            {'file': 'plain.png', 'title': 'plain', 'category': 'New additions'},
            {'file': 'clip.mp4', 'title': 'clip', 'category': 'Night'},
            {'file': '../outside.png', 'title': 'Unsafe', 'category': 'Nope'},
            {'file': 'described.png', 'title': 'Already described', 'category': 'Old'}], 'seed': seed}))
        if with_project:
            project = json.loads(story.encode(story.BLANK_PROJECT))
            project.update(id='old-story', title='Old story')
            project['extraImages'] = [{'file': 'described.png', 'title': 'Kept description', 'category': 'Kept'}]
            (folder / story.PROJECT_NAME).write_text(json.dumps(project))
        self.untouched = {name: (folder / name).read_bytes()
                          for name in ['editor-data.js', 'story-server.py', 'editor.html', 'garden.png']}
        return folder

    def assert_legacy_untouched(self, folder):
        self.assertEqual({name: (folder / name).read_bytes() for name in self.untouched}, self.untouched)

    def test_descriptions_are_merged_after_a_backup(self):
        folder = self.legacy_folder()
        original = (folder / story.PROJECT_NAME).read_bytes()
        added = self.add(folder)
        self.assertEqual(added['title'], 'Old story')
        self.assertEqual((folder / story.PRE_MIGRATION_NAME).read_bytes(), original)
        project = json.loads((folder / story.PROJECT_NAME).read_text())
        self.assertEqual(project['id'], 'old-story')
        self.assertEqual(project['extraImages'], [
            {'file': 'described.png', 'title': 'Kept description', 'category': 'Kept'},
            {'file': 'garden.png', 'title': 'Two figures in the garden', 'category': 'Garden'},
            {'file': 'clip.mp4', 'title': 'clip', 'category': 'Night'}])
        self.assert_legacy_untouched(folder)
        self.assertIn(b'Stand-in editor', self.open(f'/s/{added["id"]}/editor.html')[2])

        # Idempotent: adding again (still registered, or after removing it) changes nothing.
        migrated = (folder / story.PROJECT_NAME).read_bytes()
        self.add(folder, status=200)
        self.post(f'/api/stories/{added["id"]}/remove')
        self.add(folder)
        self.assertEqual((folder / story.PROJECT_NAME).read_bytes(), migrated)
        self.assertEqual((folder / story.PRE_MIGRATION_NAME).read_bytes(), original)
        self.assert_legacy_untouched(folder)

    def test_later_edits_survive_re_adding(self):
        folder = self.legacy_folder()
        added = self.add(folder)
        boot = self.bootstrap(added)
        project = boot['project']
        project['extraImages'][1]['title'] = 'Renamed in the editor'
        self.save(added, project, boot['revision'])
        self.post(f'/api/stories/{added["id"]}/remove')
        self.add(folder)
        saved = json.loads((folder / story.PROJECT_NAME).read_text())['extraImages']
        self.assertEqual([image['title'] for image in saved if image['file'] == 'garden.png'], ['Renamed in the editor'])

    def test_legacy_folder_without_a_project_starts_from_its_seed(self):
        folder = self.legacy_folder(with_project=False)
        added = self.add(folder, title='Body title is not used for a legacy seed')
        project = json.loads((folder / story.PROJECT_NAME).read_text())
        self.assertEqual((project['id'], project['title'], added['title']), ('old-story', 'The legacy seed', 'The legacy seed'))
        self.assertEqual([image['file'] for image in project['extraImages']], ['garden.png', 'clip.mp4', 'described.png'])
        self.assertFalse((folder / story.PRE_MIGRATION_NAME).exists())
        self.assert_legacy_untouched(folder)

    def test_legacy_folder_with_an_unusable_seed_starts_blank(self):
        folder = self.folder('Seedless')
        (folder / 'editor-data.js').write_text(legacy_js({'images': [], 'seed': {'format': 'other'}}))
        added = self.add(folder)
        self.assertEqual((added['title'], added['chapters']), ('Seedless', 1))


class UpdateEndpointTest(ServerTest):
    """The endpoints against the updater's fetch seam; tests/test_updates.py covers the updater itself."""

    def test_state_check_skip_and_install(self):
        state = self.get('/api/update')
        self.assertEqual({key: state[key] for key in ['current', 'latest', 'available', 'skipped', 'state', 'progress', 'error']},
                         {'current': __version__, 'latest': None, 'available': False, 'skipped': False, 'state': 'idle',
                          'progress': 0, 'error': None})
        self.assertEqual(set(state), {'current', 'latest', 'available', 'skipped', 'notes', 'publishedAt', 'state',
                                      'progress', 'error', 'lastCheck'})
        self.assertIsNone(state['lastCheck'])
        self.server.updater.fetch = lambda: None
        checked = self.post('/api/update/check')
        self.assertIsNotNone(checked['lastCheck'])
        self.assertEqual(self.get('/api/update')['lastCheck'], checked['lastCheck'])
        self.post('/api/update/skip', {'version': '0.3.0'})
        self.assertEqual(registry.Settings(self.home).get('skippedVersion'), '0.3.0')
        for bad in [{}, {'version': 3}, {'version': 'latest'}]:
            self.post('/api/update/skip', bad, status=400)
        self.assertIn('Windows', self.post('/api/update/install', status=501)['error'])  # Not the packaged exe.
        self.server.updater.packaged = True
        self.assertIn('No update', self.post('/api/update/install', status=409)['error'])

    def test_failed_check_reports_an_error_state(self):
        def offline():
            raise OSError('GitHub could not be reached')
        self.server.updater.fetch = offline
        failed = self.post('/api/update/check')
        self.assertEqual((failed['available'], failed['state'], failed['error']), (False, 'error', 'GitHub could not be reached'))
        self.assertEqual(self.get('/api/update')['state'], 'error')
        self.server.updater.fetch = lambda: None
        recovered = self.post('/api/update/check')
        self.assertEqual((recovered['state'], recovered['error']), ('idle', None))


if __name__ == '__main__':
    unittest.main()
