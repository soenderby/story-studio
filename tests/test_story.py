"""Story folders, the registry and settings, without HTTP. Run: python -m unittest discover -s tests"""
import json
import sys
import unittest
from unittest import mock

from helpers import TempHome, legacy_js  # noqa: E402 (helpers puts src/ on sys.path)
from story_studio import paths, registry, story  # noqa: E402

sys.dont_write_bytecode = True


class StoryFolderTest(TempHome):
    def test_new_folder_gets_a_blank_project_titled_after_the_folder(self):
        folder = self.folder('My Tale')
        studio = story.prepare_story(folder)
        project = studio.read_project()[0]
        self.assertEqual((project['title'], len(project['chapters']), project['chapters'][0]['blocks']),
                         ('My Tale', 1, []))
        self.assertRegex(project['id'], r'^story-[0-9a-f]{12}$')  # Unique, never the seed's "new-story".
        self.assertNotEqual(project['id'], story.prepare_story(self.folder('Other')).read_project()[0]['id'])

    def test_blank_project_falls_back_when_the_bundled_seed_is_absent(self):
        (self.web / 'editor-data.js').unlink()
        project = story.prepare_story(self.folder('Plain'), 'Given title').read_project()[0]
        self.assertEqual((project['title'], project['chapters'][0]['title']), ('Given title', 'Chapter one'))

    def test_existing_project_is_never_replaced(self):
        folder = self.folder('Kept')
        story.prepare_story(folder, 'First')
        before = (folder / story.PROJECT_NAME).read_bytes()
        story.prepare_story(folder, 'Second')
        self.assertEqual((folder / story.PROJECT_NAME).read_bytes(), before)

    def test_corrupt_project_is_not_replaced_or_backed_up_as_good(self):
        folder = self.folder('Broken')
        (folder / story.PROJECT_NAME).write_text('broken project')
        with self.assertRaises(ValueError):
            story.prepare_story(folder)
        self.assertEqual((folder / story.PROJECT_NAME).read_text(), 'broken project')
        self.assertFalse((folder / (story.PROJECT_NAME + '.bak')).exists())

    def test_linked_project_and_not_a_folder_are_refused(self):
        folder = self.folder('Linked')
        target = self.base / 'elsewhere.json'
        target.write_bytes(story.encode(story.BLANK_PROJECT))
        try:
            (folder / story.PROJECT_NAME).symlink_to(target)
        except OSError:
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(ValueError):
            story.prepare_story(folder)
        with self.assertRaises(ValueError):
            story.prepare_story(self.base / 'nothing here')

    def test_legacy_editor_data_supplies_descriptions(self):
        folder = self.folder('Legacy')
        (folder / 'legacy.png').write_bytes(b'image')
        (folder / 'editor-data.js').write_text(legacy_js(
            {'images': [{'file': 'legacy.png', 'title': 'A described picture', 'category': 'Garden'}], 'seed': {}}))
        studio = story.prepare_story(folder)
        self.assertEqual([(i['title'], i['category']) for i in studio.images()[0]], [('A described picture', 'Garden')])

    def test_malformed_legacy_file_is_reported_not_guessed(self):
        folder = self.folder('Odd legacy')
        (folder / 'editor-data.js').write_text('not the expected format')
        with self.assertRaises(ValueError):
            story.prepare_story(folder)
        self.assertFalse((folder / story.PROJECT_NAME).exists())

    def test_byte_ranges(self):
        cases = {None: (0, 9, False), 'bytes=2-5': (2, 5, True), 'bytes=7-': (7, 9, True), 'bytes=-3': (7, 9, True),
                 'bytes=8-99': (8, 9, True), 'bytes=10-': None, 'bytes=5-2': None, 'nonsense': (0, 9, False)}
        for header, expected in cases.items():
            self.assertEqual(story.byte_range(header, 10), expected, header)

    def test_same_folder_from_windows_and_wsl_is_one_story(self):
        self.assertEqual(story.directory_key('/mnt/c/Users/Me/Story'), story.directory_key('C:\\Users\\me\\Story\\'))
        self.assertNotEqual(story.directory_key('/mnt/c/Users/Me/Story'), story.directory_key('C:\\Users\\Me\\Other'))
        sid = registry.story_id('/mnt/c/Users/Me/Story')
        self.assertEqual(sid, registry.story_id('C:\\Users\\me\\Story'))
        self.assertRegex(sid, r'^s[0-9a-f]{16}$')

    def test_validation_rejects_unhashable_values_cleanly(self):
        project = json.loads(story.encode(story.BLANK_PROJECT))
        project['chapters'][0]['blocks'] = [{'id': 'a', 'type': 'image', 'file': 'a.png', 'caption': '', 'size': []}]
        with self.assertRaises(ValueError):
            story.validate_project(project)

    def test_validation_requires_an_integer_format_version(self):
        project = json.loads(story.encode(story.BLANK_PROJECT))
        for version in (True, 1.0, '1'):
            project['version'] = version
            with self.assertRaises(ValueError):
                story.validate_project(project)

    def commented(self, text, *comments):
        project = json.loads(story.encode(story.BLANK_PROJECT))
        project['chapters'][0]['blocks'] = [{'id': 't1', 'type': 'text', 'text': text, 'comments': list(comments)}]
        return project

    def test_comments_on_text_blocks_round_trip(self):
        comment = {'id': 'c1', 'start': 4, 'end': 9, 'label': 'change', 'note': 'Too slow', 'quote': 'quick',
                   'suggestion': 'swift', 'resolved': True, 'created': '2026-11-01T10:00:00.000Z',
                   'updated': '2026-11-01T10:05:00.000Z', 'unknown': 'dropped'}
        clean = story.validate_project(self.commented('The quick fox', comment))
        expected = {k: v for k, v in comment.items() if k != 'unknown'}
        self.assertEqual(clean['chapters'][0]['blocks'][0]['comments'], [expected])
        self.assertEqual(story.validate_project(clean), clean)

    def test_comment_defaults_and_empty_lists(self):
        clean = story.validate_project(self.commented('Text', {'id': 'c1', 'start': 0, 'end': 4, 'resolved': False}))
        self.assertEqual(clean['chapters'][0]['blocks'][0]['comments'],
                         [{'id': 'c1', 'start': 0, 'end': 4, 'label': 'note', 'note': '', 'quote': ''}])
        self.assertNotIn('comments', story.validate_project(self.commented('Text'))['chapters'][0]['blocks'][0])

    def test_comment_offsets_are_clamped_in_javascript_string_units(self):
        # '🦊' is one code point in Python but two UTF-16 units in the editor.
        clean = story.validate_project(self.commented('a🦊b', {'id': 'c1', 'start': 1, 'end': 4},
                                                      {'id': 'c2', 'start': 9, 'end': 3}, {'id': 'c3', 'start': -2, 'end': 99}))
        self.assertEqual([(c['start'], c['end']) for c in clean['chapters'][0]['blocks'][0]['comments']], [(1, 4), (3, 3), (0, 4)])

    def test_malformed_comments_are_refused(self):
        good = {'id': 'c1', 'start': 0, 'end': 1}
        for bad in ({**good, 'start': True}, {**good, 'end': 1.0}, {**good, 'label': 'shout'}, {**good, 'id': 'bad id'},
                    {**good, 'id': 't1'}, {**good, 'note': 5}, {**good, 'resolved': 'yes'}, {**good, 'suggestion': None}, 'c1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                story.validate_project(self.commented('Text', bad))
        with self.assertRaises(ValueError):
            story.validate_project(self.commented('Text', good, dict(good)))  # Duplicate id
        project = self.commented('Text')
        project['chapters'][0]['blocks'][0]['comments'] = {}
        with self.assertRaises(ValueError):
            story.validate_project(project)


class PathsTest(TempHome):
    def test_app_home_uses_override_and_creates_it(self):
        target = self.base / 'fresh' / 'home'
        with mock.patch.dict('os.environ', {'STORY_STUDIO_HOME': str(target)}):
            self.assertEqual(paths.app_home(), target)
        self.assertTrue(target.is_dir())

    def test_web_dir_from_source_and_from_a_pyinstaller_bundle(self):
        self.assertTrue((paths.web_dir() / 'editor.html').is_file())
        bundle = self.base / 'bundle'
        (bundle / 'story_studio' / 'web').mkdir(parents=True)
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, '_MEIPASS', str(bundle), create=True):
            self.assertEqual(paths.web_dir(), bundle / 'story_studio' / 'web')


