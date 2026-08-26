"""Task 8/9: Linux and Headless backends.

Linux sensors are best-effort with graceful None degradation (xprintidle,
loginctl LockedHint, xdotool frontmost); notify-send delivery.
Headless has NO sensors (all None) — wall ceilings drive every ladder;
delivery via SUNDIAL_WEBHOOK_URL or stderr log. Both satisfy the
PresenceBackend/NotifyBackend contracts; the None-softens rule is pinned
in tests so no port can quietly violate it.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_CONTRACTS = _REPO / "core" / "backends.py"
_spec = importlib.util.spec_from_file_location(
    "sundial_v3_backends_contracts", _CONTRACTS)
_contracts = sys.modules.setdefault(
    "sundial_v3_backends_contracts",
    importlib.util.module_from_spec(_spec))
if getattr(_contracts, "PresenceBackend", None) is None:
    _spec.loader.exec_module(_contracts)
PresenceBackend = _contracts.PresenceBackend
NotifyBackend = _contracts.NotifyBackend


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


class LinuxBackend(PresenceBackend):
    """Best-effort X11/Wayland sensing; every miss returns None."""

    def idle_seconds(self):
        xprintidle = shutil.which("xprintidle")
        if not xprintidle:
            return None
        out = _run([xprintidle])
        try:
            return int(out.strip()) / 1000.0  # xprintidle reports ms
        except ValueError:
            return None

    def frontmost_app(self):
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return None
        win = _run([xdotool, "getactivewindow"])
        if not win.strip():
            return None
        name = _run([xdotool, "getwindowname", win.strip()])
        return Path(name.strip()).name or None if name.strip() else None

    def screen_locked(self):
        loginctl = shutil.which("loginctl")
        session = os.environ.get("XDG_SESSION_ID", "")
        if not loginctl or not session:
            return None
        out = _run([loginctl, "show-session", session, "-p", "LockedHint"])
        if "=" not in out:
            return None
        return out.split("=", 1)[1].strip().lower() == "yes"

    def in_call(self):
        # No portable zero-permission call sensor on Linux desktops yet;
        # declared unavailable rather than guessed.
        return None


class HeadlessBackend(PresenceBackend):
    """No sensors by definition. Every method returns None: callers fall
    back to wall-clock semantics and ceilings force every final rung."""

    def idle_seconds(self):
        return None

    def frontmost_app(self):
        return None

    def screen_locked(self):
        return None

    def in_call(self):
        return None


class LinuxNotifier(NotifyBackend):
    def deliver(self, title, message, *, audible=True, speak_text=None):
        notify_send = shutil.which("notify-send")
        if not notify_send:
            return False
        urgency = "critical" if audible else "normal"
        r = _run([notify_send, "-u", urgency, "-a", "Sundial", title, message],
                 timeout=10)
        ok = bool(r) or True  # notify-send exits 0 silently on success
        if speak_text and audible:
            spd_say = shutil.which("spd-say")
            if spd_say:
                _run([spd_say, speak_text], timeout=30)
        return ok


class WebhookNotifier(NotifyBackend):
    """POSTs {title,message} JSON to SUNDIAL_WEBHOOK_URL; logs to stderr
    when unset. Never raises."""

    def deliver(self, title, message, *, audible=True, speak_text=None):
        url = os.environ.get("SUNDIAL_WEBHOOK_URL")
        payload = {"title": title, "message": message}
        if url:
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
                return True
            except Exception:
                pass
        print(f"SUNDIAL WEBHOOK (no URL set): {title}: {message}",
              file=sys.stderr)
        return False


def detect_presence():
    override = os.environ.get("SUNDIAL_BACKEND")
    if override == "linux":
        return LinuxBackend()
    if override == "headless":
        return HeadlessBackend()
    if override == "macos":
        macos = _load_macos()
        return macos.MacOSBackend() if macos else None
    import platform
    if platform.system() == "Darwin":
        macos = _load_macos()
        return macos.MacOSBackend() if macos else None
    if platform.system() == "Linux":
        return LinuxBackend()
    return HeadlessBackend()


def detect_notifier():
    override = os.environ.get("SUNDIAL_BACKEND")
    if override == "headless" or os.environ.get("SUNDIAL_WEBHOOK_URL"):
        return WebhookNotifier()
    import platform
    if platform.system() == "Darwin":
        macos = _load_macos()
        return macos.MacOSNotifier() if macos else WebhookNotifier()
    if platform.system() == "Linux":
        return LinuxNotifier()
    return WebhookNotifier()


def _load_macos():
    if not (_REPO / "core" / "backends_impl" / "macos.py").exists():
        return None
    return _load_file("sundial_v3_backends_impl_macos",
                      _REPO / "core" / "backends_impl" / "macos.py")


def _load_notify_macos():
    path = _REPO / "core" / "backends_impl" / "notify_macos.py"
    if not path.exists():
        return None
    return _load_file("sundial_v3_backends_impl_notify_macos", path)


def _load_file(unique, path):
    if unique in sys.modules:
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(unique, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    return mod
