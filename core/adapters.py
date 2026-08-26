"""Sundial v3 — agent adapter contracts.

An AgentAdapter wires Sundial to one agentic runtime (Claude Code, Hermes,
a generic stdin/stdout harness). Adapters never make policy decisions —
they translate ledger state into the runtime's native injection/delivery
mechanisms. The generic hook protocol (see
docs/superpowers/specs/2026-08-26-universal-backend-design.md) is THE
portability contract: JSON on stdin, text block on stdout, exit 0 always.
"""

from abc import ABC, abstractmethod


class AgentAdapter(ABC):
    """One adapter per agentic runtime."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'hermes', 'claude-code', 'generic'."""

    @abstractmethod
    def session_claim_path(self):
        """Path this adapter's session-claim heartbeat is written to,
        or None when the runtime has no claim concept."""

    @abstractmethod
    def deliver_fire(self, rung_text: str, tier: str, urgency: str) -> str:
        """Route one ripe fire. Returns the channel used:
        'popup' | 'session' | 'webhook'."""

    @abstractmethod
    def context_block(self) -> str:
        """The <sundial> context payload for session start (time, age,
        due items, estimation health), built from the shared engine."""
