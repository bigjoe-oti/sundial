"""Task 5: Hermes adapter + generic hook script (bin/sundial-hermes-hook).

Pins:
- exit 0 on every input, including corrupt/empty JSON (fail-safe rule)
- <sundial> block with local time + due-count lines on session_start
- machine events NEVER disarm asks or stamp last_prompt
- human prompts DO disarm and stamp
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "bin" / "sundial-hermes-hook"


def _run_hook(payload, data_dir):
    env = dict(__import__("os").environ, SUNDIAL_DATA_DIR=str(data_dir))
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload) if payload is not None else "",
        capture_output=True, text=True, env=env, timeout=30)
    return proc


class TestHookFailSafe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exit_zero_on_corrupt_json(self):
        proc = _run_hook(None, self.data)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_exit_zero_on_empty_input(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input="", capture_output=True,
            text=True, timeout=30,
            env=dict(__import__("os").environ,
                     SUNDIAL_DATA_DIR=str(self.data)))
        self.assertEqual(proc.returncode, 0)


class TestSessionStartBlock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_block_has_time_and_due_lines(self):
        proc = _run_hook({"event": "session_start"}, self.data)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout
        self.assertIn("<sundial>", out)
        self.assertIn("It is", out)
        self.assertIn("No commitments due right now.", out)
        self.assertIn("</sundial>", out)


class TestMachineEventFiltering(unittest.TestCase):
    """The honesty seam: machine re-invocations must not count as humans."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        # One open awaiting-reply ask, overdue.
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        overdue = (now - datetime.timedelta(minutes=30)).isoformat()
        created = (now - datetime.timedelta(hours=1)).isoformat()
        (self.data / "commitments.json").write_text(json.dumps([{
            "id": "abc123", "kind": "awaiting-reply", "status": "open",
            "text": "integration smoke question", "due_at": overdue,
            "created_at": created}]), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _ask_disarmed(self):
        items = json.loads(
            (self.data / "commitments.json").read_text(encoding="utf-8"))
        return all(c.get("status") != "open"
                   for c in items if c.get("id") == "abc123")

    def _last_prompt_stamped(self):
        p = self.data / "last_prompt.json"
        return p.exists()

    def test_machine_marker_prompt_leaves_ask_open(self):
        proc = _run_hook({"event": "prompt_submit",
                          "prompt": "<task-notification> background done"},
                         self.data)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self._ask_disarmed())
        self.assertFalse(self._last_prompt_stamped())

    def test_machine_true_flag_leaves_ask_open(self):
        proc = _run_hook({"event": "prompt_submit", "machine": True,
                          "prompt": "cron injected context"}, self.data)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self._ask_disarmed())

    def test_human_prompt_disarms_and_stamps(self):
        proc = _run_hook({"event": "prompt_submit",
                          "prompt": "yes do it my answer"}, self.data)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(self._ask_disarmed())
        self.assertTrue(self._last_prompt_stamped())


if __name__ == "__main__":
    unittest.main()
