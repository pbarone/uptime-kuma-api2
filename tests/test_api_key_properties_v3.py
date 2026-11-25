"""
Property test for get_api_keys() normalization. No live server: the
socketio.Client is patched and sio.call returns the generated payload.
"""
import copy
import unittest
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

from uptime_kuma_api import UptimeKumaApi

URL = "http://kuma.invalid:3001"


def _make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL)
    return api, sio


_native_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1, max_size=24)
_legacy_id = st.integers(min_value=0, max_value=10**6).map(lambda n: "legacy-%d" % n)
_expiry = st.one_of(
    st.none(),
    st.just("2000-01-01T00:00:00.000Z"),
    st.just("2999-01-01T00:00:00.000Z"),
    st.just("2023-03-30 12:20:00"),
    st.just("not-a-date"),
)
_entry = st.fixed_dictionaries(
    {
        "id": st.one_of(_native_id, _legacy_id),
        "name": st.text(alphabet="abcdef", max_size=8),
        "enabled": st.booleans(),
        "createdAt": st.just("2026-03-20T11:15:05.000Z"),
        "expiresAt": _expiry,
    },
    optional={"start": st.just("uk_9X")},
)


class ApiKeyListNormalizationProperty(unittest.TestCase):
    # Feature: uptime-kuma-v3-support, Property 4: API-key list normalization
    # preserves id and derives active from enabled
    # Validates: Requirements 10.1, 10.2, 10.3
    @settings(max_examples=200, deadline=None)
    @given(st.lists(_entry, max_size=10))
    def test_normalization(self, entries):
        api, sio = _make_api()
        sio.call.return_value = {"apiKeyList": copy.deepcopy(entries)}

        result = api.get_api_keys()

        self.assertEqual(sio.call.call_args[0][0], "getAPIKeyList")
        self.assertEqual(len(result), len(entries))
        for sent, got in zip(entries, result):
            self.assertEqual(got["id"], sent["id"])
            self.assertIs(got["active"], sent["enabled"])
            self.assertEqual(got["createdDate"], sent["createdAt"])
            self.assertEqual(got["expires"], sent["expiresAt"])
            self.assertNotIn("enabled", got)
            self.assertEqual(got.get("start"), sent.get("start"))
            self.assertEqual(got["name"], sent["name"])
            self.assertIn(got["status"], ("active", "inactive", "expired"))


if __name__ == "__main__":
    unittest.main()
