"""
Version gating on the 3.x line (issue #88).

Every gate whose floor was below Uptime Kuma 3.0 was removed: the behaviour a
2.x server got is what a 3.x server gets. The gating mechanism stays, with no
live entries. This file covers both halves without a live server:

* TestFormerGatesAreUnconditional: every removed gate is now unconditional, at
  3.x versions and at the old-looking stubs (2.0.0, 1.21.3) that used to select
  the old branches. The 3.x rows are characterization (they passed before the
  removal too). The old-looking rows fail against the pre-removal code.
* The synthetic-gate classes patch a floor of 3.1 onto existing registry rows
  and prove withholding, the single warning, the edit path, the per-type floor
  and pre-release handling still work for the first field a later 3.x release
  gates.
"""
import inspect
import os
import unittest
import warnings
from unittest.mock import MagicMock, patch

from uptime_kuma_api import (
    Event,
    MonitorType,
    UnsupportedFieldWarning,
    UptimeKumaException,
)
from uptime_kuma_api.api import (
    UptimeKumaApi,
    _FieldRule,
    _HTTP_FIELD_TYPES,
    _MONITOR_FIELD_RULES,
    _MONITOR_TYPE_FLOORS,
    _STATUS_PAGE_FIELD_FLOORS,
)

THREE_X_VERSIONS = ["3.0.0", "3.0.0-beta.2", "3.1.0"]
# Not servers this library supports. They are stubs that used to select the
# pre-3.0 branches, and must now behave like any other version.
FORMER_GATE_STUBS = ["2.0.0", "1.21.3"]
ALL_STUBS = THREE_X_VERSIONS + FORMER_GATE_STUBS

EDIT_MONITOR_EXISTING = {
    "id": 7,
    "type": MonitorType.HTTP,
    "name": "existing",
    "url": "http://x",
    "interval": 60,
    "maxretries": 0,
    "retryInterval": 60,
    "maxredirects": 10,
    "accepted_statuscodes": ["200-299"],
    "notificationIDList": [],
    "databaseConnectionString": None,
    "dns_resolve_type": "A",
}

# Sample values for every registry field, with the monitor type that carries it.
FIELD_SAMPLES = {
    "ipFamily": ("IPv4", MonitorType.HTTP),
    "cacheBust": (True, MonitorType.HTTP),
    "retryOnlyOnStatusCodeFailure": (True, MonitorType.HTTP),
    "bearer_token": ("tok", MonitorType.HTTP),
    "oauth_audience": ("aud", MonitorType.HTTP),
    "domainExpiryNotification": (True, MonitorType.HTTP),
    "saveResponse": (True, MonitorType.HTTP),
    "saveErrorResponse": (True, MonitorType.HTTP),
    "responseMaxLength": (5000, MonitorType.HTTP),
    "responsecheck": ("check", MonitorType.HTTP),
    "subtype": ("sub", MonitorType.HTTP),
    "wsSubprotocol": ("proto", MonitorType.HTTP),
    "wsIgnoreSecWebsocketAcceptHeader": (True, MonitorType.HTTP),
    "remoteBrowsersToggle": (True, MonitorType.HTTP),
    "remote_browser": ("chromium", MonitorType.HTTP),
    "screenshot_delay": (1000, MonitorType.HTTP),
    "gamedigToken": ("tok", MonitorType.HTTP),
    "protocol": ("tcp", MonitorType.HTTP),
    "jsonPathOperator": ("==", MonitorType.JSON_QUERY),
    "snmp_v3_username": ("snmpuser", MonitorType.SNMP),
    "ping_count": (3, MonitorType.PING),
    "ping_numeric": (True, MonitorType.PING),
    "ping_per_request_timeout": (5, MonitorType.PING),
    "mqttWebsocketPath": ("/mqtt", MonitorType.MQTT),
    "mqttCheckType": ("keyword", MonitorType.MQTT),
    "ntpStratumThreshold": (5, MonitorType.NTP),
    "ntpTimeOffsetThreshold": (1000, MonitorType.NTP),
    "ntpRootDispersionThreshold": (500, MonitorType.NTP),
    "sshUsername": ("u", MonitorType.SFTP),
    "sshAuthMethod": ("privateKey", MonitorType.SFTP),
    "sshPassword": ("pw", MonitorType.SFTP),
    "sshPrivateKey": ("KEY", MonitorType.SFTP),
    "sshPassphrase": ("pp", MonitorType.SFTP),
    "sftpPath": ("/data", MonitorType.SFTP),
}


