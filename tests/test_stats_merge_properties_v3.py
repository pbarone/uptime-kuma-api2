"""
Property test for stats replacement on Uptime Kuma 3.x.
No live server: socketio.Client and connect are patched.
"""
from unittest.mock import MagicMock, patch
from hypothesis import assume, given, settings, strategies as st
from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.event import Event
URL = "http://kuma.invalid:3001"
def _make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        client_cls.return_value = MagicMock()
        api = UptimeKumaApi(URL)
    api.wait_events = 0
    return api
_ping = st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False))
_uptime = st.floats(min_value=0, max_value=1)
_record = st.fixed_dictionaries({
    "avgPing": _ping,
    "avgPing720": _ping,
    "avgPing1y": _ping,
    "uptime24": _uptime,
    "uptime720": _uptime,
    "uptime1y": _uptime,
})
# ids come from a small pool so the two payloads overlap often
_ids = st.integers(min_value=1, max_value=12)
_payload = st.dictionaries(_ids.map(str), _record, min_size=1, max_size=12)
# Feature: uptime-kuma-v3-support, Property 3: A later stats event for a
# monitor id replaces that id's values and leaves other ids untouched
# Validates: Requirements 14.4
@settings(max_examples=200, deadline=None)
@given(first=_payload, later=_payload)
def test_property_3_stats_replacement(first, later):
    first_ids = {int(k) for k in first}
    later_ids = {int(k) for k in later}
    # P2 has at least one id in P1 and at least one id absent from P1
    assume(first_ids & later_ids)
    assume(later_ids - first_ids)
    api = _make_api()
    # non-empty monitor list so the no-monitors short-circuit does not fire
    api._event_data[Event.MONITOR_LIST] = {1: {}}
    api._event_stats(first)
    api._event_stats(later)
    expected = {int(k): v for k, v in first.items()}
    expected.update({int(k): v for k, v in later.items()})
    cache = api._event_data[Event.STATS]
    assert set(cache.keys()) == first_ids | later_ids
    assert cache == expected
    uptime = api.uptime()
    avg_ping = api.avg_ping()
    assert set(uptime.keys()) == first_ids | later_ids
    assert set(avg_ping.keys()) == first_ids | later_ids
    for monitor_id, record in expected.items():
        assert uptime[monitor_id] == {
            24: record["uptime24"],
            720: record["uptime720"],
            "1y": record["uptime1y"],
        }
        assert avg_ping[monitor_id] == {
            24: record["avgPing"],
            720: record["avgPing720"],
            "1y": record["avgPing1y"],
        }
