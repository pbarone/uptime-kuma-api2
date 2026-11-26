"""
Unit tests for incident management on Uptime Kuma 3.x (issue #103).

edit_incident, resolve_incident, delete_incident, get_incident_history and
iter_incident_history, plus post_incident no longer saving the status page.

No live server required: ``_call`` is a mock that records the socket events.
"""
import unittest
from unittest.mock import MagicMock

from uptime_kuma_api import IncidentStyle, UptimeKumaException
from uptime_kuma_api.api import UptimeKumaApi


def incident(id_, style="danger", created="2026-01-01 00:00:00", **extra):
    return {
        "id": id_,
        "style": style,
        "title": f"title {id_}",
        "content": f"content {id_}",
        "pin": True,
        "active": True,
        "createdDate": created,
        "lastUpdatedDate": None,
        "status_page_id": 1,
        **extra,
    }


def make_api(call):
    api = MagicMock(spec=UptimeKumaApi)
    api._call = MagicMock(side_effect=call)
    for name in (
        "post_incident", "edit_incident", "resolve_incident", "delete_incident",
        "get_incident_history", "iter_incident_history",
    ):
        setattr(api, name, getattr(UptimeKumaApi, name).__get__(api))
    return api


class TestIncidentWrites(unittest.TestCase):
    def test_post_incident_does_not_save_the_status_page(self):
        api = make_api(lambda event, data=None: {"incident": incident(1)})
        api.save_status_page = MagicMock()
        r = api.post_incident("slug1", "title 1", "content 1", IncidentStyle.DANGER)
        api.save_status_page.assert_not_called()
        self.assertEqual([c.args[0] for c in api._call.call_args_list], ["postIncident"])
        self.assertIs(r["style"], IncidentStyle.DANGER)

    def test_edit_incident_sends_all_fields_and_parses_style(self):
        api = make_api(lambda event, data=None: {"incident": incident(7, style="info")})
        r = api.edit_incident("slug1", 7, "new title", "new content", IncidentStyle.INFO, pin=False)
        event, data = api._call.call_args.args
        self.assertEqual(event, "editIncident")
        self.assertEqual(data, ("slug1", 7, {
            "title": "new title", "content": "new content",
            "style": IncidentStyle.INFO, "pin": False,
        }))
        self.assertIs(r["style"], IncidentStyle.INFO)
        self.assertEqual(r["id"], 7)

    def test_edit_incident_defaults_are_primary_and_pinned(self):
        api = make_api(lambda event, data=None: {"incident": incident(7, style="primary")})
        api.edit_incident("slug1", 7, "t", "c")
        incident_arg = api._call.call_args.args[1][2]
        self.assertIs(incident_arg["style"], IncidentStyle.PRIMARY)
        self.assertIs(incident_arg["pin"], True)

    def test_edit_incident_requires_title_and_content(self):
        api = make_api(lambda event, data=None: {"incident": incident(1)})
        with self.assertRaises(TypeError):
            api.edit_incident("slug1", 1)
        with self.assertRaises(TypeError):
            api.edit_incident("slug1", 1, "title only")

    def test_resolve_incident(self):
        api = make_api(lambda event, data=None: {
            "incident": incident(3, style="warning", pin=False, active=False)})
        r = api.resolve_incident("slug1", 3)
        self.assertEqual(api._call.call_args.args, ("resolveIncident", ("slug1", 3)))
        self.assertIs(r["style"], IncidentStyle.WARNING)
        self.assertIs(r["active"], False)

    def test_delete_incident_returns_the_server_response(self):
        response = {"msg": "successDeleted", "msgi18n": True}
        api = make_api(lambda event, data=None: dict(response))
        self.assertEqual(api.delete_incident("slug1", 3), response)
        self.assertEqual(api._call.call_args.args, ("deleteIncident", ("slug1", 3)))

    def test_server_error_is_raised(self):
        def call(event, data=None):
            raise UptimeKumaException("Incident not found or access denied")
        api = make_api(call)
        for fn, args in (
            (api.edit_incident, ("slug1", 9, "t", "c")),
            (api.resolve_incident, ("slug1", 9)),
            (api.delete_incident, ("slug1", 9)),
        ):
            with self.assertRaises(UptimeKumaException):
                fn(*args)


