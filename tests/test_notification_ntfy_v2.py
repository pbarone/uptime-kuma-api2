import unittest

from uptime_kuma_api import NotificationType
from uptime_kuma_api.api import _build_notification_data, _check_arguments_notification


class TestNtfyV2Fields(unittest.TestCase):
    """ntfy fields read by the Uptime Kuma 2.x provider (#82).

    server/notification-providers/ntfy.js at tag 2.5.9 reads ntfyPriorityDown,
    ntfyUseTemplate, ntfyCustomTitle, ntfyCustomMessage and ntfyCall; the 1.23.x
    provider reads none of them.
    """

    def _ntfy(self, **extra):
        return _build_notification_data(
            name="test",
            type=NotificationType.NTFY,
            ntfyserverurl="https://ntfy.example.com",
            ntfytopic="alerts",
            ntfyPriority=2,
            **extra,
        )

    def test_accepts_2x_fields(self):
        """add_notification()/test_notification() accept the 2.x fields."""
        data = self._ntfy(
            ntfyPriorityDown=4,
            ntfyUseTemplate=True,
            ntfyCustomTitle="{{ name }} {{ status }}",
            ntfyCustomMessage="{{ msg }}",
            ntfyCall="yes",
        )
        assert data["ntfyPriorityDown"] == 4
        assert data["ntfyUseTemplate"] is True
        assert data["ntfyCustomTitle"] == "{{ name }} {{ status }}"
        assert data["ntfyCustomMessage"] == "{{ msg }}"
        assert data["ntfyCall"] == "yes"
        _check_arguments_notification(data)

    def test_2x_fields_are_optional(self):
        """A 1.x-style payload without the new fields still validates."""
        _check_arguments_notification(self._ntfy())

    def test_priority_down_is_bounded_like_ntfy_priority(self):
        """ntfyPriorityDown takes 1-5, as the 2.x form and ntfyPriority do."""
        for value in (0, 6):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _check_arguments_notification(self._ntfy(ntfyPriorityDown=value))


if __name__ == "__main__":
    unittest.main()
