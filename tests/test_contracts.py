"""v3 portability contracts: core/backends.py and core/adapters.py must be
importable, define the documented abstract interfaces, and reject direct
instantiation. The None-softening rule is pinned at the contract level:
every PresenceBackend sensor method MAY return None and callers must treat
None as 'sensor unavailable — soften, never block'."""

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    """Load a repo module under a UNIQUE name via file location.

    Why not plain import: lib/core.py is imported as top-level 'core' by
    sibling tests that prepend lib/ to sys.path, shadowing the core/
    package ('core' is not a package). File-location loading is immune to
    sys.path ordering — the same technique runtime consumers of the
    contracts must use until a future version unifies the namespace.
    """
    import importlib.util
    target = REPO / (name.replace(".", "/") + ".py")
    unique = "sundial_v3_" + name.replace(".", "_")
    if unique in sys.modules:
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(unique, target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    return mod


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
