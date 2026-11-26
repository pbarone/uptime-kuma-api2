"""
Unit tests for new monitor parameters in _build_monitor_data.

These tests mock the API version and call _build_monitor_data directly
without connecting to a live server.
"""
import inspect
import os
import random
import unittest
import warnings
from unittest.mock import MagicMock

from packaging.version import InvalidVersion, parse as parse_version

from uptime_kuma_api import (
    MonitorType,
    AuthMethod,
    Event,
    MonitorBuilder,
    UnsupportedFieldWarning,
    UptimeKumaException,
)
from uptime_kuma_api.api import UptimeKumaApi


class TestMonitorParamsV2(unittest.TestCase):
    """Tests for monitor parameter handling in _build_monitor_data."""

    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.version = "3.0.0"
        # the real version-gate choke point, so gates parse self.version for real
        self.api._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api)
        self.api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(self.api)
        )
        self.api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(self.api)
        )
        self.build = UptimeKumaApi._build_monitor_data.__get__(self.api)

    # ─── 1. jsonPathOperator ──────────────────────────────────────────

    def test_json_path_operator_included_for_json_query(self):
        """jsonPathOperator is included when type is JSON_QUERY."""
        result = self.build(
            type=MonitorType.JSON_QUERY,
            name="test",
            jsonPath="$.status",
            expectedValue="ok",
            jsonPathOperator="contains",
        )
        self.assertEqual(result["jsonPathOperator"], "contains")

    def test_json_path_operator_omitted_for_http(self):
        """jsonPathOperator is NOT included for non-JSON_QUERY types."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            jsonPathOperator="contains",
        )
        self.assertNotIn("jsonPathOperator", result)

    def test_json_path_operator_omitted_for_ping(self):
        """jsonPathOperator is NOT included for PING type."""
        result = self.build(
            type=MonitorType.PING,
            name="test",
            hostname="127.0.0.1",
            jsonPathOperator=">=",
        )
        self.assertNotIn("jsonPathOperator", result)

    def test_json_path_operator_omitted_when_none(self):
        """jsonPathOperator is NOT included when None (even for JSON_QUERY)."""
        result = self.build(
            type=MonitorType.JSON_QUERY,
            name="test",
            jsonPath="$.status",
            expectedValue="ok",
            jsonPathOperator=None,
        )
        self.assertNotIn("jsonPathOperator", result)

    # ─── 2. ipFamily version gate ─────────────────────────────────────

    def test_ip_family_included_for_network_type_v2(self):
        """ipFamily is included for network types on v2."""
        for mtype in [MonitorType.HTTP, MonitorType.PING, MonitorType.PORT,
                      MonitorType.DNS, MonitorType.MQTT, MonitorType.SNMP]:
            result = self.build(type=mtype, name="test", hostname="h", ipFamily="IPv4")
            self.assertIn("ipFamily", result, f"ipFamily missing for {mtype}")
            self.assertEqual(result["ipFamily"], "IPv4")

    def test_ip_family_omitted_for_non_network_type_v2(self):
        """ipFamily is omitted for non-network types (e.g., DOCKER) even on v2."""
        result = self.build(
            type=MonitorType.DOCKER,
            name="test",
            docker_container="c",
            docker_host=1,
            ipFamily="IPv6",
        )
        self.assertNotIn("ipFamily", result)

    def test_ip_family_omitted_when_none_v2(self):
        """ipFamily is omitted when None even for network types on v2."""
        result = self.build(type=MonitorType.HTTP, name="test", ipFamily=None)
        self.assertNotIn("ipFamily", result)

    # ─── 3. HTTP params version gate ──────────────────────────────────

    def test_http_params_included_for_http_family_v2(self):
        """HTTP v2 params are included for HTTP-family types on v2."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            cacheBust=True,
            retryOnlyOnStatusCodeFailure=False,
            bearer_token="tok123",
            oauth_audience="aud",
            domainExpiryNotification=True,
            saveResponse=True,
            saveErrorResponse=False,
            responseMaxLength=5000,
            responsecheck="check",
        )
        self.assertTrue(result["cacheBust"])
        self.assertFalse(result["retryOnlyOnStatusCodeFailure"])
        self.assertEqual(result["bearer_token"], "tok123")
        self.assertEqual(result["oauth_audience"], "aud")
        self.assertTrue(result["domainExpiryNotification"])
        self.assertTrue(result["saveResponse"])
        self.assertFalse(result["saveErrorResponse"])
        self.assertEqual(result["responseMaxLength"], 5000)
        self.assertEqual(result["responsecheck"], "check")

    def test_http_params_omitted_for_non_http_family_v2(self):
        """HTTP v2 params are omitted for non-HTTP-family types like PING."""
        result = self.build(
            type=MonitorType.PING,
            name="test",
            hostname="127.0.0.1",
            cacheBust=True,
            bearer_token="tok",
        )
        self.assertNotIn("cacheBust", result)
        self.assertNotIn("bearer_token", result)

    def test_http_params_none_not_included(self):
        """HTTP v2 params left as None are not included in output."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            cacheBust=None,
            bearer_token=None,
        )
        self.assertNotIn("cacheBust", result)
        self.assertNotIn("bearer_token", result)

    # ─── 4. responseMaxLength boundary validation ─────────────────────

    def test_response_max_length_zero_raises(self):
        """responseMaxLength=0 raises ValueError."""
        with self.assertRaises(ValueError):
            self.build(
                type=MonitorType.HTTP,
                name="test",
                url="http://example.com",
                responseMaxLength=0,
            )

    def test_response_max_length_too_large_raises(self):
        """responseMaxLength=10_000_001 raises ValueError."""
        with self.assertRaises(ValueError):
            self.build(
                type=MonitorType.HTTP,
                name="test",
                url="http://example.com",
                responseMaxLength=10_000_001,
            )

    def test_response_max_length_minimum_ok(self):
        """responseMaxLength=1 is valid (lower boundary)."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            responseMaxLength=1,
        )
        self.assertEqual(result["responseMaxLength"], 1)

    def test_response_max_length_maximum_ok(self):
        """responseMaxLength=10_000_000 is valid (upper boundary)."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            responseMaxLength=10_000_000,
        )
        self.assertEqual(result["responseMaxLength"], 10_000_000)

    def test_response_max_length_negative_raises(self):
        """responseMaxLength=-1 raises ValueError."""
        with self.assertRaises(ValueError):
            self.build(
                type=MonitorType.HTTP,
                name="test",
                url="http://example.com",
                responseMaxLength=-1,
            )

    # ─── 5. PING params ──────────────────────────────────────────────

    def test_ping_params_included_for_ping_type(self):
        """PING-specific params are included for PING type."""
        result = self.build(
            type=MonitorType.PING,
            name="test",
            hostname="127.0.0.1",
            ping_count=5,
            ping_numeric=True,
            ping_per_request_timeout=10,
        )
        self.assertEqual(result["ping_count"], 5)
        self.assertTrue(result["ping_numeric"])
        self.assertEqual(result["ping_per_request_timeout"], 10)

    def test_ping_params_omitted_for_non_ping_type(self):
        """PING-specific params are NOT included for HTTP type."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            ping_count=5,
            ping_numeric=True,
            ping_per_request_timeout=10,
        )
        self.assertNotIn("ping_count", result)
        self.assertNotIn("ping_numeric", result)
        self.assertNotIn("ping_per_request_timeout", result)

    def test_ping_params_none_omitted(self):
        """PING params left as None are not included even for PING type."""
        result = self.build(
            type=MonitorType.PING,
            name="test",
            hostname="127.0.0.1",
            ping_count=None,
            ping_numeric=None,
        )
        self.assertNotIn("ping_count", result)
        self.assertNotIn("ping_numeric", result)

    # ─── 6. MQTT params ──────────────────────────────────────────────

    def test_mqtt_params_included_for_mqtt_type(self):
        """MQTT new params are included for MQTT type."""
        result = self.build(
            type=MonitorType.MQTT,
            name="test",
            hostname="broker.local",
            port=1883,
            mqttWebsocketPath="/ws",
            mqttCheckType="keyword",
        )
        self.assertEqual(result["mqttWebsocketPath"], "/ws")
        self.assertEqual(result["mqttCheckType"], "keyword")

    def test_mqtt_params_omitted_for_non_mqtt_type(self):
        """MQTT new params are NOT included for HTTP type."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            mqttWebsocketPath="/ws",
            mqttCheckType="keyword",
        )
        self.assertNotIn("mqttWebsocketPath", result)
        self.assertNotIn("mqttCheckType", result)

    def test_mqtt_check_type_json_query_valid(self):
        """mqttCheckType='json-query' is valid."""
        result = self.build(
            type=MonitorType.MQTT,
            name="test",
            hostname="broker.local",
            mqttCheckType="json-query",
        )
        self.assertEqual(result["mqttCheckType"], "json-query")

    def test_mqtt_check_type_invalid_raises(self):
        """Invalid mqttCheckType raises ValueError."""
        with self.assertRaises(ValueError):
            self.build(
                type=MonitorType.MQTT,
                name="test",
                hostname="broker.local",
                mqttCheckType="invalid-value",
            )

    def test_mqtt_websocket_path_too_long_raises(self):
        """mqttWebsocketPath exceeding 255 chars raises ValueError."""
        with self.assertRaises(ValueError):
            self.build(
                type=MonitorType.MQTT,
                name="test",
                hostname="broker.local",
                mqttWebsocketPath="x" * 256,
            )

    def test_mqtt_websocket_path_at_limit_ok(self):
        """mqttWebsocketPath at exactly 255 chars is valid."""
        result = self.build(
            type=MonitorType.MQTT,
            name="test",
            hostname="broker.local",
            mqttWebsocketPath="x" * 255,
        )
        self.assertEqual(result["mqttWebsocketPath"], "x" * 255)

    # ─── 7. Low-priority params version gate ──────────────────────────

    def test_low_priority_params_included_on_v2(self):
        """Low-priority params are included on v2 (not type-gated)."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            subtype="http",
            wsSubprotocol="proto",
            wsIgnoreSecWebsocketAcceptHeader=True,
            remoteBrowsersToggle=True,
            remote_browser="chromium",
            screenshot_delay=1000,
            gamedigToken="tok",
            protocol="tcp",
        )
        self.assertEqual(result["subtype"], "http")
        self.assertEqual(result["wsSubprotocol"], "proto")
        self.assertTrue(result["wsIgnoreSecWebsocketAcceptHeader"])
        self.assertTrue(result["remoteBrowsersToggle"])
        self.assertEqual(result["remote_browser"], "chromium")
        self.assertEqual(result["screenshot_delay"], 1000)
        self.assertEqual(result["gamedigToken"], "tok")
        self.assertEqual(result["protocol"], "tcp")

    def test_low_priority_params_none_omitted_on_v2(self):
        """Low-priority params left as None are not included on v2."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            subtype=None,
            wsSubprotocol=None,
        )
        self.assertNotIn("subtype", result)
        self.assertNotIn("wsSubprotocol", result)

    # ─── 8. bearer_token independent of authMethod ────────────────────

    def test_bearer_token_with_auth_method_none(self):
        """bearer_token works when authMethod is NONE."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.NONE,
            bearer_token="my-token",
        )
        self.assertEqual(result["bearer_token"], "my-token")

    def test_bearer_token_with_auth_method_basic(self):
        """bearer_token works when authMethod is HTTP_BASIC."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.HTTP_BASIC,
            basic_auth_user="user",
            basic_auth_pass="pass",
            bearer_token="my-token",
        )
        self.assertEqual(result["bearer_token"], "my-token")

    def test_bearer_token_with_auth_method_ntlm(self):
        """bearer_token works when authMethod is NTLM."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.NTLM,
            bearer_token="my-token",
        )
        self.assertEqual(result["bearer_token"], "my-token")

    def test_bearer_token_with_auth_method_mtls(self):
        """bearer_token works when authMethod is MTLS."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.MTLS,
            bearer_token="my-token",
        )
        self.assertEqual(result["bearer_token"], "my-token")

    def test_bearer_token_with_auth_method_oauth2(self):
        """bearer_token works when authMethod is OAUTH2_CC."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.OAUTH2_CC,
            bearer_token="my-token",
        )
        self.assertEqual(result["bearer_token"], "my-token")

    def test_bearer_token_for_keyword_type(self):
        """bearer_token works for KEYWORD type on v2."""
        result = self.build(
            type=MonitorType.KEYWORD,
            name="test",
            url="http://example.com",
            keyword="up",
            bearer_token="tok",
        )
        self.assertEqual(result["bearer_token"], "tok")

    def test_bearer_token_for_json_query_type(self):
        """bearer_token works for JSON_QUERY type on v2."""
        result = self.build(
            type=MonitorType.JSON_QUERY,
            name="test",
            url="http://example.com",
            jsonPath="$.ok",
            expectedValue="true",
            bearer_token="tok",
        )
        self.assertEqual(result["bearer_token"], "tok")


