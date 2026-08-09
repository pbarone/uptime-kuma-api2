import unittest
import warnings
from unittest.mock import MagicMock, patch
from uptime_kuma_api.api import UptimeKumaApi
from uptime_kuma_api import UnsupportedFieldWarning


class TestStatusPageV2(unittest.TestCase):
    """Unit tests for _build_status_page_data version-gated behavior.

    These tests mock the version attribute and call _build_status_page_data
    directly — no live server connection required.
    """

    def setUp(self):
        self.api_v2 = MagicMock(spec=UptimeKumaApi)
        self.api_v2.version = "2.4.0"
        # bind the real version-gate choke point so gates parse self.version
        self.api_v2._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api_v2)
        self.build_v2 = UptimeKumaApi._build_status_page_data.__get__(self.api_v2)

        self.api_v1 = MagicMock(spec=UptimeKumaApi)
        self.api_v1.version = "1.23.2"
        self.api_v1._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api_v1)
        self.build_v1 = UptimeKumaApi._build_status_page_data.__get__(self.api_v1)

    # --- Test 1: v2 analytics fields included when provided ---
    def test_v2_analytics_fields_included(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            analyticsType="plausible",
            analyticsId="my-domain.com",
            analyticsScriptUrl="https://plausible.io/js/script.js",
        )
        self.assertEqual(config["analyticsType"], "plausible")
        self.assertEqual(config["analyticsId"], "my-domain.com")
        self.assertEqual(config["analyticsScriptUrl"], "https://plausible.io/js/script.js")

    # --- Test 2: v2 googleAnalyticsId omitted from config ---
    def test_v2_google_analytics_id_omitted(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            googleAnalyticsId="UA-12345",
        )
        self.assertNotIn("googleAnalyticsId", config)

    # --- Test 3: v1 googleAnalyticsId included in config ---
    def test_v1_google_analytics_id_included(self):
        _, config, _, _ = self.build_v1(
            slug="test", id=1, title="Test Page",
            googleAnalyticsId="UA-12345",
        )
        self.assertIn("googleAnalyticsId", config)
        self.assertEqual(config["googleAnalyticsId"], "UA-12345")

    # --- Test 4: v1 v2 analytics params silently discarded ---
    def test_v1_v2_analytics_params_discarded(self):
        _, config, _, _ = self.build_v1(
            slug="test", id=1, title="Test Page",
            analyticsType="plausible",
            analyticsId="my-domain.com",
            analyticsScriptUrl="https://plausible.io/js/script.js",
        )
        self.assertNotIn("analyticsType", config)
        self.assertNotIn("analyticsId", config)
        self.assertNotIn("analyticsScriptUrl", config)

    # --- Test 5: v2 password omitted even when provided ---
    def test_v2_password_omitted(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            password="secret123",
        )
        self.assertNotIn("password", config)

    # --- Test 6: v1 password included when provided ---
    def test_v1_password_included(self):
        _, config, _, _ = self.build_v1(
            slug="test", id=1, title="Test Page",
            password="secret123",
        )
        self.assertIn("password", config)
        self.assertEqual(config["password"], "secret123")

    # --- Test 7: v1 password omitted when not provided ---
    def test_v1_password_omitted_when_not_provided(self):
        _, config, _, _ = self.build_v1(
            slug="test", id=1, title="Test Page",
        )
        self.assertNotIn("password", config)

    # --- Test 8: v2 showOnlyLastHeartbeat and rssTitle included when provided ---
    def test_v2_new_fields_included(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            showOnlyLastHeartbeat=True,
            rssTitle="Custom RSS Title",
        )
        self.assertEqual(config["showOnlyLastHeartbeat"], True)
        self.assertEqual(config["rssTitle"], "Custom RSS Title")

    # --- Test 9: v2 showOnlyLastHeartbeat and rssTitle omitted when None ---
    def test_v2_new_fields_omitted_when_none(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            showOnlyLastHeartbeat=None,
            rssTitle=None,
        )
        self.assertNotIn("showOnlyLastHeartbeat", config)
        self.assertNotIn("rssTitle", config)

    # --- Test 10: v1 showOnlyLastHeartbeat and rssTitle omitted regardless of value ---
    def test_v1_new_fields_omitted_regardless(self):
        _, config, _, _ = self.build_v1(
            slug="test", id=1, title="Test Page",
            showOnlyLastHeartbeat=True,
            rssTitle="Custom RSS Title",
        )
        self.assertNotIn("showOnlyLastHeartbeat", config)
        self.assertNotIn("rssTitle", config)

    # --- Test 11: v2 analytics keys must be PRESENT even when None ---
    def test_v2_analytics_keys_always_present_when_none(self):
        """Regression: the v2 server rejects the save with "Invalid analytics
        type" when analyticsType is absent from the payload. Verified against
        2.4.0: null is accepted, an absent key is not. Omitting the key broke
        save_status_page for every page with no analytics configured, which in
        turn broke post_incident and unpin_incident (both call save)."""
        _, config, _, _ = self.build_v2(slug="test", id=1, title="Test Page")
        self.assertIn("analyticsType", config)
        self.assertIsNone(config["analyticsType"])
        self.assertIn("analyticsId", config)
        self.assertIn("analyticsScriptUrl", config)

    # --- Test 12: explicit None is still sent as null, not dropped ---
    def test_v2_analytics_explicit_none_sent_as_null(self):
        _, config, _, _ = self.build_v2(
            slug="test", id=1, title="Test Page",
            analyticsType=None, analyticsId=None, analyticsScriptUrl=None,
        )
        self.assertIsNone(config["analyticsType"])
        self.assertIsNone(config["analyticsId"])
        self.assertIsNone(config["analyticsScriptUrl"])

    # --- Test 13: v1 must NOT gain the v2 analytics keys ---
    def test_v1_analytics_keys_absent_when_none(self):
        _, config, _, _ = self.build_v1(slug="test", id=1, title="Test Page")
        self.assertNotIn("analyticsType", config)
        self.assertNotIn("analyticsId", config)
        self.assertNotIn("analyticsScriptUrl", config)


