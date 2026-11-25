"""
Property test for the heartbeatLists merge (Requirement 16.2). No live server:
socketio.Client and connect are patched, as in test_heartbeat_lists_v3.py.
"""
import copy
import unittest
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"


def _make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        client_cls.return_value = MagicMock()
        return UptimeKumaApi(URL, timeout=0.5, wait_events=0)


def _heartbeat(monitor_id):
    return st.fixed_dictionaries({
        "monitorID": st.just(monitor_id),
        "status": st.sampled_from([0, 1, 2, 3]),
        "time": st.text(alphabet="0123456789-: ", min_size=1, max_size=19),
        "msg": st.text(alphabet="abcdef ", max_size=10),
        "ping": st.one_of(st.none(), st.integers(0, 5000)),
        "important": st.sampled_from([0, 1]),
        "duration": st.integers(0, 3600),
    })


_ids = st.integers(1, 12)


@st.composite
def _scenario(draw):
    live_ids = draw(st.sets(_ids, max_size=8))
    live = []
    for mid in live_ids:
        live.extend(draw(st.lists(_heartbeat(mid), min_size=1, max_size=4)))
    list_ids = draw(st.sets(_ids, max_size=8))
    payload = {str(mid): [draw(_heartbeat(mid))] for mid in list_ids}
    return live, payload


class TestHeartbeatMergeProperty(unittest.TestCase):
    # Property 8: heartbeatLists never overwrites a monitor id heartbeat()
    # already populated  (Validates: Requirements 16.2)
    @settings(max_examples=200, deadline=None)
    @given(_scenario())
    def test_property_8_merge_only_if_absent(self, scenario):
        live, payload = scenario
        api = _make_api()
        for hb in live:
            api._event_heartbeat(copy.deepcopy(hb))
        before = copy.deepcopy(api._event_data[Event.HEARTBEAT_LIST]) or {}
        expected_payload = copy.deepcopy(payload)

        api._event_heartbeat_lists(payload)

        after = api._event_data[Event.HEARTBEAT_LIST]
        self.assertTrue(api._heartbeat_lists_received)
        for mid, rows in before.items():
            self.assertEqual(after[mid], rows)
        for key, rows in expected_payload.items():
            if int(key) not in before:
                self.assertEqual(after[int(key)], rows)
        self.assertEqual(set(after), set(before) | {int(k) for k in payload})


if __name__ == "__main__":
    unittest.main()
