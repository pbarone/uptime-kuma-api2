"""Compact monitor list on Uptime Kuma 3.x.

Uptime Kuma 3.0 sends a compact ``monitorList`` at login (CompactMonitor in
server/compact-monitor.ts, tag 3.0.0-beta.2): eleven fields, and no
``notificationIDList``. Before the fix ``get_monitors`` and ``delete_monitor``
raised ``KeyError: 'notificationIDList'`` against it.

Unit tests, no live server. The cache is driven through the real event
handlers; ``_call`` is a mock that mimics the server.

**Validates: Requirements 15.1, 15.2, 15.5, 15.6, 15.7, 15.9, 18.6**
"""

import unittest
from copy import deepcopy
from unittest.mock import MagicMock

from uptime_kuma_api import Event, MonitorType, UptimeKumaApi, UptimeKumaException

COMPACT_FIELDS = {
    "id", "name", "type", "active", "parent", "childrenIDs",
    "tags", "path", "pathName", "interval", "weight",
}

REAL_METHODS = (
    "wait_for_event",
    "_get_event_data",
    "_refresh_monitor_list",
    "get_monitors",
    "get_monitor",
    "delete_monitor",
    "_event_monitor_list",
    "_event_update_monitor_into_list",
    "_event_delete_monitor_from_list",
)


def compact(id_, **extra):
    """An entry shaped like CompactMonitor (no notificationIDList)."""
    entry = {
        "id": id_,
        "name": "monitor {0}".format(id_),
        "type": "http",
        "active": 1,
        "parent": None,
        "childrenIDs": [],
        "tags": [{"tag_id": 3, "monitor_id": id_, "value": "prod"}],
        "path": ["monitor {0}".format(id_)],
        "pathName": "monitor {0}".format(id_),
        "interval": 60,
        "weight": 2000,
    }
    entry.update(extra)
    return entry


def full(id_, **extra):
    """A full-form entry as updateMonitorIntoList may carry it."""
    entry = compact(id_)
    entry.update({
        "notificationIDList": {"1": True, "2": True},
        "authMethod": None,
        "url": "http://127.0.0.1",
        "method": "GET",
        "accepted_statuscodes": ["200-299"],
    })
    entry.update(extra)
    return entry


def cache_of(*monitors):
    return {str(m["id"]): m for m in monitors}


def make_api(cache):
    """Mock API with a real cache, real handlers and real readers."""
    api = MagicMock(spec=UptimeKumaApi)
    api._event_data = {Event.MONITOR_LIST: None}
    api.wait_events = 0
    api.timeout = 1
    api._dashboard_rendered_sent = False

    def _call(event, data=None):
        if event == "getMonitorList":
            # server emits monitorList, then acks
            api._event_monitor_list(deepcopy(cache))
            return {}
        if event == "deleteMonitor":
            return {"msg": "Deleted Successfully."}
        if event == "getMonitor":
            return {"monitor": full(data, notificationIDList={"1": True})}
        return {}

    api._call.side_effect = _call
    for name in REAL_METHODS:
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    api._event_monitor_list(deepcopy(cache))
    return api


def sent_events(api):
    return [c.args[0] for c in api._call.call_args_list]


class TestGetMonitorsCompact(unittest.TestCase):
    """**Validates: Requirements 15.1, 18.6**"""

    def test_compact_list_without_notification_id_list_works(self):
        api = make_api(cache_of(compact(1), compact(2, type="ping", active=0)))

        result = api.get_monitors()

        self.assertEqual([m["id"] for m in result], [1, 2])
        for m in result:
            self.assertEqual(set(m), COMPACT_FIELDS)
            self.assertNotIn("notificationIDList", m)
        self.assertIs(result[0]["active"], True)
        self.assertIs(result[1]["active"], False)
        self.assertEqual(result[0]["type"], MonitorType.HTTP)
        self.assertEqual(result[1]["type"], MonitorType.PING)
        self.assertEqual(
            result[0]["tags"], [{"tag_id": 3, "monitor_id": 1, "value": "prod"}]
        )

    def test_no_get_monitor_request_is_emitted(self):
        api = make_api(cache_of(compact(1), compact(2)))

        api.get_monitors()

        self.assertNotIn("getMonitor", sent_events(api))

    def test_missing_compact_field_is_left_out_not_raised(self):
        entry = compact(1)
        del entry["weight"]
        api = make_api(cache_of(entry))

        result = api.get_monitors()

        self.assertNotIn("weight", result[0])
        self.assertEqual(result[0]["id"], 1)

    def test_full_entry_from_update_event_is_reduced(self):
        """**Validates: Requirements 15.2**"""
        api = make_api(cache_of(compact(1)))

        api._event_update_monitor_into_list({"2": full(2)})
        result = api.get_monitors()

        self.assertEqual([m["id"] for m in result], [1, 2])
        for m in result:
            self.assertEqual(set(m), COMPACT_FIELDS)
        self.assertNotIn("getMonitor", sent_events(api))

    def test_full_entry_replacing_a_compact_one_is_reduced(self):
        api = make_api(cache_of(compact(1)))

        api._event_update_monitor_into_list({"1": full(1, name="renamed")})
        result = api.get_monitors()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "renamed")
        self.assertEqual(set(result[0]), COMPACT_FIELDS)


