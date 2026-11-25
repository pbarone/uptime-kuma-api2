"""
Unit tests for the Uptime Kuma 3.x session/stats/heartbeatLists events and the
connection cache reset. No live server: socketio.Client and connect are patched.
"""
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"


def _make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL)
    return api, sio


class TestEventMembers(unittest.TestCase):
    def test_wire_names(self):
        self.assertEqual(Event.SESSION.value, "session")
        self.assertEqual(Event.STATS.value, "stats")
        self.assertEqual(Event.HEARTBEAT_LISTS.value, "heartbeatLists")


class TestHandlerRegistration(unittest.TestCase):
    def test_handlers_registered(self):
        api, sio = _make_api()
        registered = {c.args[0]: c.args[1] for c in sio.on.call_args_list}
        self.assertEqual(registered[Event.SESSION], api._event_session)
        self.assertEqual(registered[Event.STATS], api._event_stats)
        self.assertEqual(registered[Event.HEARTBEAT_LISTS], api._event_heartbeat_lists)


class TestSession(unittest.TestCase):
    def test_session_sets_slot(self):
        api, _ = _make_api()
        self.assertIsNone(api._event_data[Event.SESSION])
        api._event_session()
        self.assertTrue(api._event_data[Event.SESSION])


class TestStats(unittest.TestCase):
    def test_first_event_uses_int_ids(self):
        api, _ = _make_api()
        api._event_stats({"1": {"uptime24": 1}, "2": {"uptime24": 0.5}})
        self.assertEqual(api._event_data[Event.STATS],
                         {1: {"uptime24": 1}, 2: {"uptime24": 0.5}})

    def test_replaces_only_carried_ids(self):
        api, _ = _make_api()
        api._event_stats({"1": {"uptime24": 1}, "2": {"uptime24": 0.5}})
        before = api._event_data[Event.STATS]
        api._event_stats({"2": {"uptime24": 0.9}, "3": {"uptime24": 0.1}})
        after = api._event_data[Event.STATS]
        self.assertEqual(after, {1: {"uptime24": 1}, 2: {"uptime24": 0.9},
                                 3: {"uptime24": 0.1}})
        # a new dict is bound; the earlier snapshot is not mutated
        self.assertIsNot(before, after)
        self.assertEqual(before, {1: {"uptime24": 1}, 2: {"uptime24": 0.5}})


class TestHeartbeatLists(unittest.TestCase):
    def test_fills_empty_cache(self):
        api, _ = _make_api()
        api._event_heartbeat_lists({"1": [{"status": 1}]})
        self.assertEqual(api._event_data[Event.HEARTBEAT_LIST], {1: [{"status": 1}]})

    def test_never_overwrites_existing_id(self):
        api, _ = _make_api()
        api._event_data[Event.HEARTBEAT_LIST] = {1: [{"status": 0}]}
        api._event_heartbeat_lists({"1": [{"status": 1}], "2": [{"status": 1}]})
        self.assertEqual(api._event_data[Event.HEARTBEAT_LIST],
                         {1: [{"status": 0}], 2: [{"status": 1}]})


class TestResetEventCache(unittest.TestCase):
    def test_clears_every_slot_and_flag(self):
        api, _ = _make_api()
        for key in api._event_data:
            api._event_data[key] = "stale"
        api._dashboard_rendered_sent = True
        api._reset_event_cache()
        self.assertTrue(api._event_data)
        self.assertIn(Event.STATS, api._event_data)
        self.assertIn(Event.SESSION, api._event_data)
        for key, value in api._event_data.items():
            self.assertIsNone(value, key)
        self.assertFalse(api._dashboard_rendered_sent)

    def test_flag_initialized_false(self):
        api, _ = _make_api()
        self.assertFalse(api._dashboard_rendered_sent)


if __name__ == "__main__":
    unittest.main()
