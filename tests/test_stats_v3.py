"""
Unit tests for uptime() and avg_ping() reading the Uptime Kuma 3.x `stats`
event (Requirement 14). No live server: socketio.Client and connect are patched.
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api import Timeout, UptimeKumaApi
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"

PAYLOAD = {
    "1": {"avgPing": 10, "avgPing720": 20, "avgPing1y": 30,
          "uptime24": 1, "uptime720": 0.9, "uptime1y": 0.8},
    "2": {"avgPing": None, "avgPing720": None, "avgPing1y": None,
          "uptime24": 0.5, "uptime720": 0.6, "uptime1y": 0.7},
}


def _make_api(monitors=True):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        client_cls.return_value = MagicMock()
        api = UptimeKumaApi(URL, timeout=0.3, wait_events=0)
    api.timeout = 0.3
    api.wait_events = 0
    api._event_data[Event.MONITOR_LIST] = {1: {}, 2: {}} if monitors else {}
    return api


class TestUptime(unittest.TestCase):
    def test_mapping(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        self.assertEqual(api.uptime(), {
            1: {24: 1, 720: 0.9, "1y": 0.8},
            2: {24: 0.5, 720: 0.6, "1y": 0.7},
        })

    def test_later_event_replaces_only_its_ids(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        api._event_stats({"2": dict(PAYLOAD["2"], uptime24=0.1)})
        result = api.uptime()
        self.assertEqual(result[1][24], 1)
        self.assertEqual(result[2][24], 0.1)

    def test_empty_server_returns_without_waiting(self):
        api = _make_api(monitors=False)
        api.timeout = 5
        start = time.time()
        self.assertEqual(api.uptime(), {})
        self.assertLess(time.time() - start, 1)

    def test_timeout_when_no_stats(self):
        api = _make_api()
        with self.assertRaises(Timeout):
            api.uptime()


class TestAvgPing(unittest.TestCase):
    def test_mapping(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        self.assertEqual(api.avg_ping()[1], {24: 10, 720: 20, "1y": 30})

    def test_none_averages_kept(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        self.assertEqual(api.avg_ping()[2], {24: None, 720: None, "1y": None})

    def test_later_event_replaces_only_its_ids(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        api._event_stats({"1": dict(PAYLOAD["1"], avgPing=99)})
        result = api.avg_ping()
        self.assertEqual(result[1][24], 99)
        self.assertEqual(result[2][24], None)

    def test_empty_server_returns_without_waiting(self):
        api = _make_api(monitors=False)
        api.timeout = 5
        start = time.time()
        self.assertEqual(api.avg_ping(), {})
        self.assertLess(time.time() - start, 1)

    def test_timeout_when_no_stats(self):
        api = _make_api()
        with self.assertRaises(Timeout):
            api.avg_ping()


class TestOldEventsRemoved(unittest.TestCase):
    def test_members_and_handlers_gone(self):
        self.assertFalse(hasattr(Event, "AVG_PING"))
        self.assertFalse(hasattr(Event, "UPTIME"))
        api = _make_api()
        self.assertFalse(hasattr(api, "_event_avg_ping"))
        self.assertFalse(hasattr(api, "_event_uptime"))
        self.assertNotIn("avgPing", [e.value for e in Event])
        self.assertNotIn("uptime", [e.value for e in Event])


class TestStatsRequestsDashboardRendered(unittest.TestCase):
    """Live finding (task 12.4): the server sends `stats` only inside the
    deferred lists, after the client emits dashboardRendered. uptime() and
    avg_ping() called first on a connection never emitted it and timed out."""

    def _emits(self, api):
        return [c.args[0] for c in api.sio.emit.call_args_list]

    def test_uptime_emits_dashboard_rendered(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        api.uptime()
        self.assertEqual(self._emits(api), ["dashboardRendered"])

    def test_avg_ping_emits_dashboard_rendered(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        api.avg_ping()
        self.assertEqual(self._emits(api), ["dashboardRendered"])

    def test_one_emit_across_uptime_and_avg_ping(self):
        api = _make_api()
        api._event_stats(PAYLOAD)
        api.uptime()
        api.avg_ping()
        self.assertEqual(self._emits(api), ["dashboardRendered"])

    def test_no_emit_for_empty_server(self):
        api = _make_api(monitors=False)
        self.assertEqual(api.uptime(), {})
        self.assertEqual(self._emits(api), [])

if __name__ == "__main__":
    unittest.main()
