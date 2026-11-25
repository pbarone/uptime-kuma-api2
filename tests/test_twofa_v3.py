"""
Unit tests for the two-factor methods (twofa_status, prepare_2fa, verify_token,
save_2fa, disable_2fa). No live server: the requests transport adapter and
socketio.Client are patched, so the real cookie jar runs and the Cookie and
Origin headers on the wire can be asserted.
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
PASSWORD = "pw-secret-123"
CODE = "424242"
TOTP_URI = "otpauth://totp/Uptime%20Kuma:admin?secret=ABCDEF"


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


def _logged_in():
    api, sio = _make_api()
    api.login_by_token(TOKEN)
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


def _session(enabled):
    return {"session": {"id": "s"}, "user": {"id": "u", "twoFactorEnabled": enabled}}


def _assert_cookie_and_origin(test, req):
    test.assertEqual(req.headers["Origin"], URL)
    test.assertIn(f"better-auth.session_token={TOKEN}", req.headers["Cookie"])


class TestTwofaStatus(unittest.TestCase):
    def test_request_shape(self):
        api, _ = _logged_in()
        with _transport(_response(200, _session(True))) as send:
            api.twofa_status()
        (req,) = _requests(send)
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.url, f"{URL}/api/auth/get-session")
        self.assertIsNone(req.body)
        _assert_cookie_and_origin(self, req)

    def test_return_shapes(self):
        cases = [
            (_session(True), True),
            (_session(False), False),
            ({"user": {"id": "u"}}, False),
            ({"user": None}, False),
            ({}, False),
        ]
        for body, expected in cases:
            api, _ = _logged_in()
            with _transport(_response(200, body)):
                self.assertEqual(api.twofa_status(), {"status": expected})

    def test_not_logged_in(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.twofa_status()
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_error_message_from_server(self):
        api, _ = _logged_in()
        with _transport(_response(401, {"message": "Unauthorized"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.twofa_status()
        self.assertEqual(str(cm.exception), "Unauthorized")


class TestPrepare2fa(unittest.TestCase):
    def test_request_shape(self):
        api, _ = _logged_in()
        with _transport(_response(200, {"totpURI": TOTP_URI, "backupCodes": ["a"]})) as send:
            api.prepare_2fa(PASSWORD)
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/two-factor/enable")
        self.assertEqual(_body(req), {"password": PASSWORD})
        _assert_cookie_and_origin(self, req)

    def test_return_shape_excludes_backup_codes(self):
        api, _ = _logged_in()
        with _transport(_response(200, {"totpURI": TOTP_URI, "backupCodes": ["a"]})):
            self.assertEqual(api.prepare_2fa(PASSWORD), {"uri": TOTP_URI})

    def test_missing_uri_raises(self):
        api, _ = _logged_in()
        with _transport(_response(200, {"backupCodes": []})):
            with self.assertRaises(UptimeKumaException):
                api.prepare_2fa(PASSWORD)

    def test_not_logged_in(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.prepare_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_error_message_from_server_without_secrets(self):
        api, _ = _logged_in()
        with _transport(_response(400, {"message": "Invalid password"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.prepare_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "Invalid password")
        self.assertNotIn(PASSWORD, str(cm.exception))
        self.assertNotIn(TOKEN, str(cm.exception))


class TestVerifyToken(unittest.TestCase):
    def test_request_shape_sends_code_only(self):
        api, _ = _logged_in()
        with _transport(_response(200, {"status": True})) as send:
            api.verify_token(CODE, PASSWORD)
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/two-factor/verify-totp")
        self.assertEqual(_body(req), {"code": CODE})
        self.assertNotIn(PASSWORD, req.body.decode())
        _assert_cookie_and_origin(self, req)

    def test_return_shape(self):
        api, _ = _logged_in()
        with _transport(_response(200, {})):
            self.assertEqual(api.verify_token(CODE, PASSWORD), {"valid": True})

    def test_not_logged_in(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.verify_token(CODE, PASSWORD)
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_rejected_code_raises_with_server_message(self):
        api, _ = _logged_in()
        with _transport(_response(400, {"message": "Invalid code"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.verify_token(CODE, PASSWORD)
        self.assertEqual(str(cm.exception), "Invalid code")
        self.assertNotIn(CODE, str(cm.exception))
        self.assertNotIn(PASSWORD, str(cm.exception))


class TestSave2fa(unittest.TestCase):
    def test_request_shape_is_session_read_without_password(self):
        api, _ = _logged_in()
        with _transport(_response(200, _session(True))) as send:
            api.save_2fa(PASSWORD)
        (req,) = _requests(send)
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.url, f"{URL}/api/auth/get-session")
        self.assertIsNone(req.body)
        self.assertNotIn(PASSWORD, req.url)
        _assert_cookie_and_origin(self, req)

    def test_return_shape_when_enabled(self):
        api, _ = _logged_in()
        with _transport(_response(200, _session(True))):
            self.assertEqual(api.save_2fa(PASSWORD), {"msg": "2FA Enabled."})

    def test_raises_when_not_verified(self):
        api, _ = _logged_in()
        with _transport(_response(200, _session(False))):
            with self.assertRaises(UptimeKumaException) as cm:
                api.save_2fa(PASSWORD)
        self.assertIn("verify_token", str(cm.exception))
        self.assertNotIn(PASSWORD, str(cm.exception))

    def test_not_logged_in(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.save_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_error_message_from_server(self):
        api, _ = _logged_in()
        with _transport(_response(500, {"message": "boom"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.save_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "boom")


class TestDisable2fa(unittest.TestCase):
    def test_request_shape(self):
        api, _ = _logged_in()
        with _transport(_response(200, {"status": True})) as send:
            api.disable_2fa(PASSWORD)
        (req,) = _requests(send)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, f"{URL}/api/auth/two-factor/disable")
        self.assertEqual(_body(req), {"password": PASSWORD})
        _assert_cookie_and_origin(self, req)

    def test_return_shape(self):
        api, _ = _logged_in()
        with _transport(_response(200, {})):
            self.assertEqual(api.disable_2fa(PASSWORD), {"msg": "2FA Disabled."})

    def test_not_logged_in(self):
        api, _ = _make_api()
        with _transport() as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api.disable_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "the session is not logged in")
        send.assert_not_called()

    def test_error_message_from_server_without_secrets(self):
        api, _ = _logged_in()
        with _transport(_response(400, {"message": "Invalid password"})):
            with self.assertRaises(UptimeKumaException) as cm:
                api.disable_2fa(PASSWORD)
        self.assertEqual(str(cm.exception), "Invalid password")
        self.assertNotIn(PASSWORD, str(cm.exception))


class TestEnableSequence(unittest.TestCase):
    def test_prepare_verify_save(self):
        api, _ = _logged_in()
        with _transport(
            _response(200, {"totpURI": TOTP_URI, "backupCodes": []}),
            _response(200, {"status": True}),
            _response(200, _session(True)),
        ) as send:
            self.assertEqual(api.prepare_2fa(PASSWORD), {"uri": TOTP_URI})
            self.assertEqual(api.verify_token(CODE, PASSWORD), {"valid": True})
            self.assertEqual(api.save_2fa(PASSWORD), {"msg": "2FA Enabled."})
        reqs = _requests(send)
        self.assertEqual(
            [(r.method, r.url) for r in reqs],
            [
                ("POST", f"{URL}/api/auth/two-factor/enable"),
                ("POST", f"{URL}/api/auth/two-factor/verify-totp"),
                ("GET", f"{URL}/api/auth/get-session"),
            ],
        )
        for req in reqs:
            _assert_cookie_and_origin(self, req)
        # the password goes out once, on enable, and never on verify or save
        self.assertIn(PASSWORD, reqs[0].body.decode())
        self.assertNotIn(PASSWORD, reqs[1].body.decode())
        self.assertIsNone(reqs[2].body)

    def test_save_without_verify_raises(self):
        api, _ = _logged_in()
        with _transport(
            _response(200, {"totpURI": TOTP_URI}),
            _response(200, _session(False)),
        ):
            api.prepare_2fa(PASSWORD)
            with self.assertRaises(UptimeKumaException):
                api.save_2fa(PASSWORD)


if __name__ == "__main__":
    unittest.main()
