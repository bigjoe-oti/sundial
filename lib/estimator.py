#!/usr/bin/env python3
"""Self-estimation engine (Phase B). Deterministic, no LLM: calibrates a raw
duration guess into P50/P90 from the agent's own measured history in
data/habits.jsonl. Method B -- empirical ratio-distribution percentiles with an
explicit small-n honesty rule. Read-only over the ledger except record_estimate.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402  (lib/core.py, same directory)

MIN_CONFIDENT_N = 5
BUCKET_MIN_N = 5
SMALL_N_FLOOR_RATIO = 2.0
# A done-close records open->close WALL time as actual_s. When that wall
# span dwarfs the estimate the pair is calendar idleness, not execution --
# ratio would poison P50/P90 calibration (live incident 2026-07-17:
# ratio 392.7x from a 5.5-day idle span). Past this bound the close is
# recorded with ratio=None (excluded from calibration) plus a note.
WALL_OUTLIER_MAX_RATIO = 20.0
HABIT_FILES = ("habits.1.jsonl", "habits.jsonl")
_DUR_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([smh]?)")
# The WHOLE (trimmed) string must be a run of <number><optional s|m|h> tokens,
# so garbage never parses to a confidently-wrong number: a leading sign,
# unknown units ("30ms"), or stray chars ("1e3s") all fail this and return None.
_DUR_FULL = re.compile(r"(?:\d+(?:\.\d+)?\s*[smh]?\s*)+")
_UNIT_S = {"s": 1.0, "m": 60.0, "h": 3600.0, "": 1.0}


def parse_duration(s):
    """'30m'/'1h'/'1800s'/'1800'/'1h30m'/'1.5h' -> seconds (float), or None.
    Bare numbers are seconds. Strict: the whole string must be valid duration
    tokens, else None -- the caller errors rather than guessing (spec §6)."""
    if s is None:
        return None
    s = str(s).strip().lower()
    if not s or not _DUR_FULL.fullmatch(s):
        return None
    return sum(float(num) * _UNIT_S[unit]
               for num, unit in _DUR_TOKEN.findall(s) if num)


def percentile(sorted_vals, q):
    """Linear-interpolation percentile, q in [0,1]. Raises ValueError on empty;
    callers guard on n first."""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("percentile of empty sample")
    if n == 1:
        return float(sorted_vals[0])
    k = (n - 1) * q
    lo = int(k)
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def calibrate(raw_s, sample, *, absolute=False):
    """Percentile calibration with the small-n honesty rule.
    - absolute=False: `sample` are actual/est ratios; scale raw_s by them.
    - absolute=True:  `sample` are absolute seconds (latencies); return directly.
    Returns {p50_s, p90_s, n, confidence}."""
    n = len(sample)
    if n == 0:
        if absolute:
            return {"p50_s": None, "p90_s": None, "n": 0, "confidence": "none"}
        return {"p50_s": float(raw_s),
                "p90_s": float(raw_s) * SMALL_N_FLOOR_RATIO,
                "n": 0, "confidence": "none"}
    s = sorted(float(x) for x in sample)
    p50 = percentile(s, 0.50)
    p90 = percentile(s, 0.90)
    low = n < MIN_CONFIDENT_N
    if absolute:
        p50_s = p50
        p90_s = max(p90, p50 * SMALL_N_FLOOR_RATIO) if low else p90
    else:
        raw = float(raw_s)
        p50_s = raw * p50
        p90_s = raw * (max(p90, SMALL_N_FLOOR_RATIO) if low else p90)
    return {"p50_s": p50_s, "p90_s": p90_s, "n": n,
            "confidence": "low" if low else "high"}


def _read_habits(data_dir):
    """All dict events from the habit ledger (rotated file first). Malformed
    lines and non-dict rows are skipped; never raises."""
    events = []
    for name in HABIT_FILES:
        p = Path(data_dir) / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                events.append(e)
    return events


def _estimate_ratios(events, bucket=None):
    """(ratios, used_bucket). A bucket with >= BUCKET_MIN_N tagged samples is
    used; otherwise fall back to the global sample and report used_bucket=None."""
    est = [e for e in events if e.get("kind") == "estimate"
           and isinstance(e.get("ratio"), (int, float))]
    if bucket:
        tagged = [e for e in est if e.get("bucket") == bucket]
        if len(tagged) >= BUCKET_MIN_N:
            return [float(e["ratio"]) for e in tagged], bucket
    return [float(e["ratio"]) for e in est], None


def _answered_latencies(events):
    return [float(e["latency_s"]) for e in events
            if e.get("kind") == "answered"
            and isinstance(e.get("latency_s"), (int, float))]


def estimate_execution(raw_s, data_dir, bucket=None):
    events = _read_habits(data_dir)
    sample, used = _estimate_ratios(events, bucket=bucket)
    out = calibrate(raw_s, sample, absolute=False)
    out["bucket"] = used
    return out


def estimate_review(data_dir):
    events = _read_habits(data_dir)
    return calibrate(None, _answered_latencies(events), absolute=True)


def estimate_timeline(raw_s, data_dir, bucket=None):
    ex = estimate_execution(raw_s, data_dir, bucket=bucket)
    rv = estimate_review(data_dir)
    # None (no review data) contributes nothing; an explicit is-None check so a
    # legitimate 0.0 latency is added, not conflated with the no-data sentinel.
    add50 = rv["p50_s"] if rv["p50_s"] is not None else 0.0
    add90 = rv["p90_s"] if rv["p90_s"] is not None else 0.0
    return {"execution": ex, "review": rv,
            "end_to_end_p50_s": ex["p50_s"] + add50,
            "end_to_end_p90_s": ex["p90_s"] + add90}


def sanity_line(est_s, ttd_s, calib):
    """Deadline-sanity warning, or None. Speaks ONLY when history exists
    (n > 0) and its calibrated P90 exceeds the time available before the
    deadline. Pure: no IO, no clock."""
    if not isinstance(calib, dict) or not calib.get("n"):
        return None
    p90 = calib.get("p90_s")
    if p90 is None or ttd_s is None or p90 <= ttd_s:
        return None
    return (f"⚠ history: P90 ~{core.humanize_delta(p90)} "
            f"(n={calib['n']}, {calib.get('confidence', '?')} confidence) — "
            f"deadline leaves {core.humanize_delta(ttd_s)}; "
            f"pad it or tighten scope.")


NUDGE_THRESHOLDS = (0.5, 0.8, 1.0)
NUDGE_STALE_X = 3.0


def budget_flags(commitments, fired, now):
    """One-line budget-crossing flags for open estimated plain commitments.
    Pure: no IO, no clock. `fired` maps cid -> [thresholds already flagged];
    returns (lines, new_fired). A crossing flags ONCE (highest new threshold
    only -- a flag, not a counter). elapsed > NUDGE_STALE_X * P90 is calendar
    staleness, not a live overrun: skipped (session-start running-long flag
    owns those). Malformed anything degrades to silence."""
    lines, new_fired = [], {}
    if not isinstance(fired, dict):
        fired = {}
    for c in commitments if isinstance(commitments, list) else []:
        try:
            if (not isinstance(c, dict) or c.get("status") != "open"
                    or c.get("kind") == "awaiting-reply"):
                continue
            snap = c.get("est")
            created = core.parse_iso(c.get("created_at"))
            if not isinstance(snap, dict) or created is None:
                continue
            p90 = snap.get("p90_s")
            if not isinstance(p90, (int, float)) or p90 <= 0:
                continue
            frac = (now - created).total_seconds() / p90
            if frac > NUDGE_STALE_X:
                continue
            done = [t for t in fired.get(c.get("id"), []) if t in NUDGE_THRESHOLDS]
            crossed = [t for t in NUDGE_THRESHOLDS if frac >= t and t not in done]
            if not crossed:
                if done:
                    new_fired[c.get("id")] = done
                continue
            top = max(crossed)
            lines.append(
                f"⏱ {str(c.get('text', ''))[:48]}: {int(top * 100)}% of its "
                f"P90 ({core.humanize_delta(p90)}) elapsed since open.")
            new_fired[c.get("id")] = sorted(set(done) | set(crossed))
        except Exception:
            continue
    return lines, new_fired


def calibration_health(data_dir):
    """One-glance loop health for the surfaces: closed execution samples,
    their median ratio, and review-clock sample count. Never raises."""
    events = _read_habits(data_dir)
    ratios, _ = _estimate_ratios(events)
    reviews = _answered_latencies(events)
    p50 = percentile(sorted(ratios), 0.50) if ratios else None
    n = len(ratios)
    conf = "none" if n == 0 else ("low" if n < MIN_CONFIDENT_N else "high")
    return {"n_exec": n, "p50_ratio": p50, "confidence": conf,
            "n_review": len(reviews)}


def record_estimate(data_dir, task, est_s, actual_s=None, bucket=None,
                    cid=None, note=None, force_null_ratio=False):
    """Append an estimate event to habits.jsonl. ratio computed when actual_s
    is given; actual_s=None pre-registers an open estimate (log est BEFORE the
    work, close it after -- keeps the loop honest). A single small append is
    atomic under O_APPEND, so no lock is needed. Fail-safe by contract."""
    try:
        est = float(est_s)
        act = float(actual_s) if actual_s is not None else None
        # ratio only when both are sane -- a negative/zero duration would poison
        # the percentile math, so it records as ratio=None (excluded from calib).
        ratio = (act / est if (act is not None and act >= 0 and est > 0)
                 else None)
        if force_null_ratio:
            ratio = None
        rec = {"kind": "estimate", "task": str(task), "est_s": est,
               "actual_s": act, "ratio": ratio, "ts": core.now_utc().isoformat()}
        if bucket:
            rec["bucket"] = str(bucket)
        if cid:
            rec["cid"] = str(cid)
        if note:
            rec["note"] = str(note)
        p = Path(data_dir)
        p.mkdir(parents=True, exist_ok=True)   # never silently drop on fresh env
        with open(p / "habits.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