TAG_ID = 1
MONITOR_ID = 7
TAG_VALUE = "test"
MONITOR_SNAPSHOT = {"id": MONITOR_ID, "name": "monitor 7"}


class TestMonitorTagCacheBugCondition(unittest.TestCase):
    """Bug C (#68) — monitor-list cache write crash on a ``None`` cache.

    Bug condition::

        isBugCondition_C(op, cacheState) == cacheState[MONITOR_LIST] is None

    Both ``add_monitor_tag`` and ``delete_monitor_tag`` patch the cached monitor
    list after the server call, because the ``monitorList`` event does not carry
    updated tags::

        self._event_data[Event.MONITOR_LIST][str(monitor_id)] = self.get_monitor(monitor_id)

    ``_event_data[Event.MONITOR_LIST]`` is initialised to ``None`` in
    ``__init__`` and only populated by the ``monitorList`` event, so on a
    session where that event has not landed the item assignment targets ``None``.
    ``add_status_page`` already guards the mirror case; these two methods do not.

    Unit tests: no live server. ``_call``, ``get_monitor``, ``get_monitors`` and
    ``wait_for_event`` are mocked and the real unbound method is bound to the
    mock instance, so only the cache-write behavior is under test.

    Property 5 (Bug Condition): with a ``None`` cache, neither operation may
    raise ``TypeError``.

    **Validates: Requirements 1.5, 2.6**
    """

    def _api_with_none_cache(self):
        api = MagicMock(spec=UptimeKumaApi)
        api._event_data = {Event.MONITOR_LIST: None}
        api._call.return_value = {"msg": "ok"}
        api.get_monitor.return_value = MONITOR_SNAPSHOT
        # delete_monitor_tag's tag-existence guard reads the monitor list
        api.get_monitors.return_value = [
            {
                "id": MONITOR_ID,
                "tags": [
                    {"monitor_id": MONITOR_ID, "tag_id": TAG_ID, "value": TAG_VALUE}
                ],
            }
        ]
        return api

    def test_add_monitor_tag_does_not_raise_when_cache_is_none(self):
        """add_monitor_tag with _event_data[MONITOR_LIST] = None must not raise."""
        api = self._api_with_none_cache()
        add_monitor_tag = UptimeKumaApi.add_monitor_tag.__get__(api)

        try:
            result = add_monitor_tag(TAG_ID, MONITOR_ID, TAG_VALUE)
        except TypeError as e:
            self.fail(
                "add_monitor_tag raised TypeError with an uninitialised "
                f"monitor-list cache: {e}"
            )

        self.assertEqual(result, {"msg": "ok"})
        api._call.assert_called_once_with(
            "addMonitorTag", (TAG_ID, MONITOR_ID, TAG_VALUE)
        )
        self.assertEqual(
            api._event_data[Event.MONITOR_LIST],
            {str(MONITOR_ID): MONITOR_SNAPSHOT},
        )

    def test_delete_monitor_tag_does_not_raise_when_cache_is_none(self):
        """delete_monitor_tag with _event_data[MONITOR_LIST] = None must not raise."""
        api = self._api_with_none_cache()
        delete_monitor_tag = UptimeKumaApi.delete_monitor_tag.__get__(api)

        try:
            result = delete_monitor_tag(TAG_ID, MONITOR_ID, TAG_VALUE)
        except TypeError as e:
            self.fail(
                "delete_monitor_tag raised TypeError with an uninitialised "
                f"monitor-list cache: {e}"
            )

        self.assertEqual(result, {"msg": "ok"})
        api._call.assert_called_once_with(
            "deleteMonitorTag", (TAG_ID, MONITOR_ID, TAG_VALUE)
        )
        self.assertEqual(
            api._event_data[Event.MONITOR_LIST],
            {str(MONITOR_ID): MONITOR_SNAPSHOT},
        )


