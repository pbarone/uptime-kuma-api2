"""Notification providers added in Uptime Kuma 3.0 (Indigo, MicrosoftGraph,
OpsiMate, trigv) and the removal of LINE Notify.

Names and required flags come from server/notification-providers/*.js and
src/components/notifications/*.vue at tag 3.0.0-beta.2. No server is needed.
"""
import unittest
from unittest.mock import MagicMock

from uptime_kuma_api import NotificationType, UptimeKumaApi
from uptime_kuma_api.api import _build_notification_data, _check_arguments_notification
from uptime_kuma_api.docstrings import notification_docstring
from uptime_kuma_api.notification_providers import notification_provider_options

REQUIRED = {
    "INDIGO": ("Indigo", {"indigoUrl", "indigoApiKey"}),
    "MICROSOFTGRAPH": (
        "MicrosoftGraph",
        {"msGraphTenantId", "msGraphClientId", "msGraphClientSecret", "msGraphMailbox"},
    ),
    "OPSIMATE": ("OpsiMate", {"opsimateURL", "opsimateAPIToken"}),
    "TRIGV": ("trigv", {"trigvApiKey"}),
}

OPTIONAL = {
    "INDIGO": {"indigoIgnoreTlsError", "indigoVariableId", "indigoActionGroupId"},
    "MICROSOFTGRAPH": {
        "msGraphTo", "msGraphCC", "msGraphBCC", "customSubject", "customBody", "htmlBody",
    },
    "OPSIMATE": set(),
    "TRIGV": {"trigvChannel"},
}

VALID = {
    "INDIGO": dict(indigoUrl="https://indigo.example.net", indigoApiKey="k", indigoVariableId=12),
    "MICROSOFTGRAPH": dict(
        msGraphTenantId="t", msGraphClientId="c", msGraphClientSecret="s",
        msGraphMailbox="alerts@example.com", msGraphTo="a@example.com",
    ),
    "OPSIMATE": dict(opsimateURL="https://opsimate.example.com", opsimateAPIToken="tok"),
    "TRIGV": dict(trigvApiKey="key"),
}


class TestNewProviders3x(unittest.TestCase):
    def test_type_values_are_the_server_names(self):
        for member, (value, _) in REQUIRED.items():
            with self.subTest(member=member):
                self.assertEqual(getattr(NotificationType, member).value, value)

    def test_option_tables(self):
        for member, (_, required) in REQUIRED.items():
            with self.subTest(member=member):
                table = notification_provider_options[getattr(NotificationType, member)]
                self.assertEqual({k for k, v in table.items() if v["required"]}, required)
                self.assertEqual({k for k, v in table.items() if not v["required"]}, OPTIONAL[member])

    def test_valid_payload_passes_checks(self):
        for member, options in VALID.items():
            with self.subTest(member=member):
                data = _build_notification_data(name="n", type=getattr(NotificationType, member), **options)
                _check_arguments_notification(data)
                for key, value in options.items():
                    self.assertEqual(data[key], value)

    def test_each_required_option_is_enforced(self):
        for member, (_, required) in REQUIRED.items():
            for missing in required:
                with self.subTest(member=member, missing=missing):
                    options = {k: v for k, v in VALID[member].items() if k != missing}
                    data = _build_notification_data(name="n", type=getattr(NotificationType, member), **options)
                    with self.assertRaises(TypeError):
                        _check_arguments_notification(data)

    def test_microsoft_graph_needs_one_recipient(self):
        base = {k: v for k, v in VALID["MICROSOFTGRAPH"].items() if k != "msGraphTo"}
        data = _build_notification_data(name="n", type=NotificationType.MICROSOFTGRAPH, **base)
        with self.assertRaises(TypeError):
            _check_arguments_notification(data)
        data = _build_notification_data(
            name="n", type=NotificationType.MICROSOFTGRAPH, msGraphTo="", msGraphCC=None, **base
        )
        with self.assertRaises(TypeError):
            _check_arguments_notification(data)
        for key in ("msGraphTo", "msGraphCC", "msGraphBCC"):
            with self.subTest(key=key):
                data = _build_notification_data(
                    name="n", type=NotificationType.MICROSOFTGRAPH, **base, **{key: "a@example.com"}
                )
                _check_arguments_notification(data)

    def test_recipient_rule_applies_to_other_providers_not_at_all(self):
        data = _build_notification_data(name="n", type=NotificationType.TRIGV, trigvApiKey="k")
        _check_arguments_notification(data)

    def test_add_notification_sends_the_options(self):
        api = MagicMock(spec=UptimeKumaApi)
        api._call.return_value = {"id": 1}
        UptimeKumaApi.add_notification(
            api, name="n", type=NotificationType.OPSIMATE, **VALID["OPSIMATE"]
        )
        event, (payload, id_) = api._call.call_args.args
        self.assertEqual(event, "addNotification")
        self.assertIsNone(id_)
        self.assertEqual(payload["type"], "OpsiMate")
        self.assertEqual(payload["opsimateURL"], "https://opsimate.example.com")
        self.assertEqual(payload["opsimateAPIToken"], "tok")

    def test_docstring_lists_every_new_option(self):
        text = notification_docstring("add")
        for member, (_, required) in REQUIRED.items():
            for option in required | OPTIONAL[member]:
                with self.subTest(option=option):
                    self.assertIn(f" {option}:", text)
                    self.assertIn(f"NotificationType.{member}`", text)


class TestLineNotifyRemoved(unittest.TestCase):
    def test_enum_member_is_gone(self):
        self.assertFalse(hasattr(NotificationType, "LINENOTIFY"))
        self.assertNotIn("LineNotify", [m.value for m in NotificationType])

    def test_option_is_gone_from_tables_and_docstring(self):
        names = {k for table in notification_provider_options.values() for k in table}
        self.assertNotIn("lineNotifyAccessToken", names)
        self.assertNotIn("lineNotify", notification_docstring("add"))
        with self.assertRaises(TypeError):
            _build_notification_data(name="n", type=NotificationType.LINE, lineNotifyAccessToken="x")

    def test_line_messenger_is_kept(self):
        self.assertEqual(NotificationType.LINE.value, "line")
        self.assertIn("lineChannelAccessToken", notification_provider_options[NotificationType.LINE])


if __name__ == "__main__":
    unittest.main()
