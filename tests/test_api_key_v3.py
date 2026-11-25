"""
Unit tests for the API-key methods on Uptime Kuma 3.x (Requirements 10, 11, 12).
No live server: socketio.Client is patched and every callback body mixes a
Native_Key (Better Auth string id) with Legacy_Key entries (``legacy-<n>`` id).
"""
import datetime
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException
from uptime_kuma_api.api import _api_key_status

NATIVE_ID = "kV3p0xYt9QmZ"
LEGACY_ID = "legacy-3"


def _server_keys():
    return [
        {
            "id": NATIVE_ID,
            "name": "native",
            "enabled": True,
            "createdAt": "2026-01-01 10:00:00",
            "expiresAt": None,
        },
        {
            "id": LEGACY_ID,
            "name": "legacy",
            "enabled": False,
            "createdAt": "2025-01-01 10:00:00",
            "expiresAt": "2099-01-01 00:00:00",
        },
        {
            "id": "legacy-4",
            "name": "old",
            "enabled": True,
            "createdAt": "2024-01-01 10:00:00",
            "expiresAt": "2000-01-01 00:00:00",
            "extra": "kept",
        },
    ]


def _make_api(delete_response=None, add_response=None):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi("http://kuma.invalid:3001")

    def call(event, data=None, timeout=None):
        if event == "getAPIKeyList":
            return {"ok": True, "apiKeyList": _server_keys()}
        if event == "addAPIKey":
            return add_response or {"ok": True, "msg": "successAdded",
                                    "key": "uk1_secret", "keyID": "abc123"}
        if event == "deleteAPIKey":
            return delete_response or {"ok": True, "msg": "successDeleted"}
        raise AssertionError("unexpected event " + event)

    sio.call.side_effect = call
    # Fail loudly if anything falls back to waiting on a pushed event.
    api._get_event_data = MagicMock(side_effect=AssertionError("event wait"))
    return api, sio


def _emitted(sio):
    return [c.args[0] for c in sio.call.call_args_list]


class TestGetApiKeys(unittest.TestCase):
    def test_emits_get_api_key_list_without_event_wait(self):
        api, sio = _make_api()
        api.get_api_keys()
        self.assertEqual(_emitted(sio), ["getAPIKeyList"])
        api._get_event_data.assert_not_called()

    def test_list_shape_and_ids_unchanged(self):
        api, _ = _make_api()
        keys = api.get_api_keys()
        self.assertIsInstance(keys, list)
        self.assertEqual([k["id"] for k in keys], [NATIVE_ID, LEGACY_ID, "legacy-4"])
        native, legacy, expired = keys
        self.assertEqual(native["name"], "native")
        self.assertIs(native["active"], True)
        self.assertEqual(native["createdDate"], "2026-01-01 10:00:00")
        self.assertIsNone(native["expires"])
        self.assertEqual(native["status"], "active")
        self.assertIs(legacy["active"], False)
        self.assertEqual(legacy["expires"], "2099-01-01 00:00:00")
        self.assertEqual(legacy["status"], "inactive")
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["extra"], "kept")
        for k in keys:
            for gone in ("enabled", "createdAt", "expiresAt"):
                self.assertNotIn(gone, k)


class TestGetApiKey(unittest.TestCase):
    def test_get_by_string_ids(self):
        api, _ = _make_api()
        self.assertEqual(api.get_api_key(NATIVE_ID)["name"], "native")
        self.assertEqual(api.get_api_key(LEGACY_ID)["name"], "legacy")

    def test_get_by_int_id_matches_numeric_native_id(self):
        api, sio = _make_api()
        keys = _server_keys()
        keys[0]["id"] = 7
        sio.call.side_effect = lambda e, d=None, timeout=None: {"ok": True, "apiKeyList": keys}
        self.assertEqual(api.get_api_key(7)["name"], "native")
        self.assertEqual(api.get_api_key("7")["name"], "native")

    def test_missing_id_raises(self):
        api, _ = _make_api()
        for missing in ("nope", 99, "legacy-99"):
            with self.assertRaises(UptimeKumaException) as ctx:
                api.get_api_key(missing)
            self.assertIn("api key does not exist", str(ctx.exception))