OTHER_MONITOR_ID = 99
OTHER_MONITOR_SNAPSHOT = {"id": OTHER_MONITOR_ID, "name": "monitor 99"}
STALE_MONITOR_SNAPSHOT = {"id": MONITOR_ID, "name": "monitor 7", "tags": []}


class TestMonitorTagCachePreservation(unittest.TestCase):
    """Bug C (#68) — preservation baseline for a populated monitor-list cache.

    Bug condition::

        isBugCondition_C(op, cacheState) == cacheState[MONITOR_LIST] is None

    These cases are the ``NOT isBugCondition_C`` side: the cache is already a
    dict, so ``F(X) = F'(X)`` must hold. The observed (UNFIXED) behavior encoded
    here is:

    * the server response from ``_call`` is returned unchanged;
    * the cache entry for the target monitor is written/overwritten with the
      fresh ``get_monitor(monitor_id)`` snapshot, keyed by ``str(monitor_id)``;
    * pre-existing entries for other monitors are left untouched (same object).

    The planned None-guard must not alter any of this.

    Property 6 (Preservation): populated cache behaves identically.

    **Validates: Requirements 3.5**
    """

    def _api_with_populated_cache(self, include_target=False):
        api = MagicMock(spec=UptimeKumaApi)
        cache = {str(OTHER_MONITOR_ID): OTHER_MONITOR_SNAPSHOT}
        if include_target:
            cache[str(MONITOR_ID)] = STALE_MONITOR_SNAPSHOT
        api._event_data = {Event.MONITOR_LIST: cache}
        api._call.return_value = {"msg": "ok"}
        api.get_monitor.return_value = MONITOR_SNAPSHOT
        # delete_monitor_tag's tag-existence guard reads the monitor list
        api.get_monitors.return_value = [
            {
                "id": MONITOR_ID,
                "tags": [
                    {"monitor_id": MONITOR_ID, "tag_id": TAG_ID, "value": TAG_VALUE}
                ],
            }
        ]
        return api

    def _assert_cache_updated(self, api):
        cache = api._event_data[Event.MONITOR_LIST]
        self.assertEqual(cache[str(MONITOR_ID)], MONITOR_SNAPSHOT)
        # unrelated entry untouched, same object
        self.assertIs(cache[str(OTHER_MONITOR_ID)], OTHER_MONITOR_SNAPSHOT)
        self.assertEqual(
            set(cache), {str(MONITOR_ID), str(OTHER_MONITOR_ID)}
        )
        api.get_monitor.assert_called_once_with(MONITOR_ID)

    def test_add_monitor_tag_updates_populated_cache(self):
        """add_monitor_tag adds the fresh snapshot to an already populated cache."""
        api = self._api_with_populated_cache()
        add_monitor_tag = UptimeKumaApi.add_monitor_tag.__get__(api)

        result = add_monitor_tag(TAG_ID, MONITOR_ID, TAG_VALUE)

        self.assertEqual(result, {"msg": "ok"})
        api._call.assert_called_once_with(
            "addMonitorTag", (TAG_ID, MONITOR_ID, TAG_VALUE)
        )
        self._assert_cache_updated(api)

    def test_add_monitor_tag_overwrites_existing_cache_entry(self):
        """add_monitor_tag replaces a stale entry for the same monitor."""
        api = self._api_with_populated_cache(include_target=True)
        add_monitor_tag = UptimeKumaApi.add_monitor_tag.__get__(api)

        result = add_monitor_tag(TAG_ID, MONITOR_ID, TAG_VALUE)

        self.assertEqual(result, {"msg": "ok"})
        self._assert_cache_updated(api)

    def test_delete_monitor_tag_updates_populated_cache(self):
        """delete_monitor_tag refreshes the cache entry for an existing tag."""
        api = self._api_with_populated_cache(include_target=True)
        delete_monitor_tag = UptimeKumaApi.delete_monitor_tag.__get__(api)

        result = delete_monitor_tag(TAG_ID, MONITOR_ID, TAG_VALUE)

        self.assertEqual(result, {"msg": "ok"})
        api._call.assert_called_once_with(
            "deleteMonitorTag", (TAG_ID, MONITOR_ID, TAG_VALUE)
        )
        self._assert_cache_updated(api)

    def test_delete_monitor_tag_absent_tag_still_raises(self):
        """A tag that is not on the monitor still raises and sends no delete."""
        api = self._api_with_populated_cache(include_target=True)
        delete_monitor_tag = UptimeKumaApi.delete_monitor_tag.__get__(api)

        with self.assertRaises(UptimeKumaException):
            delete_monitor_tag(TAG_ID, MONITOR_ID, "not-the-stored-value")

        api._call.assert_not_called()
        cache = api._event_data[Event.MONITOR_LIST]
        self.assertIs(cache[str(MONITOR_ID)], STALE_MONITOR_SNAPSHOT)
        self.assertIs(cache[str(OTHER_MONITOR_ID)], OTHER_MONITOR_SNAPSHOT)


NIGHTLY_VERSION = "3.0.0-dev-nightly-20240101"
GARBAGE_VERSION = "not-a-version"

# Floors an unparseable version must compare as newest against.
GATE_CONSTANTS = ["3.0", "3.1", "3.10", "4.0"]

# The two values that must reach the sentinel instead of escaping as an
# exception. ``None`` is what ``version`` returns when the cached ``info``
# payload carries no "version" key; ``parse_version(None)`` raises TypeError,
# which the original ``except InvalidVersion`` did not catch (#30 defect B).
MISSING_VERSIONS = [None, ""]


class _VersionOnlyApi(UptimeKumaApi):
    """A real ``UptimeKumaApi`` with only the server version wired up.

    ``__init__`` is deliberately not called (it would open a socket.io
    connection). ``_build_monitor_data`` touches no instance state other than
    ``self.version``, so overriding the ``version`` property is enough to drive
    every version gate offline.
    """

    def __init__(self, version):  # noqa: D107 - intentionally skips super()
        self._raw_version = version

    @property
    def version(self) -> str:
        return self._raw_version