class FakeHistory:
    """A fake server that pages like getIncidentHistory: 10 per page, by cursor."""

    def __init__(self, count):
        # newest first; created dates are distinct and sortable as strings
        self.all = [
            incident(i, style="warning", created=f"2026-01-01 00:{i // 60:02d}:{i % 60:02d}")
            for i in range(count, 0, -1)
        ]
        self.calls = []

    def __call__(self, event, data=None):
        assert event == "getIncidentHistory"
        slug, cursor = data  # the cursor argument must always be present
        self.calls.append(cursor)
        rows = [i for i in self.all if cursor is None or i["createdDate"] < cursor]
        page = rows[:10]
        more = len(rows) > 10
        return {
            "incidents": [dict(i) for i in page],
            "total": len(self.all),
            "nextCursor": page[-1]["createdDate"] if more else None,
            "hasMore": more,
        }


class TestIncidentHistory(unittest.TestCase):
    def test_first_page_sends_none_cursor_and_returns_the_four_keys(self):
        server = FakeHistory(25)
        api = make_api(server)
        page = api.get_incident_history("slug1")
        self.assertEqual(api._call.call_args.args, ("getIncidentHistory", ("slug1", None)))
        self.assertEqual(set(page), {"incidents", "total", "nextCursor", "hasMore"})
        self.assertEqual(len(page["incidents"]), 10)
        self.assertEqual(page["total"], 25)
        self.assertTrue(page["hasMore"])
        self.assertIs(page["incidents"][0]["style"], IncidentStyle.WARNING)

    def test_cursor_is_passed_through(self):
        server = FakeHistory(25)
        api = make_api(server)
        first = api.get_incident_history("slug1")
        second = api.get_incident_history("slug1", first["nextCursor"])
        self.assertEqual(api._call.call_args.args[1], ("slug1", first["nextCursor"]))
        self.assertEqual(second["incidents"][0]["id"], 15)

    def test_iter_returns_every_incident_newest_first(self):
        server = FakeHistory(25)
        api = make_api(server)
        ids = [i["id"] for i in api.iter_incident_history("slug1")]
        self.assertEqual(ids, list(range(25, 0, -1)))
        self.assertEqual(len(server.calls), 3)

    def test_iter_empty_history(self):
        server = FakeHistory(0)
        api = make_api(server)
        self.assertEqual(list(api.iter_incident_history("slug1")), [])
        self.assertEqual(len(server.calls), 1)

    def test_iter_exact_page_boundary_stops_without_extra_request(self):
        server = FakeHistory(10)
        api = make_api(server)
        self.assertEqual(len(list(api.iter_incident_history("slug1"))), 10)
        self.assertEqual(len(server.calls), 1)

    def test_iter_is_lazy(self):
        server = FakeHistory(35)
        api = make_api(server)
        it = api.iter_incident_history("slug1")
        self.assertEqual(server.calls, [], "creating the generator must not fetch")
        first = next(it)
        self.assertEqual(first["id"], 35)
        self.assertEqual(len(server.calls), 1)
        for _ in range(9):
            next(it)
        self.assertEqual(len(server.calls), 1, "the first page serves 10 items")
        next(it)
        self.assertEqual(len(server.calls), 2, "the 11th item fetches page two")

    def test_iter_docstring_states_the_limits(self):
        doc = UptimeKumaApi.iter_incident_history.__doc__
        self.assertIn("one request per 10 incidents", doc)
        self.assertIn("exact created date", doc)


if __name__ == "__main__":
    unittest.main()