class TestGetStatusPageSslVerify(unittest.TestCase):
    """Bug B (#65): ``ssl_verify`` must reach the ``get_status_page`` HTTP fetch.

    ``__init__`` funnels ``ssl_verify`` into ``socketio.Client`` only and never
    stores it on the instance, so ``get_status_page``'s ``requests.get`` call
    omits any ``verify=`` argument. A caller that asked for ``ssl_verify=False``
    still gets TLS verification on the HTTP leg and fails against a self-signed
    certificate.

    No live server: ``socketio.Client``/``connect`` are patched, ``_call`` is
    stubbed, and ``requests.get`` is a mock whose call kwargs are inspected.
    """

    # Minimal HTTP payload shaped like /api/status-page/<slug>.
    HTTP_PAYLOAD = {
        "config": {"slug": "slug1", "title": "status page 1"},
        "incident": None,
        "publicGroupList": [],
        "maintenanceList": [],
    }

    @staticmethod
    def _build_api(ssl_verify):
        """Construct an UptimeKumaApi with the transport fully mocked."""
        with patch('uptime_kuma_api.api.UptimeKumaApi.connect'), \
                patch('uptime_kuma_api.api.socketio.Client') as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            api = UptimeKumaApi("https://fake:3001", ssl_verify=ssl_verify)
        # The socket.io leg is not under test here; only the HTTP leg is.
        api._call = MagicMock(return_value={"config": {}})
        return api

    def _fetch_status_page(self, ssl_verify, payload=None):
        """Call get_status_page and return the mocked requests.get."""
        _, mock_get = self._fetch_status_page_result(ssl_verify, payload)
        return mock_get

    def _fetch_status_page_result(self, ssl_verify, payload=None):
        """Call get_status_page; return (returned dict, mocked requests.get)."""
        api = self._build_api(ssl_verify)
        with patch('uptime_kuma_api.api.requests.get') as mock_get:
            mock_get.return_value.json.return_value = (
                self.HTTP_PAYLOAD if payload is None else payload
            )
            page = api.get_status_page("slug1")
        return page, mock_get

    # --- Property 3: Bug Condition - verify forwarded when ssl_verify=False ---
    def test_ssl_verify_false_forwarded_to_requests_get(self):
        """ssl_verify=False must be forwarded as verify=False to requests.get.

        **Validates: Requirements 2.4, 2.5**

        Bug condition: isBugCondition_B(X) - X.performsRequestsGet AND
        X.ssl_verify = False. EXPECTED TO FAIL on unfixed code, where the
        request carries only ``timeout=`` and no ``verify=`` at all.
        """
        mock_get = self._fetch_status_page(ssl_verify=False)

        mock_get.assert_called_once()
        kwargs = mock_get.call_args.kwargs
        self.assertIn(
            "verify", kwargs,
            "requests.get was called without any verify= argument "
            f"(kwargs={kwargs!r}); ssl_verify=False was ignored on the HTTP leg",
        )
        self.assertFalse(
            kwargs["verify"],
            f"expected verify=False, got verify={kwargs['verify']!r}",
        )

    # ------------------------------------------------------------------
    # Property 4: Preservation - default ssl_verify=True path unchanged
    #
    # These assert the OUTSIDE of the bug condition (¬isBugCondition_B):
    # with the default ssl_verify=True, get_status_page must keep returning
    # exactly the dict structure it returned before the fix, including the
    # incident/incidents dual-key shape, the merged config, and the existing
    # requests.get arguments (URL + timeout).
    #
    # The complementary `verify=True` assertion could not hold on the unfixed
    # code (no `verify=` was passed at all), so it was deferred out of the
    # pre-fix baseline; it now lives in
    # test_default_forwards_verify_true_to_requests_get below.
    # ------------------------------------------------------------------

    # Incident object as the server sends it, pre-style-parsing.
    INCIDENT = {
        "id": 1,
        "title": "title 1",
        "content": "content 1",
        "style": "danger",
        "pin": 1,
        "createdDate": "2022-12-15 16:51:43",
        "lastUpdatedDate": None,
    }

    def test_default_forwards_verify_true_to_requests_get(self):
        """The default ssl_verify=True is forwarded as verify=True.

        **Validates: Requirements 3.3**

        Complement of the bug-condition test: honouring ``ssl_verify=False``
        must not weaken the default, which has to keep verifying certificates
        on the HTTP leg as well as the socket.io leg.
        """
        mock_get = self._fetch_status_page(ssl_verify=True)

        mock_get.assert_called_once()
        kwargs = mock_get.call_args.kwargs
        self.assertIn(
            "verify", kwargs,
            "requests.get was called without any verify= argument "
            f"(kwargs={kwargs!r}); the default ssl_verify=True is not forwarded",
        )
        self.assertTrue(
            kwargs["verify"],
            f"expected verify=True on the default path, got verify={kwargs['verify']!r}",
        )

    def test_default_returns_unchanged_top_level_shape(self):
        """Default path returns the same keys/values as before the fix.

        **Validates: Requirements 3.3, 3.4**

        ``_call`` contributes ``{"config": {}}`` and the HTTP leg contributes
        ``{"slug", "title"}``, so the merged result is exactly the config keys
        plus the four synthesised keys.
        """
        page, _ = self._fetch_status_page_result(ssl_verify=True)

        self.assertEqual(
            set(page),
            {"slug", "title", "incident", "incidents", "publicGroupList", "maintenanceList"},
        )
        self.assertEqual(page["slug"], "slug1")
        self.assertEqual(page["title"], "status page 1")
        self.assertIsNone(page["incident"])
        self.assertEqual(page["incidents"], [])
        self.assertEqual(page["publicGroupList"], [])
        self.assertEqual(page["maintenanceList"], [])

    def test_default_preserves_existing_requests_get_arguments(self):
        """The URL and timeout passed to requests.get are unchanged.

        **Validates: Requirements 3.3**
        """
        api = self._build_api(ssl_verify=True)
        with patch('uptime_kuma_api.api.requests.get') as mock_get:
            mock_get.return_value.json.return_value = self.HTTP_PAYLOAD
            api.get_status_page("slug1")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(args, (f"{api.url}/api/status-page/slug1",))
        self.assertEqual(kwargs["timeout"], api.timeout)

    def test_default_preserves_incident_incidents_dual_key_shape(self):
        """Both keys are present and consistent for every server payload shape.

        **Validates: Requirements 3.4**

        Scoped property: for each incident payload variant the 2.x and 1.x
        servers can send, the returned dict exposes ``incidents`` as a list and
        ``incident`` as its first entry (or ``None`` when empty).
        """
        from uptime_kuma_api import IncidentStyle

        second = dict(self.INCIDENT, id=2, title="title 2", style="info")
        variants = [
            # (label, incident-related payload keys, expected incident ids)
            ("v2 array", {"incidents": [dict(self.INCIDENT)]}, [1]),
            ("v2 multiple", {"incidents": [dict(self.INCIDENT), dict(second)]}, [1, 2]),
            ("v2 empty array", {"incidents": []}, []),
            ("v2 null array", {"incidents": None}, []),
            ("v1 singular", {"incident": dict(self.INCIDENT)}, [1]),
            ("v1 null singular", {"incident": None}, []),
            ("neither key", {}, []),
        ]

        for label, incident_keys, expected_ids in variants:
            with self.subTest(payload=label):
                payload = {
                    "config": {"slug": "slug1", "title": "status page 1"},
                    "publicGroupList": [],
                    "maintenanceList": [],
                    **incident_keys,
                }
                page, _ = self._fetch_status_page_result(ssl_verify=True, payload=payload)

                self.assertIn("incident", page)
                self.assertIn("incidents", page)
                self.assertIsInstance(page["incidents"], list)
                self.assertEqual([i["id"] for i in page["incidents"]], expected_ids)
                if expected_ids:
                    self.assertEqual(page["incident"], page["incidents"][0])
                    # style parsing still applies to every incident
                    self.assertEqual(page["incidents"][0]["style"], IncidentStyle.DANGER)
                else:
                    self.assertIsNone(page["incident"])

    def test_default_preserves_public_group_list_send_url_conversion(self):
        """publicGroupList monitors keep their int -> bool sendUrl conversion.

        **Validates: Requirements 3.4**
        """
        payload = {
            "config": {"slug": "slug1", "title": "status page 1"},
            "incident": None,
            "publicGroupList": [{
                "id": 1,
                "name": "Services",
                "weight": 1,
                "monitorList": [{"id": 1, "name": "monitor 1", "type": "http", "sendUrl": 0}],
            }],
            "maintenanceList": [],
        }

        page, _ = self._fetch_status_page_result(ssl_verify=True, payload=payload)

        monitor = page["publicGroupList"][0]["monitorList"][0]
        self.assertIs(monitor["sendUrl"], False)