class TestUnparseableVersionBugCondition(unittest.TestCase):
    """Bug D (#74) — non-PEP440 server versions crash every version gate.

    Bug condition::

        isBugCondition_D(X) == NOT isPep440Parseable(X)

    The ``version`` property returns the raw string the server reported, and
    roughly ten gate sites feed it straight into ``packaging.version.parse``::

        if parse_version(self.version) >= parse_version("3.1"):

    A nightly build string such as ``3.0.0-dev-nightly-20240101`` (and any other
    non-PEP440 string) makes ``parse_version`` raise ``InvalidVersion``, so every
    version-gated code path fails against such a server.

    The fix introduces a single private choke point, ``_parsed_version()``, which
    returns a max sentinel for unparseable input so an unknown/nightly version is
    treated as newest and all ``>=`` gates evaluate ``True``.

    Unit tests: no live server. A real ``UptimeKumaApi`` subclass supplies the
    raw version string via the ``version`` property; nothing else is mocked, so
    the gates run exactly as they do in production.

    Property 7 (Bug Condition): an unparseable version is treated as newest and
    never raises ``InvalidVersion``.

    **Validates: Requirements 1.6, 1.7, 2.7, 2.8**
    """

    def _assert_newest(self, raw_version):
        """The choke point must parse without raising and compare as newest."""
        api = _VersionOnlyApi(raw_version)

        try:
            parsed = api._parsed_version()
        except Exception as e:  # InvalidVersion pre-fix
            self.fail(
                f"_parsed_version() raised {type(e).__name__} for the "
                f"unparseable version {raw_version!r}: {e}"
            )

        for constant in GATE_CONSTANTS:
            self.assertTrue(
                parsed >= parse_version(constant),
                f"version {raw_version!r} did not compare as newest against "
                f"gate {constant!r}",
            )

    def test_parsed_version_nightly_is_newest(self):
        """A nightly build string parses to a newest-comparing value."""
        self._assert_newest(NIGHTLY_VERSION)

    def test_parsed_version_garbage_is_newest(self):
        """A garbage version string parses to a newest-comparing value."""
        self._assert_newest(GARBAGE_VERSION)

    def _assert_gated_path_runs(self, raw_version):
        """A version-gated code path must execute and take the newest branch."""
        api = _VersionOnlyApi(raw_version)

        try:
            result = api._build_monitor_data(
                type=MonitorType.HTTP,
                name="test",
                url="http://example.com",
                ipFamily="IPv4",
                cacheBust=True,
            )
        except Exception as e:  # InvalidVersion pre-fix
            self.fail(
                f"a version-gated path raised {type(e).__name__} for the "
                f"unparseable version {raw_version!r}: {e}"
            )

        # former 1.22 / 1.23 gates, now unconditional
        self.assertIn("parent", result)
        self.assertIn("timeout", result)
        # former 2.0 gates, now unconditional
        self.assertEqual(result["ipFamily"], "IPv4")
        self.assertTrue(result["cacheBust"])

    def test_gated_path_runs_for_nightly_version(self):
        """_build_monitor_data completes on a nightly version, newest branch taken."""
        self._assert_gated_path_runs(NIGHTLY_VERSION)

    def test_gated_path_runs_for_garbage_version(self):
        """_build_monitor_data completes on a garbage version, newest branch taken."""
        self._assert_gated_path_runs(GARBAGE_VERSION)

    # ─── #30 defect B: a missing version must not escape ──────────────

    def test_missing_version_reaches_the_sentinel(self):
        """``None`` and ``""`` are treated as newest, like any unparseable value.

        Regression test for #30 defect B. The asymmetry was the defect: ``""``
        raises ``InvalidVersion`` and was caught, while ``None`` raises
        ``TypeError`` and escaped from the same function for no stated reason.
        """
        for raw in MISSING_VERSIONS:
            with self.subTest(version=raw):
                self._assert_newest(raw)

    def test_gated_path_runs_for_missing_version(self):
        """A version-gated path completes when the server reported no version."""
        for raw in MISSING_VERSIONS:
            with self.subTest(version=raw):
                self._assert_gated_path_runs(raw)

    def test_missing_version_raises_no_type_error(self):
        """The escaping exception is named explicitly, so the fix cannot regress.

        ``_assert_newest`` above would catch this too, but only as a generic
        failure. Asserting on ``TypeError`` by name pins the actual defect: it
        is what ``except InvalidVersion`` let through.
        """
        for raw in MISSING_VERSIONS:
            with self.subTest(version=raw):
                api = _VersionOnlyApi(raw)
                try:
                    api._parsed_version()
                except TypeError as e:
                    self.fail(
                        f"_parsed_version() let a TypeError escape for "
                        f"version {raw!r}: {e}"
                    )


# A realistic Uptime Kuma 2.x conditions list. The library never validates the
# individual dicts (requirement 3.2), so the shape only has to be plausible.
SAMPLE_CONDITIONS = [
    {
        "type": "expression",
        "expression": {
            "variable": "record",
            "operator": "contains",
            "value": "1.1.1.1",
        },
    },
]


# The monitor dict a mocked ``get_monitor`` hands back to ``edit_monitor``. It
# carries `dns_resolve_type` because ``_check_arguments_monitor`` reads that key
# unconditionally on the merge path.
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

# The ``editMonitor`` payload as OBSERVED against the unfixed code for
# ``edit_monitor(7, interval=20)``, identical on both majors. Note
# ``notificationIDList`` arriving as ``{}``: ``_convert_monitor_input`` rewrites
# the list into a dict, and that conversion must keep happening (requirement
# 3.6), so the observed value is encoded rather than the input one.
EDIT_MONITOR_EXPECTED_PAYLOAD = {
    "id": 7,
    "type": MonitorType.HTTP,
    "name": "existing",
    "url": "http://x",
    "interval": 20,
    "maxretries": 0,
    "retryInterval": 60,
    "maxredirects": 10,
    "accepted_statuscodes": ["200-299"],
    "notificationIDList": {},
    "databaseConnectionString": None,
    "dns_resolve_type": "A",
}


