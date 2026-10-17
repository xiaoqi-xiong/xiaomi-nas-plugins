import io
import json
import os
import types
import unittest
from unittest.mock import Mock, patch

from test_music import music, FakeStdout


class DownloadFailure(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class MusicWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'owner': 'test-owner', 'owner_uid': os.getuid()}
        self.manager = Mock()
        self.manager.status.return_value = {'tasks': [], 'library_path': '/music'}
        self.manager.submit.return_value = {'status': 'added', 'hash': 'a' * 40}
        self.manager.submit_torrent.return_value = {'status': 'added', 'hash': 'b' * 40}
        self.factory = Mock(return_value=self.manager)
        self.module = types.SimpleNamespace(MusicDownloads=self.factory, MusicDownloadError=DownloadFailure)

    def call(self, path, method='GET', value=None, user='test-owner'):
        body = json.dumps(value).encode() if value is not None else b''
        environ = {'USER': user, 'FILE_URI': path, 'REQUEST_METHOD': method,
                   'CONTENT_LENGTH': str(len(body)), 'CONTENT_TYPE': 'application/json'}
        output = FakeStdout()
        with patch.object(music, 'get_settings', return_value=self.settings), \
                patch.dict(os.environ, environ, clear=True), \
                patch.dict('sys.modules', {'torrent_import': self.module}), \
                patch.object(music.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(body))), \
                patch.object(music.sys, 'stdout', output):
            music.serve()
        return output.buffer.getvalue()

    def test_downloads_remain_behind_native_owner_gate(self):
        result = self.call('api/downloads', 'POST', {'magnet': 'magnet:?xt=x'}, user='other')
        self.assertIn(b'Status: 403', result)
        self.factory.assert_not_called()

    def test_pansou_delegation_still_requires_the_music_owner(self):
        with patch.object(music.runpy, 'run_path') as delegated:
            result = self.call('/pansou/vendor-ui/index.html?music=1', user='other')
        self.assertIn(b'Status: 403', result)
        delegated.assert_not_called()

    def test_normalized_pansou_delegation_preserves_query_and_native_identity(self):
        captured = {}

        def run_path(*args, **kwargs):
            captured['path'] = os.environ.get('FILE_URI')
            captured['query'] = os.environ.get('QUERY_STRING')
            captured['plugin'] = os.environ.get('NATIVE_PLUGIN_ID')
            captured['app_route'] = os.environ.get('NATIVE_APP_ROUTE')
            captured['args'] = args
            captured['kwargs'] = kwargs

        with patch.object(music.runpy, 'run_path', side_effect=run_path):
            self.call('/pansou/vendor-ui/index.html?music=1&keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6')
        self.assertEqual(captured['path'], '/pansou/vendor-ui/index.html?music=1&keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6')
        self.assertEqual(captured['query'], 'music=1&keyword=%E5%91%A8%E6%9D%B0%E4%BC%A6')
        self.assertEqual(captured['plugin'], 'music')
        self.assertEqual(captured['app_route'], '')
        self.assertEqual(captured['args'][0].replace('\\', '/').split('/')[-2:], ['mediastack', 'mediastack.cgi'])
        self.assertEqual(captured['kwargs'], {'run_name': '__main__'})

    def test_non_pansou_and_traversal_routes_are_not_delegated(self):
        with patch.object(music.runpy, 'run_path') as delegated:
            regular = self.call('/pt/index.html')
            traversal = self.call('/pansou/../index.html')
        self.assertIn(b'Status: 404', regular)
        self.assertIn(b'Status: 404', traversal)
        delegated.assert_not_called()

    def test_metadata_and_magnet_are_explicit_post_operations(self):
        self.assertIn(b'Status: 200', self.call('music.cgi?api=downloads'))
        self.assertIn(b'Status: 200', self.call('api/downloads', 'POST', {'magnet': 'magnet:?xt=example'}))
        self.manager.submit.assert_called_once_with('magnet:?xt=example')
        self.assertIn(b'Status: 200', self.call('api/downloads/torrent', 'POST', {'torrent': 'dG9ycmVudA=='}))
        self.manager.submit_torrent.assert_called_once_with(b'torrent')
        self.assertIn(b'Status: 405', self.call('api/downloads/torrent'))
        self.assertIn(b'Status: 405', self.call('api/downloads', 'DELETE'))

    def test_invalid_json_shapes_and_extra_fields_cannot_choose_destination(self):
        for value in [[], {'magnet': 13}, {'magnet': 'magnet:?xt=x', 'savepath': '/private'}]:
            with self.subTest(value=value):
                self.assertIn(b'Status: 400', self.call('api/downloads', 'POST', value))
        self.manager.submit.assert_not_called()
        self.assertIn(b'Status: 400', self.call('api/downloads/torrent', 'POST', {'torrent': 'not/base64!'}))
        self.manager.submit_torrent.assert_not_called()

    def test_safe_import_error_is_visible_without_a_traceback(self):
        self.manager.submit.side_effect = DownloadFailure(409, '此资源已在普通下载任务中，请在下载器查看。')
        result = self.call('api/downloads', 'POST', {'magnet': 'magnet:?xt=x'})
        self.assertIn(b'Status: 409', result)
        self.assertIn('普通下载任务'.encode(), result)
        self.assertNotIn(b'Traceback', result)


if __name__ == '__main__':
    unittest.main()
