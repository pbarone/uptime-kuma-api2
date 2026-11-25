"""
Unit tests for heartbeat history on Uptime Kuma 3.x (Requirement 16): the
dashboardRendered emit and the heartbeatLists wait. No live server:
socketio.Client and connect are patched, and the server's reply is simulated.
"""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api import Timeout, UptimeKumaApi
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"

COMPACT = {"id": 1, "name": "m1", "type": "http", "active": 1, "interval": 60}
HB = {"monitorID": 1, "status": 1, "important": 0, "time": "t0", "msg": ""}


def _make_api(timeout=0.5):
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi(URL, timeout=timeout, wait_events=0)
    return api, sio


def _later(delay, fn, *args):
    t = threading.Timer(delay, fn, args)
    t.daemon = True
    t.start()
    return t


class TestHeartbeatHistory(unittest.TestCase):
    def test_one_emit_across_two_calls(self):  # 16.1, 16.3
        api, sio = _make_api()
        api._event_monitor_list({"1": COMPACT})
        sio.emit.side_effect = lambda *a, **k: api._event_heartbeat_lists({"1": [dict(HB)]})
        first = api.get_heartbeats()
        second = api.get_heartbeats()
        self.assertEqual(sio.emit.call_count, 1)
        sio.emit.assert_called_with("dashboardRendered")
        self.assertEqual(first, second)
        self.assertEqual(first[1][0]["important"], False)

    def test_empty_server_no_emit_no_wait(self):  # 16.4
        api, sio = _make_api(timeout=5)
        api._event_monitor_list({})
        start = time.time()
        self.assertEqual(api.get_heartbeats(), [])
        self.assertLess(time.time() - start, 1)
        sio.emit.assert_not_called()
        self.assertFalse(api._dashboard_rendered_sent)

    def test_timeout_when_no_heartbeat_lists(self):  # 16.5
        api, sio = _make_api(timeout=0.2)
        api._event_monitor_list({"1": COMPACT})
        with self.assertRaises(Timeout):
            api.get_heartbeats()
        sio.emit.assert_called_once_with("dashboardRendered")

    def test_flag_resets_after_reconnect(self):  # 16.6
        api, sio = _make_api()
        api._event_monitor_list({"1": COMPACT})
        sio.emit.side_effect = lambda *a, **k: api._event_heartbeat_lists({"1": [dict(HB)]})
        api.get_heartbeats()
        api._reset_event_cache()  # what the Auth_Flow reconnect does
        api._event_monitor_list({"1": COMPACT})
        api.get_heartbeats()
        self.assertEqual(sio.emit.call_count, 2)

    def test_important_heartbeats_with_compact_monitors(self):  # 16.1
        api, sio = _make_api()
        api._event_monitor_list({"1": COMPACT})
        row = {"monitorID": 1, "status": 0, "important": 1, "time": "t1", "msg": "down"}
        sio.call.return_value = {"ok": True, "data": [row]}
        r = api.get_important_heartbeats()
        self.assertEqual(list(r), [1])
        self.assertIs(r[1][0]["important"], True)
        self.assertEqual(r[1][0]["msg"], "down")

    def test_live_heartbeat_before_heartbeat_lists(self):  # 16.2 gap
        api, sio = _make_api(timeout=3)
        api._event_monitor_list({"1": COMPACT, "2": dict(COMPACT, id=2)})
        # a live heartbeat for monitor 1 lands right after the emit; the
        # snapshot for both monitors arrives later
        sio.emit.side_effect = lambda *a, **k: (
            api._event_heartbeat(dict(HB)),
            _later(0.3, api._event_heartbeat_lists,
                   {"1": [dict(HB)], "2": [dict(HB, monitorID=2)]}),
        )
        r = api.get_heartbeats()
        self.assertEqual(sorted(r), [1, 2])


if __name__ == "__main__":
    unittest.main()