class TestConditionsPreservation(unittest.TestCase):
    """Preservation baseline for the ``conditions`` payload field.

    Everything here holds on the UNFIXED code and must keep holding after the
    gate lands: v2 payloads byte-identical (Property 3), non-``conditions``
    behaviour unchanged on both majors (Property 4), and type validation still
    ahead of version handling (Property 5).

    Recorded observation-first -- each expectation was read off a real run
    against the unfixed code, not derived from the design -- so a later
    divergence means the fix moved something it should not have.
    """

    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.version = "3.0.0"
        self.api._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api)
        self.api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(self.api)
        )
        self.api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(self.api)
        )
        self.build = UptimeKumaApi._build_monitor_data.__get__(self.api)

    def _api_for(self, version):
        """Return a MagicMock UptimeKumaApi reporting the given server version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        return api

    def _build_for(self, version):
        """Return a _build_monitor_data bound to a mock of the given version."""
        return UptimeKumaApi._build_monitor_data.__get__(self._api_for(version))

    # ─── 1-3. v2 payloads byte-identical ──────────────────────────────

    def test_conditions_default_empty_list_on_v2(self):
        """conditions defaults to [] on v2 when the argument is absent.

        **Validates: Requirements 3.1**
        """
        result = self.build(type=MonitorType.HTTP, name="t", url="http://x")
        self.assertIn("conditions", result)
        self.assertEqual(result["conditions"], [])

    def test_conditions_explicit_list_passed_through_by_identity_on_v2(self):
        """An explicit list reaches the payload as the caller's own object.

        ``assertIs`` rather than ``assertEqual`` is the point: it pins the
        no-reallocation rule that is otherwise only stated in prose, so a future
        switch to ``conditions if conditions else list()`` fails a test instead
        of passing review. No individual condition dict is validated.

        **Validates: Requirements 3.2**
        """
        passed = [dict(c) for c in SAMPLE_CONDITIONS]

        result = self.build(
            type=MonitorType.HTTP,
            name="t",
            url="http://x",
            conditions=passed,
        )

        self.assertIs(result["conditions"], passed)

    def test_conditions_explicit_empty_list_identity_on_v2(self):
        """conditions=[] on v2 yields the caller's own empty list, not a fresh one.

        The ``is not None`` form preserves an intentional ``[]``; the rejected
        truthiness form would silently swap in a new object here.

        **Validates: Requirements 3.2**
        """
        passed = []

        result = self.build(
            type=MonitorType.HTTP,
            name="t",
            url="http://x",
            conditions=passed,
        )

        self.assertIs(result["conditions"], passed)

    # ─── 4. TypeError still precedes any version handling ─────────────

    def test_non_list_conditions_raises_type_error_on_both_majors(self):
        """A non-list conditions value raises TypeError first, on both majors.

        This is the ordering proof: on v1 the failure must be ``TypeError``,
        **not** ``UptimeKumaException``. It is the one case that catches a
        version guard being placed above the ``isinstance`` check, in which case
        a bad type on v1 would surface as a version complaint instead.

        **Validates: Requirements 3.3**
        """
        for version in ("3.0.0", "3.0.0-beta.2"):
            build = self._build_for(version)
            for bad in ("not a list", {}):
                with self.subTest(version=version, conditions=bad):
                    with self.assertRaises(TypeError) as ctx:
                        build(
                            type=MonitorType.HTTP,
                            name="t",
                            url="http://x",
                            conditions=bad,
                        )
                    self.assertEqual(
                        str(ctx.exception),
                        "conditions must be a list or None",
                    )
                    self.assertNotIsInstance(ctx.exception, UptimeKumaException)

    # ─── 5. every existing gate still at its own boundary ─────────────

    def _observed_gates(self, version):
        """Gate outcomes observed through the fields each gate emits."""
        data = self._build_for(version)(
            type=MonitorType.KEYWORD,
            name="t",
            url="http://x",
            keyword="up",
            parent=3,
            timeout=42,
            invertKeyword=True,
            ipFamily="IPv4",
            cacheBust=True,
        )
        return {
            "parent": "parent" in data,
            "timeout": "timeout" in data,
            "invertKeyword": "invertKeyword" in data,
            "ipFamily": "ipFamily" in data,
            "cacheBust": "cacheBust" in data,
        }

    # ─── 6. the rest of the v1 payload is untouched ───────────────────

    # ─── 7. edit_monitor's merge path is not disturbed ────────────────

    def test_edit_monitor_merge_path_unchanged_on_both_majors(self):
        """edit_monitor(id_, interval=20) merges and calls exactly as before.

        ``edit_monitor`` never calls ``_build_monitor_data``: it merges
        ``get_monitor(id_)`` output with the kwargs and calls ``editMonitor``
        directly. With no ``conditions`` kwarg the new guard must not interfere
        on either major -- same single ``get_monitor(7)``, same ``editMonitor``
        event, same payload.

        **Validates: Requirements 3.6**
        """
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                api = self._api_for(version)
                api.get_monitor.return_value = dict(EDIT_MONITOR_EXISTING)
                api._call.return_value = {"monitorID": 7, "msg": "Saved."}
                edit_monitor = UptimeKumaApi.edit_monitor.__get__(api)

                result = edit_monitor(7, interval=20)

                self.assertEqual(result, {"monitorID": 7, "msg": "Saved."})
                api.get_monitor.assert_called_once_with(7)
                api._call.assert_called_once_with(
                    "editMonitor", EDIT_MONITOR_EXPECTED_PAYLOAD
                )
                event, payload = api._call.call_args[0]
                self.assertEqual(event, "editMonitor")
                self.assertNotIn("conditions", payload)

    # ─── 8. MonitorBuilder is version-blind and unchanged ─────────────

    def test_monitor_builder_unchanged(self):
        """conditions() returns self and build() emits only explicitly-set fields.

        The builder holds a plain dict with no server connection, so its output
        cannot vary with the server version -- which is exactly why enforcement
        lives at the ``add_monitor`` / ``edit_monitor`` boundary and the builder
        needs no change at all.

        **Validates: Requirements 3.7**
        """
        builder = MonitorBuilder()
        self.assertIs(builder.conditions(SAMPLE_CONDITIONS), builder)

        config = (
            MonitorBuilder()
            .type(MonitorType.HTTP)
            .name("t")
            .url("http://x")
            .conditions(SAMPLE_CONDITIONS)
            .build()
        )
        self.assertEqual(
            config,
            {
                "type": MonitorType.HTTP,
                "name": "t",
                "url": "http://x",
                "conditions": SAMPLE_CONDITIONS,
            },
        )

        # unset fields stay absent
        without = MonitorBuilder().type(MonitorType.HTTP).name("t").build()
        self.assertNotIn("conditions", without)

        # build() output is identical regardless of the server version, because
        # the builder has no connection and therefore no version knowledge
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                self._api_for(version)  # a live client changes nothing here
                repeat = (
                    MonitorBuilder()
                    .type(MonitorType.HTTP)
                    .name("t")
                    .url("http://x")
                    .conditions(SAMPLE_CONDITIONS)
                    .build()
                )
                self.assertEqual(repeat, config)


# The seven adjacent v2-only fields from requirement 1.6, each with the monitor
# type whose block emits it and the minimum sibling arguments that block needs.
# ``_build_monitor_data`` does not run ``_check_arguments_monitor`` (``add_monitor``
# does), so these bases only have to satisfy the type-specific block itself.
ADJACENT_V2_FIELDS = [
    (
        "jsonPathOperator",
        "contains",
        MonitorType.JSON_QUERY,
        {"name": "t", "url": "http://x", "jsonPath": "$.status", "expectedValue": "ok"},
    ),
    (
        "snmp_v3_username",
        "snmpuser",
        MonitorType.SNMP,
        {"name": "t", "hostname": "h", "snmpOid": "1.3.6.1"},
    ),
    ("ping_count", 5, MonitorType.PING, {"name": "t", "hostname": "127.0.0.1"}),
    ("ping_numeric", True, MonitorType.PING, {"name": "t", "hostname": "127.0.0.1"}),
    (
        "ping_per_request_timeout",
        10,
        MonitorType.PING,
        {"name": "t", "hostname": "127.0.0.1"},
    ),
    (
        "mqttWebsocketPath",
        "/ws",
        MonitorType.MQTT,
        {"name": "t", "hostname": "broker.local", "port": 1883, "mqttTopic": "topic"},
    ),
    (
        "mqttCheckType",
        "keyword",
        MonitorType.MQTT,
        {"name": "t", "hostname": "broker.local", "port": 1883, "mqttTopic": "topic"},
    ),
]


class TestAdjacentV2FieldsPreservation(unittest.TestCase):
    """The seven adjacent v2-only fields are gated without new errors.

    Originally Property 6, written when the ratified policy for these seven was
    *silent* omission on v1. **That policy was narrowed by issue #14**: they are
    still omitted and still do not raise, but the omission is now announced with
    an :class:`UnsupportedFieldWarning`, one per call. The class is kept because
    its no-raise assertions are exactly what must survive that change -- a
    warning is not a raise, and these seven must never become the raising kind.

    The absence of a raise is asserted explicitly rather than left implicit, so a
    later change that starts raising for one of these fails a test instead of
    passing unnoticed. That negative assertion is the executable form of the
    policy decision, and it outlived the policy's silent half.
    """

    def _api_for(self, version):
        """Return a MagicMock UptimeKumaApi reporting the given server version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        # MagicMock(spec=...) auto-stubs the guard, so bind the real one too --
        # otherwise a stubbed no-op would hide a misplaced conditions check
        return api

    def _build_for(self, version):
        """Return a _build_monitor_data bound to a mock of the given version."""
        return UptimeKumaApi._build_monitor_data.__get__(self._api_for(version))

    # ─── 1. absent on v1, present on v2 ───────────────────────────────

    def test_adjacent_fields_present_on_v2_exactly_as_before(self):
        """Each of the seven still reaches the v2 payload with its own value.

        The v2 side is the preservation half of the property: gating in place
        must not change what a 2.x server receives.

        **Validates: Requirements 3.5**
        """
        build = self._build_for("3.0.0")
        for field, value, mtype, base in ADJACENT_V2_FIELDS:
            with self.subTest(field=field, version="3.0.0"):
                result = build(type=mtype, **base, **{field: value})
                self.assertIn(field, result)
                self.assertEqual(result[field], value)

    # ─── 2. the explicit no-raise assertion ───────────────────────────

    # ─── 3. argument validation still fires on both majors ────────────

    def test_mqtt_check_type_invalid_raises_value_error_on_both_majors(self):
        """An invalid mqttCheckType is a ValueError regardless of server version.

        The ``ValueError`` checks are argument validation, not payload emission:
        a bad value is bad on either major, so gating them would be a silent
        relaxation. They must keep firing on v1 even though the field itself is
        now dropped from the v1 payload.

        **Validates: Requirements 3.5**
        """
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError) as ctx:
                    self._build_for(version)(
                        type=MonitorType.MQTT,
                        name="t",
                        hostname="broker.local",
                        mqttCheckType="invalid-value",
                    )
                self.assertEqual(
                    str(ctx.exception),
                    "mqttCheckType must be 'keyword' or 'json-query', "
                    "got: invalid-value",
                )

    def test_mqtt_websocket_path_too_long_raises_value_error_on_both_majors(self):
        """An over-length mqttWebsocketPath is a ValueError on both majors.

        **Validates: Requirements 3.5**
        """
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError) as ctx:
                    self._build_for(version)(
                        type=MonitorType.MQTT,
                        name="t",
                        hostname="broker.local",
                        mqttWebsocketPath="x" * 256,
                    )
                self.assertEqual(
                    str(ctx.exception),
                    "mqttWebsocketPath must not exceed 255 characters",
                )


