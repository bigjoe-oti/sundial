"""Tasks 8/9: Linux + Headless backends. The None-softens honesty rail is
pinned here: every sensor of the HeadlessBackend returns None, and no
backend may raise on sensor failure."""

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


contracts = _load_file("sundial_v3_backends_contracts",
                       REPO / "core" / "backends.py")
portable = _load_file("sundial_v3_backends_impl_portable",
                      REPO / "core" / "backends_impl" / "portable.py")


class TestContractCompliance(unittest.TestCase):
    def test_linux_backend_satisfies_presence_contract(self):
        self.assertTrue(issubclass(portable.LinuxBackend,
                                   contracts.PresenceBackend))

    def test_headless_backend_satisfies_presence_contract(self):
        self.assertTrue(issubclass(portable.HeadlessBackend,
                                   contracts.PresenceBackend))

    def test_notifiers_satisfy_notify_contract(self):
        for cls in (portable.LinuxNotifier, portable.WebhookNotifier):
            self.assertTrue(issubclass(cls, contracts.NotifyBackend), cls)


class TestHeadlessHonestyRails(unittest.TestCase):
    """No sensors by definition — every read is None (soften-only)."""

    def setUp(self):
        self.b = portable.HeadlessBackend()

    def test_all_sensors_none(self):
        self.assertIsNone(self.b.idle_seconds())
        self.assertIsNone(self.b.frontmost_app())
        self.assertIsNone(self.b.screen_locked())
        self.assertIsNone(self.b.in_call())

    def test_legacy_wall_semantics_unchanged_with_none(self):
        legacy = _load_file("sundial_v3_legacy_presence",
                            REPO / "watcher" / "presence.py")
        cli = legacy.DEFAULT_CLI_APPS
        # state None = full degrade: callers must fall back to wall time
        self.assertIsNone(legacy.derive_state(None, None, cli))


class TestLinuxBackendDegradation(unittest.TestCase):
    """On a machine without X tools, sensors return None, never raise."""

    def setUp(self):
        self.b = portable.LinuxBackend()

    def test_idle_none_when_xprintidle_missing(self):
        import shutil
        orig = shutil.which
        shutil.which = lambda name: None
        try:
            self.assertIsNone(self.b.idle_seconds())
        finally:
            shutil.which = orig

    def test_lock_none_without_session(self):
        import os
        orig = os.environ.get
        os.environ.get = lambda k, d=None: None if k == "XDG_SESSION_ID" \
            else orig(k, d)
        try:
            self.assertIsNone(self.b.screen_locked())
        finally:
            os.environ.get = orig


class TestWebhookNotifier(unittest.TestCase):
    def test_no_url_logs_to_stderr_never_raises(self):
        import io
        import os
        from contextlib import redirect_stderr
        saved = os.environ.pop("SUNDIAL_WEBHOOK_URL", None)
        try:
            n = portable.WebhookNotifier()
            buf = io.StringIO()
            with redirect_stderr(buf):
                ok = n.deliver("Sundial", "test message")
            self.assertFalse(ok)  # logged, not delivered
            self.assertIn("Sundial", buf.getvalue())
        finally:
            if saved is not None:
                os.environ["SUNDIAL_WEBHOOK_URL"] = saved

    def test_url_set_posts_json(self):
        import os
        os.environ["SUNDIAL_WEBHOOK_URL"] = "http://127.0.0.1:1/nope"
        try:
            n = portable.WebhookNotifier()
            # unreachable URL: must fall through to stderr log, not raise
            ok = n.deliver("Sundial", "msg")
            self.assertFalse(ok)
        finally:
            os.environ.pop("SUNDIAL_WEBHOOK_URL", None)


if __name__ == "__main__":
    unittest.main()
