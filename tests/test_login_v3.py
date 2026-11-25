"""
Unit tests for login() on the Better Auth HTTP flow. No live server: the
requests transport adapter and socketio.Client are patched, and Set-Cookie
headers are placed on the fake raw response so the real cookie jar picks them up.
"""
import json
import unittest
from http.client import HTTPMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from requests.adapters import HTTPAdapter

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException, Timeout
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"
SESSION_VALUE = "sess-value.signature"
SESSION_COOKIE = f"better-auth.session_token={SESSION_VALUE}; Path=/; HttpOnly"
TWO_FACTOR_COOKIE = "better-auth.two_factor=tf-value; Path=/; HttpOnly"


def _make_api(**kwargs):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, **kwargs)

    def fake_connect(*args, **kw):
        api._event_data[Event.INFO] = {"version": "3.0.0"}

    sio.connect.side_effect = fake_connect
    return api, sio


def _response(status=200, body=None, cookies=()):
    msg = HTTPMessage()
    for c in cookies:
        msg.add_header("Set-Cookie", c)
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps({} if body is None else body).encode()
    r.url = URL
    r.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=msg))
    return r


def _transport(*responses):
    """Patch the adapter; each call pops the next response (or raises it)."""
    queue = list(responses)

    def send(self, request, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        item.request = request
        return item

    return patch.object(HTTPAdapter, "send", autospec=True, side_effect=send)


def _requests(mock):
    return [c.args[1] for c in mock.call_args_list]


class TestLoginPassword(unittest.TestCase):
    def test_success(self):
        api, sio = _make_api()
        with _transport(_response(cookies=[SESSION_COOKIE])) as send:
            result = api.login("admin", "secret")
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/sign-in/username")
        self.assertEqual(json.loads(req.body),
                         {"username": "admin", "password": "secret", "rememberMe": True})
        self.assertEqual(req.headers["Origin"], URL)
        self.assertEqual(result, {"token": SESSION_VALUE})
        self.assertEqual(api._session_cookie, SESSION_VALUE)
        sio.disconnect.assert_called_once()
        headers = sio.connect.call_args.kwargs["headers"]
        self.assertEqual(headers["Cookie"], f"better-auth.session_token={SESSION_VALUE}")

    def test_failure_uses_server_message(self):
        api, sio = _make_api()
        with _transport(_response(401, {"message": "Invalid username or password"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.login("admin", "wrong")
        self.assertEqual(str(cm.exception), "Invalid username or password")
        self.assertIsNone(api._session_cookie)
        sio.connect.assert_not_called()

    def test_rate_limited(self):
        api, sio = _make_api()
        with _transport(_response(429, {})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.login("admin", "secret")
        self.assertIn("rate limit", str(cm.exception))
        sio.connect.assert_not_called()

    def test_network_error(self):
        api, sio = _make_api()
        with _transport(requests.exceptions.ConnectionError("boom secret")):
            with self.assertRaises(UptimeKumaException) as cm:
                api.login("admin", "secret")
        self.assertNotIn("secret", str(cm.exception))
        sio.connect.assert_not_called()

    def test_missing_session_cookie_raises(self):
        api, sio = _make_api()
        with _transport(_response(200, {"ok": True})):
            with self.assertRaises(UptimeKumaException):
                api.login("admin", "secret")
        sio.connect.assert_not_called()

    def test_relogin_discards_previous_cookie(self):
        api, _ = _make_api()
        api._auth_session.cookies.set("better-auth.session_token", "old")
        api._session_cookie = "old"
        with _transport(_response(401, {"message": "no"})):
            with self.assertRaises(UptimeKumaException):
                api.login("admin", "wrong")
        self.assertIsNone(api._session_cookie)
        self.assertIsNone(api._auth_session.cookies.get("better-auth.session_token"))


class TestLoginTwoFactor(unittest.TestCase):
    def _challenge(self):
        return _response(200, {"twoFactorRedirect": True}, cookies=[TWO_FACTOR_COOKIE])

    def test_success(self):
        api, sio = _make_api()
        with _transport(self._challenge(), _response(cookies=[SESSION_COOKIE])) as send:
            result = api.login("admin", "secret", "123456")
        first, second = _requests(send)
        self.assertEqual(second.method, "POST")
        self.assertEqual(second.url, f"{URL}/api/auth/two-factor/verify-totp")
        self.assertEqual(json.loads(second.body), {"code": "123456"})
        self.assertEqual(second.headers["Origin"], URL)
        self.assertIn("better-auth.two_factor=tf-value", second.headers["Cookie"])
        self.assertEqual(result, {"token": SESSION_VALUE})
        self.assertEqual(sio.connect.call_args.kwargs["headers"]["Cookie"],
                         f"better-auth.session_token={SESSION_VALUE}")

    def test_no_code(self):
        api, sio = _make_api()
        with _transport(self._challenge()) as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.login("admin", "secret")
        self.assertIn("2FA", str(cm.exception))
        self.assertEqual(len(_requests(send)), 1)
        self.assertIsNone(api._session_cookie)
        sio.connect.assert_not_called()

    def test_rejected_code(self):
        api, sio = _make_api()
        with _transport(self._challenge(), _response(401, {"message": "Invalid code"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.login("admin", "secret", "000000")
        self.assertEqual(str(cm.exception), "Invalid code")
        self.assertIsNone(api._session_cookie)
        sio.connect.assert_not_called()


class TestLoginNoCredentials(unittest.TestCase):
    def test_success(self):
        api, sio = _make_api()
        api._event_data[Event.SESSION] = True
        with _transport() as send:
            self.assertEqual(api.login(), {})
        send.assert_not_called()
        sio.connect.assert_not_called()

    def test_timeout_without_session_event(self):
        api, _ = _make_api(timeout=0.05)
        with _transport() as send:
            with self.assertRaises(Timeout):
                api.login()
        send.assert_not_called()


class TestAutoLoginRemoved(unittest.TestCase):
    def test_event_member_gone(self):
        self.assertFalse(hasattr(Event, "AUTO_LOGIN"))
        self.assertNotIn("autoLogin", [e.value for e in Event])


if __name__ == "__main__":
    unittest.main()