class RegistryTest(TempHome):
    def test_entries_persist_and_the_same_folder_is_added_once(self):
        stories = registry.Registry(self.home)
        first, created = stories.add(self.folder('One'), 'One')
        again, created_again = stories.add(self.stories / 'One', 'Renamed')
        self.assertEqual((created, created_again, again['id'], again['title']), (True, False, first['id'], 'One'))
        stories.update(first['id'], lastOpened='2026-01-01T00:00:00.000Z')
        reloaded = registry.Registry(self.home)
        self.assertEqual(reloaded.list(), stories.list())
        self.assertEqual(json.loads((self.home / 'stories.json').read_text())['version'], 1)

    def test_list_is_most_recently_opened_first(self):
        stories = registry.Registry(self.home)
        a = stories.add(self.folder('A'), 'A')[0]
        b = stories.add(self.folder('B'), 'B')[0]
        stories.update(a['id'], lastOpened='2026-01-02T00:00:00.000Z')
        stories.update(b['id'], lastOpened='2026-01-01T00:00:00.000Z')
        self.assertEqual([s['title'] for s in stories.list()], ['A', 'B'])

    def test_unreadable_registry_is_set_aside_and_a_fresh_one_started(self):
        folder = self.folder('Untouched')
        (folder / 'keep.txt').write_text('mine')
        (self.home / 'stories.json').write_text('{not json')
        stories = registry.Registry(self.home)
        self.assertEqual(stories.list(), [])
        corrupt = list(self.home.glob('stories.json.corrupt-*'))
        self.assertEqual([path.read_text() for path in corrupt], ['{not json'])
        stories.add(folder, 'Untouched')
        self.assertEqual(len(registry.Registry(self.home).list()), 1)
        self.assertEqual([p.name for p in folder.iterdir()], ['keep.txt'])
        (self.home / 'stories.json').write_text('{"version": 1, "stories": "wrong"}')
        self.assertEqual(registry.Registry(self.home).list(), [])
        self.assertEqual(len(list(self.home.glob('stories.json.corrupt-*'))), 2)

    def test_malformed_entries_are_dropped_and_the_original_kept(self):
        good = {'id': registry.story_id(self.stories / 'Good'), 'path': str(self.stories / 'Good'), 'title': 'Good',
                'added': None, 'lastOpened': None}
        original = json.dumps({'version': 1, 'stories': [good, {'id': '../evil', 'path': 'x'}, 'junk']})
        (self.home / 'stories.json').write_text(original)
        self.assertEqual(registry.Registry(self.home).list(), [good])
        self.assertEqual([p.read_text() for p in self.home.glob('stories.json.corrupt-*')], [original])
        self.assertEqual(json.loads((self.home / 'stories.json').read_text())['stories'], [good])

    def test_remove_and_relocate(self):
        stories = registry.Registry(self.home)
        entry = stories.add(self.folder('Old place'), 'Tale')[0]
        moved = stories.relocate(entry['id'], self.folder('New place'))
        self.assertNotEqual(moved['id'], entry['id'])
        self.assertEqual((moved['title'], moved['added']), ('Tale', entry['added']))
        self.assertIsNone(stories.get(entry['id']))
        self.assertTrue(stories.remove(moved['id']))
        self.assertFalse(stories.remove(moved['id']))
        self.assertEqual(stories.list(), [])

    def test_relocating_onto_a_listed_folder_merges_the_entries(self):
        stories = registry.Registry(self.home)
        a = stories.add(self.folder('A'), 'A')[0]
        b = stories.add(self.folder('B'), 'B')[0]
        self.assertEqual(stories.relocate(a['id'], self.stories / 'B')['id'], b['id'])
        self.assertEqual([s['id'] for s in stories.list()], [b['id']])

    def test_settings_defaults_persistence_and_corruption(self):
        settings = registry.Settings(self.home)
        self.assertEqual(settings.get('port'), 8765)
        settings.update(port=8770, skippedVersion='1.0.0')
        self.assertEqual(registry.Settings(self.home).get('port'), 8770)
        (self.home / 'settings.json').write_text('[]')
        self.assertEqual(registry.Settings(self.home).values, registry.DEFAULT_SETTINGS)
        self.assertEqual(len(list(self.home.glob('settings.json.corrupt-*'))), 1)
        (self.home / 'settings.json').write_text('{"version": 1, "port": "high", "future": 1}')
        settings = registry.Settings(self.home)
        self.assertEqual((settings.get('port'), settings.get('future')), (8765, 1))


if __name__ == '__main__':
    unittest.main()