# ── Seeded generated-input corpora for the conditions gate ───────────
# Hypothesis is deliberately NOT a project dependency, so generation is a
# fixed-seed ``random.Random`` walk over a deliberately constrained input space,
# matching the ``generate_valid_pep440_versions`` idiom already in this file. A
# fixed seed keeps a CI failure reproducible and bisectable; the bounded case
# count keeps the suite fast.
CONDITIONS_PBT_SEED = 20260801
CONDITIONS_PBT_CASES = 48

# Sentinel for "the caller passed no ``conditions`` argument at all", which is
# the implicit sub-case of the bug condition and is distinct from an explicit
# ``conditions=[]``. A plain ``None`` would not do: ``conditions=None`` is an
# explicit argument that happens to be falsy.
CONDITIONS_ABSENT = object()

# Monitor types paired with the minimum sibling arguments their type-specific
# block needs. ``_build_monitor_data`` does not run ``_check_arguments_monitor``
# (``add_monitor`` does), so these bases only have to satisfy the block itself.
CONDITIONS_PBT_TYPE_BASES = [
    (MonitorType.HTTP, {"url": "http://x"}),
    (MonitorType.PING, {"hostname": "127.0.0.1"}),
    (MonitorType.PORT, {"hostname": "127.0.0.1", "port": 8080}),
    (
        MonitorType.DNS,
        {"hostname": "example.com", "dns_resolve_server": "1.1.1.1", "port": 53},
    ),
    (MonitorType.KEYWORD, {"url": "http://x", "keyword": "ok"}),
    (MonitorType.PUSH, {}),
    (
        MonitorType.JSON_QUERY,
        {"url": "http://x", "jsonPath": "$.status", "expectedValue": "ok"},
    ),
    (
        MonitorType.MQTT,
        {"hostname": "broker.local", "port": 1883, "mqttTopic": "topic"},
    ),
]

# Optional parameters mixed into the generated calls, each with values that are
# legal for the parameter. The point of varying them is Property 1's "any
# combination of other parameters" -- values that trip the preamble's own
# ``ValueError`` checks are excluded on purpose, because those cases are
# already covered directly and would only mask the conditions assertion here.
CONDITIONS_PBT_OPTIONAL_PARAMS = [
    ("interval", [20, 60, 300]),
    ("retryInterval", [30, 90]),
    ("maxretries", [0, 3]),
    ("upsideDown", [True, False]),
    ("resendInterval", [0, 10]),
    ("description", [None, "generated"]),
    ("parent", [None, 4]),
    ("timeout", [16, 48]),
    ("expiryNotification", [True, False]),
    ("ignoreTls", [True, False]),
    ("ipFamily", [None, "IPv4", "IPv6"]),
    ("cacheBust", [True, False]),
    ("responseMaxLength", [50, 1000]),
    ("accepted_statuscodes", [["200-299"], ["200", "301"]]),
]

CONDITION_VARIABLES = ["record", "status_code", "response_time", "body"]
CONDITION_OPERATORS = ["contains", "not_contains", "equals", "gt", "lt"]
CONDITION_VALUES = ["1.1.1.1", "200", "ok", "500", ""]


def generate_conditions_monitor_cases(
    count=CONDITIONS_PBT_CASES, seed=CONDITIONS_PBT_SEED
):
    """Generate ``(monitor_type, kwargs, conditions_arg)`` triples for the v1 gate.

    Every monitor type is emitted once with **no** ``conditions`` argument
    first, because the implicit sub-case is the whole regression: it fires with
    no caller opt-in whatsoever. The remaining cases are seeded-random
    combinations of type, optional parameters and one of the three
    ``conditions`` shapes that matter -- absent, an explicit empty list, and an
    explicit truthy list -- so the corpus straddles the guard's truthiness test
    rather than only one side of it.
    """
    rng = random.Random(seed)
    cases = [
        (mtype, dict(base), CONDITIONS_ABSENT)
        for mtype, base in CONDITIONS_PBT_TYPE_BASES
    ]
    while len(cases) < count:
        mtype, base = rng.choice(CONDITIONS_PBT_TYPE_BASES)
        kwargs = dict(base)
        chosen = rng.sample(
            CONDITIONS_PBT_OPTIONAL_PARAMS,
            rng.randint(0, min(4, len(CONDITIONS_PBT_OPTIONAL_PARAMS))),
        )
        for name, values in chosen:
            kwargs[name] = rng.choice(values)
        conditions_arg = rng.choice(
            [
                CONDITIONS_ABSENT,
                [],
                [_generate_condition(rng) for _ in range(rng.randint(1, 3))],
            ]
        )
        cases.append((mtype, kwargs, conditions_arg))
    return cases


def _generate_condition(rng):
    """One plausible Uptime Kuma 2.x condition dict.

    The library never validates individual condition dicts (requirement 3.2),
    so the shape only has to be plausible; what is being generated is variety,
    not validity.
    """
    condition = {
        "type": rng.choice(["expression", "group"]),
        "andOr": rng.choice(["and", "or"]),
        "expression": {
            "variable": rng.choice(CONDITION_VARIABLES),
            "operator": rng.choice(CONDITION_OPERATORS),
            "value": rng.choice(CONDITION_VALUES),
        },
    }
    if rng.random() < 0.3:
        condition["children"] = [
            {
                "type": "expression",
                "expression": {
                    "variable": rng.choice(CONDITION_VARIABLES),
                    "operator": rng.choice(CONDITION_OPERATORS),
                    "value": rng.choice(CONDITION_VALUES),
                },
            }
        ]
    return condition


def generate_condition_list_shapes(
    count=CONDITIONS_PBT_CASES, seed=CONDITIONS_PBT_SEED
):
    """Generate ``conditions`` list shapes for the v2 identity property.

    Starts from the two boundary shapes -- an explicit empty list and the
    canonical sample -- then adds seeded-random lists of one to four condition
    dicts, some with nested children.
    """
    rng = random.Random(seed)
    shapes = [[], [dict(c) for c in SAMPLE_CONDITIONS]]
    while len(shapes) < count:
        shapes.append(
            [_generate_condition(rng) for _ in range(rng.randint(1, 4))]
        )
    return shapes