class TestAddApiKey(unittest.TestCase):
    def test_add_without_active(self):
        api, sio = _make_api()
        r = api.add_api_key("ci", "2099-01-01 00:00:00")
        self.assertEqual(_emitted(sio), ["addAPIKey"])
        self.assertEqual(sio.call.call_args.args[1],
                         {"name": "ci", "expires": "2099-01-01 00:00:00"})
        self.assertEqual(r["key"], "uk1_secret")
        self.assertEqual(r["keyID"], "abc123")
        self.assertIsInstance(r["keyID"], str)

    def test_expires_defaults_to_none(self):
        api, sio = _make_api()
        api.add_api_key("ci")
        self.assertEqual(sio.call.call_args.args[1], {"name": "ci", "expires": None})

    def test_active_parameter_removed(self):
        api, _ = _make_api()
        with self.assertRaises(TypeError):
            api.add_api_key("ci", None, True)
        with self.assertRaises(TypeError):
            api.add_api_key("ci", active=True)


class TestDeleteApiKey(unittest.TestCase):
    def test_delete_legacy_and_native_emit_string_id(self):
        for id_ in (LEGACY_ID, NATIVE_ID):
            api, sio = _make_api()
            r = api.delete_api_key(id_)
            self.assertEqual(_emitted(sio), ["getAPIKeyList", "deleteAPIKey"])
            sent = sio.call.call_args.args[1]
            self.assertEqual(sent, id_)
            self.assertIsInstance(sent, str)
            self.assertEqual(r["msg"], "successDeleted")

    def test_int_id_is_sent_as_string(self):
        api, sio = _make_api()
        keys = _server_keys()
        keys[0]["id"] = 7

        def call(event, data=None, timeout=None):
            if event == "getAPIKeyList":
                return {"ok": True, "apiKeyList": keys}
            return {"ok": True, "msg": "successDeleted"}

        sio.call.side_effect = call
        api.delete_api_key(7)
        self.assertEqual(sio.call.call_args.args, ("deleteAPIKey", "7"))

    def test_unknown_id_raises_before_emitting(self):
        api, sio = _make_api()
        with self.assertRaises(UptimeKumaException) as ctx:
            api.delete_api_key("nope")
        self.assertIn("api key does not exist", str(ctx.exception))
        self.assertNotIn("deleteAPIKey", _emitted(sio))


class TestRemovedMethods(unittest.TestCase):
    def test_enable_disable_absent(self):
        api, _ = _make_api()
        with self.assertRaises(AttributeError):
            api.enable_api_key
        with self.assertRaises(AttributeError):
            api.disable_api_key
        self.assertFalse(hasattr(UptimeKumaApi, "enable_api_key"))
        self.assertFalse(hasattr(UptimeKumaApi, "disable_api_key"))

    def test_no_enable_disable_emit(self):
        import inspect
        import uptime_kuma_api.api as mod
        src = inspect.getsource(mod)
        self.assertNotIn("enableAPIKey", src)
        self.assertNotIn("disableAPIKey", src)


class TestApiKeyStatus(unittest.TestCase):
    def _delta(self, **kw):
        return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**kw)

    def test_past_is_expired_even_if_inactive(self):
        past = self._delta(days=-1).isoformat()
        self.assertEqual(_api_key_status(True, past), "expired")
        self.assertEqual(_api_key_status(False, past), "expired")

    def test_future(self):
        future = self._delta(days=1).isoformat()
        self.assertEqual(_api_key_status(True, future), "active")
        self.assertEqual(_api_key_status(False, future), "inactive")

    def test_naive_string_read_as_utc(self):
        past = (self._delta(hours=-1).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M:%S")
        future = (self._delta(hours=1).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(_api_key_status(True, past), "expired")
        self.assertEqual(_api_key_status(True, future), "active")

    def test_z_suffix(self):
        self.assertEqual(_api_key_status(True, "2000-01-01T00:00:00Z"), "expired")

    def test_unparseable_is_not_expired(self):
        self.assertEqual(_api_key_status(True, "not a date"), "active")
        self.assertEqual(_api_key_status(False, "not a date"), "inactive")

    def test_none_expiry(self):
        self.assertEqual(_api_key_status(True, None), "active")
        self.assertEqual(_api_key_status(False, None), "inactive")


if __name__ == "__main__":
    unittest.main()
