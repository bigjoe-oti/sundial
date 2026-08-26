#!/usr/bin/env python3
"""Sundial v3 — macOS PresenceBackend.

Move-only refactor: the exact sensor invocations that lived in
watcher/presence.py (ioreg HIDIdleTime, lsappinfo frontmost name,
ioreg lock-state plist, pmset assertion triples) now sit behind the
core.backends.PresenceBackend contract. No logic edits. Sensor methods
return None on any failure — None softens, never blocks (honesty rail).

The watcher/presence.py module-level functions remain the canonical
implementations for existing callers; this backend delegates to them so
there is exactly ONE copy of each sensor command in the repo.
"""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_CONTRACTS = _REPO / "core" / "backends.py"

# Load the contracts module under a stable unique name (the repo contains
# lib/core.py which can shadow top-level 'core'; see v3 plan Task 3).
_spec = importlib.util.spec_from_file_location(
    "sundial_v3_backends_contracts", _CONTRACTS)
_contracts = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("sundial_v3_backends_contracts", _contracts)
_spec.loader.exec_module(_contracts)
PresenceBackend = _contracts.PresenceBackend

_REPO = Path(__file__).resolve().parents[2]


_loaded = {}


def _load_legacy_presence():
    """Load watcher/presence.py under a unique name (it imports as a bare
    top-level module in the legacy layout; see v3 plan Task 3). Cached in
    sys.modules AND _loaded so every consumer shares ONE instance — tests
    must patch legacy seams through this function's return value."""
    unique = "sundial_v3_legacy_presence"
    if unique in sys.modules:
        _loaded[unique] = sys.modules[unique]
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(
        unique, _REPO / "watcher" / "presence.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    _loaded[unique] = mod
    spec.loader.exec_module(mod)
    return mod


class MacOSBackend(PresenceBackend):
    """Delegates to watcher/presence.py — one copy of every command."""

    def idle_seconds(self):
        return _load_legacy_presence().idle_seconds()

    def frontmost_app(self):
        return _load_legacy_presence().front_app()

    def screen_locked(self):
        return _load_legacy_presence().screen_locked()

    def in_call(self):
        """True when any live WebRTC/meeting-app process shows an active
        call assertion, False otherwise; None only if pmset itself fails."""
        raw = _load_legacy_presence().assertions_raw()
        if not raw:
            return None
        triples = _load_legacy_presence().assertion_triples(raw)
        webrtc = _webrtc_procs(triples)
        return bool(webrtc)


def _webrtc_procs(triples):
    """Same discriminator opportunities.webrtc_procs uses: the NAME field
    tells a live call from mere video playback. Duplicated minimal logic
    here (substring check) rather than importing watcher.opportunities to
    keep this backend dependency-light; parity is pinned by tests."""
    hits = set()
    for proc, _kind, name in triples:
        n = (name or "").lower()
        if "webrtc" in n or "active peerconnection" in n:
            hits.add(proc.lower())
    return hits


def detect() -> "MacOSBackend | None":
    """Platform probe: macos backend only exists on darwin."""
    import sys as _sys
    return MacOSBackend() if _sys.platform == "darwin" else None
