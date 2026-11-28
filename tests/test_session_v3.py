"""
Unit tests for login_by_token() and logout() on the Better Auth HTTP flow. No
live server: the requests transport adapter and socketio.Client are patched, so
the real cookie jar runs and the Cookie header on the wire can be asserted.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from requests.adapters import HTTPAdapter

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException, Timeout
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"
TOKEN = "sess-value.signature"


def _make_api(authenticated=True, **kwargs):
    """authenticated=False simulates a server that ignores the cookie: the
    socket gets no session event and no post-login info."""
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, **kwargs)

    def fake_connect(*args, **kw):
        if authenticated:
            api._event_data[Event.SESSION] = True
            api._event_data[Event.INFO] = {"version": "3.0.0"}

    sio.connect.side_effect = fake_connect
    return api, sio


def _response(status=200, body=None):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps({} if body is None else body).encode()
    r.url = URL
    r.raw = SimpleNamespace(_original_response=None)
    return r


def _transport(*responses):
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


class TestLoginByToken(unittest.TestCase):
    def test_reconnect_sends_cookie_header(self):
        api, sio = _make_api()
        result = api.login_by_token(TOKEN)
        self.assertEqual(result, {"token": TOKEN})
        sio.disconnect.assert_called_once()
        headers = sio.connect.call_args.kwargs["headers"]
        self.assertEqual(headers["Cookie"], f"better-auth.session_token={TOKEN}")
        self.assertEqual(api._session_cookie, TOKEN)

    def test_token_authenticates_socket_and_calls_work(self):
        # Mirrors the live finding (task 12.4, 3.0.0-beta.2): the cookie value
        # login() returns, passed to login_by_token on a fresh client, makes the
        # server send session + info, and get_monitors then answers.
        api, sio = _make_api()
        monitors = {1: {"id": 1, "name": "m1", "type": "http", "active": 1}}
        authenticate = sio.connect.side_effect

        def connect(*args, **kw):
            authenticate(*args, **kw)
            api._event_data[Event.MONITOR_LIST] = monitors

        sio.connect.side_effect = connect
        result = api.login_by_token(TOKEN)
        self.assertEqual(result, {"token": TOKEN})
        headers = sio.connect.call_args.kwargs["headers"]
        self.assertEqual(headers["Cookie"], f"better-auth.session_token={TOKEN}")
        self.assertTrue(api._event_data[Event.SESSION])
        self.assertEqual([m["name"] for m in api.get_monitors()], ["m1"])

    def test_public_headers_not_mutated(self):
        api, _ = _make_api(headers={"X-Custom": "1"})
        api.login_by_token(TOKEN)
        self.assertEqual(api.headers, {"X-Custom": "1"})

    def test_rejected_cookie_raises_and_forgets_it(self):
        api, _ = _make_api(authenticated=False, timeout=0.05)
        with self.assertRaises(UptimeKumaException) as cm:
            api.login_by_token(TOKEN)
        self.assertNotIsInstance(cm.exception, Timeout)
        self.assertIn("did not authenticate", str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIsNone(api._session_cookie)
        self.assertIsNone(api._auth_session.cookies.get("better-auth.session_token"))

    def test_session_without_info_reraises_timeout(self):
        api, sio = _make_api(timeout=0.05)

        def connect(*args, **kw):
            api._event_data[Event.SESSION] = True

        sio.connect.side_effect = connect
        with self.assertRaises(Timeout):
            api.login_by_token(TOKEN)
        self.assertEqual(api._session_cookie, TOKEN)

    def test_connect_failure_forgets_cookie(self):
        api, sio = _make_api()
        sio.connect.side_effect = RuntimeError("boom " + TOKEN)
        with self.assertRaises(UptimeKumaException) as cm:
            api.login_by_token(TOKEN)
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIsNone(api._session_cookie)

    def test_empty_or_non_string_token(self):
        api, sio = _make_api()
        for bad in ("", None, 123):
            with self.assertRaises(UptimeKumaException):
                api.login_by_token(bad)
        sio.connect.assert_not_called()
        self.assertIsNone(api._session_cookie)


class TestLogout(unittest.TestCase):
    def _logged_in(self, **kwargs):
        api, sio = _make_api(**kwargs)
        api.login_by_token(TOKEN)
        sio.reset_mock()
        return api, sio

    def test_sign_out_request(self):
        api, sio = self._logged_in()
        with _transport(_response(200, {"success": True})) as send:
            self.assertIsNone(api.logout())
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/sign-out")
        self.assertEqual(req.headers["Origin"], URL)
        self.assertIn(f"better-auth.session_token={TOKEN}", req.headers["Cookie"])

    def test_cookie_forgotten_on_success(self):
        api, _ = self._logged_in()
        with _transport(_response(200, {"success": True})):
            api.logout()
        self.assertIsNone(api._session_cookie)
        self.assertIsNone(api._auth_session.cookies.get("better-auth.session_token"))

    def test_socket_not_disconnected(self):
        api, sio = self._logged_in()
        with _transport(_response(200, {"success": True})):
            api.logout()
        sio.disconnect.assert_not_called()
        sio.connect.assert_not_called()

    def test_cookie_kept_on_failure(self):
        api, sio = self._logged_in()
        with _transport(_response(500, {"message": "server broke"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.logout()
        self.assertEqual(str(cm.exception), "server broke")
        self.assertEqual(api._session_cookie, TOKEN)
        self.assertEqual(api._auth_session.cookies.get("better-auth.session_token"), TOKEN)
        sio.disconnect.assert_not_called()

    def test_not_logged_in_raises_without_http_call(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.logout()
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_second_logout_raises_without_http_call(self):
        api, _ = self._logged_in()
        with _transport(_response(200, {"success": True})) as send:
            api.logout()
            with self.assertRaises(UptimeKumaException):
                api.logout()
        self.assertEqual(send.call_count, 1)


class TestRotatedCookieAfterLoginByToken(unittest.TestCase):
    """Live finding (task 12.4): the server rotates the session cookie in a
    Set-Cookie header (for example on two-factor disable). A cookie seeded by
    login_by_token without the jar's default domain was not replaced by the
    rotated one, so both were sent and the stale one won (HTTP 401)."""

    @staticmethod
    def _rotating_response(new_value):
        from http.client import HTTPMessage
        msg = HTTPMessage()
        msg.add_header("Set-Cookie", f"better-auth.session_token={new_value}; Path=/; HttpOnly")
        r = _response(200, {"status": True})
        r.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=msg))
        return r

    def test_rotated_cookie_replaces_the_one_from_login_by_token(self):
        api, _ = _make_api()
        api.login_by_token(TOKEN)
        with _transport(self._rotating_response("rotated.sig"), _response(200, {})) as send:
            api._auth_request("POST", "/api/auth/two-factor/disable", {}, use_session_cookie=True)
            api._auth_request("POST", "/api/auth/change-password", {}, use_session_cookie=True)
        second = _requests(send)[1]
        self.assertEqual(second.headers["Cookie"], "better-auth.session_token=rotated.sig")


class TestRotatedCookieIsAdopted(unittest.TestCase):
    """Issue #111: verify_token (while 2FA is not yet enabled) and disable_2fa
    make Better Auth issue a new session and delete the old one. The library
    kept the old cookie value, so the socket and the token from login() went
    stale."""

    def _logged_in(self):
        api, sio = _make_api()
        api.login_by_token(TOKEN)
        sio.connect.reset_mock()
        return api, sio

    def test_verify_token_adopts_rotated_cookie_and_reopens_socket(self):
        api, sio = self._logged_in()
        rotating = TestRotatedCookieAfterLoginByToken._rotating_response("rotated.sig")
        with _transport(rotating):
            api.verify_token("123456", "pw")
        self.assertEqual(api._session_cookie, "rotated.sig")
        sio.connect.assert_called_once()
        self.assertEqual(sio.connect.call_args.kwargs["headers"]["Cookie"],
                         "better-auth.session_token=rotated.sig")

    def test_disable_2fa_adopts_rotated_cookie_and_reopens_socket(self):
        api, sio = self._logged_in()
        rotating = TestRotatedCookieAfterLoginByToken._rotating_response("rotated.sig")
        with _transport(rotating):
            api.disable_2fa("pw")
        self.assertEqual(api._session_cookie, "rotated.sig")
        self.assertEqual(sio.connect.call_args.kwargs["headers"]["Cookie"],
                         "better-auth.session_token=rotated.sig")

    def test_unchanged_cookie_does_not_reopen_socket(self):
        api, sio = self._logged_in()
        with _transport(_response(200, {"status": True})):
            api.twofa_status()
        self.assertEqual(api._session_cookie, TOKEN)
        sio.connect.assert_not_called()

    def test_logout_still_clears_session(self):
        api, sio = self._logged_in()
        with _transport(_response(200, {"success": True})):
            api.logout()
        self.assertIsNone(api._session_cookie)
        sio.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
