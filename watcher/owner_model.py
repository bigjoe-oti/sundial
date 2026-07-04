#!/usr/bin/env python3
"""Owner Model — deterministic distillation of the Habit Ledger.

No LLM: medians, percentiles, histograms. The agent reads the output to
write weekly reflections; tunables change only on the owner's word."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402

OWNER_MODEL_MAX_AGE_S = 6 * 3600


def _pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    frac = k - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def distill(events: list, now) -> dict:
    tz = core.tzinfo()
    stretches, latencies, hourly = [], [], [0] * 24
    offers, fires, ratios = {}, {"total": 0, "muted": 0,
                                 "by_defer_reason": {}}, []
    first_ts = last_ts = None
    stretch_start = None
    for e in events:
        ts = core.parse_iso(e.get("ts"))
        if ts is None:
            continue
        first_ts = ts if first_ts is None else min(first_ts, ts)
        last_ts = ts if last_ts is None else max(last_ts, ts)
        kind = e.get("kind")
        # Only owner-driven events feed the activity histogram -- per-cycle
        # daemon telemetry (net/meeting-net) and away-state fires/transitions
        # measure the daemon's own logging cadence, not the owner's rhythm.
        owner_driven = (
            (kind == "presence" and e.get("to") != "away")
            or kind in ("answered", "curiosity", "offer", "auto-watch")
            or (kind == "fire" and e.get("state") in ("here", "elsewhere", "present")))
        if owner_driven:
            hourly[ts.astimezone(tz).hour] += 1
        if kind == "presence":
            if e.get("from") == "away" and e.get("to") != "away":
                stretch_start = ts
            elif e.get("to") == "away" and stretch_start is not None:
                stretches.append((ts - stretch_start).total_seconds() / 60)
                stretch_start = None
        elif kind == "answered":
            try:
                latencies.append(float(e.get("latency_s", 0)))
            except (TypeError, ValueError):
                pass
        elif kind == "offer":
            k = str(e.get("opp", "?"))
            offers.setdefault(k, {"offered": 0})["offered"] += 1
        elif kind == "fire":
            fires["total"] += 1
            if e.get("muted"):
                fires["muted"] += 1
            dr = str(e.get("defer_reason", "none"))
            fires["by_defer_reason"][dr] = (
                fires["by_defer_reason"].get(dr, 0) + 1)
        elif kind == "estimate":
            try:
                ratios.append(float(e.get("ratio")))
            except (TypeError, ValueError):
                pass
    stretches.sort()
    latencies.sort()
    span = ((last_ts - first_ts).total_seconds() / 86400
            if first_ts and last_ts else 0.0)
    return {
        "generated_at": now.isoformat(),
        "data_span_days": round(span, 2),
        "events": len(events),
        "active_stretches": {"count": len(stretches),
                             "median_m": round(_pct(stretches, 0.5), 1),
                             "p90_m": round(_pct(stretches, 0.9), 1)},
        "hourly_activity": hourly,
        "reply_latency": {"count": len(latencies),
                          "median_s": round(_pct(latencies, 0.5), 1),
                          "p90_s": round(_pct(latencies, 0.9), 1)},
        "offers": offers,
        "fires": fires,
        "estimates": {"count": len(ratios),
                      "mean_ratio": (round(sum(ratios) / len(ratios), 2)
                                     if ratios else None),
                      "last5": [round(r, 2) for r in ratios[-5:]]},
    }


def refresh(force: bool = False):
    try:
        out = core.DATA / "owner_model.json"
        if not force and out.exists():
            import time
            if time.time() - out.stat().st_mtime < OWNER_MODEL_MAX_AGE_S:
                return None
        events = []
        for name in ("habits.1.jsonl", "habits.jsonl"):
            p = core.DATA / name
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
        model = distill(events, core.now_utc())
        core.write_json(out, model)
        return model
    except Exception:
        return None
