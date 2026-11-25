"""
Unit tests for need_setup(), setup() and change_password(). No live server: the
requests transport adapter and socketio.Client are patched, so the real cookie
jar runs and the Cookie and Origin headers on the wire can be asserted.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from requests.adapters import HTTPAdapter

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"
TOKEN = "sess-value.signature"


def _make_api(**kwargs):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, **kwargs)

    def fake_connect(*args, **kw):
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


def _body(req):
    return json.loads(req.body)


class TestNeedSetup(unittest.TestCase):
    def test_emits_need_setup_and_returns_value(self):
        for value in (True, False):
            api, sio = _make_api()
            sio.call.return_value = value
            with _transport() as send:
                self.assertIs(api.need_setup(), value)
            send.assert_not_called()
            self.assertEqual(sio.call.call_args.args[0], "needSetup")


class TestSetup(unittest.TestCase):
    def test_request_shape(self):
        api, _ = _make_api()
        with _transport(_response(200, {"ok": True})) as send:
            api.setup("admin", "pw123456")
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/setup")
        self.assertEqual(_body(req), {"username": "admin", "password": "pw123456"})
        self.assertEqual(req.headers["Origin"], URL)
        self.assertNotIn("Cookie", req.headers)

    def test_return_shape(self):
        api, _ = _make_api()
        with _transport(_response(200, {})):
            self.assertEqual(api.setup("a", "b"), {"msg": "Added Successfully."})

    def test_needs_no_session_cookie(self):
        api, _ = _make_api()
        self.assertIsNone(api._session_cookie)
        with _transport(_response(200, {})) as send:
            api.setup("a", "b")
        self.assertEqual(send.call_count, 1)

    def test_error_message_from_server(self):
        api, _ = _make_api()
        with _transport(_response(400, {"message": "already set up"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.setup("a", "pw-secret")
        self.assertEqual(str(cm.exception), "already set up")
        self.assertNotIn("pw-secret", str(cm.exception))

    def test_error_msg_key_and_fallback(self):
        api, _ = _make_api()
        with _transport(_response(403, {"msg": "forbidden"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.setup("a", "b")
        self.assertEqual(str(cm.exception), "forbidden")
        with _transport(_response(500, {})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.setup("a", "b")
        self.assertIn("500", str(cm.exception))


class TestChangePassword(unittest.TestCase):
    def _logged_in(self):
        api, sio = _make_api()
        api.login_by_token(TOKEN)
        return api, sio

    def test_request_shape(self):
        api, _ = self._logged_in()
        with _transport(_response(200, {"status": True})) as send:
            api.change_password("old-pw", "new-pw")
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/change-password")
        self.assertEqual(_body(req), {
            "currentPassword": "old-pw",
            "newPassword": "new-pw",
            "revokeOtherSessions": False,
        })
        self.assertIs(_body(req)["revokeOtherSessions"], False)
        self.assertEqual(req.headers["Origin"], URL)
        self.assertIn(f"better-auth.session_token={TOKEN}", req.headers["Cookie"])

    def test_return_shape(self):
        api, _ = self._logged_in()
        with _transport(_response(200, {})):
            self.assertEqual(
                api.change_password("a", "b"),
                {"msg": "Password has been updated successfully."},
            )

    def test_not_logged_in_raises_without_http_call(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.change_password("a", "b")
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_error_message_from_server(self):
        api, _ = self._logged_in()
        with _transport(_response(400, {"message": "Invalid password"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.change_password("wrong-old", "new-pw")
        self.assertEqual(str(cm.exception), "Invalid password")
        self.assertNotIn("wrong-old", str(cm.exception))
        self.assertNotIn("new-pw", str(cm.exception))

    def test_unauthorized_status_raises(self):
        api, _ = self._logged_in()
        with _transport(_response(401, {"message": "Unauthorized"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.change_password("a", "b")
        self.assertEqual(str(cm.exception), "Unauthorized")

    def test_session_kept_after_failure(self):
        api, _ = self._logged_in()
        with _transport(_response(400, {"message": "bad"})):
            with self.assertRaises(UptimeKumaException):
                api.change_password("a", "b")
        self.assertEqual(api._session_cookie, TOKEN)


if __name__ == "__main__":
    unittest.main()
