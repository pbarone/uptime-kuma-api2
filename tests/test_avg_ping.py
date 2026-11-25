import unittest
from uptime_kuma_test_case import UptimeKumaTestCase


class TestAvgPing(UptimeKumaTestCase):
    def test_avg_ping_without_monitor(self):
        self.assertEqual(self.api.avg_ping(), {})

    def test_avg_ping(self):
        self.add_monitor()
        result = self.api.avg_ping()
        for windows in result.values():
            self.assertEqual(set(windows), {24, 720, "1y"})


if __name__ == '__main__':
    unittest.main()