class TestConditionsGeneratedInputs(unittest.TestCase):
    """Seeded generated-input tests for the ``conditions`` version gate.

    The ``FOR ALL input`` in Properties 1, 2 and 3 covers a domain far too wide
    to hand-enumerate: every server version, every monitor type, every
    combination of other parameters, every condition-list shape. These tests
    walk seeded slices of that domain instead of a few hand-picked points.

    ``subTest`` is used throughout so a generated counterexample is identifiable
    from the failure output alone -- with a fixed seed, the reported case is
    reproducible by re-running the same command.
    """

    def _api_for(self, version):
        """Return a MagicMock UptimeKumaApi reporting the given server version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        # MagicMock(spec=...) auto-stubs both of these; bind the real ones or a
        # stubbed no-op guard would silently pass every rejection assertion
        return api

    def _build_for(self, version):
        """Return a _build_monitor_data bound to a mock of the given version."""
        return UptimeKumaApi._build_monitor_data.__get__(self._api_for(version))

    # ─── 1. the version boundary, over generated PEP440 strings ───────

    # ─── 2. monitor type x parameter combinations on v1 ───────────────

    # ─── 3. condition-list shapes on v2 keep caller identity ──────────

    def test_generated_condition_shapes_pass_through_by_identity_on_v2(self):
        """PBT: the emitted value is the same object the caller passed in.

        The no-reallocation property across generated shapes: empty lists,
        single conditions, multi-condition lists and nested-children forms all
        arrive in the payload as the caller's own object, with the nested dicts
        untouched too. This is what fails if the expression is ever changed to
        ``conditions if conditions else list()``, which allocates a fresh list
        and conflates ``None`` with ``[]``.

        **Validates: Requirements 3.1, 3.2**
        """
        build = self._build_for("3.0.0")
        for shape in generate_condition_list_shapes():
            with self.subTest(size=len(shape), conditions=shape):
                result = build(
                    type=MonitorType.HTTP,
                    name="t",
                    url="http://x",
                    conditions=shape,
                )
                self.assertIs(result["conditions"], shape)
                # no deep copy either: the nested dicts are the caller's own
                for index, condition in enumerate(shape):
                    self.assertIs(result["conditions"][index], condition)


# Monitor types that older servers lacked (former version floors), each with
# the minimum companion arguments its type-specific block needs.
# ``_build_monitor_data`` does not run ``_check_arguments_monitor``
# (``add_monitor`` does), so these bases only have to satisfy the block itself.
FORMERLY_GATED_TYPE_CASES = {
    MonitorType.RABBITMQ: dict(
        rabbitmqNodes=["http://127.0.0.1:15672"],
        rabbitmqUsername="guest",
        rabbitmqPassword="guest",
    ),
    MonitorType.SNMP: dict(
        hostname="127.0.0.1",
        port=161,
        snmpOid="1.3.6.1.2.1.1.3.0",
        snmpVersion="2c",
        jsonPath="$",
        expectedValue="1",
    ),
    MonitorType.SMTP: dict(hostname="127.0.0.1", port=25),
    MonitorType.SYSTEM_SERVICE: dict(system_service_name="cron"),
    MonitorType.MANUAL: dict(),
    MonitorType.WEBSOCKET_UPGRADE: dict(url="ws://127.0.0.1:8080"),
    MonitorType.GLOBALPING: dict(hostname="example.com"),
    MonitorType.SIP_OPTIONS: dict(hostname="127.0.0.1", port=5060),
    MonitorType.ORACLEDB: dict(databaseConnectionString="localhost:1521/ORCL"),
    MonitorType.NTP: dict(hostname="pool.ntp.org"),
    MonitorType.SFTP: dict(hostname="127.0.0.1", sshUsername="u"),
}


class TestV2OnlyMonitorTypesPreservation(unittest.TestCase):
    """Everything the type gate must leave alone.

    The type gate is an empty table today, and a table entry goes wrong by
    catching types it should not. These are the negative assertions: a future
    over-broad gate fails a test here rather than passing unnoticed.
    """

    def setUp(self):
        self.maxDiff = None

    def _api_for(self, version):
        """Return a MagicMock UptimeKumaApi reporting the given server version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(api)
        )
        return api

    def _build_for(self, version):
        """Return a _build_monitor_data bound to a mock at the given version."""
        return UptimeKumaApi._build_monitor_data.__get__(self._api_for(version))

    def test_v2_only_types_accepted_on_v2_with_companion_fields(self):
        """On v2 all eight types build a payload carrying their companion fields.

        The gate must not narrow v2 in any way, so every companion argument is
        asserted present in the built payload rather than merely "no exception
        was raised".

        **Validates: Requirements 3.1, issue #29**
        """
        build = self._build_for("3.0.0")
        # ``rabbitmqNodes`` is JSON-serialised on the way into the payload, so
        # the expectation records the observed wire value rather than the input.
        expected_companions = {
            MonitorType.RABBITMQ: {
                "rabbitmqNodes": '["http://127.0.0.1:15672"]',
                "rabbitmqUsername": "guest",
                "rabbitmqPassword": "guest",
            },
            MonitorType.SNMP: {
                "snmpOid": "1.3.6.1.2.1.1.3.0",
                "snmpVersion": "2c",
            },
            MonitorType.SMTP: {"smtpSecurity": "starttls"},
            MonitorType.SYSTEM_SERVICE: {"config": {"systemServiceName": "cron"}},
            MonitorType.MANUAL: {},
            MonitorType.WEBSOCKET_UPGRADE: {"url": "ws://127.0.0.1:8080"},
            MonitorType.GLOBALPING: {"hostname": "example.com"},
            MonitorType.SIP_OPTIONS: {"hostname": "127.0.0.1"},
            MonitorType.ORACLEDB: {
                "databaseConnectionString": "localhost:1521/ORCL",
            },
            MonitorType.NTP: {"hostname": "pool.ntp.org"},
            MonitorType.SFTP: {"hostname": "127.0.0.1", "sshUsername": "u"},
        }
        for type_, kwargs in FORMERLY_GATED_TYPE_CASES.items():
            with self.subTest(type=type_):
                result = build(type=type_, name="t", **kwargs)
                self.assertEqual(result["type"], type_)
                for key, value in expected_companions[type_].items():
                    self.assertIn(key, result)
                    self.assertEqual(result[key], value)

    def test_unparseable_version_permits_v2_only_types(self):
        """A nightly/garbage version string is treated as newest, so types pass.

        The gate routes through ``_parsed_version()`` like every other gate, so a
        server reporting a non-PEP440 build is treated as the newest version
        rather than having all four types rejected.

        **Validates: Requirements 3.7**
        """
        for raw_version in (NIGHTLY_VERSION, GARBAGE_VERSION):
            with self.subTest(version=raw_version):
                # guard the premise: these really are unparseable
                with self.assertRaises(InvalidVersion):
                    parse_version(raw_version)
                build = self._build_for(raw_version)
                for type_, kwargs in FORMERLY_GATED_TYPE_CASES.items():
                    result = build(type=type_, name="t", **kwargs)
                    self.assertEqual(result["type"], type_)

    def test_monitor_type_enum_is_untouched(self):
        """All four members still exist with their documented string values.

        The fix gates these types; it does not remove, rename or re-value any of
        them. A caller's ``MonitorType.SNMP`` keeps working against a 2.x server.

        **Validates: Requirements 3.5**
        """
        self.assertEqual(MonitorType.RABBITMQ.value, "rabbitmq")
        self.assertEqual(MonitorType.SNMP.value, "snmp")
        self.assertEqual(MonitorType.SMTP.value, "smtp")
        self.assertEqual(MonitorType.SYSTEM_SERVICE.value, "system-service")

    def test_no_new_public_export(self):
        """The type set and the guard stay private -- no new API surface.

        Requirement 3.5. ``docs/api.rst`` is driven by autodoc over the public
        exports, so an accidental export here would silently publish an internal.
        """
        import uptime_kuma_api

        self.assertNotIn("_MONITOR_TYPE_FLOORS", dir(uptime_kuma_api))
        self.assertNotIn("_check_monitor_type_supported", uptime_kuma_api.__all__
                         if hasattr(uptime_kuma_api, "__all__") else [])
        # nothing this fix introduced may be reachable from the package root
        public = [n for n in dir(uptime_kuma_api) if not n.startswith("_")]
        self.assertFalse(
            [n for n in public if "FLOORS" in n.upper()],
            f"unexpected public export: {public}",
        )


