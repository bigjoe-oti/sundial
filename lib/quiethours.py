"""Sundial v3 — learned quiet hours (Task 12).

Deterministic, no LLM: hours where the Owner Model's hourly activity
histogram shows p25 == 0 across a minimum observation window become
"learned quiet" hours.

PRECEDENCE (composes with existing mechanisms, never conflicts):
  1. owner-declared snooze   (sundial snooze — holds DELIVERY)
  2. learned quiet hours     (this module — gates SOUND only)
  3. nothing

Learned quiet NEVER holds a delivery the current code would deliver; it
only mutes audio, exactly matching sound_allowed()'s contract.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "lib"))

MIN_DAYS_OBSERVED = 14


def compute_quiet_hours(hourly: list, days_observed: int) -> list:
    """hourly: 24 activity counts (owner-driven events per local hour).
    Returns sorted list of quiet hour ints. Fail-open: any insufficiency
    in the data returns [] (no quiet hours — current behavior preserved)."""
    try:
        if not isinstance(hourly, list) or len(hourly) != 24:
            return []
        if not all(isinstance(h, int) and h >= 0 for h in hourly):
            return []
        if days_observed < MIN_DAYS_OBSERVED:
            return []
        counts = sorted(hourly)
        # Guard against the inactive install: if there is essentially NO
        # activity anywhere, this is not a rest pattern — fail open.
        if sum(hourly) < len(hourly):  # fewer events than observed hours
            return []
        # p25 of 24 values with all-zero hours present == 0 for those hours;
        # an hour is "learned quiet" when its count is 0 AND overall p25 is 0
        # (i.e., at least a quarter of hours are silent — real rest pattern,
        # not an inactive install).
        p25_idx = max(0, int(0.25 * (len(counts) - 1)))
        if counts[p25_idx] != 0:
            return []
        return [h for h, c in enumerate(hourly) if c == 0]
    except Exception:
        return []


def load_or_compute(data_dir, owner_model_doc=None):
    """Read cached quiethours.json; recompute from the owner model doc when
    given. Never raises; absent file -> [] (default-off)."""
    p = Path(data_dir) / "quiethours.json"
    try:
        cached = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and "hours" in cached:
            return [int(h) for h in cached["hours"]]
    except Exception:
        pass
    if owner_model_doc:
        hourly = owner_model_doc.get("hourly_activity")
        days = owner_model_doc.get("days_observed", 0)
        return compute_quiet_hours(hourly, days)
    return []


def save(data_dir, hours):
    """Persist learned hours; called by the watcher after owner-model
    refresh. Pure JSON write under the caller's discipline."""
    p = Path(data_dir) / "quiethours.json"
    try:
        p.write_text(json.dumps({"hours": sorted(hours)}), encoding="utf-8")
    except Exception:
        pass
