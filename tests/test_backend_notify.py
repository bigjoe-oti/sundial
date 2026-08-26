"""Task 4 parity: MacOSNotifier delegates to the canonical watcher delivery
functions with the exact courtesy semantics (chime map, speech gating,
audible flag). Uses stubs for the subprocess seams — nothing real fires."""

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_file(unique, path):
    if unique in sys.modules:
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(unique, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    return mod


class TestNotifyBackendContractShape(unittest.TestCase):
    def test_macos_notifier_satisfies_contract(self):
        contracts = _load_file(
            "sundial_v3_backends_contracts", REPO / "core" / "backends.py")
        impl = _load_file(
            "sundial_v3_backends_impl_notify_macos",
            REPO / "core" / "backends_impl" / "notify_macos.py")
        self.assertTrue(issubclass(impl.MacOSNotifier,
                                   contracts.NotifyBackend))


class TestMacOSNotifierDelegation(unittest.TestCase):
    def _backend_with_stub_watcher(self):
        """Load the legacy watcher module with its sibling dir importable
        (watcher.py does bare `import presence`), then bind recording
        stubs over desktop_notify/chime/speak_final."""
        watcher_dir = str(REPO / "watcher")
        if watcher_dir not in sys.path:
            sys.path.insert(0, watcher_dir)
        w = _load_file("sundial_v3_legacy_watcher",
                       REPO / "watcher" / "watcher.py")
        calls = []

        def fake_desktop_notify(title, message):
            calls.append(("notify", title, message))
            return True

        def fake_chime(kind, state, audible):
            calls.append(("chime", kind, state, audible))

        def fake_speak_final(message, audible, force=False):
            calls.append(("speak", message, audible, force))

        self._orig = (w.desktop_notify, w.chime, w.speak_final)
        w.desktop_notify, w.chime, w.speak_final = (
            fake_desktop_notify, fake_chime, fake_speak_final)
        return _load_file("sundial_v3_backends_impl_notify_macos",
                          REPO / "core" / "backends_impl"
                          / "notify_macos.py").MacOSNotifier(), calls

    def tearDown(self):
        w = sys.modules["sundial_v3_legacy_watcher"]
        w.desktop_notify, w.chime, w.speak_final = self._orig

    def test_deliver_notifies(self):
        backend, calls = self._backend_with_stub_watcher()
        ok = backend.deliver("Sundial", "hello")
        self.assertTrue(ok)
        self.assertEqual(calls[0], ("notify", "Sundial", "hello"))

    def test_mute_suppresses_chime_and_speech_but_not_popup(self):
        backend, calls = self._backend_with_stub_watcher()
        backend.deliver("Sundial", "quiet", audible=False,
                        speak_text="final words")
        kinds = [c[0] for c in calls]
        self.assertIn("notify", kinds)
        self.assertNotIn("chime", kinds)
        self.assertNotIn("speak", kinds)

    def test_speech_gated_on_audible(self):
        backend, calls = self._backend_with_stub_watcher()
        backend.deliver("Sundial", "msg", audible=True,
                        speak_text="the contract")
        speak_calls = [c for c in calls if c[0] == "speak"]
        self.assertEqual(len(speak_calls), 1)
        self.assertEqual(speak_calls[0][1], "the contract")


if __name__ == "__main__":
    unittest.main()
