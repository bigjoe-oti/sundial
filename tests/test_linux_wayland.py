"""Tests for LinuxBackend.idle_seconds() D-Bus/xprintidle fallback chain."""

import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "backends_impl"))

# Force the Linux backend regardless of host OS.
with patch.dict("os.environ", {"SUNDIAL_BACKEND": "linux"}):
    import portable  # noqa: E402


class TestLinuxIdleFallbackChain(unittest.TestCase):
    """Verify the 3-tier idle_seconds() fallback: Mutter → freedesktop → xprintidle."""

    def _backend(self) -> portable.LinuxBackend:
        return portable.LinuxBackend()

    @patch("portable.shutil.which")
    @patch("portable._run")
    def test_tier1_mutter_idlemonitor(self, mock_run: MagicMock,
                                       mock_which: MagicMock) -> None:
        """gdbus + Mutter IdleMonitor returns milliseconds."""
        mock_which.return_value = "/usr/bin/gdbus"
        # Mutter returns "(uint64 5000,)\n"
        mock_run.return_value = "(uint64 5000,)\n"
        result = self._backend().idle_seconds()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 5.0)  # 5000ms = 5.0s

    @patch("portable.shutil.which")
    @patch("portable._run")
    def test_tier2_freedesktop_screensaver(self, mock_run: MagicMock,
                                            mock_which: MagicMock) -> None:
        """Mutter fails, freedesktop ScreenSaver returns seconds."""
        mock_which.return_value = "/usr/bin/gdbus"
        # First call (Mutter) returns empty, second call (freedesktop) returns seconds.
        mock_run.side_effect = ["", "(uint32 42,)\n"]
        result = self._backend().idle_seconds()
        self.assertEqual(result, 42)

    @patch("portable.shutil.which")
    @patch("portable._run")
    def test_tier3_xprintidle_fallback(self, mock_run: MagicMock,
                                        mock_which: MagicMock) -> None:
        """gdbus not found, falls through to xprintidle."""
        def which_side_effect(cmd: str) -> str | None:
            if cmd == "gdbus":
                return None
            if cmd == "xprintidle":
                return "/usr/bin/xprintidle"
            return None
        mock_which.side_effect = which_side_effect
        mock_run.return_value = "3000\n"
        result = self._backend().idle_seconds()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 3.0)  # 3000ms = 3.0s

    @patch("portable.shutil.which")
    def test_all_sensors_missing_returns_none(self,
                                               mock_which: MagicMock) -> None:
        """No gdbus, no xprintidle → None (softening rail)."""
        mock_which.return_value = None
        result = self._backend().idle_seconds()
        self.assertIsNone(result)

    @patch("portable.shutil.which")
    @patch("portable._run")
    def test_mutter_garbage_falls_through(self, mock_run: MagicMock,
                                           mock_which: MagicMock) -> None:
        """gdbus present but Mutter returns garbage → tries freedesktop, then xprintidle."""
        def which_side_effect(cmd: str) -> str | None:
            if cmd == "gdbus":
                return "/usr/bin/gdbus"
            if cmd == "xprintidle":
                return "/usr/bin/xprintidle"
            return None
        mock_which.side_effect = which_side_effect
        # Mutter garbage, freedesktop garbage, xprintidle OK
        mock_run.side_effect = ["garbage", "garbage", "7500\n"]
        result = self._backend().idle_seconds()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result, 7.5)

    @patch("portable.shutil.which")
    @patch("portable._run")
    def test_mutter_empty_response(self, mock_run: MagicMock,
                                    mock_which: MagicMock) -> None:
        """Mutter returns empty string (service not running) → falls through."""
        def which_side_effect(cmd: str) -> str | None:
            if cmd == "gdbus":
                return "/usr/bin/gdbus"
            if cmd == "xprintidle":
                return None
            return None
        mock_which.side_effect = which_side_effect
        # Both D-Bus calls empty, no xprintidle
        mock_run.side_effect = ["", ""]
        result = self._backend().idle_seconds()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
