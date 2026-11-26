"""
set_settings on Uptime Kuma 3.x: apiEnabled, prometheusApiEnabled, no dnsCache.

The server's setSettings handler stops the Prometheus sampling when the sent
prometheusApiEnabled differs from the stored one, and a payload without the
key compares as undefined. set_settings therefore sends the current value back
unless the caller passes one. These tests run the real set_settings against a
mocked socket (no live server).
"""
import unittest
from unittest.mock import MagicMock

from uptime_kuma_api import UptimeKumaApi


def _api(current):
    api = MagicMock(spec=UptimeKumaApi)
    api.version = "3.0.0-beta.2"
    api.get_settings = MagicMock(return_value=current)
    api._call = MagicMock(return_value={"msg": "Saved"})
    api.set_settings = UptimeKumaApi.set_settings.__get__(api)
    return api


def _sent(api):
    name, args = api._call.call_args[0]
    assert name == "setSettings"
    return args[0]


class TestSetSettings(unittest.TestCase):
    def test_omitted_prometheus_keeps_stored_value(self):
        for stored in (True, False):
            api = _api({"prometheusApiEnabled": stored})
            api.set_settings()
            self.assertIs(_sent(api)["prometheusApiEnabled"], stored)

    def test_omitted_prometheus_not_stored_sends_no_key(self):
        api = _api({})
        api.set_settings()
        self.assertNotIn("prometheusApiEnabled", _sent(api))

    def test_explicit_prometheus_skips_the_read(self):
        api = _api({"prometheusApiEnabled": True})
        api.set_settings(prometheusApiEnabled=False)
        self.assertIs(_sent(api)["prometheusApiEnabled"], False)
        api.get_settings.assert_not_called()

    def test_api_enabled_sent_only_when_given(self):
        api = _api({})
        api.set_settings()
        self.assertNotIn("apiEnabled", _sent(api))
        api.set_settings(apiEnabled=True)
        self.assertIs(_sent(api)["apiEnabled"], True)
        api.set_settings(apiEnabled=False)
        self.assertIs(_sent(api)["apiEnabled"], False)

    def test_dns_cache_removed(self):
        api = _api({})
        with self.assertRaises(TypeError):
            api.set_settings(dnsCache=True)
        api.set_settings()
        self.assertNotIn("dnsCache", _sent(api))


if __name__ == '__main__':
    unittest.main()
