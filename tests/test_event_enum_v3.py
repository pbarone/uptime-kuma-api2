"""
Final Event enum shape for Uptime Kuma 3.x (requirement 21). No live server:
socketio.Client and connect() are patched.
"""
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api import UptimeKumaApi
from uptime_kuma_api.event import Event

REMOVED_NAMES = ("AUTO_LOGIN", "AVG_PING", "UPTIME", "API_KEY_LIST")
REMOVED_WIRE = ("autoLogin", "avgPing", "uptime", "apiKeyList")
NEW = {"SESSION": "session", "STATS": "stats", "HEARTBEAT_LISTS": "heartbeatLists"}


def _make_api():
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        sio = MagicMock()
        client_cls.return_value = sio
        api = UptimeKumaApi("http://kuma.invalid:3001")
    return api, sio


class TestEventEnumV3(unittest.TestCase):
    def test_removed_member_names_are_gone(self):
        for name in REMOVED_NAMES:
            self.assertFalse(hasattr(Event, name), name)
            self.assertNotIn(name, Event.__members__)

    def test_removed_wire_values_are_gone(self):
        values = {member.value for member in Event}
        for wire in REMOVED_WIRE:
            self.assertNotIn(wire, values)

    def test_new_members_exist_with_wire_values(self):
        for name, wire in NEW.items():
            self.assertIn(name, Event.__members__)
            self.assertEqual(Event[name].value, wire)

    def test_handlers_registered_for_new_events_only(self):
        _, sio = _make_api()
        registered = {call.args[0] for call in sio.on.call_args_list}
        registered_wire = {getattr(e, "value", e) for e in registered}
        for wire in NEW.values():
            self.assertIn(wire, registered_wire)
        for wire in REMOVED_WIRE:
            self.assertNotIn(wire, registered_wire)

    def test_event_data_has_no_slot_for_removed_names(self):
        api, _ = _make_api()
        keys = {getattr(k, "value", k) for k in api._event_data}
        for wire in REMOVED_WIRE:
            self.assertNotIn(wire, keys)
        for name in REMOVED_NAMES:
            self.assertNotIn(name, keys)


if __name__ == "__main__":
    unittest.main()