def _api(version):
    """A mock api with the real gating machinery bound onto it.

    A ``MagicMock(spec=UptimeKumaApi)`` stubs every attribute it is not told
    about, so an unbound helper would silently never run and a test would pass
    vacuously. Everything the build and edit paths touch is bound explicitly.
    """
    api = MagicMock(spec=UptimeKumaApi)
    api.version = version
    for name in ("_parsed_version", "_withheld_monitor_fields",
                 "_warn_withheld_monitor_fields", "_check_monitor_type_supported",
                 "_build_monitor_data"):
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return api


def _relevant(caught):
    return [w for w in caught if issubclass(w.category, UnsupportedFieldWarning)]


class _VersionOnlyApi(UptimeKumaApi):
    """A real api with the version, ``_call`` and nothing else wired up."""

    def __init__(self, version):  # noqa: D107 - intentionally skips super()
        self._raw_version = version
        self.calls = []

    @property
    def version(self) -> str:
        return self._raw_version

    def _call(self, event, data=None):
        self.calls.append((event, data))
        return {"msg": "ok"}


class _RawVersionApi(UptimeKumaApi):
    """A real api with only ``info()`` wired up, so ``version`` is the real property."""

    def __init__(self, version):  # noqa: D107 - intentionally skips super()
        self._raw_info = {"version": version, "latestVersion": "9.9.9"}

    def info(self) -> dict:
        return self._raw_info


class TestFormerGatesAreUnconditional(unittest.TestCase):
    """What used to depend on a 1.22 / 1.23 / 1.23.1 / 2.0 / 2.1 floor is always sent."""

    def test_monitor_gates_removed(self):
        for version in ALL_STUBS:
            with self.subTest(version=version):
                data = _api(version)._build_monitor_data(
                    type=MonitorType.KEYWORD, name="t", url="http://x",
                    keyword="up", parent=3, timeout=42, invertKeyword=True,
                    ipFamily="IPv4", cacheBust=True,
                )
                self.assertEqual(data["parent"], 3)
                self.assertEqual(data["timeout"], 42)
                self.assertTrue(data["invertKeyword"])
                self.assertEqual(data["ipFamily"], "IPv4")
                self.assertTrue(data["cacheBust"])
                self.assertEqual(data["conditions"], [])

    def test_gamedig_given_port_only_always_sent(self):
        for version in ALL_STUBS:
            with self.subTest(version=version):
                data = _api(version)._build_monitor_data(
                    type=MonitorType.GAMEDIG, name="t", hostname="h",
                    game="minecraft", gamedigGivenPortOnly=False,
                )
                self.assertIn("gamedigGivenPortOnly", data)

    def test_conditions_is_never_refused(self):
        conditions = [{"type": "expression"}]
        for version in ALL_STUBS:
            with self.subTest(version=version):
                data = _api(version)._build_monitor_data(
                    type=MonitorType.HTTP, name="t", url="http://x",
                    conditions=conditions,
                )
                self.assertIs(data["conditions"], conditions)

    def test_every_registry_field_is_sent_on_every_version(self):
        self.assertEqual(set(FIELD_SAMPLES), set(_MONITOR_FIELD_RULES))
        for version in ALL_STUBS:
            for name, (value, type_) in FIELD_SAMPLES.items():
                with self.subTest(version=version, field=name):
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        data = _api(version)._build_monitor_data(
                            type=type_, name="t", hostname="h", **{name: value})
                    self.assertEqual(data[name], value)
                    self.assertEqual(_relevant(caught), [])

    def test_every_monitor_type_is_accepted_on_every_version(self):
        for version in ALL_STUBS:
            api = _api(version)
            for type_ in MonitorType:
                with self.subTest(version=version, type=type_):
                    api._check_monitor_type_supported(type_)

    def test_edit_monitor_withholds_nothing(self):
        for version in ALL_STUBS:
            with self.subTest(version=version):
                api = _api(version)
                api.get_monitor.return_value = dict(EDIT_MONITOR_EXISTING)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    UptimeKumaApi.edit_monitor.__get__(api)(
                        7, bearer_token="tok", cacheBust=True,
                        conditions=[{"type": "expression"}])
                sent = api._call.call_args[0][1]
                self.assertEqual(sent["bearer_token"], "tok")
                self.assertTrue(sent["cacheBust"])
                self.assertEqual(sent["conditions"], [{"type": "expression"}])
                self.assertEqual(_relevant(caught), [])

    def test_set_settings_sends_chrome_executable_and_nscd(self):
        for version in ALL_STUBS:
            with self.subTest(version=version):
                api = _VersionOnlyApi(version)
                api.set_settings(chromeExecutable="/usr/bin/chromium", nscd=True)
                event, payload = api.calls[0]
                self.assertEqual(event, "setSettings")
                self.assertEqual(payload[0]["chromeExecutable"], "/usr/bin/chromium")
                self.assertTrue(payload[0]["nscd"])

    def test_important_heartbeats_always_use_the_paged_call(self):
        for version in ALL_STUBS:
            with self.subTest(version=version):
                api = _VersionOnlyApi(version)
                api.get_monitors = lambda: [{"id": 4}]
                api._call = lambda event, data=None: {"data": []}
                self.assertEqual(api.get_important_heartbeats(), {4: []})