class TestNTPMonitorType(unittest.TestCase):
    """Tests for NTP monitor type fields."""

    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.version = "3.0.0"
        self.api._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api)
        self.api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(self.api)
        )
        self.api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(self.api)
        )
        self.api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(self.api)
        )
        self.build = UptimeKumaApi._build_monitor_data.__get__(self.api)

    def _build_at_version(self, version):
        """Return a _build_monitor_data bound to a mock at the given version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(api)
        )
        return UptimeKumaApi._build_monitor_data.__get__(api)

    # ─── NTP accepted ────────────────────────────────────────

    def test_ntp_accepted_on_v2_5(self):
        """NTP type is accepted."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
        )
        self.assertEqual(result["type"], MonitorType.NTP)
        self.assertEqual(result["hostname"], "pool.ntp.org")

    def test_ntp_default_port_123(self):
        """NTP type defaults port to 123 when not specified."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
        )
        self.assertEqual(result["port"], 123)

    def test_ntp_custom_port(self):
        """NTP type respects a custom port."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
            port=1234,
        )
        self.assertEqual(result["port"], 1234)

    # ─── NTP threshold fields version-gated ───────────────────────────

    def test_ntp_thresholds_included_on_v2_5(self):
        """NTP threshold fields are included."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
            ntpStratumThreshold=3,
            ntpTimeOffsetThreshold=500,
            ntpRootDispersionThreshold=250,
        )
        self.assertEqual(result["ntpStratumThreshold"], 3)
        self.assertEqual(result["ntpTimeOffsetThreshold"], 500)
        self.assertEqual(result["ntpRootDispersionThreshold"], 250)

    def test_ntp_thresholds_omitted_when_none(self):
        """NTP threshold fields are omitted when not supplied (None)."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
        )
        self.assertNotIn("ntpStratumThreshold", result)
        self.assertNotIn("ntpTimeOffsetThreshold", result)
        self.assertNotIn("ntpRootDispersionThreshold", result)

    def test_ntp_thresholds_not_emitted_for_other_types(self):
        """NTP threshold fields are not emitted for non-NTP types ."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            ntpStratumThreshold=3,
            ntpTimeOffsetThreshold=500,
            ntpRootDispersionThreshold=250,
        )
        self.assertNotIn("ntpStratumThreshold", result)
        self.assertNotIn("ntpTimeOffsetThreshold", result)
        self.assertNotIn("ntpRootDispersionThreshold", result)

    # ─── NTP supports ipFamily ────────────────────────────────────────

    def test_ntp_supports_ip_family(self):
        """NTP type accepts ipFamily."""
        result = self.build(
            type=MonitorType.NTP,
            name="NTP test",
            hostname="pool.ntp.org",
            ipFamily="IPv4",
        )
        self.assertEqual(result["ipFamily"], "IPv4")


class TestOracleDBMonitorType(unittest.TestCase):
    """Tests for OracleDB monitor type fields and version gating."""

    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.version = "3.0.0"
        self.api._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api)
        self.api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(self.api)
        )
        self.api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(self.api)
        )
        self.api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(self.api)
        )
        self.build = UptimeKumaApi._build_monitor_data.__get__(self.api)

    def _build_at_version(self, version):
        """Return a _build_monitor_data bound to a mock at the given version."""
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(api)
        )
        api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(api)
        )
        api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(api)
        )
        return UptimeKumaApi._build_monitor_data.__get__(api)

    # ─── OracleDB accepted ──────────────────────────────────

    def test_oracledb_accepted_on_v2_3(self):
        """OracleDB type is accepted."""
        build = self._build_at_version("3.0.0")
        result = build(
            type=MonitorType.ORACLEDB,
            name="Oracle test",
            databaseConnectionString="localhost:1521/ORCL",
        )
        self.assertEqual(result["type"], MonitorType.ORACLEDB)
        self.assertEqual(result["databaseConnectionString"], "localhost:1521/ORCL")

    def test_oracledb_includes_database_query(self):
        """OracleDB type emits databaseQuery when supplied."""
        result = self.build(
            type=MonitorType.ORACLEDB,
            name="Oracle test",
            databaseConnectionString="localhost:1521/ORCL",
            databaseQuery="SELECT 1 FROM DUAL",
        )
        self.assertEqual(result["databaseQuery"], "SELECT 1 FROM DUAL")

    def test_oracledb_without_query(self):
        """OracleDB type works without databaseQuery."""
        result = self.build(
            type=MonitorType.ORACLEDB,
            name="Oracle test",
            databaseConnectionString="localhost:1521/ORCL",
        )
        self.assertEqual(result["type"], MonitorType.ORACLEDB)


class TestAuthMethodBearer(unittest.TestCase):
    """Tests for AuthMethod.BEARER enum and bearer_token emission."""

    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.version = "3.0.0"
        self.api._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api)
        self.api._withheld_monitor_fields = (
            UptimeKumaApi._withheld_monitor_fields.__get__(self.api)
        )
        self.api._warn_withheld_monitor_fields = (
            UptimeKumaApi._warn_withheld_monitor_fields.__get__(self.api)
        )
        self.api._check_monitor_type_supported = (
            UptimeKumaApi._check_monitor_type_supported.__get__(self.api)
        )
        self.build = UptimeKumaApi._build_monitor_data.__get__(self.api)

    def test_bearer_enum_value(self):
        """AuthMethod.BEARER has the correct string value."""
        self.assertEqual(AuthMethod.BEARER.value, "bearer")

    def test_bearer_emits_bearer_token(self):
        """AuthMethod.BEARER emits bearer_token in the payload."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.BEARER,
            bearer_token="my-secret-token",
        )
        self.assertEqual(result["authMethod"], AuthMethod.BEARER)
        self.assertEqual(result["bearer_token"], "my-secret-token")

    def test_bearer_does_not_emit_basic_auth_fields(self):
        """AuthMethod.BEARER does not emit basic_auth_user/pass."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.BEARER,
            bearer_token="tok",
        )
        self.assertNotIn("basic_auth_user", result)
        self.assertNotIn("basic_auth_pass", result)

    def test_bearer_does_not_emit_oauth_fields(self):
        """AuthMethod.BEARER does not emit OAuth2 fields."""
        result = self.build(
            type=MonitorType.HTTP,
            name="test",
            url="http://example.com",
            authMethod=AuthMethod.BEARER,
            bearer_token="tok",
        )
        self.assertNotIn("oauth_token_url", result)
        self.assertNotIn("oauth_client_id", result)


class TestSFTPMonitorType(unittest.TestCase):
    """SFTP monitor type: fields and defaults."""

    def _build_at_version(self, version):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        for name in ("_parsed_version", "_withheld_monitor_fields",
                     "_warn_withheld_monitor_fields",
                     "_check_monitor_type_supported"):
            setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
        return UptimeKumaApi._build_monitor_data.__get__(api)

    def test_sftp_accepted_on_v2_5_4_with_all_fields(self):
        result = self._build_at_version("3.0.0")(
            type=MonitorType.SFTP, name="s", hostname="h",
            sshUsername="u", sshAuthMethod="privateKey", sshPassword="pw",
            sshPrivateKey="KEY", sshPassphrase="pp", sftpPath="/data",
        )
        self.assertEqual(result["type"], MonitorType.SFTP)
        self.assertEqual(result["sshUsername"], "u")
        self.assertEqual(result["sshAuthMethod"], "privateKey")
        self.assertEqual(result["sshPassword"], "pw")
        self.assertEqual(result["sshPrivateKey"], "KEY")
        self.assertEqual(result["sshPassphrase"], "pp")
        self.assertEqual(result["sftpPath"], "/data")

    def test_sftp_default_port_22(self):
        result = self._build_at_version("3.0.0")(
            type=MonitorType.SFTP, name="s", hostname="h", sshUsername="u")
        self.assertEqual(result["port"], 22)

    def test_sftp_custom_port_respected(self):
        result = self._build_at_version("3.0.0")(
            type=MonitorType.SFTP, name="s", hostname="h", sshUsername="u",
            port=2222)
        self.assertEqual(result["port"], 2222)

    def test_sftp_optional_fields_omitted_when_none(self):
        result = self._build_at_version("3.0.0")(
            type=MonitorType.SFTP, name="s", hostname="h", sshUsername="u")
        for name in ("sshAuthMethod", "sshPassword", "sshPrivateKey",
                     "sshPassphrase", "sftpPath"):
            self.assertNotIn(name, result)

    def test_sftp_requires_hostname_and_username(self):
        from uptime_kuma_api.api import _check_arguments_monitor
        build = self._build_at_version("3.0.0")
        ok = build(type=MonitorType.SFTP, name="s", hostname="h",
                   sshUsername="u")
        _check_arguments_monitor(ok)
        for missing in ("hostname", "sshUsername"):
            with self.subTest(missing=missing):
                with self.assertRaises(TypeError):
                    _check_arguments_monitor(dict(ok, **{missing: None}))

    def test_ssh_fields_not_emitted_for_other_types(self):
        result = self._build_at_version("3.0.0")(
            type=MonitorType.HTTP, name="t", url="http://example.com",
            sshUsername="u", sshPassword="pw", sftpPath="/x")
        for name in ("sshUsername", "sshPassword", "sftpPath"):
            self.assertNotIn(name, result)


if __name__ == "__main__":    unittest.main()
