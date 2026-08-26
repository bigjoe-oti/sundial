"""Task 12: learned quiet hours — deterministic, sound-gated only, and
fail-open (insufficient data -> no quiet hours -> current behavior)."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    if "sundial_v3_quiethours" in sys.modules:
        return sys.modules["sundial_v3_quiethours"]
    spec = importlib.util.spec_from_file_location(
        "sundial_v3_quiethours", REPO / "lib" / "quiethours.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sundial_v3_quiethours"] = mod
    spec.loader.exec_module(mod)
    return mod


qh = _load()


class TestComputeQuietHours(unittest.TestCase):
    def test_night_pattern_learns_quiet_hours(self):
        # active 9-18, silent elsewhere, 30 days observed
        hourly = [0]*9 + [10]*9 + [0]*6
        self.assertEqual(qh.compute_quiet_hours(hourly, 30),
                         [0, 1, 2, 3, 4, 5, 6, 7, 8, 18, 19, 20, 21, 22, 23])

    def test_insufficient_days_fail_open(self):
        hourly = [0]*9 + [10]*9 + [0]*6
        self.assertEqual(qh.compute_quiet_hours(hourly, 13), [])

    def test_uniformly_active_no_quiet_hours(self):
        hourly = [5] * 24
        self.assertEqual(qh.compute_quiet_hours(hourly, 60), [])

    def test_inactive_install_fails_open(self):
        # all-zero histogram = install not used; NOT a rest pattern
        self.assertEqual(qh.compute_quiet_hours([0] * 24, 30), [])

    def test_malformed_input_degrades_to_empty(self):
        for bad in (None, [], [1, 2], ["x"]*24, [-1]*24):
            with self.subTest(bad=bad):
                self.assertEqual(qh.compute_quiet_hours(bad, 30), [])


class TestCacheRoundTrip(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            qh.save(tmp, [0, 1, 23])
            loaded = qh.load_or_compute(tmp)
            self.assertEqual(loaded, [0, 1, 23])

    def test_absent_file_defaults_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(qh.load_or_compute(tmp), [])


if __name__ == "__main__":
    unittest.main()
