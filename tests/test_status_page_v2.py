import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch
from uptime_kuma_api.api import UptimeKumaApi, Event, _STATUS_PAGE_FIELD_FLOORS
from uptime_kuma_api import UnsupportedFieldWarning


class TestStatusPageV2(unittest.TestCase):
    """Unit tests for _build_status_page_data version-gated behavior.

    These tests mock the version attribute and call _build_status_page_data
    directly — no live server connection required.
    """

    def setUp(self):
        self.api_v2 = MagicMock(spec=UptimeKumaApi)
        self.api_v2.version = "3.0.0"
        # bind the real version-gate choke point so gates parse self.version
        self.api_v2._parsed_version = UptimeKumaApi._parsed_version.__get__(self.api_v2)
        self.build_v2 = UptimeKumaApi._build_status_page_data.__get__(self.api_v2)

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


    # --- Test 11: v2 analytics keys must be PRESENT even when None ---
    def test_v2_analytics_keys_always_present_when_none(self):
        """Regression: the server rejects the save with "Invalid analytics
        type" when analyticsType is absent from the payload. Verified against
        3.x: null is accepted, an absent key is not. Omitting the key broke
        save_status_page for every page with no analytics configured, which in
        turn broke unpin_incident (which calls save)."""
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

    def test_path_ssl_verify_forwarded_as_converted_str(self):
        """A path passed to ssl_verify must reach requests.get as a str, not a Path.

        **Validates: Concern raised in Issue #32**

        The socket.io leg accepts a PathLike for ssl_verify, but the HTTP leg
        must see a str. The conversion must be done with os.fspath() so that a
        PathLike whose __fspath__() and __str__() disagree still reaches the
        HTTP leg as the correct path.
        """
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            ca_path = tmp.name
        try:
            mock_get = self._fetch_status_page(ssl_verify=Path(ca_path))
        finally:
            os.unlink(ca_path)

        mock_get.assert_called_once()
        kwargs = mock_get.call_args.kwargs
        self.assertIsInstance(kwargs["verify"], str)
        self.assertEqual(kwargs["verify"], ca_path)

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
            {"slug", "title", "incidents", "publicGroupList", "maintenanceList"},
        )
        self.assertEqual(page["slug"], "slug1")
        self.assertEqual(page["title"], "status page 1")
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

    def test_incidents_list_for_every_payload_shape(self):
        """``incidents`` is always a list, with parsed styles, and no singular key.

        Uptime Kuma 3.x sends only ``incidents``. The singular ``incident`` key
        of the 2.x line is gone (BREAKING on v3).
        """
        from uptime_kuma_api import IncidentStyle

        second = dict(self.INCIDENT, id=2, title="title 2", style="info")
        variants = [
            ("array", {"incidents": [dict(self.INCIDENT)]}, [1]),
            ("multiple", {"incidents": [dict(self.INCIDENT), dict(second)]}, [1, 2]),
            ("empty array", {"incidents": []}, []),
            ("null array", {"incidents": None}, []),
            ("no key", {}, []),
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

                self.assertNotIn("incident", page)
                self.assertIsInstance(page["incidents"], list)
                self.assertEqual([i["id"] for i in page["incidents"]], expected_ids)
                if expected_ids:
                    self.assertEqual(page["incidents"][0]["style"], IncidentStyle.DANGER)

    def test_default_preserves_public_group_list_send_url_conversion(self):
        """publicGroupList monitors keep their int -> bool sendUrl conversion.

        **Validates: Requirements 3.4**
        """
        payload = {
            "config": {"slug": "slug1", "title": "status page 1"},
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
    """Withhold-and-warn rule for a version-gated status-page field.

    No status-page field has a floor above 3.0 today, so the table is empty and
    these tests patch in a synthetic floor of 3.1 for ``rssTitle`` and
    ``showOnlyLastHeartbeat`` to prove the mechanism works for the first field a
    later 3.x release gates. Warning emission lives in save_status_page,
    computed from kwargs before the get_status_page round trip. These tests bind
    the real methods onto a MagicMock and mock the round trip (get_status_page)
    and server call (_call).
    """

    FLOORS = {"showOnlyLastHeartbeat": "3.1", "rssTitle": "3.1"}

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
        "incidents": [],
        "maintenanceList": [],
        "autoRefreshInterval": 300,
    }

    def setUp(self):
        patcher = patch.dict(_STATUS_PAGE_FIELD_FLOORS, self.FLOORS)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        api._event_data = {Event.STATUS_PAGE_LIST: {}}
        return api

    def _saved_config(self, api):
        _, config, _, _ = api._call.call_args_list[0][0][1]
        return config

    def _warnings(self, caught):
        return [x for x in caught if issubclass(x.category, UnsupportedFieldWarning)]

    # --- below the floor: field withheld, warning emitted ---

    def test_below_floor_showOnlyLastHeartbeat_withheld_and_warned(self):
        for version in ("3.0.0", "3.0.0-beta.2"):
            with self.subTest(version=version):
                api = self._make_api(version)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self._save("test-sp", showOnlyLastHeartbeat=True)
                self.assertNotIn("showOnlyLastHeartbeat", self._saved_config(api))
                relevant = self._warnings(caught)
                self.assertEqual(len(relevant), 1)
                self.assertIn("showOnlyLastHeartbeat", str(relevant[0].message))
                self.assertIn("3.1", str(relevant[0].message))
                self.assertIn(version, str(relevant[0].message))

    def test_below_floor_rssTitle_withheld_and_warned(self):
        api = self._make_api("3.0.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", rssTitle="Feed")
        self.assertNotIn("rssTitle", self._saved_config(api))
        relevant = self._warnings(caught)
        self.assertEqual(len(relevant), 1)
        self.assertIn("rssTitle", str(relevant[0].message))

    def test_multiple_fields_one_warning(self):
        api = self._make_api("3.0.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", showOnlyLastHeartbeat=True, rssTitle="Feed")
        config = self._saved_config(api)
        self.assertNotIn("showOnlyLastHeartbeat", config)
        self.assertNotIn("rssTitle", config)
        relevant = self._warnings(caught)
        self.assertEqual(len(relevant), 1)
        self.assertIn("showOnlyLastHeartbeat", str(relevant[0].message))
        self.assertIn("rssTitle", str(relevant[0].message))

    # --- at or above the floor: field included, no warning ---

    def test_at_floor_fields_included_no_warning(self):
        # 3.1.0-beta.1 is gated as its release segment, 3.1
        for version in ("3.1.0", "3.1.0-beta.1", "3.2.0"):
            with self.subTest(version=version):
                api = self._make_api(version)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self._save("test-sp", showOnlyLastHeartbeat=True, rssTitle="Feed")
                config = self._saved_config(api)
                self.assertEqual(config.get("showOnlyLastHeartbeat"), True)
                self.assertEqual(config.get("rssTitle"), "Feed")
                self.assertEqual(self._warnings(caught), [])

    # --- No opt-in field supplied: no warning at any version ---

    def test_no_opt_in_fields_no_warning(self):
        for version in ("3.0.0", "3.1.0"):
            with self.subTest(version=version):
                self._make_api(version)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self._save("test-sp", title="New Title")
                self.assertEqual(self._warnings(caught), [])

    # --- Escalation: simplefilter("error") -> raises, no payload ---

    def test_escalation_raises_before_round_trip(self):
        """simplefilter("error", ...) + opt-in field -> raises before get_status_page."""
        api = self._make_api("3.0.0")
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnsupportedFieldWarning)
            with self.assertRaises(UnsupportedFieldWarning):
                self._save("test-sp", rssTitle="Feed")
        # get_status_page must NOT have been called (escalation before round trip)
        api.get_status_page.assert_not_called()

    # --- Server-returned value contract ---

    def test_server_returned_value_not_treated_as_request(self):
        """get_status_page returns a gated key + caller passes only title
        -> no warning, server-returned value survives unchanged."""
        api = self._make_api("3.0.0")
        sp_with_gated = dict(self.MINIMAL_SP)
        sp_with_gated["showOnlyLastHeartbeat"] = True
        api.get_status_page = MagicMock(return_value=sp_with_gated)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._save("test-sp", title="New Title")
        # No warning -- the gated key came from the server, not from kwargs
        self.assertEqual(self._warnings(caught), [])
        # ... and the server-returned value reaches the payload
        self.assertEqual(self._saved_config(api).get("showOnlyLastHeartbeat"), True)


