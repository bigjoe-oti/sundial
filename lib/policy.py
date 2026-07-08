#!/usr/bin/env python3
"""Sundial — the decision policy. Pure, deterministic, no LLM, no IO.

Owns the escalation-tier table (urgency → ladder timings) and the autonomy
gate (confidence + reversibility → proceed/stand-down). Imported by the
watcher, the CLI, and the session-start hook so the vocabulary lives in one
place. Nothing here touches disk or the network."""

# urgency tier -> (unseen-time rung offsets, wall ceiling seconds, rung count).
# The "normal" row is byte-identical to the pre-tier constants (UNSEEN_OFFSETS
# = (600,1200,3000), WALL_CEILING_S = 5400) so legacy behavior is unchanged.
TIER_TABLE = {
    "low":    {"offsets": (1800, 5400),      "ceiling": 10800, "rungs": 2},
    "normal": {"offsets": (600, 1200, 3000), "ceiling": 5400,  "rungs": 3},
    "high":   {"offsets": (300, 600, 1200),  "ceiling": 2400,  "rungs": 3},
}
DEFAULT_TIER = "normal"


def tier_of(commitment: dict) -> str:
    """The commitment's urgency tier, defaulting to normal. An unknown/absent
    weight degrades to normal — never raises, never a surprise tier."""
    w = (commitment or {}).get("weight")
    return w if w in TIER_TABLE else DEFAULT_TIER
