"""Task 3 parity goldens: MacOSBackend must return byte-identical results
to watcher/presence.py for every sensor, including None degradation. The
backend is a delegation shell — these tests prove it stays one."""

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


legacy = _load_file("sundial_v3_legacy_presence",
                    REPO / "watcher" / "presence.py")
macos = _load_file("sundial_v3_backends_impl_macos",
                   REPO / "core" / "backends_impl" / "macos.py")
# The backend must resolve its legacy dependency to the SAME instance this
# test file patches — both go through sys.modules under one unique name.
assert macos._load_legacy_presence() is legacy, \
    "backend and tests must share ONE presence module instance"


class _FakeIO:
    """Seam replacement: intercept legacy module's subprocess runner."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.outputs.pop(0) if self.outputs else ""


class TestMacOSBackendParity(unittest.TestCase):
    def setUp(self):
        self.backend = macos.MacOSBackend()

    def test_idle_parity(self):
        text = '"HIDIdleTime" = 42000000000'
        orig = legacy._run
        legacy._run = _FakeIO([text])
        try:
            got = self.backend.idle_seconds()
        finally:
            legacy._run = orig
        self.assertEqual(got, 42.0)

    def test_idle_none_degrades(self):
        orig = legacy._run
        legacy._run = _FakeIO(["garbage"])
        try:
            self.assertIsNone(self.backend.idle_seconds())
        finally:
            legacy._run = orig

    def test_frontmost_parity(self):
        orig = legacy._run
        front_line = "ASN:0x0-0x1a1a1a::\"LSDisplayName\"=\"iTerm2\""
        legacy._run = _FakeIO(["0x123 (iTerm2)", front_line])
        try:
            got = self.backend.frontmost_app()
        finally:
            legacy._run = orig
        self.assertEqual(got, "iTerm2")

    def test_lock_parity_true_false_none(self):
        import plistlib
        locked_plist = plistlib.dumps(
            {"IOConsoleUsers": [{"CGSSessionScreenIsLocked": True}]})
        unlocked_plist = plistlib.dumps({"IOConsoleUsers": [{}]})
        orig = legacy.screen_locked
        cases = [(True, locked_plist), (False, unlocked_plist)]
        for expected, payload in cases:
            legacy.screen_locked = lambda p=payload: legacy.parse_locked(p)
            try:
                self.assertEqual(self.backend.screen_locked(), expected)
            finally:
                legacy.screen_locked = orig
        # None case
        legacy.screen_locked = lambda: None
        try:
            self.assertIsNone(self.backend.screen_locked())
        finally:
            legacy.screen_locked = orig

    def test_in_call_none_when_pmset_fails(self):
        orig = legacy.assertions_raw
        legacy.assertions_raw = lambda: ""
        try:
            self.assertIsNone(self.backend.in_call())
        finally:
            legacy.assertions_raw = orig

    # Real-shape pmset lines, pinned from test_sundial.py's own goldens
    # (lines ~2345-2360): two spaces between ']' and the assertion type.
    WEBRTC_RAW = ('   pid 1234(Google Chrome): [0x5] 0011 '
                  'PreventUserIdleSystemSleep named: '
                  '"WebRTC has active PeerConnections"\n')
    VIDEO_RAW = ('   pid 778(Safari): [0x9] 0011 '
                 'PreventUserIdleDisplaySleep named: "Video Wake Lock"\n')

    def test_in_call_true_on_webrtc_name(self):
        orig_assert = legacy.assertions_raw
        legacy.assertions_raw = lambda: self.WEBRTC_RAW
        try:
            self.assertTrue(self.backend.in_call())
        finally:
            legacy.assertions_raw = orig_assert

    def test_in_call_false_on_video_wake_lock(self):
        orig_assert = legacy.assertions_raw
        legacy.assertions_raw = lambda: self.VIDEO_RAW
        try:
            self.assertFalse(self.backend.in_call())
        finally:
            legacy.assertions_raw = orig_assert

    def test_derive_state_unchanged_through_backend_data(self):
        """The classification function itself is untouched — backend data
        fed to legacy.derive_state yields the documented states."""
        cli = legacy.DEFAULT_CLI_APPS
        self.assertEqual(legacy.derive_state(5, "iTerm2", cli), "here")
        self.assertEqual(legacy.derive_state(5, "Figma", cli), "elsewhere")
        self.assertEqual(legacy.derive_state(999, "Figma", cli), "away")
        self.assertEqual(legacy.derive_state(5, None, cli), "present")
        self.assertIsNone(legacy.derive_state(None, None, cli))


if __name__ == "__main__":
    unittest.main()