class TestDeleteMonitorCompact(unittest.TestCase):
    """**Validates: Requirements 15.6, 15.7, 18.6**"""

    def test_delete_existing_monitor_from_compact_list(self):
        api = make_api(cache_of(compact(1), compact(7)))

        result = api.delete_monitor(7)

        api._call.assert_any_call("deleteMonitor", 7)
        self.assertEqual(result, {"msg": "Deleted Successfully."})
        self.assertNotIn("getMonitor", sent_events(api))

    def test_numeric_string_id_is_coerced(self):
        api = make_api(cache_of(compact(7)))

        api.delete_monitor("7")

        api._call.assert_any_call("deleteMonitor", 7)

    def test_unknown_id_raises_and_sends_no_delete(self):
        api = make_api(cache_of(compact(1), compact(2)))

        with self.assertRaises(UptimeKumaException) as ctx:
            api.delete_monitor(99)

        self.assertEqual(str(ctx.exception), "monitor does not exist")
        self.assertNotIn("deleteMonitor", sent_events(api))


class TestGetMonitorStaysFull(unittest.TestCase):
    """**Validates: Requirements 15.5**"""

    def test_get_monitor_returns_full_record_via_get_monitor_request(self):
        api = make_api(cache_of(compact(1)))

        result = api.get_monitor(1)

        api._call.assert_any_call("getMonitor", 1)
        self.assertEqual(result["notificationIDList"], [1])
        self.assertIn("url", result)
        self.assertIs(result["active"], True)
        self.assertEqual(result["type"], MonitorType.HTTP)


class TestCacheConsistency(unittest.TestCase):
    """add, edit and delete events keep get_monitors consistent.

    **Validates: Requirements 15.9**
    """

    def test_add_edit_delete_sequence(self):
        api = make_api(cache_of(compact(1), compact(2)))

        # add
        api._event_update_monitor_into_list({"3": full(3)})
        self.assertEqual([m["id"] for m in api.get_monitors()], [1, 2, 3])

        # edit
        api._event_update_monitor_into_list({"2": full(2, name="edited", interval=30)})
        by_id = {m["id"]: m for m in api.get_monitors()}
        self.assertEqual(by_id[2]["name"], "edited")
        self.assertEqual(by_id[2]["interval"], 30)
        self.assertEqual(by_id[1]["name"], "monitor 1")

        # delete
        api._event_delete_monitor_from_list(1)
        self.assertEqual([m["id"] for m in api.get_monitors()], [2, 3])

    def test_deleted_monitor_is_no_longer_deletable(self):
        api = make_api(cache_of(compact(1), compact(2)))
        api._event_delete_monitor_from_list(2)
        # the refresh in delete_monitor re-reads the server view, which no
        # longer has monitor 2
        api._call.side_effect = lambda event, data=None: (
            api._event_monitor_list(cache_of(compact(1))) or {}
            if event == "getMonitorList" else {}
        )

        with self.assertRaises(UptimeKumaException):
            api.delete_monitor(2)

    def test_group_delete_cascade(self):
        group = compact(1, childrenIDs=[2, 3])
        api = make_api(cache_of(group, compact(2, parent=1), compact(3, parent=1)))

        for id_ in (2, 3, 1):
            api._event_delete_monitor_from_list(id_)

        self.assertEqual(api.get_monitors(), [])


if __name__ == "__main__":
    unittest.main()
