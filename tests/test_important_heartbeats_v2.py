"""get_important_heartbeats() requests history per monitor (issue #63).

The server no longer pushes the important-heartbeat history at login (the
``importantHeartbeatList`` event): ``sendImportantHeartbeatList`` has no caller
in server/, and the history is instead served on request by
``monitorImportantHeartbeatListPaged(monitorID, offset, count, cb)``. Waiting
for the event would only raise ``Timeout``. The library no longer has an
event-cache branch, so this is the path for every server version.

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

# Stubs that used to select the event-cache path. They take the paged path now.
FORMER_EVENT_CACHE_VERSIONS = ["1.21.3", "1.22.0", "1.23.16"]
V3_VERSIONS = ["3.0.0", "3.0.0-beta.2", "3.1.0"]


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
    """A row shaped like Heartbeat.toJSON() in server/model/heartbeat.js ."""
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


@pytest.mark.parametrize("version", FORMER_EVENT_CACHE_VERSIONS)
def test_former_event_cache_versions_use_the_paged_path(version):
    api = make_api(version, [monitor(1)])
    api._event_data[Event.IMPORTANT_HEARTBEAT_LIST] = {1: [row(1, status=0)]}
    api._call.return_value = {"data": []}

    assert api.get_important_heartbeats() == {1: []}
    assert api._call.call_args_list == [call(PAGED, (1, 0, 500))]


@pytest.mark.parametrize("version", V3_VERSIONS)
def test_calls_paged_event_once_per_monitor_and_converts_rows(version):
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
    # Server keys are kept as sent; rows already carry monitorID, so no
    # alias is invented.
    assert first["monitorID"] == 1
    assert "monitor_id" not in first
    assert set(first) == set(row(1))


def test_ignores_the_never_sent_event_cache():
    api = make_api("3.0.0", [monitor(7)])
    api._call.return_value = {"data": []}

    assert api.get_important_heartbeats() == {7: []}
    assert api._event_data[Event.IMPORTANT_HEARTBEAT_LIST] is None


def test_multiple_monitors_one_call_each_in_order():
    api = make_api("3.0.0", [monitor(3), monitor(1), monitor(2)])
    api._call.side_effect = lambda event, data: {"data": [row(data[0])]}

    result = api.get_important_heartbeats()

    assert api._call.call_args_list == [
        call(PAGED, (3, 0, 500)),
        call(PAGED, (1, 0, 500)),
        call(PAGED, (2, 0, 500)),
    ]
    assert list(result) == [3, 1, 2]
    assert [result[i][0]["monitorID"] for i in result] == [3, 1, 2]


@pytest.mark.parametrize("version", V3_VERSIONS)
def test_no_monitors_returns_empty_dict_without_calling_server(version):
    api = make_api(version, [])

    assert api.get_important_heartbeats() == {}
    api._call.assert_not_called()


def real_call_api(monitors=(monitor(1),)):
    api = make_api("3.0.0", monitors)
    api._call = UptimeKumaApi._call.__get__(api)
    api.sio = MagicMock()
    return api


def test_real_call_sends_arguments_as_a_tuple():
    api = real_call_api()
    api.sio.call.return_value = {"ok": True, "data": [row(1)]}

    result = api.get_important_heartbeats()

    api.sio.call.assert_called_once_with(PAGED, (1, 0, 500), timeout=api.timeout)
    assert result[1][0]["status"] == MonitorStatus.UP


def test_ok_false_ack_raises_uptime_kuma_exception():
    api = real_call_api()
    api.sio.call.return_value = {"ok": False, "msg": "x"}

    with pytest.raises(UptimeKumaException, match="x"):
        api.get_important_heartbeats()
    api.sio.call.assert_called_once()


def test_socket_timeout_raises_timeout():
    api = real_call_api()
    api.sio.call.side_effect = socketio.exceptions.TimeoutError()

    with pytest.raises(Timeout):
        api.get_important_heartbeats()
    api.sio.call.assert_called_once()
