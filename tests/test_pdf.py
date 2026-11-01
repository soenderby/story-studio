"""PDF export through a headless browser, with the browser replaced by a stand-in. Run: python -m unittest discover -s tests"""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import url2pathname

import helpers  # noqa: F401 (puts src/ on sys.path)
from story_studio import pdf  # noqa: E402

sys.dont_write_bytecode = True


class RenderPdfTest(unittest.TestCase):
    def fake_browser(self, write=b'%PDF-1.7 stand-in', stderr=b''):
        calls = []

        def run(command, **options):
            calls.append((command, options))
            page = Path(url2pathname(urlsplit(command[-1]).path))
            calls.append(page.read_bytes())
            output = next(arg.split('=', 1)[1] for arg in command if arg.startswith('--print-to-pdf='))
            if write is not None:
                Path(output).write_bytes(write)
            return subprocess.CompletedProcess(command, 0 if write else 1, b'', stderr)
        return calls, run

    def test_prints_the_page_with_a_private_profile(self):
        calls, run = self.fake_browser()
        with mock.patch.object(pdf, 'find_browser', return_value=Path('/opt/edge/msedge')), \
                mock.patch.object(pdf.subprocess, 'run', run):
            self.assertEqual(pdf.render_pdf('<h1>Æ</h1>'.encode()), b'%PDF-1.7 stand-in')
        (command, options), page = calls
        self.assertEqual(page, '<h1>Æ</h1>'.encode())
        self.assertEqual(command[0], str(Path('/opt/edge/msedge')))
        self.assertIn('--headless=new', command)
        self.assertIn('--no-pdf-header-footer', command)
        profile = next(arg for arg in command if arg.startswith('--user-data-dir='))
        self.assertFalse(Path(profile.split('=', 1)[1]).exists())  # The throwaway folder is removed afterwards.
        self.assertEqual(options['timeout'], pdf.TIMEOUT)

    def test_drops_the_windows_compatibility_layer(self):
        calls, run = self.fake_browser()
        with mock.patch.object(pdf, 'find_browser', return_value=Path('/opt/edge/msedge')), \
                mock.patch.object(pdf.subprocess, 'run', run), \
                mock.patch.dict(pdf.os.environ, {'__COMPAT_LAYER': 'DetectorsAppHealth', 'STORY_STUDIO_PROBE': 'kept'}):
            pdf.render_pdf(b'<p>x</p>')
        env = calls[0][1]['env']
        self.assertNotIn('__COMPAT_LAYER', {key.upper() for key in env})
        self.assertIn('kept', env.values())

    def test_reports_a_browser_that_writes_nothing(self):
        _, run = self.fake_browser(write=None, stderr=b'noise\nPage load failed\n')
        with mock.patch.object(pdf, 'find_browser', return_value=Path('/opt/edge/msedge')), \
                mock.patch.object(pdf.subprocess, 'run', run):
            with self.assertRaisesRegex(OSError, 'Page load failed'):
                pdf.render_pdf(b'<p>x</p>')

    def test_without_a_browser(self):
        with mock.patch.object(pdf, 'find_browser', return_value=None):
            with self.assertRaises(pdf.PdfUnavailable):
                pdf.render_pdf(b'<p>x</p>')


if __name__ == '__main__':
    unittest.main()
