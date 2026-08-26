"""v3 portability contracts: core/backends.py and core/adapters.py must be
importable, define the documented abstract interfaces, and reject direct
instantiation. The None-softening rule is pinned at the contract level:
every PresenceBackend sensor method MAY return None and callers must treat
None as 'sensor unavailable — soften, never block'."""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    """Import a repo-top-level module without polluting global sys.path."""
    import importlib
    saved = sys.path[:]
    try:
        sys.path.insert(0, str(REPO))
        return importlib.import_module(name)
    finally:
        sys.path = saved


class TestPresenceBackendContract(unittest.TestCase):
    def test_abc_rejects_instantiation(self):
        backends = _load("core.backends")
        with self.assertRaises(TypeError):
            backends.PresenceBackend()

    def test_sensor_method_names(self):
        backends = _load("core.backends")
        for m in ("idle_seconds", "frontmost_app", "screen_locked",
                  "in_call"):
            self.assertTrue(hasattr(backends.PresenceBackend, m), m)


class TestNotifyBackendContract(unittest.TestCase):
    def test_abc_rejects_instantiation(self):
        backends = _load("core.backends")
        with self.assertRaises(TypeError):
            backends.NotifyBackend()

    def test_deliver_method(self):
        backends = _load("core.backends")
        self.assertTrue(hasattr(backends.NotifyBackend, "deliver"))


class TestAgentAdapterContract(unittest.TestCase):
    def test_abc_rejects_instantiation(self):
        adapters = _load("core.adapters")
        with self.assertRaises(TypeError):
            adapters.AgentAdapter()

    def test_documented_members(self):
        adapters = _load("core.adapters")
        for m in ("name", "session_claim_path", "deliver_fire",
                  "context_block"):
            self.assertTrue(hasattr(adapters.AgentAdapter, m), m)


class TestGenericHookProtocolSpec(unittest.TestCase):
    SPEC = REPO / "docs" / "superpowers" / "specs" / \
        "2026-08-26-universal-backend-design.md"

    def test_spec_exists_and_pins_the_protocol(self):
        self.assertTrue(self.SPEC.exists(), self.SPEC)
        text = self.SPEC.read_text(encoding="utf-8")
        for token in ('"event"', "session_start", "prompt_submit", "ALWAYS 0",
                      "machine"):
            self.assertIn(token, text, token)


if __name__ == "__main__":
    unittest.main()