class TestStatusPageWithholdAndWarn(unittest.TestCase):
    """Tests for the withhold-and-warn rule on status-page v2-only fields.

    Warning emission lives in save_status_page, computed from kwargs before
    the get_status_page round trip. These tests bind the real methods onto a
    MagicMock and mock the round trip (get_status_page) and server call (_call).
    """

    # Minimal status page config as get_status_page would return it.
    # Includes the keys save_status_page pops before calling the builder.
    MINIMAL_SP = {
        "id": 1,
        "slug": "test-sp",
        "title": "Test",
        "description": "",
        "icon": "/icon.svg",
        "theme": "auto",
        "published": True,
        "showTags": False,
        "domainNameList": [],
        "customCSS": "",
        "footerText": None,
        "showPoweredBy": True,
        "showCertificateExpiry": False,
        "incident": None,
        "incidents": [],
        "maintenanceList": [],
        "autoRefreshInterval": 300,
    }

    def _make_api(self, version):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._build_status_page_data = UptimeKumaApi._build_status_page_data.__get__(api)
        api._withheld_status_page_fields = UptimeKumaApi._withheld_status_page_fields.__get__(api)
        api._warn_withheld_status_page_fields = UptimeKumaApi._warn_withheld_status_page_fields.__get__(api)
        # Store bound save as test attribute (MagicMock spec intercepts attribute access)
        self._save = UptimeKumaApi.save_status_page.__get__(api)
        # Mock the round trip and server calls.
        # save_status_page calls _call twice:
        #   1. _call('saveStatusPage', ...) -> save response
        #   2. _call('getStatusPage', slug) -> {"config": {...}}
        api.get_status_page = MagicMock(return_value=dict(self.MINIMAL_SP))
        sp_config = dict(self.MINIMAL_SP)
        api._call = MagicMock(side_effect=[
            {"publicGroupList": []},            # saveStatusPage response
            {"config": sp_config},              # getStatusPage response
        ])
        api._event_data = {None: None}  # minimal event_data stub
        from uptime_kuma_api.api import Event
        api._event_data = {Event.STATUS_PAGE_LIST: {}}
        return api

    # --- v1 (1.23.2): field withheld, warning emitted ---

    def test_v1_showOnlyLastHeartbeat_withheld_and_warned(self):
        """1.23.2 + showOnlyLastHeartbeat -> absent from payload, one warning."""
        api = self._make_api("1.23.2")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", showOnlyLastHeartbeat=True)
        # The field must not appear in the payload passed to _call
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertNotIn("showOnlyLastHeartbeat", config)
        # Exactly one UnsupportedFieldWarning
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 1)
        self.assertIn("showOnlyLastHeartbeat", str(relevant[0].message))
        self.assertIn("2.1", str(relevant[0].message))
        self.assertIn("1.23.2", str(relevant[0].message))

    def test_v1_rssTitle_withheld_and_warned(self):
        """1.23.2 + rssTitle -> absent from payload, one warning."""
        api = self._make_api("1.23.2")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", rssTitle="Feed")
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertNotIn("rssTitle", config)
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 1)
        self.assertIn("rssTitle", str(relevant[0].message))

    def test_v1_multiple_fields_one_warning(self):
        """1.23.2 + both fields -> both absent, exactly one warning."""
        api = self._make_api("1.23.2")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", showOnlyLastHeartbeat=True, rssTitle="Feed")
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertNotIn("showOnlyLastHeartbeat", config)
        self.assertNotIn("rssTitle", config)
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 1)
        self.assertIn("showOnlyLastHeartbeat", str(relevant[0].message))
        self.assertIn("rssTitle", str(relevant[0].message))

    # --- 2.0.2: below the 2.1 floor, same behavior as 1.23.2 ---

    def test_v2_0_2_showOnlyLastHeartbeat_withheld(self):
        """2.0.2 is below the 2.1 floor -> field withheld, warning emitted.

        This is the only test that distinguishes a 2.1 floor from a 2.0 floor.
        """
        api = self._make_api("2.0.2")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", showOnlyLastHeartbeat=True)
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertNotIn("showOnlyLastHeartbeat", config)
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 1)
        self.assertIn("2.0.2", str(relevant[0].message))

    # --- v2 (2.4.0): field included, no warning ---

    def test_v2_showOnlyLastHeartbeat_included_no_warning(self):
        """2.4.0 + showOnlyLastHeartbeat -> in payload, no warning."""
        api = self._make_api("2.4.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", showOnlyLastHeartbeat=True)
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertEqual(config.get("showOnlyLastHeartbeat"), True)
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 0)

    def test_v2_rssTitle_included_no_warning(self):
        """2.4.0 + rssTitle -> in payload, no warning."""
        api = self._make_api("2.4.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", rssTitle="Feed")
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        self.assertEqual(config.get("rssTitle"), "Feed")
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 0)

    # --- No opt-in field supplied: no warning at any version ---

    def test_no_opt_in_fields_no_warning(self):
        """No v2-only field in kwargs -> no warning at any version."""
        for version in ("1.23.2", "2.0.2", "2.4.0"):
            with self.subTest(version=version):
                api = self._make_api(version)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self._save("test-sp", title="New Title")
                relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
                self.assertEqual(len(relevant), 0)

    # --- Escalation: simplefilter("error") -> raises, no payload ---

    def test_escalation_raises_before_round_trip(self):
        """simplefilter("error", ...) + opt-in field -> raises before get_status_page."""
        api = self._make_api("1.23.2")
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnsupportedFieldWarning)
            with self.assertRaises(UnsupportedFieldWarning):
                self._save("test-sp", rssTitle="Feed")
        # get_status_page must NOT have been called (escalation before round trip)
        api.get_status_page.assert_not_called()

    # --- Server-returned value contract (Req 3.11) ---

    def test_server_returned_value_not_treated_as_request(self):
        """v1 + get_status_page returns a gated key + caller passes only title
        -> no warning, server-returned value survives unchanged."""
        api = self._make_api("1.23.2")
        # Simulate a server that returns a gated key in its config
        sp_with_gated = dict(self.MINIMAL_SP)
        sp_with_gated["showOnlyLastHeartbeat"] = True
        api.get_status_page = MagicMock(return_value=sp_with_gated)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", title="New Title")
        # No warning -- the gated key came from the server, not from kwargs
        relevant = [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]
        self.assertEqual(len(relevant), 0)
        # The server-returned value must survive into the payload
        save_call = api._call.call_args_list[0]
        _, config, _, _ = save_call[0][1]
        # Note: _build_status_page_data's is-not-None guard fires here on v1 and
        # drops the field from the config dict. This is correct -- the builder's
        # existing behavior on v1 is preserved. The key contract is: NO WARNING.
        # The server-returned value was not treated as a caller request.

