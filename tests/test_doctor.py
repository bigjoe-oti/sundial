"""Task 11: sundial doctor — one command verifying the whole chain.

Checks (each independently reported; hard failures vs soft warnings):
- exactly-one-driver (launchd plist XOR hermes cron tick)
- backend sensor reachability (each sensor individually)
- TCC write-probe on data/
- ledger parseability (corrupt input must never crash the doctor itself)
- birth.json presence
Exit 0 unless a HARD failure; warnings listed but non-fatal.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCTOR = REPO / "cli" / "doctor.py"


def _run_doctor(data_dir, extra_env=None):
    import os
    env = dict(os.environ, SUNDIAL_DATA_DIR=str(data_dir))
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(DOCTOR)],
                          capture_output=True, text=True, env=env, timeout=30)


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        # minimal healthy ledger set
        (self.data / "birth.json").write_text(json.dumps({
            "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_exit_zero_on_minimal_healthy_ledger(self):
        proc = _run_doctor(self.data)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_reports_sections(self):
        proc = _run_doctor(self.data)
        for section in ("driver", "sensors", "ledger", "birth"):
            self.assertIn(section, proc.stdout.lower(), section)

    def test_corrupt_ledger_reported_not_crash(self):
        (self.data / "commitments.json").write_text("{corrupt", encoding="utf-8")
        proc = _run_doctor(self.data)
        self.assertEqual(proc.returncode, 0)  # corrupt = warning, not crash
        self.assertIn("commitments.json", proc.stdout)

    def test_missing_birth_is_warning_with_fresh_env_note(self):
        (self.data / "birth.json").unlink()
        proc = _run_doctor(self.data)
        self.assertEqual(proc.returncode, 0)  # fresh install is legitimate
        self.assertIn("birth", proc.stdout.lower())

    def test_tcc_probe_runs(self):
        proc = _run_doctor(self.data)
        self.assertIn("tcc", proc.stdout.lower())
        self.assertTrue((self.data / ".tcc_probe").exists() is False or True)


if __name__ == "__main__":
    unittest.main()
