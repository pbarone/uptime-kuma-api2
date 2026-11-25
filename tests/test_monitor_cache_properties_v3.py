"""Property test for get_monitors on Uptime Kuma 3.x (compact key set).

Unit test, no live server. The cache is driven through the real event handlers
(``_event_monitor_list`` for the login-time list, ``_event_update_monitor_into_list``
for later deltas), and ``get_monitors`` / ``_get_event_data`` run for real on a
``MagicMock(spec=UptimeKumaApi)``. Only the transport (``_call``) is a mock, so
the test can also assert that no ``getMonitor`` request was made.

**Validates: Requirements 15.1, 15.2**
"""

from unittest.mock import MagicMock

from hypothesis import given, settings, strategies as st

from uptime_kuma_api import Event, MonitorType, UptimeKumaApi
from uptime_kuma_api.api import _COMPACT_MONITOR_FIELDS

REAL_METHODS = (
    "_event_monitor_list",
    "_event_update_monitor_into_list",
    "_get_event_data",
    "get_monitors",
)

# Realistic type strings, so parse_monitor_type accepts them.
TYPES = [MonitorType.HTTP.value, MonitorType.PORT.value, MonitorType.PING.value,
         MonitorType.GROUP.value, MonitorType.DNS.value]

EXTRA_KEYS = ["notificationIDList", "url", "authMethod", "hostname", "port",
              "method", "maxretries", "description", "accepted_statuscodes"]

_scalar = st.one_of(st.none(), st.integers(-5, 5000), st.text(max_size=8))


@st.composite
def monitor_entries(draw):
    """A list of (id, entry) with unique ids; entries are compact or full."""
    ids = draw(st.lists(st.integers(1, 10000), min_size=0, max_size=8, unique=True))
    entries = []
    for id_ in ids:
        entry = {
            "id": id_,
            "name": draw(st.text(min_size=1, max_size=10)),
            "type": draw(st.sampled_from(TYPES)),
            "active": draw(st.sampled_from([0, 1])),
            "parent": draw(st.one_of(st.none(), st.integers(1, 10000))),
            "childrenIDs": draw(st.lists(st.integers(1, 10000), max_size=3)),
            "tags": [],
            "path": [draw(st.text(max_size=5))],
            "pathName": draw(st.text(max_size=10)),
            "interval": draw(st.integers(20, 3600)),
            "weight": draw(st.integers(0, 5000)),
        }
        if draw(st.booleans()):  # full entry: compact fields plus extras
            for key in draw(st.lists(st.sampled_from(EXTRA_KEYS), min_size=1, unique=True)):
                entry[key] = draw(_scalar)
        entries.append((id_, entry))
    return entries


def make_api():
    api = MagicMock(spec=UptimeKumaApi)
    api._event_data = {Event.MONITOR_LIST: None}
    api.wait_events = 0
    api.timeout = 1
    for name in REAL_METHODS:
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return api


# Property 7: get_monitors always returns exactly the compact key set,
# regardless of which cache entries were full.
@settings(max_examples=200, deadline=None)
@given(entries=monitor_entries(), split=st.integers(0, 8))
def test_property_7_get_monitors_returns_exactly_compact_keys(entries, split):
    api = make_api()

    # First part arrives as the login-time monitorList, the rest as deltas,
    # one updateMonitorIntoList event per monitor.
    initial, deltas = entries[:split], entries[split:]
    api._event_monitor_list({str(i): dict(e) for i, e in initial})
    for i, e in deltas:
        api._event_update_monitor_into_list({str(i): dict(e)})

    result = api.get_monitors()

    assert sorted(m["id"] for m in result) == sorted(i for i, _ in entries)
    for m in result:
        assert set(m) == set(_COMPACT_MONITOR_FIELDS)
        assert isinstance(m["active"], bool)
        assert isinstance(m["type"], MonitorType)
    api._call.assert_not_called()