class TestMaintenanceNoV2Surface(unittest.TestCase):
    """Regression guard: maintenance has no v2-only surface.

    _build_maintenance_data produces identical payloads at v1 and v2. If a
    future change introduces a version gate, this test catches it.
    """

    def _make_api(self, version):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        return UptimeKumaApi._build_maintenance_data.__get__(api)

    def test_payload_identical_at_v1_and_v2(self):
        """_build_maintenance_data produces the same dict at 1.23.2 and 2.4.0."""
        from uptime_kuma_api import MaintenanceStrategy

        build_v1 = self._make_api("1.23.2")
        build_v2 = self._make_api("2.4.0")

        kwargs = dict(
            title="test maintenance",
            strategy=MaintenanceStrategy.MANUAL,
            active=True,
            description="desc",
            intervalDay=1,
            weekdays=[],
            daysOfMonth=[],
        )

        result_v1 = build_v1(**kwargs)
        result_v2 = build_v2(**kwargs)

        self.assertEqual(result_v1, result_v2)


class TestSettingsNoV2Surface(unittest.TestCase):
    """Regression guard: settings has no v2-only surface beyond existing gates.

    set_settings at 1.23.2 and 2.4.0 produces payloads that differ only in the
    existing 1.23 and 1.23.1 gated fields (chromeExecutable, nscd). If a future
    change introduces a >= 2.0 gate, this test catches it.
    """

    def _make_api(self, version):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api.set_settings = UptimeKumaApi.set_settings.__get__(api)
        api._call = MagicMock(return_value={"msg": "Saved"})
        return api

    def test_payload_identical_at_v1_and_v2_excluding_known_gates(self):
        """set_settings payload at 1.23.2 and 2.4.0 differs only by the known
        1.23/1.23.1 fields (chromeExecutable, nscd)."""
        api_v1 = self._make_api("1.23.2")
        api_v2 = self._make_api("2.4.0")

        kwargs = dict(
            checkUpdate=True,
            checkBeta=False,
            keepDataPeriodDays=180,
            serverTimezone="UTC",
            entryPage="dashboard",
            searchEngineIndex=False,
            primaryBaseURL="",
            steamAPIKey="",
            dnsCache=False,
            tlsExpiryNotifyDays=[7, 14, 21],
            disableAuth=False,
            trustProxy=False,
            # These are the known gated fields:
            chromeExecutable="/usr/bin/chromium",
            nscd=True,
        )

        api_v1.set_settings(**kwargs)
        api_v2.set_settings(**kwargs)

        payload_v1 = api_v1._call.call_args[0][1][0]
        payload_v2 = api_v2._call.call_args[0][1][0]

        # Both should have chromeExecutable and nscd (1.23.2 >= 1.23 and >= 1.23.1)
        self.assertIn("chromeExecutable", payload_v1)
        self.assertIn("nscd", payload_v1)
        self.assertIn("chromeExecutable", payload_v2)
        self.assertIn("nscd", payload_v2)

        # The payloads should be identical — no v2-only difference
        self.assertEqual(payload_v1, payload_v2)

