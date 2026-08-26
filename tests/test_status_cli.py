"""Task 13: status CLI JSON contract + SwiftBar render parity.

Pins the behavior parity that the v1 SwiftBar plugin established:
- curiosity offers NEVER inflate the actionable badge
- presence passes through as the raw state word
- estimate at-risk semantics match the plugin's red-line logic
- session queue counts UNCONSUMED entries only
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_status(data_dir):
    import os
    spec = importlib.util.spec_from_file_location(
        "sundial_v3_status", REPO / "cli" / "status.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sundial_v3_status"] = mod
    saved = os.environ.get("SUNDIAL_DATA_DIR")
    os.environ["SUNDIAL_DATA_DIR"] = str(data_dir)
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            os.environ.pop("SUNDIAL_DATA_DIR", None)
        else:
            os.environ["SUNDIAL_DATA_DIR"] = saved
        mod.core.DATA = Path(data_dir)
    return mod


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


class TestStatusContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        (self.data / "birth.json").write_text(json.dumps(
            {"created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_fields_present_on_empty_ledgers(self):
        mod = _load_status(self.data)
        s = mod.build_status(mod.core.DATA)
        for field in ("presence", "open_asks", "actionable_offers",
                      "estimate_at_risk", "snoozed", "session_queue"):
            self.assertIn(field, s, field)
        self.assertEqual(s["open_asks"], 0)

    def test_curiosity_excluded_from_offers_badge(self):
        _write(self.data / "opportunities.json", [
            {"status": "offered", "kind": "curiosity", "offer_msg": "x"},
            {"status": "offered", "kind": "meeting-start", "offer_msg": "y"},
        ])
        mod = _load_status(self.data)
        self.assertEqual(mod.build_status(mod.core.DATA)["actionable_offers"], 1)

    def test_queue_counts_unconsumed_only(self):
        _write(self.data / "session_speak.json", {"queue": [
            {"consumed": False}, {"consumed": True}, {"consumed": False}]})
        mod = _load_status(self.data)
        self.assertEqual(mod.build_status(mod.core.DATA)["session_queue"], 2)

    def test_at_risk_deadline_tighter_than_p90(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        due = (now + timedelta(minutes=5)).isoformat()
        created = (now - timedelta(minutes=30)).isoformat()
        _write(self.data / "commitments.json", [{
            "id": "a1", "kind": "remember", "status": "open", "text": "t1",
            "created_at": created, "due_at": due,
            "est": {"p50_s": 60, "p90_s": 3600}}])
        mod = _load_status(self.data)
        risk = mod.build_status(mod.core.DATA)["estimate_at_risk"]
        self.assertIsNotNone(risk)
        self.assertEqual(risk["reason"], "deadline-tight")

    def test_presence_passthrough(self):
        _write(self.data / "presence.json", {"state": "away"})
        mod = _load_status(self.data)
        self.assertEqual(mod.build_status(mod.core.DATA)["presence"], "away")


if __name__ == "__main__":
    unittest.main()
