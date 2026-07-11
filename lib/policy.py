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


AUTONOMY_PROCEED_MIN = 0.95   # reversible actions proceed unattended at/above this
PRESENT_SILENCE_CONF_MIN = 0.80   # floor of the present-silence proceed band
PRESENT_SILENCE_MIN_CYCLES = 3    # ripe-while-"here" cycles (~30 min at 10-min cycles)


def _present_silence(entry) -> bool:
    """True only when the entry proves informed silence: ≥ N watcher cycles
    sampled strictly "here" while the ask was already ripe. Counted by
    accrue(ripe=True) — never from raw here_s, whose ask-time chunking the
    2026-07-08 audit found unsound (S1)."""
    if not isinstance(entry, dict):
        return False
    cycles = entry.get("ripe_here_cycles")
    if not isinstance(cycles, int) or isinstance(cycles, bool):
        return False
    return cycles >= PRESENT_SILENCE_MIN_CYCLES


def autonomy_decision(commitment: dict, entry: dict | None = None) -> dict:
    """Pure, total gate. Given a commitment (confidence/irreversible) and its
    notified entry, decide what the agent may do once the ladder is exhausted
    and the human still hasn't answered.

      - irreversible                      -> require_explicit_yes
                                             (no silence ever authorizes)
      - reversible, conf ≥ 0.95           -> proceed
      - reversible, 0.80 ≤ conf < 0.95,
        present-silence proven            -> proceed
      - otherwise                         -> stand_down

    Never raises; any malformed input degrades to the safest outcome."""
    commitment = commitment or {}
    if commitment.get("irreversible"):
        return {"action": "require_explicit_yes",
                "reason": "irreversible: no silence ever authorizes it"}
    try:
        conf = float(commitment.get("confidence"))
    except (TypeError, ValueError):
        return {"action": "stand_down", "reason": "no usable confidence stated"}
    if conf >= AUTONOMY_PROCEED_MIN:
        return {"action": "proceed",
                "reason": f"confidence {conf:.2f} ≥ {AUTONOMY_PROCEED_MIN}"}
    if conf >= PRESENT_SILENCE_CONF_MIN and _present_silence(entry):
        return {"action": "proceed",
                "reason": (f"present-silence: seen ripe ≥"
                           f"{PRESENT_SILENCE_MIN_CYCLES} cycles while here, "
                           f"no objection; confidence {conf:.2f} ≥ "
                           f"{PRESENT_SILENCE_CONF_MIN}")}
    return {"action": "stand_down",
            "reason": f"confidence {conf:.2f} below {AUTONOMY_PROCEED_MIN}"}
