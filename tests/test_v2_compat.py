"""Task 10b: v2-era ledger back-compat.

Fixtures in tests/fixtures/v2_data are COPIES of live data/ file shapes
(commitments with est/P50/P90 snapshots, session_speak queue entries,
session-ledger rows). v3 must load them unchanged; migrate_entry() must
still absorb legacy bare-ISO stamps; no schema changes at version 3.0.0.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "v2_data"
LIB = REPO / "lib"


def _load_core():
    if "sundial_v2_compat_core" in sys.modules:
        return sys.modules["sundial_v2_compat_core"]
    spec = importlib.util.spec_from_file_location(
        "sundial_v2_compat_core", LIB / "core.py")
    core = importlib.util.module_from_spec(spec)
    sys.modules["sundial_v2_compat_core"] = core
    spec.loader.exec_module(core)
    return core


def _load_watcher_entry_helpers():
    if "sundial_v2_compat_legacy_presence" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "sundial_v2_compat_legacy_presence",
            REPO / "watcher" / "presence.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sundial_v2_compat_legacy_presence"] = mod
        spec.loader.exec_module(mod)
    # migrate_entry lives in watcher.py; load it with sibling dir on path
    if "sundial_v2_compat_legacy_watcher" not in sys.modules:
        watcher_dir = str(REPO / "watcher")
        if watcher_dir not in sys.path:
            sys.path.insert(0, watcher_dir)
        spec = importlib.util.spec_from_file_location(
            "sundial_v2_compat_legacy_watcher", REPO / "watcher" / "watcher.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sundial_v2_compat_legacy_watcher"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["sundial_v2_compat_legacy_watcher"]


class TestV2LedgerCompat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = _load_core()
        cls.watcher = _load_watcher_entry_helpers()

    def test_fixture_commitments_load_unchanged(self):
        path = FIXTURES / "commitments.json"
        if not path.exists():
            self.skipTest("no commitments fixture")
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.core.DATA = FIXTURES
        try:
            items = self.core.load_commitments()
        finally:
            self.core.DATA = REPO / "data"
        self.assertEqual(len(items), len(raw))
        for got, want in zip(items, raw):
            self.assertEqual(got.get("id"), want.get("id"))

    def test_session_speak_queue_parses(self):
        path = FIXTURES / "session_speak.json"
        if not path.exists():
            self.skipTest("no session_speak fixture")
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("queue", doc)

    def test_migrate_entry_absorbs_legacy_bare_iso(self):
        # The old hermes cron_check.py writer stamped notified.json with
        # bare ISO strings. migrate_entry must keep absorbing those.
        entry = self.watcher.migrate_entry("2026-07-20T08:00:00+00:00")
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["last"], "2026-07-20T08:00:00+00:00")
        self.assertEqual(entry["unseen_s"], 0.0)      # defaults filled
        self.assertEqual(entry["ripe_here_cycles"], 0)

    def test_migrate_entry_structured_passthrough(self):
        entry = self.watcher.migrate_entry({
            "count": 2, "last": "2026-07-20T09:00:00+00:00",
            "unseen_s": 1500.0, "ripe_here_cycles": 3})
        self.assertEqual(entry["count"], 2)
        self.assertEqual(entry["ripe_here_cycles"], 3)


if __name__ == "__main__":
    unittest.main()