class TestRegistriesHaveNoLiveGate(unittest.TestCase):
    """No gate below Uptime Kuma 3.0 survives in any registry."""

    def test_no_floor_below_three_zero(self):
        from packaging.version import parse
        floors = [r.floor for r in _MONITOR_FIELD_RULES.values() if r.floor is not None]
        floors += list(_MONITOR_TYPE_FLOORS.values())
        floors += list(_STATUS_PAGE_FIELD_FLOORS.values())
        for floor in floors:
            self.assertGreaterEqual(parse(floor), parse("3.0"), floor)

    def test_type_and_status_page_tables_are_empty_today(self):
        self.assertEqual(_MONITOR_TYPE_FLOORS, {})
        self.assertEqual(_STATUS_PAGE_FIELD_FLOORS, {})

    def test_every_registry_key_is_a_build_monitor_data_parameter(self):
        """A mistyped key would silently read None from ``locals()`` and the
        field would be neither emitted nor reported. This is the guard."""
        params = set(inspect.signature(UptimeKumaApi._build_monitor_data).parameters)
        self.assertEqual(set(_MONITOR_FIELD_RULES) - params, set())

    def test_type_restriction_holds(self):
        """A type-restricted field is omitted for an out-of-set type."""
        for name, rule in _MONITOR_FIELD_RULES.items():
            if rule.types is None or MonitorType.REDIS in rule.types:
                continue
            with self.subTest(field=name):
                value = FIELD_SAMPLES[name][0]
                data = _api("3.0.0")._build_monitor_data(
                    type=MonitorType.REDIS, name="t", **{name: value})
                self.assertNotIn(name, data)

    def test_public_surface_unchanged(self):
        import uptime_kuma_api
        self.assertIn("UnsupportedFieldWarning", dir(uptime_kuma_api))
        for private in ("_MONITOR_FIELD_RULES", "_MONITOR_TYPE_FLOORS",
                        "_STATUS_PAGE_FIELD_FLOORS"):
            self.assertNotIn(private, dir(uptime_kuma_api))
        self.assertTrue(issubclass(UnsupportedFieldWarning, UserWarning))
        self.assertFalse(issubclass(UnsupportedFieldWarning, UptimeKumaException))
        self.assertEqual(
            list(inspect.signature(UptimeKumaApi.add_monitor).parameters),
            ["self", "kwargs"])
        self.assertEqual(
            list(inspect.signature(UptimeKumaApi.edit_monitor).parameters),
            ["self", "id_", "kwargs"])


