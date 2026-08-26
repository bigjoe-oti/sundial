"""Sundial v3 — macOS NotifyBackend.

Move-only encapsulation of the delivery mechanics that live in
watcher/watcher.py (desktop_notify's applet-preference chain, chime map,
speech gating). The backend delegates to the canonical functions — one copy
of every mechanic. Linux and webhook implementations arrive in Phase 3.
"""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_CONTRACTS = _REPO / "core" / "backends.py"
_spec = importlib.util.spec_from_file_location(
    "sundial_v3_backends_contracts", _CONTRACTS)
_contracts = sys.modules.setdefault(
    "sundial_v3_backends_contracts",
    importlib.util.module_from_spec(_spec))
if getattr(_contracts, "NotifyBackend", None) is None:
    _spec.loader.exec_module(_contracts)
NotifyBackend = _contracts.NotifyBackend


def _load_legacy_watcher():
    unique = "sundial_v3_legacy_watcher"
    if unique in sys.modules:
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(
        unique, _REPO / "watcher" / "watcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    return mod


class MacOSNotifier(NotifyBackend):
    """Delegates to watcher/watcher.py delivery functions."""

    def deliver(self, title: str, message: str, *, audible: bool = True,
                speak_text=None) -> bool:
        w = _load_legacy_watcher()
        ok = w.desktop_notify(title, message)
        if audible:
            w.chime("return", "here", True)
        if speak_text and audible:
            w.speak_final(speak_text, True)
        return ok


def detect():
    import platform as _p
    return MacOSNotifier() if _p.system() == "Darwin" else None
