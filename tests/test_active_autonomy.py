"""Tests for Active Autonomy Dispatcher (Task 1)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402
import policy  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "watcher"))
import watcher  # noqa: E402


class TestActiveAutonomy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self._orig_data = core.DATA
        core.DATA = self.tmp_dir
        self.log_file = core.DATA / "autonomy_exec.log"

    def tearDown(self) -> None:
        core.DATA = self._orig_data
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_commitment_autonomy_fields(self) -> None:
        rec = core.add_commitment(
            "Test ask",
            kind="awaiting-reply",
            confidence=0.98,
            on_proceed="echo proceed",
            on_stand_down="echo stand_down",
        )
        self.assertEqual(rec["on_proceed"], "echo proceed")
        self.assertEqual(rec["on_stand_down"], "echo stand_down")
        loaded = core.load_commitments()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["on_proceed"], "echo proceed")
        self.assertEqual(loaded[0]["on_stand_down"], "echo stand_down")

    @patch("subprocess.Popen")
    def test_autonomy_dispatch_proceed_high_confidence(self, mock_popen: MagicMock) -> None:
        rec = core.add_commitment(
            "Test high conf",
            kind="awaiting-reply",
            confidence=0.98,
            on_proceed="touch /tmp/proceed.txt",
        )
        entry = {"count": 3, "unseen_s": 3000.0}
        now = core.now_utc()
        watcher._autonomy_dispatch(rec, entry, now)

        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], "touch /tmp/proceed.txt")
        self.assertTrue(kwargs.get("shell"))

        # Commitment status updated
        updated = core.load_commitments()[0]
        self.assertEqual(updated["status"], "auto-proceeded")

        # Execution log written
        self.assertTrue(self.log_file.exists())
        log_content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("[PROCEED]", log_content)
        self.assertIn(rec["id"], log_content)
        self.assertIn("touch /tmp/proceed.txt", log_content)

    @patch("subprocess.Popen")
    def test_autonomy_dispatch_irreversible_blocks_execution(self, mock_popen: MagicMock) -> None:
        rec = core.add_commitment(
            "Destructive action",
            kind="awaiting-reply",
            confidence=0.99,
            irreversible=True,
            on_proceed="rm -rf /tmp/data",
        )
        entry = {"count": 3, "unseen_s": 3000.0}
        now = core.now_utc()
        watcher._autonomy_dispatch(rec, entry, now)

        # Irreversible MUST NEVER run
        mock_popen.assert_not_called()
        self.assertFalse(self.log_file.exists())
        updated = core.load_commitments()[0]
        self.assertEqual(updated["status"], "open")

    @patch("subprocess.Popen")
    def test_autonomy_dispatch_stand_down(self, mock_popen: MagicMock) -> None:
        rec = core.add_commitment(
            "Low conf ask",
            kind="awaiting-reply",
            confidence=0.50,
            on_stand_down="echo fallback",
        )
        entry = {"count": 3, "unseen_s": 3000.0}
        now = core.now_utc()
        watcher._autonomy_dispatch(rec, entry, now)

        mock_popen.assert_called_once()
        args, _kwargs = mock_popen.call_args
        self.assertEqual(args[0], "echo fallback")
        updated = core.load_commitments()[0]
        self.assertEqual(updated["status"], "auto-stood-down")
        log_content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("[STAND_DOWN]", log_content)

    @patch("subprocess.Popen")
    def test_autonomy_dispatch_present_silence_does_not_execute_headless(
        self, mock_popen: MagicMock
    ) -> None:
        """Present-silence (0.80-0.95) proceeds in interactive sessions, but NOT headlessly."""
        rec = core.add_commitment(
            "Medium conf ask",
            kind="awaiting-reply",
            confidence=0.88,
            on_proceed="echo proceed",
        )
        entry = {"count": 3, "unseen_s": 3000.0, "ripe_here_cycles": 4}
        now = core.now_utc()
        # policy.autonomy_decision returns 'proceed' due to present-silence
        self.assertEqual(policy.autonomy_decision(rec, entry)["action"], "proceed")

        # But headless dispatcher restricts to >= 0.95
        watcher._autonomy_dispatch(rec, entry, now)
        mock_popen.assert_not_called()
        updated = core.load_commitments()[0]
        self.assertEqual(updated["status"], "open")


if __name__ == "__main__":
    unittest.main()
