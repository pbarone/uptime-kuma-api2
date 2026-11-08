from uptime_kuma_api.api import UptimeKumaApi, Event


def _make_api():
    # Construct without opening a Socket.IO connection.
    api = UptimeKumaApi.__new__(UptimeKumaApi)
    api._event_data = {
        Event.HEARTBEAT_LIST: None,
        Event.IMPORTANT_HEARTBEAT_LIST: None,
    }
    return api


HB1 = {"status": 1, "time": "2026-01-01 12:00:00"}
HB2 = {"status": 3, "time": "2026-01-01 12:01:00"}
HB3 = {"status": 1, "time": "2026-01-01 12:02:00"}


def test_heartbeat_list_append_does_not_nest():
    api = _make_api()

    # Initial event.
    api._event_heartbeat_list(42, [HB1], True)

    # Additional event must extend, not append a nested list.
    api._event_heartbeat_list(42, [HB2, HB3], False)

    records = api._event_data[Event.HEARTBEAT_LIST][42]

    assert records == [HB1, HB2, HB3]
    assert all(isinstance(record, dict) for record in records)

    # Overwrite must replace existing records.
    api._event_heartbeat_list(42, [HB3], True)

    assert api._event_data[Event.HEARTBEAT_LIST][42] == [HB3]


def test_heartbeat_list_first_emission_without_overwrite_stores_list():
    api = _make_api()

    api._event_heartbeat_list(42, [HB1, HB2], False)

    assert api._event_data[Event.HEARTBEAT_LIST][42] == [HB1, HB2]


def test_important_heartbeat_list_append_does_not_nest():
    api = _make_api()

    api._event_important_heartbeat_list(42, [HB1], True)
    api._event_important_heartbeat_list(42, [HB2, HB3], False)

    records = api._event_data[Event.IMPORTANT_HEARTBEAT_LIST][42]

    assert records == [HB1, HB2, HB3]
    assert all(isinstance(record, dict) for record in records)

    api._event_important_heartbeat_list(42, [HB3], True)

    assert api._event_data[Event.IMPORTANT_HEARTBEAT_LIST][42] == [HB3]


def test_important_heartbeat_list_first_emission_without_overwrite_stores_list():
    api = _make_api()

    api._event_important_heartbeat_list(42, [HB1, HB2], False)

    assert api._event_data[Event.IMPORTANT_HEARTBEAT_LIST][42] == [HB1, HB2]


def test_single_heartbeat_event_still_appends_one_beat():
    api = _make_api()
    beat1 = {"monitorID": 42, "status": 1, "important": False}
    beat2 = {"monitorID": 42, "status": 0, "important": True}

    api._event_heartbeat(beat1)
    api._event_heartbeat(beat2)

    assert api._event_data[Event.HEARTBEAT_LIST][42] == [beat1, beat2]
    assert api._event_data[Event.IMPORTANT_HEARTBEAT_LIST][42] == [beat2]