class TestStatusPageFieldsAreUngated(unittest.TestCase):
    """With the real (empty) floor table, no status-page field is withheld.

    The analytics trio, ``showOnlyLastHeartbeat`` and ``rssTitle`` used to need
    an older server. Every supported server accepts them, so they are sent
    whatever version the stub reports.
    """

    def test_floor_table_is_empty(self):
        self.assertEqual(_STATUS_PAGE_FIELD_FLOORS, {})

    def test_fields_sent_on_every_version(self):
        # 2.0.0 and 1.21.3 are not servers this library supports. They are used
        # as stubs because they selected the old gates, and must not any more.
        for version in ("3.0.0", "3.0.0-beta.2", "3.1.0", "2.0.0", "1.21.3"):
            with self.subTest(version=version):
                api = MagicMock(spec=UptimeKumaApi)
                api.version = version
                api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
                build = UptimeKumaApi._build_status_page_data.__get__(api)
                _, config, _, _ = build(
                    slug="t", id=1, title="T",
                    showOnlyLastHeartbeat=True, rssTitle="Feed",
                )
                self.assertEqual(config["showOnlyLastHeartbeat"], True)
                self.assertEqual(config["rssTitle"], "Feed")
                self.assertIn("analyticsType", config)
                self.assertIn("analyticsId", config)
                self.assertIn("analyticsScriptUrl", config)
                self.assertIn("showCertificateExpiry", config)
                self.assertEqual(config["theme"], "auto")


