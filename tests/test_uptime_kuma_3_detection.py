"""
The 2.x library against an Uptime Kuma 3.x server (issue #89).

Uptime Kuma 3.0 removed the socket.io ``login``, ``loginByToken`` and ``setup``
events. Before this fix the library connected, emitted ``login``, waited out the
timeout and raised ``Timeout`` with nothing saying the server version was the
cause. Now ``login``, ``login_by_token`` and ``setup`` first check
``GET /api/auth/ok`` (JSON ``{"ok": true}`` on 3.x, the web UI's HTML on 1.x and
2.x) and raise a clear ``UptimeKumaException`` on 3.x.

The other half matters as much: anything that is not that JSON answer must leave
1.x and 2.x behavior unchanged, including network errors and odd proxies.

No live server: ``connect`` and the socket.io client are patched, and
``requests.get`` is mocked.
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException
from uptime_kuma_api.exceptions import Timeout

URL = "http://kuma.invalid:3001"


def _response(status=200, content_type="application/json", json_body=None,
              json_error=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    if json_error is not None:
        r.json.side_effect = json_error
    else:
        r.json.return_value = json_body
    return r


V3_RESPONSE = _response(json_body={"ok": True})
V2_HTML_RESPONSE = _response(content_type="text/html; charset=utf-8",
                             json_error=ValueError("not json"))


class _ApiMixin:
    def _api(self, **kwargs):
        with patch("uptime_kuma_api.api.UptimeKumaApi.connect"), \
                patch("uptime_kuma_api.api.socketio.Client") as client_cls:
            client_cls.return_value = MagicMock()
            api = UptimeKumaApi(URL, **kwargs)
        api._call = MagicMock(return_value={"token": "t"})
        return api


@patch("uptime_kuma_api.api.requests.get")
class TestUptimeKuma3Refused(_ApiMixin, unittest.TestCase):
    """On a 3.x server, the auth entry points raise before emitting anything."""

    def _assert_refused(self, call):
        with self.assertRaises(UptimeKumaException) as ctx:
            call()
        message = str(ctx.exception)
        self.assertIn("Uptime Kuma 3.x", message)
        self.assertIn("uptime-kuma-api2>=3", message)
        # Not the Timeout subclass: the point is to stop waiting it out.
        self.assertNotIsInstance(ctx.exception, Timeout)

    def test_login_refused(self, get):
        get.return_value = V3_RESPONSE
        api = self._api()
        self._assert_refused(lambda: api.login("admin", "secret"))
        api._call.assert_not_called()

    def test_autologin_refused(self, get):
        """login() without credentials would otherwise wait for autoLogin."""
        get.return_value = V3_RESPONSE
        api = self._api()
        self._assert_refused(lambda: api.login())

    def test_login_by_token_refused(self, get):
        get.return_value = V3_RESPONSE
        api = self._api()
        self._assert_refused(lambda: api.login_by_token("token"))
        api._call.assert_not_called()

    def test_setup_refused(self, get):
        get.return_value = V3_RESPONSE
        api = self._api()
        self._assert_refused(lambda: api.setup("admin", "secret"))
        api._call.assert_not_called()

    def test_extra_keys_in_ok_response_still_detected(self, get):
        get.return_value = _response(json_body={"ok": True, "extra": 1})
        api = self._api()
        self._assert_refused(lambda: api.login("admin", "secret"))

    def test_request_uses_instance_settings(self, get):
        get.return_value = V3_RESPONSE
        api = self._api(timeout=7, headers={"X-Proxy": "1"}, ssl_verify=False)
        with self.assertRaises(UptimeKumaException):
            api.login("admin", "secret")
        get.assert_called_once_with(
            f"{URL}/api/auth/ok",
            headers={"X-Proxy": "1"},
            timeout=7,
            verify=False,
        )

    def test_checked_once_per_instance(self, get):
        get.return_value = V3_RESPONSE
        api = self._api()
        for _ in range(3):
            with self.assertRaises(UptimeKumaException):
                api.login("admin", "secret")
        self.assertEqual(get.call_count, 1)


@patch("uptime_kuma_api.api.requests.get")
class TestOlderServersUnchanged(_ApiMixin, unittest.TestCase):
    """Anything that isn't the 3.x JSON answer keeps today's behavior."""

    NOT_V3 = {
        "2.x / 1.x web UI html": V2_HTML_RESPONSE,
        "404": _response(status=404, content_type="text/html", json_body=None),
        "500 with json": _response(status=500, json_body={"ok": True}),
        "json ok false": _response(json_body={"ok": False}),
        "json ok truthy string": _response(json_body={"ok": "true"}),
        "json list": _response(json_body=[{"ok": True}]),
        "json content type, bad body": _response(json_error=ValueError("bad")),
        "html with json-looking body": _response(content_type="text/html",
                                                 json_body={"ok": True}),
    }

    def test_login_proceeds_for_every_non_v3_answer(self, get):
        for label, response in self.NOT_V3.items():
            with self.subTest(response=label):
                get.return_value = response
                api = self._api()
                self.assertEqual(api.login("admin", "secret"), {"token": "t"})
                api._call.assert_called_once_with("login", {
                    "username": "admin", "password": "secret", "token": "",
                })

    def test_login_proceeds_on_request_errors(self, get):
        errors = [
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.Timeout("slow"),
            requests.exceptions.SSLError("bad cert"),
            RuntimeError("anything else"),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                get.side_effect = error
                api = self._api()
                self.assertEqual(api.login("admin", "secret"), {"token": "t"})

    def test_login_by_token_and_setup_proceed(self, get):
        get.return_value = V2_HTML_RESPONSE
        api = self._api()
        api.login_by_token("token")
        api.setup("admin", "secret")
        self.assertEqual(
            [c.args[0] for c in api._call.call_args_list],
            ["loginByToken", "setup"],
        )

    def test_no_request_until_an_auth_call(self, get):
        """Constructing the client adds no HTTP request."""
        self._api()
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