class _SyntheticFloor(unittest.TestCase):
    """Patches a floor of 3.1 onto three existing registry rows."""

    def setUp(self):
        rules = {
            "bearer_token": _FieldRule("3.1", _HTTP_FIELD_TYPES),
            "cacheBust": _FieldRule("3.1", _HTTP_FIELD_TYPES),
            "subtype": _FieldRule("3.1", None),
        }
        patcher = patch.dict(_MONITOR_FIELD_RULES, rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _build(self, version, **kwargs):
        kwargs.setdefault("type", MonitorType.HTTP)
        return _api(version)._build_monitor_data(
            name="m", url="http://127.0.0.1", **kwargs)

    def _capture(self, call, *args, **kwargs):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = call(*args, **kwargs)
        return result, _relevant(caught)


class TestSyntheticFieldFloor(_SyntheticFloor):

    def test_withheld_below_the_floor(self):
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                data, caught = self._capture(self._build, version, bearer_token="tok")
                self.assertNotIn("bearer_token", data)
                self.assertEqual(len(caught), 1)

    def test_sent_at_and_above_the_floor(self):
        # 3.1.0-beta.1 is gated as its release segment, 3.1
        for version in ("3.1.0", "3.1.0-beta.1", "3.2.0"):
            with self.subTest(version=version):
                data, caught = self._capture(self._build, version, bearer_token="tok")
                self.assertEqual(data["bearer_token"], "tok")
                self.assertEqual(caught, [])

    def test_unparseable_or_missing_version_counts_as_newest(self):
        for version in ("not-a-version", "3.0.0-dev-nightly-20240101", None, ""):
            with self.subTest(version=version):
                data, caught = self._capture(self._build, version, bearer_token="tok")
                self.assertEqual(data["bearer_token"], "tok")
                self.assertEqual(caught, [])

    def test_row_without_floor_is_never_withheld(self):
        data, caught = self._capture(self._build, "3.0.0", saveResponse=True)
        self.assertTrue(data["saveResponse"])
        self.assertEqual(caught, [])

    def test_one_warning_names_every_withheld_field_and_the_floor(self):
        data, caught = self._capture(
            self._build, "3.0.0", bearer_token="tok", cacheBust=True, subtype="s",
            interval=120)
        self.assertEqual(len(caught), 1)
        message = str(caught[0].message)
        for name in ("bearer_token", "cacheBust", "subtype"):
            self.assertIn(name, message)
            self.assertNotIn(name, data)
        self.assertIn("3.1", message)
        self.assertIn("3.0.0", message)
        self.assertNotIn("interval", message)
        self.assertEqual(data["interval"], 120)

    def test_message_is_stable_across_calls(self):
        def once():
            return str(self._capture(
                self._build, "3.0.0", subtype="s", bearer_token="tok",
                cacheBust=True)[1][0].message)
        self.assertEqual(once(), once())

    def test_no_warning_when_nothing_gated_is_supplied(self):
        _, caught = self._capture(self._build, "3.0.0")
        self.assertEqual(caught, [])

    def test_falsy_but_not_none_is_withheld_and_announced(self):
        for name, value in (("cacheBust", False), ("subtype", "")):
            with self.subTest(field=name):
                data, caught = self._capture(self._build, "3.0.0", **{name: value})
                self.assertNotIn(name, data)
                self.assertEqual(len(caught), 1)

    def test_type_restriction_applies_below_the_floor_too(self):
        data, caught = self._capture(
            self._build, "3.0.0", type=MonitorType.PING, hostname="h",
            bearer_token="tok")
        self.assertNotIn("bearer_token", data)
        self.assertEqual(caught, [])

    def test_stacklevel_blames_the_caller(self):
        api = _api("3.0.0")
        add_monitor = UptimeKumaApi.add_monitor.__get__(api)
        _, caught = self._capture(
            add_monitor, type=MonitorType.HTTP, name="m", url="http://127.0.0.1",
            bearer_token="tok")
        self.assertEqual(len(caught), 1)
        self.assertEqual(
            os.path.basename(caught[0].filename), os.path.basename(__file__))

    def test_simplefilter_error_escalates(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnsupportedFieldWarning)
            with self.assertRaises(UnsupportedFieldWarning):
                self._build("3.0.0", bearer_token="tok")


class TestSyntheticFieldFloorEditPath(_SyntheticFloor):

    def _edit_api(self, version, existing=None):
        api = _api(version)
        api.get_monitor.return_value = dict(existing or EDIT_MONITOR_EXISTING)
        return api, UptimeKumaApi.edit_monitor.__get__(api)

    def test_withheld_field_is_not_merged_and_warned_once(self):
        api, edit = self._edit_api("3.0.0")
        _, caught = self._capture(edit, 7, bearer_token="tok", cacheBust=True)
        self.assertNotIn("bearer_token", api._call.call_args[0][1])
        self.assertNotIn("cacheBust", api._call.call_args[0][1])
        self.assertEqual(len(caught), 1)

    def test_a_key_the_server_returned_is_preserved(self):
        existing = dict(EDIT_MONITOR_EXISTING, bearer_token="server-side")
        api, edit = self._edit_api("3.0.0", existing)
        _, caught = self._capture(edit, 7, interval=120)
        sent = api._call.call_args[0][1]
        self.assertEqual(sent["bearer_token"], "server-side")
        self.assertEqual(sent["interval"], 120)
        self.assertEqual(caught, [])

    def test_supported_field_is_merged_at_the_floor(self):
        api, edit = self._edit_api("3.1.0")
        _, caught = self._capture(edit, 7, bearer_token="tok")
        self.assertEqual(api._call.call_args[0][1]["bearer_token"], "tok")
        self.assertEqual(caught, [])

    def test_escalated_warning_raises_before_get_monitor(self):
        api, edit = self._edit_api("3.0.0")
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnsupportedFieldWarning)
            with self.assertRaises(UnsupportedFieldWarning):
                edit(7, bearer_token="tok")
        api.get_monitor.assert_not_called()
        api._call.assert_not_called()

    def test_ordinary_edit_is_silent(self):
        api, edit = self._edit_api("3.0.0")
        _, caught = self._capture(edit, 7, interval=120)
        self.assertEqual(caught, [])
        self.assertEqual(api._call.call_args[0][0], "editMonitor")


