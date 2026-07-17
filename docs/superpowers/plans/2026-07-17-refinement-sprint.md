# Refinement Sprint Implementation Plan (wall-time guard, snooze, P90 nudges, prior-art refresh)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three evidence-backed refinement doors from the 2026-07-17 scene sweep (wall-time guard on `done`, owner-declared snooze, P90 budget-crossing nudges) plus the prior-art documentation refresh — each TDD'd, reviewed, and confidence-scored.

**Architecture:** All changes extend existing seams: the estimate close path (`lib/core.py:_close_estimate` → `lib/estimator.py:record_estimate`), the watcher delivery path (`watcher/watcher.py:run_cycle`), and the per-prompt hook (`hooks/prompt_submit.py:build_context`). One new CLI verb (`cli/snooze.py`) follows the existing verb pattern. No schema migrations; new state lives in two new small JSON files under `data/`.

**Tech Stack:** Python 3.11 (`/usr/local/bin/python3`), stdlib only, `unittest` + `tempfile` test isolation (house pattern in `tests/test_sundial.py`).

## Global Constraints

- Repo root: `/Users/OTI_1/Desktop/AI-WallClock-Project` (live install IS the working copy — commit to `main`, author Yousef <yousou88@gmail.com>, Claude trailer OK).
- **Fail-safe contract:** capture/surface code must never block a verb or crash the watcher/hook — every new IO path wraps in try/except and degrades to a no-op (house rule, see `_attach_estimate` docstring).
- **Test isolation:** tests NEVER touch the live `data/` dir — use `tempfile.TemporaryDirectory()` and point `core.DATA`/estimator `data_dir` at it (pattern: `TestCore.setUp` in `tests/test_sundial.py:120-134`). Never write synthetic closes to the live ledger.
- Run suite: `cd /Users/OTI_1/Desktop/AI-WallClock-Project && python3 -m unittest tests.test_sundial -v` (241 tests green before starting; must be green after every task).
- **QC gates (every task):** red test first → minimal code → green → full suite → commit. After Tasks 1–3 are done: `/code-review` on the diff, then the `verify` skill (drive the real surface), then per-task confidence score with named assumptions (95% gate — below 0.95 on any task = flag to Yousef, don't ship silently).
- No new dependencies, no LLM in any trigger path, nothing leaves the machine.

---

### Task 1: Wall-time guard on estimate close

The defect: `_close_estimate` records open→close **calendar** time as `actual_s`; a commitment that sat idle for days closes with a garbage ratio (live incident: sample #9, ratio 392.7x from a 5.5-day span vs 20-min estimate) and poisons P90 calibration. Guard: when the wall ratio is wildly out of band, record the pair with `ratio: null` + explanatory note so the ledger stays complete but calibration stays clean.

**Files:**
- Modify: `lib/estimator.py` (add `WALL_OUTLIER_MAX_RATIO` constant near line 20 constants block; extend `record_estimate` signature, currently line 179)
- Modify: `lib/core.py:_close_estimate` (lines 326–341)
- Test: `tests/test_sundial.py` (new class `TestWallTimeGuard`)

**Interfaces:**
- Consumes: `estimator.record_estimate(data_dir, task, est_s, actual_s=None, bucket=None, cid=None)` (existing), `core.resolve_commitment(id, status)` (existing).
- Produces: `record_estimate(..., note=None, force_null_ratio=False)` — later tasks and future callers may pass a `note` string that lands verbatim in the event; `estimator.WALL_OUTLIER_MAX_RATIO: float = 20.0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sundial.py` (imports at top of file already include `tempfile`, `json`; follow the existing `sys.path` setup):

```python
class TestWallTimeGuard(unittest.TestCase):
    """done on a long-idle commitment must not poison calibration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _events(self):
        p = self.data / "habits.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_outlier_ratio_recorded_null_with_note(self):
        # actual 21x the estimate -> ratio must be None, note must explain
        estimator.record_estimate(self.data, "t", 1200.0, actual_s=1200.0 * 21,
                                  force_null_ratio=True,
                                  note="wall-time outlier, excluded")
        e = self._events()[-1]
        self.assertIsNone(e["ratio"])
        self.assertIn("wall-time", e["note"])
        self.assertEqual(e["actual_s"], 1200.0 * 21)   # actual preserved

    def test_normal_close_unaffected(self):
        estimator.record_estimate(self.data, "t", 1200.0, actual_s=6000.0)
        e = self._events()[-1]
        self.assertAlmostEqual(e["ratio"], 5.0)
        self.assertNotIn("note", e)

    def test_null_ratio_excluded_from_calibration(self):
        estimator.record_estimate(self.data, "a", 100.0, actual_s=80.0)
        estimator.record_estimate(self.data, "b", 100.0, actual_s=100.0 * 30,
                                  force_null_ratio=True, note="wall-time")
        out = estimator.estimate_execution(1200, self.data)
        self.assertEqual(out["n"], 1)   # only the sane sample counts

    def test_close_estimate_guards_wall_outlier(self):
        # end-to-end through core: commitment created long ago, closed now
        import core as core_mod
        old_data = core_mod.DATA
        try:
            self._repoint_core(core_mod)
            created = core_mod.now_utc() - timedelta(days=5)
            rec = {"id": "cafe0001", "text": "long idler", "status": "open",
                   "created_at": created.isoformat(),
                   "est": {"est_s": 1200.0, "bucket": "ops"}}
            core_mod.write_json(core_mod.COMMITMENTS, [rec])
            core_mod.resolve_commitment("cafe0001", "done")
            e = self._events()[-1]
            self.assertIsNone(e["ratio"])
            self.assertIn("wall-time", e["note"])
        finally:
            self._restore_core(core_mod, old_data)

    def test_close_estimate_normal_still_records_ratio(self):
        import core as core_mod
        old_data = core_mod.DATA
        try:
            self._repoint_core(core_mod)
            created = core_mod.now_utc() - timedelta(minutes=15)
            rec = {"id": "cafe0002", "text": "quick one", "status": "open",
                   "created_at": created.isoformat(),
                   "est": {"est_s": 1200.0, "bucket": "ops"}}
            core_mod.write_json(core_mod.COMMITMENTS, [rec])
            core_mod.resolve_commitment("cafe0002", "done")
            e = self._events()[-1]
            self.assertIsNotNone(e["ratio"])
            self.assertLess(e["ratio"], 2.0)
        finally:
            self._restore_core(core_mod, old_data)

    # helpers: repoint core's module paths at the temp dir the way
    # TestCore.setUp does (copy that exact mechanism — DATA, COMMITMENTS,
    # and any module-level derived paths).
```

NOTE to implementer: open `tests/test_sundial.py:120-134` (`TestCore.setUp`) first and copy its exact repoint/restore mechanism into `_repoint_core`/`_restore_core` (it sets `core.DATA`, `core.COMMITMENTS`, etc. to the temp dir). Do not invent a new mechanism. Add `from datetime import timedelta` to the test-file imports if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sundial.TestWallTimeGuard -v`
Expected: FAIL — `TypeError: record_estimate() got an unexpected keyword argument 'force_null_ratio'` (first two tests), assertion failures on the core tests.

- [ ] **Step 3: Implement `record_estimate` extension**

In `lib/estimator.py`, constants block (near `MIN_CONFIDENT_N`/`BUCKET_MIN_N`):

```python
# A done-close records open->close WALL time as actual_s. When that wall
# span dwarfs the estimate the pair is calendar idleness, not execution --
# ratio would poison P50/P90 calibration (live incident 2026-07-17:
# ratio 392.7x from a 5.5-day idle span). Past this bound the close is
# recorded with ratio=None (excluded from calibration) plus a note.
WALL_OUTLIER_MAX_RATIO = 20.0
```

Extend `record_estimate` (line 179) signature and body:

```python
def record_estimate(data_dir, task, est_s, actual_s=None, bucket=None,
                    cid=None, note=None, force_null_ratio=False):
```

Inside, after the existing ratio computation, add:

```python
        if force_null_ratio:
            ratio = None
```

and after the `if cid:` block:

```python
        if note:
            rec["note"] = str(note)
```

(Place `if force_null_ratio` immediately after the existing `ratio = (...)` expression so the sanity guard for negative/zero durations stays intact. `rec` keys keep their current order; `note` slots after `cid`.)

- [ ] **Step 4: Implement the guard in `_close_estimate`**

Replace `lib/core.py:_close_estimate` body's record call (lines 335–339) with:

```python
        import estimator  # lazy: estimator imports core
        actual = (now_utc() - created).total_seconds()
        est_s = snap.get("est_s")
        wall_outlier = (isinstance(est_s, (int, float)) and est_s > 0
                        and actual / est_s > estimator.WALL_OUTLIER_MAX_RATIO)
        estimator.record_estimate(
            DATA, str(rec.get("text", ""))[:80], est_s, actual_s=actual,
            bucket=snap.get("bucket"), cid=rec.get("id"),
            force_null_ratio=wall_outlier,
            note=("wall-time outlier, excluded: open-to-close span "
                  f"{actual / est_s:.0f}x the estimate; calendar time, "
                  "not execution time") if wall_outlier else None)
```

- [ ] **Step 5: Run task tests, then the full suite**

Run: `python3 -m unittest tests.test_sundial.TestWallTimeGuard -v` → all PASS
Run: `python3 -m unittest tests.test_sundial -v` → 246/246 PASS (241 existing + 5 new), zero failures.

- [ ] **Step 6: Commit**

```bash
git add lib/estimator.py lib/core.py tests/test_sundial.py
git commit -m "fix(estimator): wall-time guard on done-close — out-of-band ratios record null, not poison

Live incident 2026-07-17: soak-check close logged ratio 392.7x (5.5d idle
calendar span vs 20m estimate), wrecking P90. Past WALL_OUTLIER_MAX_RATIO
(20x) a close now records actual_s faithfully but ratio=null + note, which
calibration already excludes by design."
```

---

### Task 2: `sundial snooze` — owner-declared quiet window

Owner-word-gated delivery hold: `sundial snooze 45m` suppresses popups AND sound for that window (the sensed ELSEWHERE tier can't tell "busy in Figma" from "in an interview — do not ping"). Detection, accrual, and ledgers keep running; only delivery is held. One honesty-rail exception: a HIGH-tier commitment past its wall ceiling still breaks through (same shape as Apple Critical Alerts; the ceiling guarantee in README stays true).

**Files:**
- Create: `cli/snooze.py`
- Modify: `bin/sundial` (dispatcher case + usage line)
- Modify: `watcher/watcher.py:run_cycle` (delivery gates at lines ~693-708 return-nudge, ~720-748 batch fire, and the two offer sites at lines ~829, ~859)
- Test: `tests/test_sundial.py` (new class `TestSnooze`)

**Interfaces:**
- Consumes: `estimator.parse_duration(s)` (lib/estimator.py:28, returns seconds or None), `core.DATA`, `core.now_utc()`, `core.read_json/write_json`, `policy.tier_of(c)` (existing, used at watcher.py:737), `wall_ceiling_passed(c, now)` (existing watcher fn, line ~712).
- Produces: `data/snooze.json` `{"until": iso8601-utc, "set_at": iso8601-utc}`; watcher helper `snooze_active(now) -> bool` (module-level in watcher.py); habit event `{"kind": "snooze-hold", "held": <int>}` when a cycle suppresses ≥1 delivery.

- [ ] **Step 1: Write the failing tests**

```python
class TestSnooze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snooze_active_true_within_window(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        (self.data / "snooze.json").write_text(json.dumps(
            {"until": (now + timedelta(minutes=30)).isoformat(),
             "set_at": now.isoformat()}))
        self.assertTrue(w.snooze_active(now, data_dir=self.data))

    def test_snooze_active_false_when_expired(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        (self.data / "snooze.json").write_text(json.dumps(
            {"until": (now - timedelta(minutes=1)).isoformat(),
             "set_at": now.isoformat()}))
        self.assertFalse(w.snooze_active(now, data_dir=self.data))

    def test_snooze_active_false_no_file_or_garbage(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        self.assertFalse(w.snooze_active(now, data_dir=self.data))
        (self.data / "snooze.json").write_text("{not json")
        self.assertFalse(w.snooze_active(now, data_dir=self.data))

    def test_breakthrough_filter_keeps_high_tier_ceiling_only(self):
        import watcher as w
        # batch entries are (commitment, entry, rung, message, ceiling)
        high_ceiling = ({"id": "a", "tier": "high"}, {}, 3, "m", True)
        high_no_ceiling = ({"id": "b", "tier": "high"}, {}, 1, "m", False)
        norm_ceiling = ({"id": "c", "tier": "normal"}, {}, 3, "m", True)
        kept = w.snooze_filter([high_ceiling, high_no_ceiling, norm_ceiling])
        self.assertEqual([b[0]["id"] for b in kept], ["a"])
```

NOTE to implementer: check how `policy.tier_of` reads tier from a commitment dict (open `lib/policy.py`) and build the minimal fake commitment dicts accordingly — the `{"tier": "high"}` shape above is a guess; use the real field. Import `datetime/timezone/timedelta` per the test file's existing imports. `snooze_active` must accept `data_dir=` keyword (defaulting to `core.DATA`) for test isolation.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sundial.TestSnooze -v`
Expected: FAIL — `AttributeError: module 'watcher' has no attribute 'snooze_active'`. (If `import watcher` itself fails in the test env, follow the import mechanism the existing watcher tests use — grep `tests/test_sundial.py` for how watcher functions are currently imported and copy it.)

- [ ] **Step 3: Implement watcher helpers**

In `watcher/watcher.py` (near the other small helpers, after `speak_final`):

```python
def snooze_active(now, data_dir=None) -> bool:
    """Owner-declared quiet window (data/snooze.json). Fail-safe: missing,
    malformed, or expired file means not snoozed."""
    try:
        d = Path(data_dir) if data_dir is not None else core.DATA
        s = core.read_json(d / "snooze.json", None)
        until = core.parse_iso(s.get("until")) if isinstance(s, dict) else None
        return until is not None and now < until
    except Exception:
        return False


def snooze_filter(batch):
    """During snooze only a HIGH-tier commitment past its wall ceiling may
    deliver -- the owner's word holds everything else. Same breakthrough
    shape as the ceiling honesty rail."""
    return [b for b in batch if b[4] and policy.tier_of(b[0]) == "high"]
```

- [ ] **Step 4: Wire the three delivery gates in `run_cycle`**

(a) Compute once, right after `audible = sound_allowed(...)` (~line 649):

```python
    snoozed = snooze_active(now)
```

(b) Return-nudge site (~line 693): change `if returned and c.get("kind") == "awaiting-reply":` to `if returned and not snoozed and c.get("kind") == "awaiting-reply":`

(c) Batch fire (~line 720): immediately after the `if batch:` line's defer logic decides, before the fire loop, add:

```python
        if snoozed:
            held = len(batch)
            batch = snooze_filter(batch)
            if held - len(batch) > 0:
                opportunities.log_habit({"kind": "snooze-hold",
                                         "held": held - len(batch)})
```

Place this right after the `batch = [b for b in batch ...still_open...]` re-filter so the count reflects real holds.

(d) Offer sites (~lines 829 and 859): wrap each `desktop_notify("Sundial", msg)` + `count_offer` pair in `if not snoozed:` (offers are droppable, not deferrable — do NOT accumulate them for later; the dedupe ledger already prevents re-offers over the same evidence, so a held offer is simply lost, which is the intended cost of snoozing).

- [ ] **Step 5: Implement the CLI verb**

Create `cli/snooze.py`:

```python
#!/usr/bin/env python3
"""snooze -- owner-declared quiet window: hold popups and sound.

  sundial snooze 45m     hold delivery for 45 minutes
  sundial snooze off     clear the window
  sundial snooze         show status

Detection and ledgers keep running; only delivery is held. A HIGH-tier
commitment past its wall ceiling still breaks through (honesty rail)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core       # noqa: E402
import estimator  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Hold notification delivery.")
    ap.add_argument("duration", nargs="?", default=None,
                    help="e.g. 45m, 2h — or 'off' to clear; omit for status")
    args = ap.parse_args()
    p = core.DATA / "snooze.json"
    now = core.now_utc()

    if args.duration is None:
        s = core.read_json(p, None)
        until = core.parse_iso(s.get("until")) if isinstance(s, dict) else None
        if until and now < until:
            print(f"snoozed for another {core.humanize_delta((until - now).total_seconds())}.")
        else:
            print("not snoozed.")
        return

    if args.duration == "off":
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        print("snooze cleared.")
        core.refresh_menubar()
        return

    secs = estimator.parse_duration(args.duration)
    if not secs or secs <= 0:
        print(f"can't parse duration {args.duration!r} (try 45m, 2h).")
        sys.exit(1)
    from datetime import timedelta
    core.write_json(p, {"until": (now + timedelta(seconds=secs)).isoformat(),
                        "set_at": now.isoformat()})
    print(f"snoozed for {core.humanize_delta(secs)}. high-tier wall-ceiling "
          "fires still break through.")
    core.refresh_menubar()


if __name__ == "__main__":
    main()
```

In `bin/sundial`: add `  snooze)   exec python3 "$ROOT/cli/snooze.py" "$@" ;;` after the `estimate)` line, and add `snooze` to the usage string.

NOTE to implementer: verify `core.humanize_delta` exists with that name (it's referenced in `estimator.sanity_line`); if the signature differs, adapt the print lines, not the core function.

- [ ] **Step 6: Run task tests, then the full suite**

Run: `python3 -m unittest tests.test_sundial.TestSnooze -v` → PASS
Run: `python3 -m unittest tests.test_sundial -v` → all PASS
Manual smoke (allowed to touch live data — snooze.json is owner-facing state, not history): `./bin/sundial snooze 1m && ./bin/sundial snooze && sleep 65 && ./bin/sundial snooze && ./bin/sundial snooze off` → "snoozed for another ~1m." → "not snoozed." → "snooze cleared."

- [ ] **Step 7: Commit**

```bash
git add cli/snooze.py bin/sundial watcher/watcher.py tests/test_sundial.py
git commit -m "feat(snooze): owner-declared quiet window — delivery held, high-tier ceiling breaks through"
```

---

### Task 3: P90 budget-crossing nudges (execution clock, in-session)

Evidence: live elapsed-time feedback against a budget moved on-time completion 30%→53.3% (Timely-RL, arXiv 2601.16486). Extend the per-prompt hook: for each open estimated plain commitment, when elapsed-since-open crosses 50%/80%/100% of its P90 snapshot, print a one-line flag — once per threshold per commitment, never as a running counter. Skip stale monsters (elapsed > 3x P90 — the session-start running-long flag already owns those, and post-guard they're calendar idleness anyway).

**Files:**
- Modify: `lib/estimator.py` (new pure fn `budget_flags` — pure logic, no IO, mirroring `sanity_line`'s style)
- Modify: `hooks/prompt_submit.py:build_context` (line 106; call the pure fn, manage state file)
- Test: `tests/test_sundial.py` (new class `TestBudgetFlags`)

**Interfaces:**
- Consumes: commitment dicts with `status`, `kind`, `created_at`, `text`, `est: {est_s, p50_s, p90_s, ...}` (shape written by `_attach_estimate`, core.py:296-298); `core.humanize_delta`, `core.parse_iso` (pass `parse_iso`/`humanize` in or import core — estimator already imports core for `sanity_line`).
- Produces: `estimator.budget_flags(commitments, fired, now) -> (lines: list[str], new_fired: dict)` where `fired`/`new_fired` map `cid -> [crossed thresholds as floats]`; state file `data/est_nudges.json` (managed by the hook, not the pure fn); constants `NUDGE_THRESHOLDS = (0.5, 0.8, 1.0)`, `NUDGE_STALE_X = 3.0`.

- [ ] **Step 1: Write the failing tests**

```python
class TestBudgetFlags(unittest.TestCase):
    def _c(self, cid, minutes_ago, p90_min=40.0, status="open", kind=None):
        created = (datetime.now(timezone.utc)
                   - timedelta(minutes=minutes_ago)).isoformat()
        c = {"id": cid, "text": f"task {cid}", "status": status,
             "created_at": created,
             "est": {"est_s": p90_min * 60 * 0.8, "p50_s": p90_min * 48,
                     "p90_s": p90_min * 60, "n": 8, "confidence": "high"}}
        if kind:
            c["kind"] = kind
        return c

    def test_crossing_fires_once(self):
        now = datetime.now(timezone.utc)
        c = self._c("aa", minutes_ago=22)     # 55% of a 40m P90
        lines, fired = estimator.budget_flags([c], {}, now)
        self.assertEqual(len(lines), 1)
        self.assertIn("50%", lines[0])
        # same state again -> silent
        lines2, _ = estimator.budget_flags([c], fired, now)
        self.assertEqual(lines2, [])

    def test_multiple_thresholds_highest_only(self):
        now = datetime.now(timezone.utc)
        c = self._c("bb", minutes_ago=34)     # 85% -> crossed 0.5 and 0.8
        lines, fired = estimator.budget_flags([c], {}, now)
        self.assertEqual(len(lines), 1)       # one line, the highest
        self.assertIn("80%", lines[0])
        self.assertEqual(sorted(fired["bb"]), [0.5, 0.8])

    def test_stale_and_closed_and_asks_skipped(self):
        now = datetime.now(timezone.utc)
        stale = self._c("cc", minutes_ago=40 * 4)          # >3x P90
        closed = self._c("dd", minutes_ago=22, status="done")
        ask = self._c("ee", minutes_ago=22, kind="awaiting-reply")
        no_est = {"id": "ff", "text": "bare", "status": "open",
                  "created_at": now.isoformat()}
        lines, fired = estimator.budget_flags(
            [stale, closed, ask, no_est], {}, now)
        self.assertEqual(lines, [])
        self.assertEqual(fired, {})

    def test_malformed_degrade_silent(self):
        now = datetime.now(timezone.utc)
        junk = [{"id": "gg", "status": "open", "est": "not-a-dict",
                 "created_at": "garbage"}, "not-even-a-dict"]
        lines, fired = estimator.budget_flags(junk, "bad-state", now)
        self.assertEqual(lines, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_sundial.TestBudgetFlags -v`
Expected: FAIL — `AttributeError: module 'estimator' has no attribute 'budget_flags'`.

- [ ] **Step 3: Implement the pure function**

In `lib/estimator.py` (after `sanity_line`):

```python
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
```

- [ ] **Step 4: Run task tests to verify they pass**

Run: `python3 -m unittest tests.test_sundial.TestBudgetFlags -v` → PASS. (If `test_multiple_thresholds_highest_only` expects `fired["bb"] == [0.5, 0.8]`, note the implementation records ALL crossed thresholds while printing only the highest — that is the intended contract.)

- [ ] **Step 5: Wire into the hook**

In `hooks/prompt_submit.py`, inside `build_context` (line 106) where other context lines are assembled (open the function and place this alongside the existing optional blocks, following its local style for line assembly):

```python
    # Budget-crossing flags (execution clock): once per threshold per
    # commitment; state in data/est_nudges.json. Fail-safe: never blocks.
    try:
        import estimator
        d = data if data is not None else core.DATA
        state_p = d / "est_nudges.json"
        fired = core.read_json(state_p, {})
        lines, new_fired = estimator.budget_flags(
            [c for c in core.load_commitments()], fired, core.now_utc())
        if new_fired != fired and isinstance(new_fired, dict):
            merged = dict(fired) if isinstance(fired, dict) else {}
            merged.update(new_fired)
            core.write_json(state_p, merged)
        for ln in lines:
            out.append(ln)
    except Exception:
        pass
```

NOTE to implementer: `build_context(core, data=None)`'s actual local variable for accumulating output may not be named `out` — open the function first and append to whatever it actually uses; likewise confirm how it accesses the data dir (the `data` param vs `core.DATA`) and match it. `core.load_commitments()` reads the live ledger path from module state — if `build_context` already loads commitments for another block, reuse that load instead of a second read.

- [ ] **Step 6: Full suite + live smoke**

Run: `python3 -m unittest tests.test_sundial -v` → all PASS.
Live smoke: `sundial remember "nudge smoke test" --est 1m --bucket ops` (a real 1-minute estimate; you'll close it honestly in a moment — the pair is real work, not synthetic), wait >60s, then send any prompt in a Claude session and confirm the ⏱ 100% line appears once and does not repeat on the next prompt. Then `sundial done <id>` (closes with a sane ~1-2x ratio — honest sample). Remove nothing from the ledger.

- [ ] **Step 7: Commit**

```bash
git add lib/estimator.py hooks/prompt_submit.py tests/test_sundial.py
git commit -m "feat(estimator): P90 budget-crossing nudges — once-per-threshold flags on the live execution clock"
```

---

### Task 4: Prior-art refresh + digest commit (docs only)

Our own kill squad found present-silence's mechanism ancestors; the README's prior-art section claims we publish corrections. Publish them.

**Files:**
- Modify: `README.md` (section "Prior art, honestly mapped", lines 174-195)
- Add: `docs/research/2026-07-17-temporal-scene-sweep.md` (already written, uncommitted)

- [ ] **Step 1: Extend the prior-art paragraph**

In `README.md`, after the sentence ending "…desktop→pager escalation timed by how long the user had been away." (line 188), insert:

```markdown
The mechanism under present-silence — sensor-gated delivery timing with a
confidence measure, instead of elapsed-time firing — is also Horvitz-era
prior art: "Attention-Sensitive Alerting" ([UAI 1999](https://arxiv.org/abs/1301.6707))
framed it, and [US7,444,383](https://patents.google.com/patent/US7444383)
(bounded deferral via local sensors, filed 2004) implemented it, decades
before us. What we could not find is that mechanism applied to an agent
gating its own self-initiated speech; a 2026 adversarial prior-art sweep
([digest](docs/research/2026-07-17-temporal-scene-sweep.md)) is the current
record of where each claim stands.
```

Keep the existing closing sentences ("Sundial stands on that lineage… That combination is Sundial. Corrections welcome…") — this insert strengthens them, it must not replace them.

- [ ] **Step 2: Verify links + render**

Run: `grep -n "1301.6707\|US7444383\|temporal-scene-sweep" README.md` → 3 hits; confirm the relative digest path exists: `ls docs/research/2026-07-17-temporal-scene-sweep.md`.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/research/2026-07-17-temporal-scene-sweep.md docs/superpowers/plans/2026-07-17-refinement-sprint.md
git commit -m "docs: name present-silence's mechanism ancestors (Horvitz UAI'99, US7,444,383); add 2026-07-17 scene-sweep digest + sprint plan"
```

---

### Task 5: QC gate — review, verify, confidence report

- [ ] **Step 1: Full suite once more:** `python3 -m unittest tests.test_sundial -v` → all PASS, count recorded.
- [ ] **Step 2: `/code-review`** on the sprint diff (`git diff main~4..main` equivalent range); fix CONFIRMED findings, re-run suite, amend/commit fixes.
- [ ] **Step 3: `verify` skill** — drive the real surfaces end-to-end: (a) wall-guard: inspect the next real long-idle close OR replay the Task 1 core-level test against a temp dir and read the emitted event; (b) snooze: the Task 2 manual smoke; (c) nudges: the Task 3 live smoke. Evidence (actual output) pasted into the report, not asserted.
- [ ] **Step 4: Confidence report to Yousef** — per task: score, named assumptions, anything below 0.95 flagged with what would raise it. Known accepted limits to restate: the 20x wall-outlier threshold is a judgment constant (tunable, documented); snooze drops (not defers) opportunity offers; nudge elapsed-time is wall-clock within a session by design.
- [ ] **Step 5: Update memory** (`project_wallclock.md`): sprint shipped, doors 1-3+docs closed, remaining doors (batching, confidence-advisory, off-desktop escalation) still queued.

---

## Self-review notes (run before handoff)

- Spec coverage: doors #1 (prior-art), #2 (wall guard), #3 (snooze), #4 (P90 nudges) → Tasks 4, 1, 2, 3. Deferred doors intentionally absent.
- Line numbers are anchors, not gospel — every task says "open the function first"; the two knowingly-uncertain integration points (tier field shape in Task 2, `build_context` local style in Task 3) carry explicit implementer NOTEs.
- Test-count expectation (246) assumes 5 new tests in Task 1; adjust arithmetic as later tasks add theirs.
