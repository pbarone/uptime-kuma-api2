import unittest
import logging
from unittest.mock import patch, MagicMock

from uptime_kuma_api.api import UptimeKumaApi, Event


def test_heartbeat_list_append_does_not_nest():
    # Construct without opening a Socket.IO connection.
    api = UptimeKumaApi.__new__(UptimeKumaApi)
    api._event_data = {Event.HEARTBEAT_LIST: None}

    hb1 = {"status": 1, "time": "2026-01-01 12:00:00"}
    hb2 = {"status": 3, "time": "2026-01-01 12:01:00"}
    hb3 = {"status": 1, "time": "2026-01-01 12:02:00"}

    # Initial event.
    api._event_heartbeat_list(42, [hb1], True)

    # Additional event must extend, not append a nested list.
    api._event_heartbeat_list(42, [hb2, hb3], False)

    records = api._event_data[Event.HEARTBEAT_LIST][42]

    assert records == [hb1, hb2, hb3]
    assert all(isinstance(record, dict) for record in records)

    # Overwrite must replace existing records.
    api._event_heartbeat_list(42, [hb3], True)

    assert api._event_data[Event.HEARTBEAT_LIST][42] == [hb3]
