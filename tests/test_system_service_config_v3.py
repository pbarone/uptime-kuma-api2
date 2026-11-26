"""
SYSTEM_SERVICE and PM2 monitors on Uptime Kuma 3.x.

Uptime Kuma 3.0 moved system_service_name from a column into the monitor's
JSON config (config.systemServiceName). The server's toJSON no longer emits
system_service_name, add and editMonitor only apply config keys listed in
monitorConfigSchema, and a payload carrying the old key fails on an unknown
column. These tests run the real add_monitor, edit_monitor and get_monitor
code against a mocked socket (no live server).
"""
import unittest
from unittest.mock import MagicMock

from uptime_kuma_api import MonitorBuilder, MonitorType, UptimeKumaApi
from uptime_kuma_api.api import _check_arguments_monitor, _convert_monitor_input

TYPES = (MonitorType.SYSTEM_SERVICE, MonitorType.PM2)


def _api():
    api = MagicMock(spec=UptimeKumaApi)
    api.version = "3.0.0-beta.2"
    for name in ("_parsed_version", "_withheld_monitor_fields",
                 "_warn_withheld_monitor_fields",
                 "_check_monitor_type_supported", "_build_monitor_data",
                 "add_monitor", "edit_monitor", "get_monitor"):
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return api


def _server_monitor(type_, service="nginx"):
    """A getMonitor record as 3.x sends it: config, no system_service_name."""
    return {
        "id": 7, "name": "svc", "type": type_.value, "active": 1,
        "interval": 60, "retryInterval": 60, "maxretries": 0,
        "accepted_statuscodes": ["200-299"], "notificationIDList": {},
        "databaseConnectionString": None, "authMethod": None,
        "dns_resolve_type": "A",
        "config": {"systemServiceName": service},
    }


class TestAddMonitor(unittest.TestCase):
    def test_payload_has_config_and_not_the_old_key(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                api = _api()
                api.add_monitor(type=type_, name="n", system_service_name="nginx")
                event, payload = api._call.call_args.args
                self.assertEqual(event, "add")
                self.assertEqual(payload["config"], {"systemServiceName": "nginx"})
                self.assertNotIn("system_service_name", payload)

    def test_missing_name_raises_type_error(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                api = _api()
                with self.assertRaises(TypeError) as ctx:
                    api.add_monitor(type=type_, name="n")
                self.assertIn("system_service_name", str(ctx.exception))
                api._call.assert_not_called()

    def test_builder_round_trip(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                kwargs = (MonitorBuilder().type(type_).name("n")
                          .system_service_name("cron").build())
                api = _api()
                api.add_monitor(**kwargs)
                payload = api._call.call_args.args[1]
                self.assertEqual(payload["config"], {"systemServiceName": "cron"})
                self.assertNotIn("system_service_name", payload)


class TestCheckArguments(unittest.TestCase):
    def _data(self, type_, config):
        data = _api()._build_monitor_data(type=type_, name="n",
                                          system_service_name="x")
        data["config"] = config
        _convert_monitor_input(data)
        return data

    def test_reads_the_value_from_config(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                _check_arguments_monitor(self._data(type_, {"systemServiceName": "a"}))

    def test_empty_config_is_missing(self):
        for type_ in TYPES:
            for config in ({}, None, {"systemServiceName": None}):
                with self.subTest(type=type_, config=config):
                    with self.assertRaises(TypeError):
                        _check_arguments_monitor(self._data(type_, config))


class TestGetMonitor(unittest.TestCase):
    def test_mirrors_the_config_value_and_keeps_config(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                api = _api()
                api._call.return_value = {"monitor": _server_monitor(type_, "web")}
                monitor = api.get_monitor(7)
                self.assertEqual(monitor["system_service_name"], "web")
                self.assertEqual(monitor["config"], {"systemServiceName": "web"})

    def test_other_types_get_no_such_key(self):
        api = _api()
        record = _server_monitor(MonitorType.HTTP)
        record["config"] = {}
        api._call.return_value = {"monitor": record}
        self.assertNotIn("system_service_name", api.get_monitor(7))


class TestEditMonitor(unittest.TestCase):
    def _edit(self, type_, **kwargs):
        api = _api()
        api._call.side_effect = [
            {"monitor": _server_monitor(type_)},  # getMonitor
            {"msg": "Saved."},                    # editMonitor
        ]
        api.edit_monitor(7, **kwargs)
        event, payload = api._call.call_args.args
        self.assertEqual(event, "editMonitor")
        return payload

    def test_unrelated_edit_keeps_the_value_in_config(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                payload = self._edit(type_, description="d")
                self.assertEqual(payload["config"], {"systemServiceName": "nginx"})
                self.assertNotIn("system_service_name", payload)

    def test_edit_changes_the_value_in_config(self):
        for type_ in TYPES:
            with self.subTest(type=type_):
                payload = self._edit(type_, system_service_name="other")
                self.assertEqual(payload["config"], {"systemServiceName": "other"})
                self.assertNotIn("system_service_name", payload)

    def test_edit_of_a_monitor_without_the_old_key_does_not_raise(self):
        # The pre-fix failure: TypeError "missing 1 required argument".
        for type_ in TYPES:
            with self.subTest(type=type_):
                self._edit(type_, name="renamed")


if __name__ == "__main__":
    unittest.main()
