"""Memory decay scoring (ACT-R base-level activation), compute-only.

We do NOT act on these scores in v1 (nothing is forgotten or pruned). We only
compute and store them, so the signal starts accruing and we can trust it before
we ever wire it to actual forgetting.

ACT-R base-level activation for a chunk i:

    B_i = ln( sum_j  t_j^(-d) )

where t_j is the time since the j-th access and d is the decay rate (~0.5).
We approximate per memory file with the file's mtime as the most-recent access
and a maintained access tally, giving the ln(accesses) + recency shape:

    score ~= ln(accesses) - d * ln(age_seconds)

Higher score = more salient (recently and/or frequently touched).
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DECAY_RATE = 0.5


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
