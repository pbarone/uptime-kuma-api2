"""get_important_heartbeats() on Uptime Kuma 2.x (issue #63).

Uptime Kuma 1.x pushes the important-heartbeat history to the client at login
(the ``importantHeartbeatList`` event, one per monitor), which this library
caches. Uptime Kuma 2.x no longer does: ``sendImportantHeartbeatList`` has no
caller in server/ at 2.0.0 or 2.5.9, and the history is instead served on
request by ``monitorImportantHeartbeatListPaged(monitorID, offset, count, cb)``.
The unfixed code waited for the event forever and raised ``Timeout``.

No live server: ``api.version`` is set directly, the event cache is a real dict,
and ``_call`` (or ``sio.call`` where the real ``_call`` is under test) is mocked.
``api.timeout`` is tiny so the unfixed code fails fast rather than hanging.
"""

from unittest.mock import MagicMock, call

import pytest
import socketio

from uptime_kuma_api import (
    Event,
    MonitorStatus,
    Timeout,
    UptimeKumaApi,
    UptimeKumaException,
)

PAGED = "monitorImportantHeartbeatListPaged"

V1_VERSIONS = ["1.21.3", "1.22.0", "1.23.16"]
V2_VERSIONS = ["2.0.0", "2.1.0", "2.5.5", "2.0.0-beta.2"]


def monitor(id_):
    return {
        "id": id_,
        "name": "monitor {0}".format(id_),
        "active": 1,
        "type": "http",
        "authMethod": None,
        "notificationIDList": {},
        "tags": [],
    }


def row(monitor_id, status=1, important=1, time="2023-05-01 17:22:20.289"):
    """A row shaped like Heartbeat.toJSON() in server/model/heartbeat.js (2.0.0)."""
    return {
        "monitorID": monitor_id,
        "status": status,
        "time": time,
        "msg": "",
        "ping": 10.5,
        "important": important,
        "duration": 0,
        "retries": 0,
    }


def make_api(version, monitors=(), real=("get_important_heartbeats",)):
    api = MagicMock(spec=UptimeKumaApi)
    api.version = version
    api.timeout = 0.05
    api.wait_events = 0
    api._event_data = {
        Event.MONITOR_LIST: {str(m["id"]): m for m in monitors},
        Event.IMPORTANT_HEARTBEAT_LIST: None,
    }
    for name in tuple(real) + (
        "get_monitors", "_get_event_data", "_parsed_version",
    ):
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return api


@pytest.mark.parametrize("version", V1_VERSIONS)
def test_v1_reads_event_cache_and_never_calls_server(version):
    api = make_api(version, [monitor(1), monitor(2)])
    api._event_data[Event.IMPORTANT_HEARTBEAT_LIST] = {
        1: [row(1, status=0, important=1)],
        2: [],
    }

    result = api.get_important_heartbeats()

    api._call.assert_not_called()
    assert list(result) == [1, 2]
    assert result[1][0]["status"] == MonitorStatus.DOWN
    assert result[1][0]["important"] is True
    assert result[2] == []


@pytest.mark.parametrize("version", V2_VERSIONS)
def test_v2_calls_paged_event_once_per_monitor_and_converts_rows(version):
    api = make_api(version, [monitor(1)])
    api._call.return_value = {
        "data": [row(1, status=0, important=1), row(1, status=1, important=1)],
    }

    result = api.get_important_heartbeats()

    assert api._call.call_args_list == [call(PAGED, (1, 0, 500))]
    assert list(result) == [1]
    first, second = result[1]
    assert first["status"] == MonitorStatus.DOWN
    assert second["status"] == MonitorStatus.UP
    assert first["important"] is True and second["important"] is True
    # Server keys are kept as sent; 2.x rows already carry monitorID, so no
    # alias is invented.
    assert first["monitorID"] == 1
    assert "monitor_id" not in first
    assert set(first) == set(row(1))


def test_v2_ignores_the_never_sent_event_cache():
    api = make_api("2.5.5", [monitor(7)])
    api._call.return_value = {"data": []}

    assert api.get_important_heartbeats() == {7: []}
    assert api._event_data[Event.IMPORTANT_HEARTBEAT_LIST] is None


def test_v2_multiple_monitors_one_call_each_in_order():
    api = make_api("2.5.5", [monitor(3), monitor(1), monitor(2)])
    api._call.side_effect = lambda event, data: {"data": [row(data[0])]}

    result = api.get_important_heartbeats()

    assert api._call.call_args_list == [
        call(PAGED, (3, 0, 500)),
        call(PAGED, (1, 0, 500)),
        call(PAGED, (2, 0, 500)),
    ]
    assert list(result) == [3, 1, 2]
    assert [result[i][0]["monitorID"] for i in result] == [3, 1, 2]


@pytest.mark.parametrize("version", V2_VERSIONS)
def test_v2_no_monitors_returns_empty_dict_without_calling_server(version):
    api = make_api(version, [])

    assert api.get_important_heartbeats() == {}
    api._call.assert_not_called()


def real_call_api(monitors=(monitor(1),)):
    api = make_api("2.5.5", monitors)
    api._call = UptimeKumaApi._call.__get__(api)
    api.sio = MagicMock()
    return api


def test_v2_real_call_sends_arguments_as_a_tuple():
    api = real_call_api()
    api.sio.call.return_value = {"ok": True, "data": [row(1)]}

    result = api.get_important_heartbeats()

    api.sio.call.assert_called_once_with(PAGED, (1, 0, 500), timeout=api.timeout)
    assert result[1][0]["status"] == MonitorStatus.UP


def test_v2_ok_false_ack_raises_uptime_kuma_exception():
    api = real_call_api()
    api.sio.call.return_value = {"ok": False, "msg": "x"}

    with pytest.raises(UptimeKumaException, match="x"):
        api.get_important_heartbeats()
    api.sio.call.assert_called_once()


def test_v2_socket_timeout_raises_timeout():
    api = real_call_api()
    api.sio.call.side_effect = socketio.exceptions.TimeoutError()

    with pytest.raises(Timeout):
        api.get_important_heartbeats()
    api.sio.call.assert_called_once()
