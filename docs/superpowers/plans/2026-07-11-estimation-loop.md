# Phase B Estimation Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the self-estimation loop — automatic estimate capture on the commitment mutation path, three read surfaces, proven live.

**Architecture:** Estimate open/close becomes a synchronous, fail-safe side effect inside `core.add_commitment` (plain commitments with a resolvable duration) and `core.resolve_commitment` (status `done`). The commitment record carries a calibration snapshot (`est` object) so all surfaces (creation-time sanity line, session-start block, SwiftBar) are pure readers of `commitments.json`. The engine (`lib/estimator.py`) is untouched except two additive helpers.

**Tech Stack:** Python 3 stdlib only (house rule), unittest, ruff, bash/SwiftBar.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-11-estimation-loop-design.md`.
- No LLM, no network, no new dependencies. Deterministic everywhere.
- Capture must NEVER raise or block through a CLI verb (`record_estimate` is fail-safe by contract; keep it).
- Only `kind == "plain"` commitments open execution estimates; `awaiting-reply` feeds the review clock via the existing `answered` events (no change).
- Only a close with status `"done"` from prior status `"open"` records a ratio.
- Small-n honesty: no confident-sounding output when there is no data.
- `estimator` imports `core`; therefore `core` must import `estimator` LAZILY (inside the function) to avoid a circular import.
- Suite baseline 223 passed; every task ends green with `python3 -m pytest tests -q` and `python3 -m ruff check .`.
- Repo: `/Users/OTI_1/Desktop/sundial-staging`, branch `feat/estimation-loop` off `main`.

---

### Task 1: estimator additive helpers — `cid` linkage, `sanity_line`, `calibration_health`

**Files:**
- Modify: `lib/estimator.py` (append/extend; engine math untouched)
- Test: `tests/test_sundial.py` (class `TestEstimator`, near line 2946)

**Interfaces:**
- Produces: `record_estimate(data_dir, task, est_s, actual_s=None, bucket=None, cid=None)` — `cid` stored on the event when given.
- Produces: `sanity_line(est_s, ttd_s, calib) -> str | None` — pure; warning line iff `calib["n"] > 0` and `calib["p90_s"]` and `ttd_s` is not None and `calib["p90_s"] > ttd_s`.
- Produces: `calibration_health(data_dir) -> {"n_exec": int, "p50_ratio": float|None, "confidence": str, "n_review": int}`.

- [ ] **Step 1: Write the failing tests** (in `TestEstimator`)

```python
    def test_record_estimate_carries_cid(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            estimator.record_estimate(d, "t", 60, actual_s=90, bucket="build",
                                      cid="abc12345")
            e = json.loads((Path(d) / "habits.jsonl").read_text().strip())
            self.assertEqual(e["cid"], "abc12345")
            self.assertEqual(e["bucket"], "build")

    def test_sanity_line_warns_only_with_data_and_overrun(self):
        import estimator
        calib = {"p50_s": 700.0, "p90_s": 7200.0, "n": 6, "confidence": "high"}
        line = estimator.sanity_line(3600.0, 5400.0, calib)
        self.assertIsNotNone(line)
        self.assertIn("P90", line)
        # no data -> silent
        none_calib = {"p50_s": None, "p90_s": None, "n": 0, "confidence": "none"}
        self.assertIsNone(estimator.sanity_line(3600.0, 5400.0, none_calib))
        # p90 fits inside the deadline -> silent
        ok = {"p50_s": 700.0, "p90_s": 4000.0, "n": 6, "confidence": "high"}
        self.assertIsNone(estimator.sanity_line(3600.0, 5400.0, ok))
        # no deadline -> silent
        self.assertIsNone(estimator.sanity_line(3600.0, None, calib))

    def test_calibration_health_counts_both_clocks(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            estimator.record_estimate(d, "a", 100, actual_s=150)
            estimator.record_estimate(d, "b", 100, actual_s=50)
            estimator.record_estimate(d, "open", 100)          # open: excluded
            with open(Path(d) / "habits.jsonl", "a") as f:
                f.write(json.dumps({"kind": "answered", "latency_s": 30.0}) + "\n")
            h = estimator.calibration_health(d)
            self.assertEqual(h["n_exec"], 2)
            self.assertAlmostEqual(h["p50_ratio"], 1.0)
            self.assertEqual(h["confidence"], "low")
            self.assertEqual(h["n_review"], 1)
            self.assertEqual(estimator.calibration_health(Path(d) / "nope")["n_exec"], 0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests -q -k "cid or sanity_line or calibration_health"`
Expected: FAIL — `TypeError: record_estimate() got an unexpected keyword argument 'cid'`, `AttributeError: ... sanity_line`, `... calibration_health`.

- [ ] **Step 3: Implement**

In `record_estimate`, change signature to `(data_dir, task, est_s, actual_s=None, bucket=None, cid=None)` and after the `if bucket:` block add:

```python
        if cid:
            rec["cid"] = str(cid)
```

Append to `lib/estimator.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests -q` — Expected: all pass. `python3 -m ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add lib/estimator.py tests/test_sundial.py
git commit -m "feat(estimator): cid linkage, sanity_line, calibration_health"
```

---

### Task 2: capture at creation — `add_commitment` est snapshot + open event

**Files:**
- Modify: `lib/core.py` (`add_commitment`, line ~242)
- Test: `tests/test_sundial.py` (new class `TestEstimateCapture`)

**Interfaces:**
- Consumes: `estimator.record_estimate(..., cid=)`, `estimator.estimate_execution(raw_s, data_dir, bucket=)`, `estimator.parse_duration`.
- Produces: `add_commitment(..., est_str=None, bucket=None)`. On a `plain` commitment where `est_str` parses OR a positive due-derived duration exists, the returned record carries `rec["est"] = {"est_s", "bucket", "p50_s", "p90_s", "n", "confidence"}` and one open estimate event (actual `None`, `cid` = record id) is appended to `DATA/habits.jsonl`.

- [ ] **Step 1: Write the failing tests** (new class, use the temp-DATA pattern from `TestEstimator`; `import estimator` and set `core.DATA`/`core.COMMITMENTS` to a temp dir, restore in `finally`)

```python
class TestEstimateCapture(unittest.TestCase):
    def _tmp(self):
        d = tempfile.TemporaryDirectory()
        self._orig = (core.DATA, core.COMMITMENTS)
        core.DATA = Path(d.name)
        core.COMMITMENTS = Path(d.name) / "commitments.json"
        self.addCleanup(lambda: setattr(core, "DATA", self._orig[0]))
        self.addCleanup(lambda: setattr(core, "COMMITMENTS", self._orig[1]))
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _events(self, d):
        p = d / "habits.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def test_explicit_est_wins_and_opens_event(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="45m", bucket="build")
        self.assertEqual(rec["est"]["est_s"], 2700.0)
        self.assertEqual(rec["est"]["bucket"], "build")
        self.assertIn("p90_s", rec["est"])
        ev = self._events(d)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["cid"], rec["id"])
        self.assertIsNone(ev[0]["actual_s"])

    def test_due_derived_when_no_est(self):
        d = self._tmp()
        rec = core.add_commitment("ship y", "+1h")
        self.assertAlmostEqual(rec["est"]["est_s"], 3600.0, delta=5.0)
        self.assertEqual(len(self._events(d)), 1)

    def test_no_est_no_due_no_capture(self):
        d = self._tmp()
        rec = core.add_commitment("someday z")
        self.assertNotIn("est", rec)
        self.assertEqual(self._events(d), [])

    def test_awaiting_reply_never_opens_execution_estimate(self):
        d = self._tmp()
        rec = core.add_commitment("q?", "+10m", kind="awaiting-reply",
                                  est_str="10m")
        self.assertNotIn("est", rec)
        self.assertEqual(self._events(d), [])

    def test_bad_est_string_is_ignored_not_fatal(self):
        d = self._tmp()
        rec = core.add_commitment("ship w", "+1h", est_str="soonish")
        # falls back to due-derived; the verb layer is where strict parse errors
        self.assertAlmostEqual(rec["est"]["est_s"], 3600.0, delta=5.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests -q -k TestEstimateCapture`
Expected: FAIL — `TypeError: add_commitment() got an unexpected keyword argument 'est_str'`.

- [ ] **Step 3: Implement** — in `lib/core.py`, extend the signature:

```python
def add_commitment(text: str, due_str: str | None = None, source: str = "manual",
                   kind: str = "plain", session_id: str | None = None,
                   weight: str | None = None, confidence: float | None = None,
                   irreversible: bool = False,
                   default_action: str | None = None,
                   rungs: list | None = None,
                   est_str: str | None = None,
                   bucket: str | None = None) -> dict:
```

Immediately after the `rec` dict is fully built (after the existing optional-field blocks, still inside the lock, BEFORE `items.append(rec)` / the final write — match the existing structure), add:

```python
        if kind == "plain":
            _attach_estimate(rec, due, est_str, bucket)
```

And add the helper below `add_commitment` (module level):

```python
def _attach_estimate(rec: dict, due, est_str, bucket) -> None:
    """Open the execution-clock estimate for a plain commitment: snapshot the
    calibration on the record (the display surface) and pre-register the open
    event in habits.jsonl (the audit trail). Fail-safe: any error leaves the
    commitment untouched -- capture must never block a verb."""
    try:
        import estimator  # lazy: estimator imports core
        est_s = estimator.parse_duration(est_str) if est_str else None
        if est_s is None and due is not None:
            ttd = (due - parse_iso(rec["created_at"])).total_seconds()
            est_s = ttd if ttd > 0 else None
        if not est_s or est_s <= 0:
            return
        ex = estimator.estimate_execution(est_s, DATA, bucket=bucket)
        rec["est"] = {"est_s": float(est_s), "bucket": bucket,
                      "p50_s": ex["p50_s"], "p90_s": ex["p90_s"],
                      "n": ex["n"], "confidence": ex["confidence"]}
        estimator.record_estimate(DATA, str(rec.get("text", ""))[:80], est_s,
                                  bucket=bucket, cid=rec["id"])
    except Exception:
        rec.pop("est", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests -q` — Expected: all pass (223 + new). Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_sundial.py
git commit -m "feat(core): plain commitments open execution estimates automatically"
```

---

### Task 3: `remember --est/--bucket` + deadline-sanity line

**Files:**
- Modify: `cli/remember.py`
- Test: `tests/test_sundial.py` (extend `TestEstimateCapture`)

**Interfaces:**
- Consumes: `add_commitment(..., est_str=, bucket=)` (Task 2), `estimator.sanity_line` (Task 1), `estimator.parse_duration`.
- Produces: `sundial remember TEXT [--due D] [--est 45m] [--bucket build]`; prints the armed line, then the sanity warning line when it applies. Strict `--est` grammar: unparseable → `ap.error` (exit 2).

- [ ] **Step 1: Write the failing test** — the sanity decision is pure (Task 1); here test the CLI wiring via a subprocess-free unit: extract the printable logic? No — keep it thin: test `remember` end-to-end the way other CLI behavior is tested (direct `main()` invocation with argv patch and captured stdout):

```python
    def test_remember_est_flags_and_sanity(self):
        d = self._tmp()
        # seed history: chronic 2x overrun so P90 blows any tight deadline
        import estimator
        for i in range(6):
            estimator.record_estimate(core.DATA, f"h{i}", 100, actual_s=200)
        import importlib, io, contextlib
        sys.path.insert(0, str(Path(core.__file__).resolve().parent.parent / "cli"))
        import remember
        importlib.reload(remember)
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["remember", "tight promise", "--due", "+1h",
                    "--est", "50m", "--bucket", "build"]
        try:
            with contextlib.redirect_stdout(buf):
                remember.main()
        finally:
            sys.argv = argv
        out = buf.getvalue()
        self.assertIn("recorded [", out)
        self.assertIn("P90", out)   # 50m * 2.0 ratio = 100m > 60m deadline
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests -q -k remember_est`
Expected: FAIL — `remember.main` raises `SystemExit 2` (unknown `--est`) or missing P90 output.

- [ ] **Step 3: Implement** — `cli/remember.py` becomes:

```python
#!/usr/bin/env python3
"""remember -- record a ripening commitment with an optional due date."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core       # noqa: E402
import estimator  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Record a ripening commitment.")
    ap.add_argument("text", help="what the commitment is")
    ap.add_argument("--due", default=None,
                    help="YYYY-MM-DD (end of that local day) or full ISO datetime")
    ap.add_argument("--source", default="manual", help="where it came from")
    ap.add_argument("--est", default=None,
                    help="your raw duration guess, e.g. 45m, 1h30m (default: due - now)")
    ap.add_argument("--bucket", default=None,
                    help="task-shape bucket, e.g. build/research/ops/write")
    args = ap.parse_args()

    if args.est is not None and estimator.parse_duration(args.est) is None:
        ap.error(f"bad --est '{args.est}': use e.g. 30m, 1h, 1h30m")

    rec = core.add_commitment(args.text, args.due, args.source,
                              est_str=args.est, bucket=args.bucket)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    print(f"recorded [{rec['id']}] {rec['text']}  (due: {when})")
    snap = rec.get("est")
    if snap:
        ttd = ((due - core.parse_iso(rec["created_at"])).total_seconds()
               if due else None)
        line = estimator.sanity_line(snap["est_s"], ttd, snap)
        if line:
            print(line)
    core.refresh_menubar()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests -q` — all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add cli/remember.py tests/test_sundial.py
git commit -m "feat(cli): remember --est/--bucket with deadline-sanity line"
```

---

### Task 4: capture at close — `done` records the actual

**Files:**
- Modify: `lib/core.py` (`resolve_commitment`, line ~278)
- Test: `tests/test_sundial.py` (extend `TestEstimateCapture`)

**Interfaces:**
- Consumes: `estimator.record_estimate(..., actual_s=, cid=)` (Task 1).
- Produces: `resolve_commitment(commitment_id, status="done") -> dict | None` — the resolved record, or `None` when no match (truthy/falsy compatible with the existing bool usage in `cli/done.py`, which needs no change). A `done` close of a previously-`open` plain commitment carrying `est` appends the closing event (actual + ratio).

- [ ] **Step 1: Write the failing tests**

```python
    def test_done_records_actual_and_ratio(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        out = core.resolve_commitment(rec["id"], "done")
        self.assertIsInstance(out, dict)
        closes = [e for e in self._events(d) if e.get("actual_s") is not None]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["cid"], rec["id"])
        self.assertEqual(closes[0]["est_s"], 3600.0)
        self.assertIsNotNone(closes[0]["ratio"])

    def test_non_done_close_records_nothing(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        core.resolve_commitment(rec["id"], "declined")
        self.assertEqual(
            [e for e in self._events(d) if e.get("actual_s") is not None], [])

    def test_double_done_records_once(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        core.resolve_commitment(rec["id"], "done")
        core.resolve_commitment(rec["id"], "done")
        self.assertEqual(
            len([e for e in self._events(d) if e.get("actual_s") is not None]), 1)

    def test_done_without_estimate_records_nothing(self):
        d = self._tmp()
        rec = core.add_commitment("someday z")
        self.assertTrue(core.resolve_commitment(rec["id"], "done"))
        self.assertEqual(self._events(d), [])

    def test_resolve_missing_returns_none(self):
        self._tmp()
        self.assertIsNone(core.resolve_commitment("nope", "done"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests -q -k "done_records or non_done or double_done or without_estimate or resolve_missing"`
Expected: FAIL — no closing events / `resolve_commitment` returns `True`/`False` not dict/None.

- [ ] **Step 3: Implement** — replace `resolve_commitment`:

```python
def resolve_commitment(commitment_id: str, status: str = "done"):
    """Set a commitment's status. Returns the resolved record (dict) or None
    if no commitment matched -- truthiness-compatible with the old bool.
    A done-close of a previously-open estimated commitment records the
    actual on the execution clock; capture is fail-safe and never blocks."""
    with _ledger_lock():
        items = load_commitments()
        hit = None
        was_open = False
        for c in items:
            if c.get("id") == commitment_id:
                was_open = c.get("status") == "open"
                c["status"] = status
                hit = c
        if hit is not None:
            write_json(COMMITMENTS, items)
    if hit is not None and was_open and status == "done":
        _close_estimate(hit)
    return dict(hit) if hit is not None else None


def _close_estimate(rec: dict) -> None:
    """Append the closing estimate event (actual + ratio) for a done
    commitment that opened one. Fail-safe by contract."""
    try:
        snap = rec.get("est")
        created = parse_iso(rec.get("created_at"))
        if not isinstance(snap, dict) or created is None:
            return
        import estimator  # lazy: estimator imports core
        actual = (now_utc() - created).total_seconds()
        estimator.record_estimate(DATA, str(rec.get("text", ""))[:80],
                                  snap.get("est_s"), actual_s=actual,
                                  bucket=snap.get("bucket"), cid=rec.get("id"))
    except Exception:
        pass
```

Note `_close_estimate` runs OUTSIDE `_ledger_lock()` — `record_estimate` appends under `O_APPEND` and needs no lock; keeping IO out of the lock matches the `log_habit stays OUTSIDE the lock` precedent in `prompt_submit.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests -q` — all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_sundial.py
git commit -m "feat(core): done-close records the actual on the execution clock"
```

---

### Task 5: session-start two-clock block

**Files:**
- Modify: `hooks/session_start.py` (inside `build_block`, after the autonomy-verdicts section, same fail-safe pattern)
- Test: `tests/test_sundial.py` (wherever `build_block` is currently tested — extend that class; grep `build_block`)

**Interfaces:**
- Consumes: `rec["est"]` snapshot (Task 2), `estimator.calibration_health` (Task 1).
- Produces: block lines — up to 5 `running long:` flags for open plain commitments whose elapsed exceeds snapshot P90, plus one `Estimation:` health line (always, phrased honestly at n=0).

- [ ] **Step 1: Write the failing test** (match the file's existing `build_block` test setup — temp DATA, stub birth/previous):

```python
    def test_two_clock_block_flags_running_long(self):
        # inside the existing build_block test class, using its temp-DATA harness
        rec = core.add_commitment("slow task", "+2h", est_str="1m")
        # force elapsed > p90: backdate created_at
        items = core.load_commitments()
        items[0]["created_at"] = (
            core.now_utc() - timedelta(hours=1)).isoformat()
        # keep the snapshot's p90 tiny (n=0 floor: est*2 = 120s)
        core.write_json(core.COMMITMENTS, items)
        block = session_start.build_block(core, birth, previous)  # per harness
        self.assertIn("running long", block)
        self.assertIn("Estimation:", block)
        self.assertIn("no closed samples", block)
```

(Adapt names to the harness the file already uses for `build_block`; if none exists, build the minimal one: temp DATA dir, `birth = {"created_at": core.now_utc().isoformat()}`, `previous = None`.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests -q -k two_clock`
Expected: FAIL — `"running long" not found`.

- [ ] **Step 3: Implement** — in `build_block`, after the autonomy-verdicts `try/except`, add:

```python
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import estimator
        now2 = core.now_utc()
        long_lines = []
        for c in core.load_commitments():
            if c.get("status") != "open" or not isinstance(c.get("est"), dict):
                continue
            p90 = c["est"].get("p90_s")
            created = core.parse_iso(c.get("created_at"))
            if p90 is None or created is None:
                continue
            elapsed = (now2 - created).total_seconds()
            if elapsed > p90:
                long_lines.append(
                    f"  - running long: {str(c.get('text', ''))[:80]} "
                    f"(elapsed {core.humanize_delta(elapsed)} > "
                    f"P90 {core.humanize_delta(p90)})")
        if long_lines:
            lines.append("\nAgainst your own history:")
            lines.extend(long_lines[:5])
        h = estimator.calibration_health(core.DATA)
        if h["n_exec"]:
            lines.append(
                f"\nEstimation: {h['n_exec']} closed samples, "
                f"ratio P50 {h['p50_ratio']:.1f}x ({h['confidence']} confidence); "
                f"review clock n={h['n_review']}.")
        else:
            lines.append("\nEstimation: no closed samples yet — "
                         "estimates are uncalibrated guesses.")
    except Exception:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests -q` — all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add hooks/session_start.py tests/test_sundial.py
git commit -m "feat(hooks): session-start two-clock block — running-long flags + calibration health"
```

---

### Task 6: SwiftBar estimated-commitment line

**Files:**
- Modify: `contrib/sundial.30s.sh` (new read function + one dropdown line; read-only charter unchanged)

**Interfaces:**
- Consumes: `commitments.json` records with the `est` snapshot (Task 2). No project imports — raw JSON read, matching the plugin's existing style.

- [ ] **Step 1: Implement** (no unit harness exists for the plugin; verification is Step 2). Add beside the existing read functions:

```bash
estimate_line() {
    python3 -c '
import json, datetime
try:
    with open("'"$DATA_DIR"'/commitments.json") as f:
        items = json.load(f)
    now = datetime.datetime.now(datetime.timezone.utc)
    best = None
    for c in items:
        est = c.get("est")
        if c.get("status") != "open" or not isinstance(est, dict):
            continue
        due = c.get("due_at")
        key = due or "9999"
        if best is None or key < best[0]:
            best = (key, c, est)
    if best is None:
        raise SystemExit
    _, c, est = best
    created = datetime.datetime.fromisoformat(c["created_at"])
    elapsed = (now - created).total_seconds()
    p90 = est.get("p90_s")
    def h(s):
        s = int(s)
        return f"{s//3600}h{(s%3600)//60:02d}m" if s >= 3600 else f"{s//60}m"
    text = str(c.get("text", ""))[:40]
    if p90 is not None and elapsed > p90:
        print(f"⏱ {text} — over P90 {h(p90)} | color=red")
    elif p90 is not None:
        print(f"⏱ {text} — P90 {h(p90)}, {h(elapsed)} in")
except SystemExit:
    pass
except Exception:
    pass
' 2>/dev/null
}
```

And in the dropdown-rendering section (after the open-asks lines — locate the section that prints menu rows), add:

```bash
EST_LINE="$(estimate_line)"
[ -n "$EST_LINE" ] && echo "$EST_LINE"
```

- [ ] **Step 2: Verify against a fixture**

```bash
cd /Users/OTI_1/Desktop/sundial-staging
TMP=$(mktemp -d) && mkdir -p "$TMP/data"
python3 - <<'EOF'
import json, sys, datetime, os
tmp = os.environ.get("TMP") or sys.argv[1] if len(sys.argv)>1 else None
EOF
python3 -c "
import json, datetime, os, sys
d = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc)
json.dump([{'id':'f1','created_at':(now-datetime.timedelta(hours=2)).isoformat(),
 'due_at':(now+datetime.timedelta(hours=1)).isoformat(),'text':'fixture task',
 'status':'open','est':{'est_s':600,'bucket':None,'p50_s':600,'p90_s':1200,'n':0,'confidence':'none'}}],
 open(d+'/data/commitments.json','w'))
" "$TMP"
SUNDIAL_HOME="$TMP" bash contrib/sundial.30s.sh | grep "⏱"
```

Expected: one `⏱ fixture task — over P90 20m | color=red` line (elapsed 2h > p90 20m).

- [ ] **Step 3: Full suite + commit**

```bash
python3 -m pytest tests -q && python3 -m ruff check .
git add contrib/sundial.30s.sh
git commit -m "feat(menubar): estimated-commitment line, red past P90"
```

---

### Task 7: docs, merge, deploy live, dogfood, soak

**Files:**
- Modify: `README.md` (Roadmap section, line ~213)
- Live install: `/Users/OTI_1/Desktop/AI-WallClock-Project`

- [ ] **Step 1: README** — replace the `v2 — self-estimation` roadmap bullet with a shipped feature note in the features area (keep roadmap honest):

```markdown
- **v2 — self-estimation (shipped):** plain commitments carry calibrated
  P50/P90 from the agent's own measured ratio history (`--est/--bucket` on
  `remember`); deadlines get a sanity check at creation, sessions flag work
  running past its own P90, and the menu bar shows the active promise's
  calibrated state. Remaining: learned quiet hours; sibling-session
  awareness; cross-machine commitments; `sundial doctor`.
```

- [ ] **Step 2: Merge + push**

```bash
git checkout main && git merge --no-ff feat/estimation-loop \
  -m "merge: estimation loop — auto capture on the mutation path, three surfaces"
python3 -m pytest tests -q && git push origin main
```

- [ ] **Step 3: Deploy to the live install**

```bash
cd /Users/OTI_1/Desktop/AI-WallClock-Project
git status --short   # expect clean tracked tree (data/ is git-ignored)
git remote -v        # confirm it shares the sundial origin (else pull from staging path)
git pull origin main # or: git pull /Users/OTI_1/Desktop/sundial-staging main
python3 -m pytest tests -q
```

Check the watcher is alive (`launchctl list | grep -i sundial` / `ps aux | grep watcher.py`); restart per `SETUP.md` if the deploy replaced watcher code.

- [ ] **Step 4: Dogfood sample #1** — the Phase B build itself was pre-registered in the live ledger at execution start (`bucket=build`, open event). Close it now through the NEW pipe:

```bash
cd /Users/OTI_1/Desktop/AI-WallClock-Project
./bin/sundial due   # find the phase-b commitment id
./bin/sundial done <id>
tail -2 data/habits.jsonl   # expect the closing event with ratio + cid
```

- [ ] **Step 5: Arm the soak** — the clock schedules its own verification:

```bash
./bin/sundial remember "Phase B soak check: estimate pairs accruing? surfaces rendering?" \
  --due 2026-07-14 --est 20m --bucket ops
./bin/sundial estimate "sanity" --raw 30m   # verify CLI renders with live data
```

- [ ] **Step 6: Verify surfaces live** — new session shows the `Estimation:` line; SwiftBar dropdown shows the ⏱ line. Record findings; done bar is met only when the soak (2026-07-14) confirms unattended accrual.

## Self-Review

- **Spec coverage:** capture-create (T2/T3), capture-close (T4), sanity (T1/T3), session block (T5), SwiftBar (T6), buckets (T2/T3), deploy+soak (T7), review clock (no-op by design). ✓
- **Placeholders:** none — every code step carries the code. ✓
- **Type consistency:** `est` snapshot keys (`est_s/bucket/p50_s/p90_s/n/confidence`) used identically in T2, T3, T4, T5, T6; `record_estimate(..., cid=)` consistent T1→T2→T4; `resolve_commitment` dict|None return consumed truthily by unchanged `done.py`. ✓
