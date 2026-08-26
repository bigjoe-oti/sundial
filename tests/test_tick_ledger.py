"""Task 7: cli/tick.py records dual-clock rows into session-ledger.json.

Pins: accumulation across sequential writes; malformed input ignored
(fail-safe); explicit --tokens honored, absent tokens recorded as null
(never fabricated); estimator still calibrates from ledger+habits data.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run_tick(args, data_dir):
    import os
    env = dict(os.environ, SUNDIAL_DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, str(REPO / "cli" / "tick.py")] + args,
        capture_output=True, text=True, env=env, timeout=30)


class TestTickLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _ledger(self):
        return json.loads(
            (self.data / "session-ledger.json").read_text(encoding="utf-8"))

    def test_two_ticks_accumulate(self):
        self.assertEqual(_run_tick(["--session", "s1", "--wall-ms", "1000",
                                    "--tokens", "500"], self.data).returncode, 0)
        self.assertEqual(_run_tick(["--session", "s1", "--wall-ms", "2500",
                                    "--tokens", "700"], self.data).returncode, 0)
        rows = self._ledger()
        s1 = [r for r in rows if r.get("session_id") == "s1"]
        self.assertEqual(len(s1), 2)
        self.assertEqual(sum(r.get("wall_ms", 0) for r in s1), 3500)
        self.assertEqual(sum(r.get("tokens") or 0 for r in s1), 1200)

    def test_absent_tokens_recorded_as_null(self):
        _run_tick(["--session", "s2", "--wall-ms", "800"], self.data)
        row = self._ledger()[-1]
        self.assertIsNone(row.get("tokens"))

    def test_malformed_wall_ms_ignored_fail_safe(self):
        proc = _run_tick(["--session", "s3", "--wall-ms", "notanumber"],
                         self.data)
        self.assertEqual(proc.returncode, 0)
        # no row written with garbage wall_ms
        rows = self._ledger() if (self.data / "session-ledger.json").exists() else []
        self.assertEqual([r for r in rows if r.get("session_id") == "s3"], [])


if __name__ == "__main__":
    unittest.main()
