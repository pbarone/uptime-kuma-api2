"""
Refuse servers older than Uptime Kuma 3.0 at connect time (issue #88).

This line of the library supports Uptime Kuma 3.x only. ``connect`` (called by
``UptimeKumaApi()``) probes ``GET /api/auth/ok`` after the socket connects:
1.x and 2.x answer the web UI's HTML for that unknown path, 3.x answers JSON.
Only positive evidence of an older server (200, text/html, body mentioning
"Uptime Kuma") refuses. Any other answer proceeds, so a 3.x server behind an
odd proxy is never blocked.

No live server: the socket.io client is patched and ``requests.get`` is mocked.
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException
from uptime_kuma_api.exceptions import Timeout

URL = "http://kuma.invalid:3001"

UI_HTML = "<html><head><title>Uptime Kuma</title></head><body></body></html>"


def _response(status=200, content_type="application/json", json_body=None,
              json_error=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.text = text
    if json_error is not None:
        r.json.side_effect = json_error
    else:
        r.json.return_value = json_body
    return r


PRE_3_RESPONSE = _response(content_type="text/html; charset=utf-8", text=UI_HTML,
                           json_error=ValueError("not json"))


def _construct(get, response=None, error=None, sio_error=None, **kwargs):
    """Build UptimeKumaApi without patching connect; return (api, sio)."""
    if error is not None:
        get.side_effect = error
    else:
        get.return_value = response
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls:
        sio = MagicMock()
        if sio_error is not None:
            sio.connect.side_effect = sio_error
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, **kwargs)
    return api, sio


@patch("uptime_kuma_api.api.requests.get")
class TestPre3Refused(unittest.TestCase):
    def test_refused_with_clear_message(self, get):
        with patch("uptime_kuma_api.api.socketio.Client") as client_cls:
            sio = MagicMock()
            client_cls.return_value = sio
            get.return_value = PRE_3_RESPONSE
            with self.assertRaises(UptimeKumaException) as ctx:
                UptimeKumaApi(URL)
        message = str(ctx.exception)
        self.assertIn("older than Uptime Kuma 3.0", message)
        self.assertIn("uptime-kuma-api2<3", message)
        self.assertNotIsInstance(ctx.exception, Timeout)
        sio.connect.assert_called_once()
        sio.disconnect.assert_called_once()

    def test_explicit_connect_refused(self, get):
        api, _ = _construct_unconnected()
        get.return_value = PRE_3_RESPONSE
        with self.assertRaises(UptimeKumaException) as ctx:
            api.connect()
        self.assertIn("older than Uptime Kuma 3.0", str(ctx.exception))

    def test_request_uses_instance_settings(self, get):
        with self.assertRaises(UptimeKumaException):
            _construct(get, PRE_3_RESPONSE, timeout=7,
                       headers={"X-Proxy": "1"}, ssl_verify=False)
        get.assert_called_once_with(
            f"{URL}/api/auth/ok",
            headers={"X-Proxy": "1"},
            timeout=7,
            verify=False,
        )


def _construct_unconnected():
    with patch("uptime_kuma_api.api.UptimeKumaApi.connect"), \
            patch("uptime_kuma_api.api.socketio.Client") as client_cls:
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL)
    return api, sio


@patch("uptime_kuma_api.api.requests.get")
class TestInconclusiveProceeds(unittest.TestCase):
    """Anything but positive evidence of an older server connects normally."""
    ANSWERS = {
        "3.x json ok": _response(json_body={"ok": True}),
        "404 html": _response(status=404, content_type="text/html", text=UI_HTML),
        "500 html": _response(status=500, content_type="text/html", text=UI_HTML),
        "200 html without Uptime Kuma": _response(
            content_type="text/html", text="<html>Please sign in</html>"),
        "200 json ok false": _response(json_body={"ok": False}),
        "200 json list": _response(json_body=[{"ok": True}]),
        "json content type, bad body": _response(json_error=ValueError("bad")),
        "200 json mentioning Uptime Kuma": _response(
            content_type="application/json", text=UI_HTML),
    }

    def test_answers_proceed(self, get):
        for label, response in self.ANSWERS.items():
            with self.subTest(answer=label):
                api, sio = _construct(get, response)
                sio.connect.assert_called_once()
                sio.disconnect.assert_not_called()

    def test_request_errors_proceed(self, get):
        errors = [
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.Timeout("slow"),
            requests.exceptions.SSLError("bad cert"),
            RuntimeError("anything else"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                api, sio = _construct(get, error=error)
                sio.connect.assert_called_once()
                sio.disconnect.assert_not_called()


@patch("uptime_kuma_api.api.requests.get")
class TestSocketFailure(unittest.TestCase):
    def test_socket_failure_is_unable_to_connect_and_skips_probe(self, get):
        """A 3.x server still in database setup serves HTML but mounts no
        socket.io; the user must see "unable to connect", not "older"."""
        get.return_value = PRE_3_RESPONSE
        with self.assertRaises(UptimeKumaException) as ctx:
            _construct(get, PRE_3_RESPONSE, sio_error=RuntimeError("boom"))
        self.assertEqual(str(ctx.exception), "unable to connect")
        get.assert_not_called()


@patch("uptime_kuma_api.api.requests.get")
class TestAuthCallsDoNotProbe(unittest.TestCase):
    def test_login_login_by_token_and_setup_reach_call(self, get):
        get.side_effect = AssertionError("auth calls must not probe")
        api, _ = _construct_unconnected()
        api._call = MagicMock(return_value={"token": "t"})
        # login goes over Better Auth HTTP now, not the "login" socket event
        def fake_auth_request(method, path, json_body=None, **kwargs):
            api._auth_session.cookies.set("better-auth.session_token", "cookie")
            return {}
        api._auth_request = MagicMock(side_effect=fake_auth_request)
        api._reconnect_with_cookie = MagicMock()
        self.assertEqual(api.login("admin", "secret"), {"token": "cookie"})
        # login_by_token reconnects with the cookie, not the removed socket event
        self.assertEqual(api.login_by_token("token"), {"token": "token"})
        api._reconnect_with_cookie.assert_called_with("token")
        # setup goes over HTTP now, not the "setup" socket event
        api._auth_request.reset_mock(side_effect=True)
        api.setup("admin", "secret")
        api._auth_request.assert_called_once_with(
            "POST", "/api/setup", {"username": "admin", "password": "secret"})
        api._call.assert_not_called()
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
