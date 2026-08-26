"""Sundial v3 — platform backend contracts.

Two abstract interfaces separate WHAT the watcher needs from HOW a platform
provides it:

PresenceBackend — zero-permission sensor reads. Every method MAY return
None, which means "sensor unavailable on this platform." Callers MUST treat
None as soften-only: degrade to legacy wall-clock semantics, never block,
never crash. This is a load-bearing honesty rail (see the v3 plan, Phase 0).

NotifyBackend — delivery of one notification with optional sound/speech.
Implementations encapsulate their platform's attribution mechanics (on macOS:
the compiled applet so banners say "Sundial", not Script Editor).

No I/O at import time; no LLM anywhere; pure contracts.
"""

from abc import ABC, abstractmethod


class PresenceBackend(ABC):
    """Sensor seam. None = unavailable: soften, never block."""

    @abstractmethod
    def idle_seconds(self):
        """Seconds since last human input, or None if unknowable."""

    @abstractmethod
    def frontmost_app(self):
        """Frontmost application NAME only (never window titles), or None."""

    @abstractmethod
    def screen_locked(self):
        """True/False, or None if lock state is unknowable."""

    @abstractmethod
    def in_call(self):
        """True when a live call (WebRTC/meet app) is detectable, else
        False, or None when undetectable."""


class NotifyBackend(ABC):
    """Delivery seam for desktop notifications and audio."""

    @abstractmethod
    def deliver(self, title: str, message: str, *, audible: bool = True,
                speak_text: str | None = None) -> bool:
        """Deliver one notification. Returns True when delivery was
        dispatched without error. speak_text (optional) is spoken aloud
        when the backend supports speech and audible is True."""
