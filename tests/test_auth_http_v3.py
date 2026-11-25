"""
Unit tests for the Better Auth HTTP helper (_auth_request) and the cookie
reconnect (_reconnect_with_cookie). No live server: requests.Session.send and
socketio.Client are patched.
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException, Timeout
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"
SECRET_COOKIE = "s3cr3t-cookie-value"
SECRET_BODY = {"password": "hunter2-password", "code": "987654"}


def _make_api(**kwargs):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, **kwargs)
    return api, sio


def _response(status=200, body=b'{"ok": true}'):
    r = requests.Response()
    r.status_code = status
    r._content = body
    r.url = URL
    return r


def _sent(send_mock):
    return send_mock.call_args.args[0], send_mock.call_args.kwargs


class TestAuthRequest(unittest.TestCase):
    def test_origin_always_sent(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send", return_value=_response()) as send:
            api._auth_request("POST", "/api/auth/x", {"a": 1})
            api._auth_request("GET", "/api/auth/y")
        for call in send.call_args_list:
            self.assertEqual(call.args[0].headers["Origin"], URL)

    def test_url_method_and_json_body(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send", return_value=_response()) as send:
            body = api._auth_request("POST", "/api/auth/x", {"a": 1})
        prep, _ = _sent(send)
        self.assertEqual(prep.method, "POST")
        self.assertEqual(prep.url, f"{URL}/api/auth/x")
        self.assertEqual(prep.body, b'{"a": 1}')
        self.assertEqual(body, {"ok": True})

    def test_timeout_and_verify_forwarded(self):
        api, _ = _make_api(timeout=7, ssl_verify=False)
        with patch.object(requests.Session, "send", return_value=_response()) as send:
            api._auth_request("GET", "/api/auth/x")
        _, kwargs = _sent(send)
        self.assertEqual(kwargs["timeout"], 7)
        self.assertIs(kwargs["verify"], False)

    def test_custom_headers_forwarded(self):
        api, _ = _make_api(headers={"X-Custom": "yes"})
        with patch.object(requests.Session, "send", return_value=_response()) as send:
            api._auth_request("GET", "/api/auth/x")
        prep, _ = _sent(send)
        self.assertEqual(prep.headers["X-Custom"], "yes")
        self.assertEqual(prep.headers["Origin"], URL)

    def test_429_message_names_rate_limit(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          return_value=_response(429, b"<html>slow down</html>")):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("POST", "/api/auth/x", SECRET_BODY)
        self.assertIn("rate limit", str(cm.exception))

    def test_non_json_body(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          return_value=_response(200, b"<html>not json</html>")):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("GET", "/api/auth/x")
        self.assertNotIn("not json", str(cm.exception))

    def test_non_2xx_with_message(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          return_value=_response(401, b'{"message": "Invalid credentials"}')):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("POST", "/api/auth/x")
        self.assertEqual(str(cm.exception), "Invalid credentials")

    def test_non_2xx_with_msg_key(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          return_value=_response(400, b'{"msg": "bad thing"}')):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("POST", "/api/auth/x")
        self.assertEqual(str(cm.exception), "bad thing")

    def test_non_2xx_without_message(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send", return_value=_response(500, b"{}")):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("POST", "/api/auth/x")
        self.assertIn("/api/auth/x", str(cm.exception))
        self.assertIn("500", str(cm.exception))

    def test_requests_timeout_becomes_timeout(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          side_effect=requests.exceptions.ReadTimeout("slow")):
            with self.assertRaises(Timeout):
                api._auth_request("GET", "/api/auth/x")

    def test_connection_error_becomes_library_exception(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("GET", "/api/auth/x")
        self.assertNotIsInstance(cm.exception, Timeout)
        self.assertIn("ConnectionError", str(cm.exception))

    def test_not_logged_in_raises_before_any_request(self):
        api, _ = _make_api()
        with patch.object(requests.Session, "send") as send:
            with self.assertRaises(UptimeKumaException) as cm:
                api._auth_request("GET", "/api/auth/x", use_session_cookie=True)
        self.assertIn("not logged in", str(cm.exception))
        send.assert_not_called()

    def test_no_secret_in_any_exception_message(self):
        api, _ = _make_api()
        api._session_cookie = SECRET_COOKIE
        leaky = f"cookie={SECRET_COOKIE} password={SECRET_BODY['password']}"
        cases = [
            {"return_value": _response(429, leaky.encode())},
            {"return_value": _response(200, leaky.encode())},
            {"return_value": _response(500, b"{}")},
            {"side_effect": requests.exceptions.ConnectionError(leaky)},
            {"side_effect": requests.exceptions.ReadTimeout(leaky)},
        ]
        for case in cases:
            with patch.object(requests.Session, "send", **case):
                with self.assertRaises(UptimeKumaException) as cm:
                    api._auth_request("POST", "/api/auth/x", SECRET_BODY,
                                      use_session_cookie=True)
            text = str(cm.exception)
            for secret in (SECRET_COOKIE, SECRET_BODY["password"], SECRET_BODY["code"]):
                self.assertNotIn(secret, text)


class TestReconnectWithCookie(unittest.TestCase):
    def _connect_sets_info(self, api):
        def _connect(*args, **kwargs):
            api._event_data[Event.INFO] = {"version": "3.0.0"}
        return _connect

    def test_cookie_header_sent_on_connect(self):
        api, sio = _make_api(headers={"X-Custom": "yes"})
        sio.connect.side_effect = self._connect_sets_info(api)
        api._reconnect_with_cookie(SECRET_COOKIE)
        sio.disconnect.assert_called_once()
        args, kwargs = sio.connect.call_args
        self.assertEqual(args[0], f"{URL}/socket.io/")
        self.assertEqual(kwargs["headers"], {
            "X-Custom": "yes",
            "Cookie": f"better-auth.session_token={SECRET_COOKIE}",
        })
        self.assertEqual(kwargs["wait_timeout"], api.timeout)

    def test_works_without_constructor_headers(self):
        api, sio = _make_api()
        sio.connect.side_effect = self._connect_sets_info(api)
        api._reconnect_with_cookie("abc")
        self.assertEqual(sio.connect.call_args.kwargs["headers"],
                         {"Cookie": "better-auth.session_token=abc"})

    def test_resets_cache_and_flag(self):
        api, sio = _make_api()
        api._event_data[Event.MONITOR_LIST] = [{"id": 1}]
        api._event_data[Event.STATS] = {1: {}}
        api._dashboard_rendered_sent = True
        sio.connect.side_effect = self._connect_sets_info(api)
        api._reconnect_with_cookie("abc")
        self.assertIsNone(api._event_data[Event.MONITOR_LIST])
        self.assertIsNone(api._event_data[Event.STATS])
        self.assertFalse(api._dashboard_rendered_sent)

    def test_leaves_headers_and_public_attributes_unchanged(self):
        api, sio = _make_api(headers={"X-Custom": "yes"}, timeout=5)
        before = (api.url, api.timeout, dict(api.headers), api.wait_events, api.ssl_verify)
        sio.connect.side_effect = self._connect_sets_info(api)
        api._reconnect_with_cookie("abc")
        after = (api.url, api.timeout, dict(api.headers), api.wait_events, api.ssl_verify)
        self.assertEqual(before, after)
        self.assertNotIn("Cookie", api.headers)

    def test_second_reconnect_does_not_accumulate_cookies(self):
        api, sio = _make_api(headers={"X-Custom": "yes"})
        sio.connect.side_effect = self._connect_sets_info(api)
        api._reconnect_with_cookie("first")
        api._reconnect_with_cookie("second")
        headers = sio.connect.call_args.kwargs["headers"]
        self.assertEqual(headers["Cookie"], "better-auth.session_token=second")
        self.assertNotIn("first", str(headers))

    def test_connect_failure_raises_without_secret(self):
        api, sio = _make_api()
        sio.connect.side_effect = Exception(f"boom {SECRET_COOKIE}")
        with self.assertRaises(UptimeKumaException) as cm:
            api._reconnect_with_cookie(SECRET_COOKIE)
        self.assertNotIn(SECRET_COOKIE, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
