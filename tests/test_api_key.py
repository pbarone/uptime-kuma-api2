import unittest
from datetime import datetime, timedelta
from uptime_kuma_api import UptimeKumaException
from uptime_kuma_test_case import UptimeKumaTestCase


class TestApiKey(UptimeKumaTestCase):
    def test_api_key(self):
        # get empty list to make sure that future accesses will also work
        self.api.get_api_keys()

        # add api key (the server stores the expiry in its own format, so only
        # the name is compared). Better Auth rejects an expiry more than a year
        # ahead, so it is relative to now.
        expires = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        r = self.api.add_api_key(name="name 1", expires=expires)
        self.assertIn("key", r)
        api_key_id = r["keyID"]
        self.assertIsInstance(api_key_id, str)

        # get api key
        api_key = self.api.get_api_key(api_key_id)
        self.assertEqual(api_key["name"], "name 1")
        self.assertTrue(api_key["active"])
        self.assertEqual(api_key["status"], "active")

        # get api keys
        api_keys = self.api.get_api_keys()
        api_key = self.find_by_id(api_keys, api_key_id)
        self.assertIsNotNone(api_key)
        self.assertEqual(api_key["name"], "name 1")

        # delete api key
        self.api.delete_api_key(api_key_id)
        with self.assertRaises(UptimeKumaException):
            self.api.get_api_key(api_key_id)

    def test_delete_not_existing_api_key(self):
        with self.assertRaises(UptimeKumaException):
            self.api.delete_api_key(42)


if __name__ == '__main__':
    unittest.main()