if __name__ == '__main__':
    unittest.main()


class TestStatusPageAnalyticsBoundaryIs2_1(unittest.TestCase):
    """Issue #41: the status-page analytics boundary is 2.1, not 2.0.

    The three analytics keys and the two fields alongside them first ship in
    Uptime Kuma 2.1.0, while a 2.0.x server still reads ``googleAnalyticsId``.
    Gating them at ``2.0`` sent four keys 2.0.x has no columns for and withheld
    the one it does read, so a caller's ``googleAnalyticsId`` was silently
    dropped on every save against 2.0.0-2.0.2.

    Provenance is upstream source at tags rather than inference:
    ``server/socket-handlers/status-page-socket-handler.js`` and
    ``server/model/status_page.js`` carry ``google_analytics_tag_id`` at 2.0.0
    and 2.0.2 with no ``analytics_*`` columns; at 2.1.0 the ``analytics_*``
    columns exist and ``google_analytics_tag_id`` is gone.

    2.0.2 is the version that discriminates: at 1.23.2 and 2.4.0 the pre-fix
    and post-fix code agree, so a test at either would pass against the bug.
    """

    ANALYTICS = ("analyticsType", "analyticsId", "analyticsScriptUrl")

    def _build(self, version):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = version
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        return UptimeKumaApi._build_status_page_data.__get__(api)

    def _config(self, version, **kwargs):
        build = self._build(version)
        _, config, _, _ = build(slug="test", id=1, title="Test Page", **kwargs)
        return config

    # --- The bug condition: 2.0.x keeps googleAnalyticsId ---

    def test_2_0_2_sends_google_analytics_id(self):
        """A 2.0.2 server reads googleAnalyticsId, so it must be sent.

        Pre-fix this key was withheld from every 2.0.x save, which is the data
        loss issue #41 reports.
        """
        config = self._config("2.0.2", googleAnalyticsId="UA-123")
        self.assertIn("googleAnalyticsId", config)
        self.assertEqual(config["googleAnalyticsId"], "UA-123")

    def test_2_0_2_omits_the_analytics_trio(self):
        """A 2.0.2 server has no analytics_* columns, so none are sent."""
        config = self._config("2.0.2", analyticsType="google",
                              analyticsId="G-1", analyticsScriptUrl="u")
        for key in self.ANALYTICS:
            self.assertNotIn(key, config)

    def test_2_0_0_and_2_0_1_behave_as_2_0_2(self):
        """The whole 2.0.x line is below the floor, not just 2.0.2."""
        for version in ("2.0.0", "2.0.1", "2.0.2"):
            with self.subTest(version=version):
                config = self._config(version, googleAnalyticsId="UA-123",
                                      analyticsType="google")
                self.assertIn("googleAnalyticsId", config)
                self.assertNotIn("analyticsType", config)

    def test_2_0_2_omits_showonlylastheartbeat_and_rsstitle(self):
        """Both fields have a 2.1 floor in the registry; the builder agrees.

        Pre-fix the builder placed them on a 2.0.x server, one minor version
        looser than _V2_ONLY_STATUS_PAGE_FIELDS says.
        """
        config = self._config("2.0.2", showOnlyLastHeartbeat=True,
                              rssTitle="Feed")
        self.assertNotIn("showOnlyLastHeartbeat", config)
        self.assertNotIn("rssTitle", config)

    # --- The boundary itself ---

    def test_2_1_0_is_the_first_version_with_the_trio(self):
        """2.1.0 gets the analytics trio and loses googleAnalyticsId."""
        config = self._config("2.1.0", googleAnalyticsId="UA-123",
                              analyticsType="google", analyticsId="G-1",
                              analyticsScriptUrl="u")
        for key in self.ANALYTICS:
            self.assertIn(key, config)
        self.assertNotIn("googleAnalyticsId", config)

    def test_2_1_0_sends_the_trio_even_when_none(self):
        """The unconditional-send contract applies from the floor upward.

        The server rejects the save outright when analyticsType is absent
        (verified against 2.4.0), so presence -- not truthiness -- is what
        matters, and that has to hold at 2.1.0 too, not only at 2.4.0.
        """
        config = self._config("2.1.0")
        for key in self.ANALYTICS:
            self.assertIn(key, config)
            self.assertIsNone(config[key])

    def test_pre_release_of_2_1_is_treated_as_2_1(self):
        """2.1.0-beta.1 introduced the columns, so it must clear the floor.

        _parsed_version compares on the release segment, so a pre-release of
        the very version that adds a field is not rejected by its own floor --
        the same trap the #28 per-type floors had to avoid.
        """
        config = self._config("2.1.0-beta.1")
        for key in self.ANALYTICS:
            self.assertIn(key, config)
        self.assertNotIn("googleAnalyticsId", config)

    # --- Unchanged behaviour on either side ---

    def test_1_23_2_unchanged(self):
        """v1 behaviour is untouched: googleAnalyticsId in, trio out."""
        config = self._config("1.23.2", googleAnalyticsId="UA-123",
                              analyticsType="google")
        self.assertEqual(config["googleAnalyticsId"], "UA-123")
        for key in self.ANALYTICS:
            self.assertNotIn(key, config)

    def test_2_4_0_unchanged(self):
        """2.x behaviour above the floor is untouched."""
        config = self._config("2.4.0", googleAnalyticsId="UA-123",
                              analyticsType="google", analyticsId="G-1",
                              analyticsScriptUrl="u",
                              showOnlyLastHeartbeat=True, rssTitle="Feed")
        self.assertEqual(config["analyticsType"], "google")
        self.assertNotIn("googleAnalyticsId", config)
        self.assertTrue(config["showOnlyLastHeartbeat"])
        self.assertEqual(config["rssTitle"], "Feed")

    # --- password is a separate boundary and really is 2.0 ---

    def test_password_boundary_stays_at_2_0(self):
        """password is ignored by the v2 server; its 2.0 boundary is unchanged.

        Upstream comments the assignment out at 1.23.2, 2.0.0 and 2.1.0 alike,
        so the analytics boundary being wrong says nothing about this one.
        """
        self.assertIn("password", self._config("1.23.2", password="secret"))
        for version in ("2.0.2", "2.1.0", "2.4.0"):
            with self.subTest(version=version):
                self.assertNotIn(
                    "password", self._config(version, password="secret"))
