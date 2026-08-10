"""Unit tests for issue #32: a custom CA bundle path via ``ssl_verify``.

``UptimeKumaApi(url, ssl_verify=<path>)`` must reach engineio's custom-CA
mechanism, which only activates through ``socketio.Client(http_session=...)``
with a ``requests.Session`` whose ``.verify`` is a plain ``str`` - passing
the path as the ``ssl_verify`` kwarg itself is silently ignored (engineio just
checks truthiness). No live server: ``socketio.Client``/``connect`` are
patched throughout.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from uptime_kuma_api.api import UptimeKumaApi


class WeirdPath(os.PathLike):
    """A PathLike whose str() and __fspath__() disagree. str() would
    silently mangle this into an unusable, non-existent path.
    """

    def __init__(self, path):
        self._path = path

    def __fspath__(self):
        return self._path

    def __str__(self):
        return f"<WeirdPath to {self._path!r}>"


class TestSslVerifyCustomCertificate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(delete=False)
        self._tmp.close()
        self.ca_path = self._tmp.name

    def tearDown(self):
        os.unlink(self.ca_path)

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_str_path_builds_http_session_with_matching_verify(self, mock_client_cls, mock_connect):
        mock_client_cls.return_value = MagicMock()

        UptimeKumaApi("http://fake:3001", ssl_verify=self.ca_path)

        kwargs = mock_client_cls.call_args.kwargs
        self.assertIn("http_session", kwargs)
        self.assertEqual(kwargs["http_session"].verify, self.ca_path)
        self.assertEqual(kwargs["ssl_verify"], self.ca_path)

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_pathlib_path_is_normalized_to_str(self, mock_client_cls, mock_connect):
        mock_client_cls.return_value = MagicMock()

        api = UptimeKumaApi("http://fake:3001", ssl_verify=Path(self.ca_path))

        kwargs = mock_client_cls.call_args.kwargs
        self.assertIsInstance(kwargs["http_session"].verify, str)
        self.assertEqual(kwargs["http_session"].verify, self.ca_path)
        # self.ssl_verify is the same converted str, so a later get_status_page
        # call forwards the normalized value rather than the raw Path.
        self.assertIsInstance(api.ssl_verify, str)
        self.assertEqual(api.ssl_verify, self.ca_path)

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_fspath_used_instead_of_str_for_conversion(self, mock_client_cls, mock_connect):
        """Regression pin: str(path_like) must not be used for the conversion.

        A PathLike whose __fspath__() and __str__() disagree would silently
        produce a non-existent path if converted with str(). os.fspath()
        must be used instead.
        """
        mock_client_cls.return_value = MagicMock()

        weird = WeirdPath(self.ca_path)
        UptimeKumaApi("http://fake:3001", ssl_verify=weird)

        kwargs = mock_client_cls.call_args.kwargs
        self.assertEqual(kwargs["http_session"].verify, self.ca_path)
        self.assertNotEqual(kwargs["http_session"].verify, str(weird))

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_bool_true_omits_http_session(self, mock_client_cls, mock_connect):
        mock_client_cls.return_value = MagicMock()

        UptimeKumaApi("http://fake:3001", ssl_verify=True)

        kwargs = mock_client_cls.call_args.kwargs
        self.assertNotIn("http_session", kwargs)
        self.assertEqual(kwargs["ssl_verify"], True)

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_bool_false_omits_http_session(self, mock_client_cls, mock_connect):
        mock_client_cls.return_value = MagicMock()

        UptimeKumaApi("http://fake:3001", ssl_verify=False)

        kwargs = mock_client_cls.call_args.kwargs
        self.assertNotIn("http_session", kwargs)
        self.assertEqual(kwargs["ssl_verify"], False)

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_nonexistent_path_rejected_before_connecting(self, mock_client_cls, mock_connect):
        missing = os.path.join(tempfile.gettempdir(), "does-not-exist-ca.crt")
        self.assertFalse(os.path.exists(missing))

        with self.assertRaises(ValueError):
            UptimeKumaApi("http://fake:3001", ssl_verify=missing)

        mock_client_cls.assert_not_called()
        mock_connect.assert_not_called()

    @patch('uptime_kuma_api.api.UptimeKumaApi.connect')
    @patch('uptime_kuma_api.api.socketio.Client')
    def test_directory_path_rejected_before_connecting(self, mock_client_cls, mock_connect):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                UptimeKumaApi("http://fake:3001", ssl_verify=tmpdir)

        mock_client_cls.assert_not_called()
        mock_connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
