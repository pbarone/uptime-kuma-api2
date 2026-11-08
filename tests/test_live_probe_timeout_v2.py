"""
Unit tests for probe() in tests/live_test_late_monitor_fields_v2.py (issue #75).

The live script is a manual probe, but its verdict logic decides whether a
below-floor run exits 0, so it is worth pinning without a server. ``Timeout``
subclasses ``UptimeKumaException``; a transport timeout must be recorded as
NOT_OBSERVED, never as REJECTED (the "server has no such column" verdict).

Importing the script needs care: it does ``from dotenv import load_dotenv`` and
calls ``load_dotenv(tests/.env)`` at import. python-dotenv is not installed in
CI, and on a dev machine that call would load real-instance keys into
os.environ. So the module is imported with a stub ``dotenv`` in sys.modules,
and sys.modules / sys.path are restored afterwards.
"""
import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

from uptime_kuma_api import Timeout, UptimeKumaException

SCRIPT = os.path.join(os.path.dirname(__file__),
                      "live_test_late_monitor_fields_v2.py")

# wsSubprotocol is floored at 2.1 in the registry.
FIELD = "wsSubprotocol"
BELOW_FLOOR = "2.0.2"
AT_FLOOR = "2.1.0"


def _load_script():
    stub = types.ModuleType("dotenv")
    stub.load_dotenv = lambda *args, **kwargs: False
    saved_path = list(sys.path)
    try:
        with mock.patch.dict(sys.modules, {"dotenv": stub}):
            spec = importlib.util.spec_from_file_location(
                "_live_late_fields_under_test", SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
    return module


class ProbeTimeoutTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = _load_script()

    def setUp(self):
        s = self.script
        s.verdicts.clear()
        s.verdicts.update({name: (s.NOT_OBSERVED, "") for name, _, _ in s.FIELDS})
        patcher = mock.patch.object(s, "build_base_payload", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _probe(self, exc):
        s = self.script
        field, type_, value = next(f for f in s.FIELDS if f[0] == FIELD)
        api = mock.MagicMock()
        api._call.side_effect = exc
        s.probe(api, field, type_, value, 1, [])
        return s.verdicts[field][0]

    def test_timeout_below_floor_is_not_observed(self):
        s = self.script
        verdict = self._probe(Timeout("add timed out"))
        self.assertEqual(verdict, s.NOT_OBSERVED)
        # Not an expected verdict, so main() counts a disagreement and exits 1.
        self.assertNotIn(verdict, s.expected_verdicts(FIELD, BELOW_FLOOR))

    def test_plain_exception_below_floor_is_still_rejected(self):
        s = self.script
        verdict = self._probe(UptimeKumaException(
            "SQLITE_ERROR: table monitor has no column named x"))
        self.assertEqual(verdict, s.REJECTED)
        self.assertIn(verdict, s.expected_verdicts(FIELD, BELOW_FLOOR))

    def test_generic_exception_is_not_observed(self):
        s = self.script
        self.assertEqual(self._probe(RuntimeError("boom")), s.NOT_OBSERVED)

    def test_timeout_at_floor_is_not_observed(self):
        s = self.script
        verdict = self._probe(Timeout("add timed out"))
        self.assertEqual(verdict, s.NOT_OBSERVED)
        self.assertNotIn(verdict, s.expected_verdicts(FIELD, AT_FLOOR))


if __name__ == "__main__":
    unittest.main()
