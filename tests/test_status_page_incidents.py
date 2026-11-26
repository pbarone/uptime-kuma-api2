"""
Tests for the ``incidents`` list that get_status_page returns on Uptime Kuma 3.x.

Uptime Kuma 3.x sends only the plural ``incidents`` array from
/api/status-page/{slug}. The singular ``incident`` key of the 2.x line is not
returned (BREAKING on v3). Reading ``incidents`` must not lose entries, must
tolerate an absent or null array, and must parse every style.

No live server required: the websocket call and the HTTP fetch are both mocked.
"""
import unittest
from unittest.mock import MagicMock, patch

from uptime_kuma_api.api import UptimeKumaApi

INCIDENT_V1 = {
    "id": 1,
    "title": "title 1",
    "content": "content 1",
    "style": "danger",
    "pin": 1,
    "createdDate": "2022-12-15 16:51:43",
    "lastUpdatedDate": None,
}


class TestStatusPageIncidents(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock(spec=UptimeKumaApi)
        self.api.url = "http://127.0.0.1:3001"
        self.api.timeout = 10
        self.api.ssl_verify = True
        self.api._call = MagicMock(return_value={"config": {"id": 1, "slug": "slug1"}})
        self.get_status_page = UptimeKumaApi.get_status_page.__get__(self.api)

    def _run(self, http_payload: dict) -> dict:
        response = MagicMock()
        response.json.return_value = http_payload
        with patch("uptime_kuma_api.api.requests.get", return_value=response):
            return self.get_status_page("slug1")

    def _payload(self, **extra):
        return {"config": {}, "publicGroupList": [], "maintenanceList": [], **extra}

    def test_incidents_array_is_surfaced(self):
        """The full incidents list must reach the caller, not be dropped."""
        page = self._run(self._payload(incidents=[INCIDENT_V1]))
        self.assertEqual(len(page["incidents"]), 1)
        self.assertEqual(page["incidents"][0]["title"], "title 1")

    def test_multiple_incidents_all_returned_in_order(self):
        second = dict(INCIDENT_V1, id=2, title="title 2")
        page = self._run(self._payload(incidents=[INCIDENT_V1, second]))
        self.assertEqual([i["id"] for i in page["incidents"]], [1, 2])

    def test_empty_incidents_array(self):
        page = self._run(self._payload(incidents=[]))
        self.assertEqual(page["incidents"], [])

    def test_null_incidents_treated_as_empty(self):
        page = self._run(self._payload(incidents=None))
        self.assertEqual(page["incidents"], [])

    def test_absent_incidents_key_does_not_raise(self):
        page = self._run(self._payload())
        self.assertEqual(page["incidents"], [])

    def test_singular_incident_key_is_not_returned(self):
        """The singular key is gone, and a singular payload is not read."""
        for payload in (
            self._payload(incidents=[INCIDENT_V1]),
            self._payload(incident=INCIDENT_V1),
            self._payload(),
        ):
            page = self._run(payload)
            self.assertNotIn("incident", page)
        # a pre-2.1 singular payload is ignored: 3.x never sends it
        self.assertEqual(self._run(self._payload(incident=INCIDENT_V1))["incidents"], [])

    def test_incident_style_parsed_for_all_incidents(self):
        from uptime_kuma_api import IncidentStyle
        second = dict(INCIDENT_V1, id=2, style="info")
        page = self._run(self._payload(incidents=[INCIDENT_V1, second]))
        self.assertEqual(page["incidents"][0]["style"], IncidentStyle.DANGER)
        self.assertEqual(page["incidents"][1]["style"], IncidentStyle.INFO)


if __name__ == "__main__":
    unittest.main()