class TestSyntheticTypeFloor(unittest.TestCase):

    def setUp(self):
        patcher = patch.dict(_MONITOR_TYPE_FLOORS, {MonitorType.PUSH: "3.1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejected_below_the_floor_with_its_own_floor_in_the_message(self):
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                with self.assertRaises(UptimeKumaException) as ctx:
                    _api(version)._build_monitor_data(type=MonitorType.PUSH, name="t")
                message = str(ctx.exception)
                self.assertIn("push", message)
                self.assertIn("3.1", message)
                self.assertIn(version, message)

    def test_accepted_at_and_above_the_floor(self):
        for version in ("3.1.0", "3.1.0-beta.1", "3.2.0", "not-a-version"):
            with self.subTest(version=version):
                data = _api(version)._build_monitor_data(type=MonitorType.PUSH, name="t")
                self.assertEqual(data["type"], MonitorType.PUSH)

    def test_a_bare_string_type_is_floored_identically(self):
        with self.assertRaises(UptimeKumaException):
            _api("3.0.0")._build_monitor_data(type="push", name="t")

    def test_other_types_are_unaffected(self):
        data = _api("3.0.0")._build_monitor_data(
            type=MonitorType.HTTP, name="t", url="http://x")
        self.assertEqual(data["type"], MonitorType.HTTP)

    def test_edit_monitor_raises_before_any_server_call(self):
        api = _api("3.0.0")
        with self.assertRaises(UptimeKumaException):
            UptimeKumaApi.edit_monitor.__get__(api)(7, type=MonitorType.PUSH)
        api.get_monitor.assert_not_called()
        api._call.assert_not_called()

    def test_edit_monitor_unrelated_field_does_not_raise(self):
        api = _api("3.0.0")
        api.get_monitor.return_value = dict(EDIT_MONITOR_EXISTING, type=MonitorType.PUSH)
        UptimeKumaApi.edit_monitor.__get__(api)(7, interval=120)
        api._call.assert_called_once()


class TestVersionHandling(unittest.TestCase):

    def test_version_property_returns_the_raw_server_string(self):
        for raw in ("3.0.0-beta.2", "3.0.0", "3.0.0-dev-nightly-20240101"):
            with self.subTest(version=raw):
                self.assertEqual(_RawVersionApi(raw).version, raw)

    def test_pre_releases_are_gated_as_their_release(self):
        for raw in ("3.0.0-beta.2", "3.0.0b1", "3.0.0rc1", "3.0.0.dev1", "3.0.0"):
            with self.subTest(version=raw):
                parsed = _VersionOnlyApi(raw)._parsed_version()
                self.assertEqual(str(parsed), "3.0.0")
