"""
Property test for the stats-to-avg_ping mapping on Uptime Kuma 3.x.
No live server: socketio.Client and connect are patched.
"""
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

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

# payload keyed by monitor-id strings, as the server sends it
_payload = st.dictionaries(
    st.integers(min_value=1, max_value=10**6).map(str),
    _record,
    min_size=1,
    max_size=20,
)


# Feature: uptime-kuma-v3-support, Property 2: Stats-to-avg_ping mapping
# preserves every monitor id and carries all three windows
# Validates: Requirements 14.1, 14.3
@settings(max_examples=100, deadline=None)
@given(payload=_payload)
def test_property_2_avg_ping_mapping(payload):
    api = _make_api()
    # non-empty monitor list so the no-monitors short-circuit does not fire
    api._event_data[Event.MONITOR_LIST] = {1: {}}
    api._event_stats(payload)

    result = api.avg_ping()

    assert set(result.keys()) == {int(k) for k in payload}
    for key, record in payload.items():
        entry = result[int(key)]
        assert set(entry.keys()) == {24, 720, "1y"}
        assert entry[24] == record["avgPing"]
        assert entry[720] == record["avgPing720"]
        assert entry["1y"] == record["avgPing1y"]
