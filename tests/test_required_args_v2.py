"""
Required-argument check for every monitor type (issue #98).

add_monitor() and edit_monitor() run _check_arguments_monitor on the built
payload before sending it. That check looks the monitor type up in a
per-type table of required fields. MANUAL, WEBSOCKET_UPGRADE, GLOBALPING and
SIP_OPTIONS were missing from the table, so the lookup raised KeyError for
types the library lists and the server supports. The tests for those types
called _build_monitor_data directly, which skips the check, so nothing
caught it.

These tests build the payload the way add_monitor does (build, convert,
check) against a mocked 2.5.12 server, so the check runs.
"""
import unittest
from unittest.mock import MagicMock

from uptime_kuma_api import MonitorType, UptimeKumaApi
from uptime_kuma_api.api import _check_arguments_monitor, _convert_monitor_input


def _build_at(version):
    api = MagicMock(spec=UptimeKumaApi)
    api.version = version
    for name in ("_parsed_version", "_withheld_v2_fields",
                 "_warn_withheld_v2_fields", "_check_conditions_supported",
                 "_check_monitor_type_supported"):
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return UptimeKumaApi._build_monitor_data.__get__(api)


def _checked_payload(**kwargs):
    """Build, convert and check a payload exactly as add_monitor does."""
    data = _build_at("2.5.12")(**kwargs)
    _convert_monitor_input(data)
    _check_arguments_monitor(data)
    return data


# The smallest valid arguments for each type, as a caller would pass them.
VALID_KWARGS = {
    MonitorType.HTTP: dict(url="http://127.0.0.1"),
    MonitorType.PORT: dict(hostname="127.0.0.1", port=80),
    MonitorType.PING: dict(hostname="127.0.0.1"),
    MonitorType.KEYWORD: dict(url="http://127.0.0.1", keyword="ok"),
    MonitorType.GRPC_KEYWORD: dict(grpcUrl="127.0.0.1:50051", keyword="ok",
                                   grpcServiceName="svc", grpcMethod="Check"),
    MonitorType.DNS: dict(hostname="example.com", port=53),
    MonitorType.DOCKER: dict(docker_container="c", docker_host=1),
    MonitorType.PUSH: dict(),
    MonitorType.STEAM: dict(hostname="127.0.0.1", port=27015),
    MonitorType.GAMEDIG: dict(game="minecraft", hostname="127.0.0.1", port=25565),
    MonitorType.MQTT: dict(hostname="127.0.0.1", port=1883, mqttTopic="t"),
    MonitorType.SQLSERVER: dict(),
    MonitorType.POSTGRES: dict(),
    MonitorType.MYSQL: dict(),
    MonitorType.MONGODB: dict(),
    MonitorType.RADIUS: dict(hostname="127.0.0.1", radiusUsername="u",
                             radiusPassword="p", radiusSecret="s",
                             radiusCalledStationId="a",
                             radiusCallingStationId="b"),
    MonitorType.REDIS: dict(),
    MonitorType.GROUP: dict(),
    MonitorType.JSON_QUERY: dict(url="http://127.0.0.1", jsonPath="$.a",
                                 expectedValue="1"),
    MonitorType.REAL_BROWSER: dict(url="http://127.0.0.1"),
    MonitorType.KAFKA_PRODUCER: dict(kafkaProducerTopic="t",
                                     kafkaProducerMessage="m"),
    MonitorType.TAILSCALE_PING: dict(hostname="127.0.0.1"),
    MonitorType.RABBITMQ: dict(rabbitmqNodes=["http://127.0.0.1:15672"]),
    MonitorType.SNMP: dict(hostname="127.0.0.1", snmpOid="1.3.6.1"),
    MonitorType.SMTP: dict(hostname="127.0.0.1"),
    MonitorType.SYSTEM_SERVICE: dict(system_service_name="cron"),
    MonitorType.PM2: dict(system_service_name="app"),
    MonitorType.NTP: dict(hostname="pool.ntp.org"),
    MonitorType.SFTP: dict(hostname="127.0.0.1", sshUsername="u"),
    MonitorType.ORACLEDB: dict(databaseConnectionString="localhost:1521/ORCL"),
    MonitorType.MANUAL: dict(),
    MonitorType.WEBSOCKET_UPGRADE: dict(url="ws://127.0.0.1:8080"),
    MonitorType.GLOBALPING: dict(subtype="ping", hostname="example.com"),
    MonitorType.SIP_OPTIONS: dict(hostname="127.0.0.1", port=5060),
}


class TestEveryTypeIsChecked(unittest.TestCase):

    def test_every_monitor_type_has_test_arguments(self):
        """Keeps VALID_KWARGS complete, so the next test covers every type."""
        self.assertEqual(set(VALID_KWARGS), set(MonitorType))

    def test_every_monitor_type_passes_the_check(self):
        """No member of MonitorType raises KeyError in the required-args check."""
        for type_, kwargs in VALID_KWARGS.items():
            with self.subTest(type=type_.value):
                data = _checked_payload(type=type_, name="m", **kwargs)
                self.assertEqual(data["type"], type_)


class TestTypesMissingFromTheTable(unittest.TestCase):
    """The four types from issue #98, with their required fields."""

    def test_manual_needs_nothing_extra(self):
        _checked_payload(type=MonitorType.MANUAL, name="m")

    def test_websocket_upgrade_requires_url(self):
        _checked_payload(type=MonitorType.WEBSOCKET_UPGRADE, name="m",
                         url="ws://127.0.0.1:8080")
        with self.assertRaises(TypeError) as ctx:
            _checked_payload(type=MonitorType.WEBSOCKET_UPGRADE, name="m",
                             url=None)
        self.assertIn("url", str(ctx.exception))

    def test_sip_options_requires_hostname(self):
        _checked_payload(type=MonitorType.SIP_OPTIONS, name="m",
                         hostname="127.0.0.1")
        with self.assertRaises(TypeError) as ctx:
            _checked_payload(type=MonitorType.SIP_OPTIONS, name="m")
        self.assertIn("hostname", str(ctx.exception))

    def test_globalping_requires_subtype(self):
        with self.assertRaises(TypeError) as ctx:
            _checked_payload(type=MonitorType.GLOBALPING, name="m",
                             hostname="example.com")
        self.assertIn("subtype", str(ctx.exception))

    def test_globalping_ping_and_dns_require_hostname(self):
        for subtype in ("ping", "dns"):
            with self.subTest(subtype=subtype):
                _checked_payload(type=MonitorType.GLOBALPING, name="m",
                                 subtype=subtype, hostname="example.com")
                with self.assertRaises(TypeError) as ctx:
                    _checked_payload(type=MonitorType.GLOBALPING, name="m",
                                     subtype=subtype)
                self.assertIn("hostname", str(ctx.exception))

    def test_globalping_http_requires_url(self):
        _checked_payload(type=MonitorType.GLOBALPING, name="m",
                         subtype="http", url="https://example.com")
        with self.assertRaises(TypeError) as ctx:
            _checked_payload(type=MonitorType.GLOBALPING, name="m",
                             subtype="http", url=None)
        self.assertIn("url", str(ctx.exception))

    def test_globalping_rejects_an_unknown_subtype(self):
        with self.assertRaises(ValueError) as ctx:
            _checked_payload(type=MonitorType.GLOBALPING, name="m",
                             subtype="traceroute", hostname="example.com")
        self.assertIn("traceroute", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
