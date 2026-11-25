import unittest
from uptime_kuma_test_case import UptimeKumaTestCase


class TestUptime(UptimeKumaTestCase):
    def test_uptime_without_monitor(self):
        self.assertEqual(self.api.uptime(), {})

    def test_uptime_with_monitor(self):
        self.add_monitor()
        result = self.api.uptime()
        for windows in result.values():
            self.assertEqual(set(windows), {24, 720, "1y"})


if __name__ == '__main__':
    unittest.main()