class TestSaveStatusPageSignature(unittest.TestCase):
    """save_status_page no longer takes ``password`` or ``googleAnalyticsId``.

    Uptime Kuma 3.x neither reads nor returns either key (its status page
    toJSON/toPublicJSON carry analyticsId, analyticsScriptUrl and
    analyticsType), so a caller passing one gets a TypeError instead of a
    silently ignored argument.
    """

    # What the 3.0.0-beta.2 server returns for a status page (toJSON keys),
    # after get_status_page has added its own incident keys.
    SERVER_SP = {
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
        "analyticsId": "site-1",
        "analyticsScriptUrl": None,
        "analyticsType": "plausible",
        "showOnlyLastHeartbeat": False,
        "rssTitle": None,
        "incidents": [],
        "maintenanceList": [],
        "autoRefreshInterval": 300,
    }

    def _make_api(self):
        api = MagicMock(spec=UptimeKumaApi)
        api.version = "3.0.0"
        api._parsed_version = UptimeKumaApi._parsed_version.__get__(api)
        api._build_status_page_data = UptimeKumaApi._build_status_page_data.__get__(api)
        api._withheld_status_page_fields = UptimeKumaApi._withheld_status_page_fields.__get__(api)
        api._warn_withheld_status_page_fields = UptimeKumaApi._warn_withheld_status_page_fields.__get__(api)
        self._save = UptimeKumaApi.save_status_page.__get__(api)
        api.get_status_page = MagicMock(return_value=dict(self.SERVER_SP))
        api._call = MagicMock(side_effect=[
            {"publicGroupList": []},
            {"config": dict(self.SERVER_SP)},
        ])
        api._event_data = {Event.STATUS_PAGE_LIST: {}}
        return api

    def test_removed_keywords_raise_type_error_without_saving(self):
        for key, value in (("password", "secret"), ("googleAnalyticsId", "G-1")):
            with self.subTest(key=key):
                api = self._make_api()
                with self.assertRaises(TypeError):
                    self._save("test-sp", **{key: value})
                sent = [c.args[0] for c in api._call.call_args_list]
                self.assertNotIn("saveStatusPage", sent)

    def test_builder_rejects_removed_keywords(self):
        build = UptimeKumaApi._build_status_page_data.__get__(self._make_api())
        for key in ("password", "googleAnalyticsId"):
            with self.subTest(key=key):
                with self.assertRaises(TypeError):
                    build(slug="t", id=1, title="T", **{key: "x"})

    def test_remaining_parameters_reach_payload(self):
        api = self._make_api()
        groups = [{"name": "Services", "weight": 1, "monitorList": [{"id": 1}]}]
        self._save(
            "test-sp",
            title="New Title", description="desc", theme="dark",
            published=False, showTags=True, domainNameList=["a.example"],
            customCSS="body{}", footerText="foot", showPoweredBy=False,
            showCertificateExpiry=True, icon="/x.svg",
            publicGroupList=groups,
            analyticsType="umami", analyticsId="u-1",
            analyticsScriptUrl="https://u.example/s.js",
            showOnlyLastHeartbeat=True, rssTitle="Feed",
        )
        name, (slug, config, icon, public_groups) = api._call.call_args_list[0][0]
        self.assertEqual(name, "saveStatusPage")
        self.assertEqual(slug, "test-sp")
        self.assertEqual(icon, "/x.svg")
        self.assertEqual(public_groups, groups)
        self.assertEqual(config["title"], "New Title")
        self.assertEqual(config["description"], "desc")
        self.assertEqual(config["theme"], "dark")
        self.assertIs(config["published"], False)
        self.assertIs(config["showTags"], True)
        self.assertEqual(config["domainNameList"], ["a.example"])
        self.assertEqual(config["customCSS"], "body{}")
        self.assertEqual(config["footerText"], "foot")
        self.assertIs(config["showPoweredBy"], False)
        self.assertIs(config["showCertificateExpiry"], True)
        self.assertEqual(config["analyticsType"], "umami")
        self.assertEqual(config["analyticsId"], "u-1")
        self.assertEqual(config["analyticsScriptUrl"], "https://u.example/s.js")
        self.assertIs(config["showOnlyLastHeartbeat"], True)
        self.assertEqual(config["rssTitle"], "Feed")
        self.assertNotIn("password", config)
        self.assertNotIn("googleAnalyticsId", config)

    def test_server_config_as_returned_by_3x_still_saves(self):
        """No kwargs: the merged server config (all toJSON keys) is accepted."""
        api = self._make_api()
        self._save("test-sp")
        config = api._call.call_args_list[0][0][1][1]
        self.assertEqual(config["analyticsId"], "site-1")
        self.assertEqual(config["analyticsType"], "plausible")


if __name__ == '__main__':
    unittest.main()
