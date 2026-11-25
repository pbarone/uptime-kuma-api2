"""
Property test for uptime() on Uptime Kuma 3.x. No live server: connect and
socketio.Client are patched, and payloads go through the real _event_stats
handler, keyed by monitor-id strings as the server sends them.
"""
from hypothesis import given, settings, strategies as st
from unittest.mock import MagicMock, patch

from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.event import Event

URL = "http://kuma.invalid:3001"

ratios = st.floats(min_value=0, max_value=1, allow_nan=False)
pings = st.one_of(st.none(), st.integers(min_value=0, max_value=60000))

records = st.fixed_dictionaries({
    "avgPing": pings,
    "avgPing720": pings,
    "avgPing1y": pings,
    "uptime24": ratios,
    "uptime720": ratios,
    "uptime1y": ratios,
})

# Zero or more monitors; keys are strings, as on the wire.
stats_payloads = st.dictionaries(
    st.integers(min_value=1, max_value=10 ** 6).map(str), records, max_size=15
)


def make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        client_cls.return_value = MagicMock()
        api = UptimeKumaApi(URL)
    api.wait_events = 0
    api.timeout = 1
    # A non-empty monitor list stops _get_event_data returning [] early.
    api._event_data[Event.MONITOR_LIST] = {1: {"id": 1}}
    return api


# Feature: uptime-kuma-v3-support, Property 1: for all stats payloads, uptime()
# returns the same monitor ids with uptime24/720/1y mapped to 24/720/"1y"
# Validates: Requirements 14.1, 14.2, 14.4
@settings(max_examples=200, deadline=None)
@given(payload=stats_payloads)
def test_uptime_maps_every_monitor_and_field(payload):
    api = make_api()
    api._event_stats(payload)

    result = api.uptime()

    assert set(result) == {int(k) for k in payload}
    for key, rec in payload.items():
        assert result[int(key)] == {
            24: rec["uptime24"],
            720: rec["uptime720"],
            "1y": rec["uptime1y"],
        }
