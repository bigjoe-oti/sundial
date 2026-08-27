"""Memory decay scoring and ranking (ACT-R base-level activation).

ACT-R base-level activation for a chunk i:

    B_i = ln( sum_j  t_j^(-d) )

where t_j is the time since the j-th access and d is the decay rate (~0.5).
We approximate per memory file with the file's mtime as the most-recent access
and a maintained access tally, giving the ln(accesses) + recency shape:

    score ~= ln(accesses) - d * ln(age_seconds)

Higher score = more salient (recently and/or frequently touched).

compute_weights() produces the raw scores; rank_memories() sorts and annotates
them with salience tags (active / fading / dormant) for context injection.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path

DECAY_RATE = 0.5

# Salience boundaries for rank_memories() annotation.
ACTIVE_THRESHOLD = 0.0     # score > 0  → active
DORMANT_THRESHOLD = -2.0   # score < -2 → dormant; between → fading


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def compute_weights(memory_dir: Path, prior: dict | None = None) -> dict:
    """Return {filename: {score, accesses, last_seen}} for every memory .md file.

    ``prior`` is the previous memory-weights.json content; we carry forward
    (and gently grow) the access tally so frequency accumulates over sessions.
    Never deletes or mutates the memory files themselves.
    """
    prior = prior or {}
    now = time.time()
    out: dict = {}
    if not memory_dir or not memory_dir.exists():
        return out
    for p in sorted(memory_dir.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        age = max(1.0, now - mtime)
        prev = prior.get(p.name, {})
        prev_seen = prev.get("last_seen")
        accesses = int(prev.get("accesses", 1) or 1)
        # If the file was touched since we last saw it, count it as a fresh access.
        if prev_seen:
            try:
                prev_mtime = datetime.fromisoformat(prev_seen).timestamp()
                if mtime > prev_mtime + 1:
                    accesses += 1
            except ValueError:
                pass
        base = -DECAY_RATE * math.log(age)
        score = round(math.log(accesses) + base, 4)
        out[p.name] = {
            "score": score,
            "accesses": accesses,
            "last_seen": _iso(mtime),
        }
    return out


def _salience_tag(score: float) -> str:
    """Classify a decay score into a human-readable salience label."""
    if score > ACTIVE_THRESHOLD:
        return "active"
    if score < DORMANT_THRESHOLD:
        return "dormant"
    return "fading"


def rank_memories(weights: dict, top_k: int = 10) -> list[dict]:
    """Sort memory weights by activation score and annotate with salience tags.

    Takes the output of compute_weights() (or a loaded memory-weights.json) and
    returns a sorted list of the top-K entries, each annotated with a salience
    label for context injection into the <sundial> block.

    Returns [] on empty input (fail-open, never raises).
    """
    if not weights:
        return []
    entries = []
    for filename, info in weights.items():
        if not isinstance(info, dict):
            continue
        score = info.get("score")
        if not isinstance(score, (int, float)):
            continue
        entries.append({
            "file": filename,
            "score": float(score),
            "salience": _salience_tag(float(score)),
            "accesses": int(info.get("accesses", 1) or 1),
        })
    entries.sort(key=lambda e: e["score"], reverse=True)
    return entries[:top_k]

