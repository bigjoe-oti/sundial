# Decision Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `sundial ask` urgency tiers, a confidence-governed autonomy gate, agent-authored escalation voice, and an instantly-syncing menu-bar face — without breaking any rail.

**Architecture:** A new `lib/policy.py` owns the decision vocabulary (tier→timings table + the pure `autonomy_decision`). `watcher.py` reads the tier table (still LLM-free, still date arithmetic). `core.add_commitment` stores optional policy fields; `cli/ask.py` exposes them. `session_start` surfaces autonomy verdicts to the returning agent. `core.refresh_menubar()` pushes SwiftBar to re-read on every state mutation.

**Tech Stack:** Python 3.9+ stdlib only, macOS built-ins (`open`, `say`). unittest. No third-party deps.

## Global Constraints

- **No LLM in the watcher/trigger path.** Agent voice is pre-composed at ask-time and replayed verbatim.
- **Delivery never suppressed.** Tiering/confidence may soften SOUND only; popups + the wall ceiling always fire.
- **Zero third-party dependencies.** Stdlib + macOS built-ins only.
- **Backward compatibility.** An untagged `sundial ask` behaves byte-identically to today; all 184 existing tests pass unchanged after every task.
- **Fail-safe.** Every new path degrades silently; never blocks a session or crashes a cycle.
- Run the full suite with `python3 -m pytest tests/ -q` (baseline: 184 passed).
- All commits land on branch `feat/decision-policy`.

---

## SLICE ① — Urgency tiering

### Task 1: `lib/policy.py` — tier table + `tier_of`

**Files:**
- Create: `lib/policy.py`
- Test: `tests/test_sundial.py` (new class `TestPolicyTiers`)

**Interfaces:**
- Produces: `TIER_TABLE: dict`, `DEFAULT_TIER = "normal"`, `tier_of(commitment: dict) -> str`

- [ ] **Step 1: Write the failing test** — add to `tests/test_sundial.py` (after the imports block, add `import policy  # noqa: E402` alongside the other `lib` imports; append the class near the other watcher tests):

```python
class TestPolicyTiers(unittest.TestCase):
    def test_normal_row_equals_legacy_constants(self):
        self.assertEqual(policy.TIER_TABLE["normal"]["offsets"], (600, 1200, 3000))
        self.assertEqual(policy.TIER_TABLE["normal"]["ceiling"], 5400)
        self.assertEqual(policy.TIER_TABLE["normal"]["rungs"], 3)

    def test_tier_of_defaults_and_reads(self):
        self.assertEqual(policy.tier_of({}), "normal")
        self.assertEqual(policy.tier_of({"weight": "high"}), "high")
        self.assertEqual(policy.tier_of({"weight": "low"}), "low")
        self.assertEqual(policy.tier_of({"weight": "bogus"}), "normal")  # unknown → normal

    def test_all_tiers_present(self):
        for t in ("low", "normal", "high"):
            self.assertIn(t, policy.TIER_TABLE)
            self.assertEqual(len(policy.TIER_TABLE[t]["offsets"]),
                             policy.TIER_TABLE[t]["rungs"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestPolicyTiers -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy'`

- [ ] **Step 3: Write minimal implementation** — create `lib/policy.py`:

```python
#!/usr/bin/env python3
"""Sundial — the decision policy. Pure, deterministic, no LLM, no IO.

Owns the escalation-tier table (urgency → ladder timings) and the autonomy
gate (confidence + reversibility → proceed/stand-down). Imported by the
watcher, the CLI, and the session-start hook so the vocabulary lives in one
place. Nothing here touches disk or the network."""

# urgency tier -> (unseen-time rung offsets, 90-min-style wall ceiling, rung count).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py::TestPolicyTiers -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/policy.py tests/test_sundial.py
git commit -m "feat(policy): tier table + tier_of (normal row == legacy)"
```

### Task 2: Make `ripe_rung` + `wall_ceiling_passed` tier-aware

**Files:**
- Modify: `watcher/watcher.py` (imports; `wall_ceiling_passed`; `ripe_rung`)
- Test: `tests/test_sundial.py::TestAbsenceClock` (new methods)

**Interfaces:**
- Consumes: `policy.TIER_TABLE`, `policy.tier_of` (Task 1)
- Produces: `ripe_rung`/`wall_ceiling_passed` now honor the commitment's tier; signatures unchanged.

- [ ] **Step 1: Write the failing test** — add to `TestAbsenceClock`:

```python
    def test_high_tier_faster_offsets(self):
        c, now = self._c(60)
        c["weight"] = "high"
        for unseen, expected in ((299, 0), (300, 1), (600, 2), (1200, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"high unseen={unseen}")

    def test_low_tier_two_rungs_and_slower(self):
        c, now = self._c(60)
        c["weight"] = "low"
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=1799), now, "away"), 0)
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=1800), now, "away"), 1)
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=5400), now, "away"), 2)
        # never a 3rd rung, even far past the last offset
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=99999), now, "away"), 2)

    def test_high_tier_wall_ceiling_at_40min(self):
        c, now = self._c(41)     # 41 wall-min
        c["weight"] = "high"
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "here"), 3)

    def test_normal_tier_unchanged_regression(self):
        c, now = self._c(60)     # no weight → normal
        for unseen, expected in ((599, 0), (600, 1), (1200, 2), (3000, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"normal unseen={unseen}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestAbsenceClock -q -k "high_tier or low_tier"`
Expected: FAIL — high/low use normal offsets (e.g. `high unseen=300` returns 0, not 1)

- [ ] **Step 3: Write minimal implementation** — in `watcher/watcher.py`:

Add the import near the top (after `import owner_model`):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import policy  # noqa: E402
```

Replace `wall_ceiling_passed`:

```python
def wall_ceiling_passed(c: dict, now) -> bool:
    """True when this commitment's tier wall ceiling has passed. Basis:
    created_at, falling back to due_at. Single source of truth for ripe_rung
    and run_cycle."""
    basis = core.parse_iso(c.get("created_at")) or core.parse_iso(c.get("due_at"))
    ceiling = policy.TIER_TABLE[policy.tier_of(c)]["ceiling"]
    return basis is not None and (now - basis).total_seconds() >= ceiling
```

In `ripe_rung`, replace the tail (from `if wall_ceiling_passed(c, now):` onward) with tier-aware offsets and max-rung:

```python
    table = policy.TIER_TABLE[policy.tier_of(c)]
    if wall_ceiling_passed(c, now):
        return table["rungs"]
    ripe = 0
    for i, th in enumerate(table["offsets"], start=1):
        if entry.get("unseen_s", 0.0) >= th:
            ripe = i
    return ripe
```

(Leave the plain-kind branch and the legacy-degrade branch above it untouched — legacy degrade only fires for weight-less records, i.e. normal semantics.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS — all prior tests + the 4 new ones (188 total)

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_sundial.py
git commit -m "feat(watcher): tier-aware ripe_rung + wall ceiling"
```

### Task 3: Store `weight`; add `--weight` to `sundial ask`

**Files:**
- Modify: `lib/core.py:241-260` (`add_commitment`)
- Modify: `cli/ask.py`
- Test: `tests/test_sundial.py::TestCore` (new method)

**Interfaces:**
- Produces: `core.add_commitment(..., weight=None)` stores `weight` on the record only when it is a non-normal tier.

- [ ] **Step 1: Write the failing test** — add to `TestCore` (its setUp already redirects `core.DATA`/`core.COMMITMENTS` to a temp dir):

```python
    def test_add_commitment_stores_weight(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply", weight="high")
        self.assertEqual(rec["weight"], "high")
        # normal / absent is NOT stored (records stay clean, legacy-identical)
        rec2 = core.add_commitment("q2?", "+0m", kind="awaiting-reply", weight="normal")
        self.assertNotIn("weight", rec2)
        rec3 = core.add_commitment("q3?", "+0m", kind="awaiting-reply")
        self.assertNotIn("weight", rec3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestCore::test_add_commitment_stores_weight -q`
Expected: FAIL — `add_commitment() got an unexpected keyword argument 'weight'`

- [ ] **Step 3: Write minimal implementation** — in `lib/core.py`, change the `add_commitment` signature and record-building:

```python
def add_commitment(text: str, due_str: str | None = None, source: str = "manual",
                   kind: str = "plain", session_id: str | None = None,
                   weight: str | None = None) -> dict:
    with _ledger_lock():
        items = load_commitments()
        due = parse_due(due_str)
        rec = {
            "id": uuid.uuid4().hex[:8],
            "created_at": now_utc().isoformat(),
            "due_at": due.isoformat() if due else None,
            "text": text,
            "source": source,
            "status": "open",
        }
        if kind != "plain":
            rec["kind"] = kind
        if session_id:
            rec["session_id"] = session_id
        if weight and weight != "normal":
            rec["weight"] = weight
        items.append(rec)
        write_json(COMMITMENTS, items)
        return rec
```

In `cli/ask.py`, add the flag and pass it through:

```python
    ap.add_argument("--weight", choices=("low", "normal", "high"),
                    default="normal", help="urgency tier (default normal)")
    args = ap.parse_args()

    rec = core.add_commitment(args.text, args.due, args.source,
                              kind="awaiting-reply", session_id=args.session,
                              weight=args.weight)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    tier = rec.get("weight", "normal")
    print(f"armed [{rec['id']}] ({tier}) {rec['text']}  (rung 1 due: {when})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (189 total)

- [ ] **Step 5: Commit**

```bash
git add lib/core.py cli/ask.py tests/test_sundial.py
git commit -m "feat(ask): --weight tier flag stored on the commitment"
```

### Task 4: High tier speaks at the final rung

**Files:**
- Modify: `watcher/watcher.py` (`speak_final`; the batch fire loop in `run_cycle`)
- Test: `tests/test_sundial.py::TestAbsenceClock` (new method)

**Interfaces:**
- Produces: `speak_final(message, audible=True, force=False)` — `force=True` speaks even without `data/speak.txt`.

- [ ] **Step 1: Write the failing test** — add to `TestAbsenceClock`:

```python
    def test_high_tier_speaks_final_without_speak_txt(self):
        spoken = []
        orig = watcher._spawn
        watcher._spawn = lambda cmd: spoken.append(cmd)
        try:
            # no data/speak.txt exists → normal tier stays silent, high speaks
            watcher.speak_final("final", audible=True, force=False)
            self.assertEqual(spoken, [])
            watcher.speak_final("final", audible=True, force=True)
            self.assertTrue(any("/usr/bin/say" in c for c in spoken))
            # courtesy still wins: force cannot override an inaudible gate
            spoken.clear()
            watcher.speak_final("final", audible=False, force=True)
            self.assertEqual(spoken, [])
        finally:
            watcher._spawn = orig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestAbsenceClock::test_high_tier_speaks_final_without_speak_txt -q`
Expected: FAIL — `speak_final() got an unexpected keyword argument 'force'`

- [ ] **Step 3: Write minimal implementation** — replace `speak_final` in `watcher/watcher.py`:

```python
def speak_final(message: str, audible=True, force=False) -> None:
    """Spoken final rung. Speaks when `force` (high-urgency tier) OR
    data/speak.txt exists. `audible=False` mutes unconditionally — the same
    courtesy gate as chime(); force never overrides silence-courtesy."""
    if not audible:
        return
    voice = ""
    try:
        voice = (core.DATA / "speak.txt").read_text(encoding="utf-8").strip()
        has_speak = True
    except Exception:
        has_speak = False
    if not force and not has_speak:
        return
    try:
        cmd = (["/usr/bin/say", "-v", voice, message] if voice
               else ["/usr/bin/say", message])
        _spawn(cmd)
    except Exception:
        pass
```

In `run_cycle`, the batch fire loop currently reads `if rung == 3: speak_final(message, audible)`. Replace with a tier-aware force:

```python
        for c, entry, rung, message, _ceiling in batch:
            try:
                desktop_notify("Sundial", message)
                chime(rung, state, audible)
                if rung == 3:
                    speak_final(message, audible,
                                force=(policy.tier_of(c) == "high"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (190 total)

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_sundial.py
git commit -m "feat(watcher): high tier speaks the final rung unprompted"
```

---

## SLICE ② — Confidence + reversibility + autonomy gate

### Task 5: `policy.autonomy_decision` (the safety-critical core)

**Files:**
- Modify: `lib/policy.py` (add constants + function)
- Test: `tests/test_sundial.py` (new class `TestAutonomyGate`)

**Interfaces:**
- Produces: `autonomy_decision(commitment: dict, entry: dict) -> {"action": str, "reason": str}` where `action ∈ {"require_explicit_yes", "proceed", "stand_down"}`. Pure and total (never raises).
- Constants: `AUTONOMY_PROCEED_UNATTENDED = 0.95`, `AUTONOMY_PROCEED_PRESENT = 0.80`, `AUTONOMY_PRESENT_MIN_S = 60.0`.

- [ ] **Step 1: Write the failing test**:

```python
class TestAutonomyGate(unittest.TestCase):
    def _e(self, here=0.0):
        return {"here_s": here, "unseen_s": 0.0, "count": 3}

    def test_irreversible_never_proceeds(self):
        for here in (0.0, 10000.0):  # absent OR long-present
            d = policy.autonomy_decision({"irreversible": True, "confidence": 0.99},
                                         self._e(here))
            self.assertEqual(d["action"], "require_explicit_yes")

    def test_reversible_high_confidence_proceeds_unattended(self):
        d = policy.autonomy_decision({"confidence": 0.95}, self._e(here=0.0))
        self.assertEqual(d["action"], "proceed")

    def test_reversible_mid_confidence_needs_present_silence(self):
        # present (here_s ≥ 60) → proceed
        d = policy.autonomy_decision({"confidence": 0.80}, self._e(here=60.0))
        self.assertEqual(d["action"], "proceed")
        # absent (here_s < 60) → stand down
        d2 = policy.autonomy_decision({"confidence": 0.80}, self._e(here=0.0))
        self.assertEqual(d2["action"], "stand_down")

    def test_reversible_low_confidence_stands_down(self):
        d = policy.autonomy_decision({"confidence": 0.5}, self._e(here=10000.0))
        self.assertEqual(d["action"], "stand_down")

    def test_no_confidence_stands_down(self):
        d = policy.autonomy_decision({}, self._e(here=10000.0))
        self.assertEqual(d["action"], "stand_down")

    def test_total_never_raises_on_garbage(self):
        for c, e in (({"confidence": "high"}, {}), ({}, None),
                     ({"confidence": None}, {"here_s": "x"})):
            d = policy.autonomy_decision(c, e if e is not None else {})
            self.assertIn(d["action"],
                          ("require_explicit_yes", "proceed", "stand_down"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestAutonomyGate -q`
Expected: FAIL — `module 'policy' has no attribute 'autonomy_decision'`

- [ ] **Step 3: Write minimal implementation** — append to `lib/policy.py`:

```python
AUTONOMY_PROCEED_UNATTENDED = 0.95   # reversible: act even on silence-while-absent
AUTONOMY_PROCEED_PRESENT = 0.80      # reversible: act on silence-while-PRESENT
AUTONOMY_PRESENT_MIN_S = 60.0        # here_s ≥ this = "they saw it and stayed silent"


def autonomy_decision(commitment: dict, entry: dict) -> dict:
    """Pure, total gate. Given a commitment (weight/confidence/irreversible)
    and its notified entry (here_s), decide what the agent may do when the
    escalation ladder is exhausted and the human still hasn't answered.

    action ∈ {require_explicit_yes, proceed, stand_down}. Never raises: any
    malformed input degrades to the safest outcome (stand_down)."""
    commitment = commitment or {}
    entry = entry or {}
    if commitment.get("irreversible"):
        return {"action": "require_explicit_yes",
                "reason": "irreversible: no silence ever authorizes it"}
    conf = commitment.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        return {"action": "stand_down", "reason": "no usable confidence stated"}
    try:
        here_s = float(entry.get("here_s", 0.0))
    except (TypeError, ValueError):
        here_s = 0.0
    present_silence = here_s >= AUTONOMY_PRESENT_MIN_S
    if conf >= AUTONOMY_PROCEED_UNATTENDED:
        return {"action": "proceed",
                "reason": f"confidence {conf:.2f} ≥ {AUTONOMY_PROCEED_UNATTENDED} (unattended ok)"}
    if conf >= AUTONOMY_PROCEED_PRESENT and present_silence:
        return {"action": "proceed",
                "reason": f"confidence {conf:.2f} ≥ {AUTONOMY_PROCEED_PRESENT} + present-silence"}
    return {"action": "stand_down",
            "reason": f"confidence {conf:.2f} below bar (present_silence={present_silence})"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py::TestAutonomyGate -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add lib/policy.py tests/test_sundial.py
git commit -m "feat(policy): autonomy_decision gate (irreversible hard-stop, two-clock)"
```

### Task 6: Store `confidence`/`irreversible`/`default_action`; ask flags

**Files:**
- Modify: `lib/core.py` (`add_commitment` signature + record)
- Modify: `cli/ask.py`
- Test: `tests/test_sundial.py::TestCore` (new method)

**Interfaces:**
- Produces: `core.add_commitment(..., weight=None, confidence=None, irreversible=False, default_action=None)`.

- [ ] **Step 1: Write the failing test** — add to `TestCore`:

```python
    def test_add_commitment_stores_policy_fields(self):
        rec = core.add_commitment(
            "drop col?", "+0m", kind="awaiting-reply",
            confidence=0.9, irreversible=True, default_action="back up then halt")
        self.assertEqual(rec["confidence"], 0.9)
        self.assertTrue(rec["irreversible"])
        self.assertEqual(rec["default_action"], "back up then halt")
        # defaults absent → keys omitted
        bare = core.add_commitment("q?", "+0m", kind="awaiting-reply")
        for k in ("confidence", "irreversible", "default_action"):
            self.assertNotIn(k, bare)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestCore::test_add_commitment_stores_policy_fields -q`
Expected: FAIL — unexpected keyword argument `confidence`

- [ ] **Step 3: Write minimal implementation** — extend `add_commitment` in `lib/core.py`:

```python
def add_commitment(text: str, due_str: str | None = None, source: str = "manual",
                   kind: str = "plain", session_id: str | None = None,
                   weight: str | None = None, confidence: float | None = None,
                   irreversible: bool = False,
                   default_action: str | None = None) -> dict:
```

After the existing `if weight ...:` line, add:

```python
        if confidence is not None:
            rec["confidence"] = confidence
        if irreversible:
            rec["irreversible"] = True
        if default_action:
            rec["default_action"] = default_action
```

In `cli/ask.py`, add the flags (before `args = ap.parse_args()`):

```python
    ap.add_argument("--confidence", type=float, default=None,
                    help="0..1 sureness in the default action if unanswered")
    ap.add_argument("--irreversible", action="store_true",
                    help="destructive/one-way; never auto-proceeds on silence")
    ap.add_argument("--default", dest="default_action", default=None,
                    help="the action taken if you never answer (stated in final rung)")
```

Validate confidence and pass through (replace the `add_commitment` call):

```python
    if args.confidence is not None and not (0.0 <= args.confidence <= 1.0):
        ap.error("--confidence must be between 0 and 1")

    rec = core.add_commitment(args.text, args.due, args.source,
                              kind="awaiting-reply", session_id=args.session,
                              weight=args.weight, confidence=args.confidence,
                              irreversible=args.irreversible,
                              default_action=args.default_action)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (197 total)

- [ ] **Step 5: Commit**

```bash
git add lib/core.py cli/ask.py tests/test_sundial.py
git commit -m "feat(ask): --confidence/--irreversible/--default policy fields"
```

### Task 7: `session_start` surfaces autonomy verdicts

**Files:**
- Modify: `hooks/session_start.py` (`build_block`)
- Test: `tests/test_sundial.py::TestSessionStartHook` (new method)

**Interfaces:**
- Consumes: `policy.autonomy_decision`, `policy.TIER_TABLE`, `policy.tier_of`; reads `core.DATA/"notified.json"`.

- [ ] **Step 1: Write the failing test** — add to `TestSessionStartHook`:

```python
    def test_verdict_block_for_exhausted_ask(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, core.BIRTH, core.LEDGER)
            core.DATA = dd
            core.COMMITMENTS = dd / "commitments.json"
            core.BIRTH = dd / "birth.json"
            core.LEDGER = dd / "session-ledger.json"
            try:
                birth = core.get_or_create_birth()
                # a high-confidence reversible ask, escalation exhausted (count=3)
                rec = core.add_commitment("ship the copy?", "+0m",
                                          kind="awaiting-reply", confidence=0.97)
                core.write_json(dd / "notified.json",
                                {rec["id"]: {"count": 3, "here_s": 0.0,
                                             "unseen_s": 4000.0, "last": None}})
                block = session_start.build_block(core, birth, None)
                self.assertIn("Escalation exhausted", block)
                self.assertIn("PROCEED", block)
            finally:
                (core.DATA, core.COMMITMENTS, core.BIRTH, core.LEDGER) = orig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestSessionStartHook::test_verdict_block_for_exhausted_ask -q`
Expected: FAIL — "Escalation exhausted" not in the block

- [ ] **Step 3: Write minimal implementation** — in `hooks/session_start.py`, inside `build_block`, immediately BEFORE the `lines.append("\nThis is passive background awareness...")` line, insert:

```python
    try:
        import policy
        notified = core.read_json(core.DATA / "notified.json", {})
        notified = notified if isinstance(notified, dict) else {}
        verdicts = []
        for c in core.load_commitments():
            if c.get("kind") != "awaiting-reply" or c.get("status") != "open":
                continue
            entry = notified.get(c.get("id"))
            if not isinstance(entry, dict):
                continue
            max_rung = policy.TIER_TABLE[policy.tier_of(c)]["rungs"]
            if entry.get("count", 0) < max_rung:
                continue  # ladder not yet exhausted — the human may still answer
            v = policy.autonomy_decision(c, entry)
            verdicts.append((c, v))
        if verdicts:
            lines.append(f"\nEscalation exhausted, your call needed ({len(verdicts)}):")
            for c, v in verdicts[:10]:
                text = str(c.get("text", ""))[:120]
                da = c.get("default_action")
                tail = f" → default: {da}" if da else ""
                lines.append(f"  - [{v['action'].upper()}] {text}{tail} ({v['reason']})")
    except Exception:
        pass
```

(`policy` is importable — `build_block`'s caller and the opportunities block already add `lib`/`watcher` to `sys.path`; add `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))` at the top of this try-block if `policy` fails to import in isolation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (198 total)

- [ ] **Step 5: Commit**

```bash
git add hooks/session_start.py tests/test_sundial.py
git commit -m "feat(hook): surface autonomy verdicts for exhausted asks on session start"
```

---

## SLICE ③ — Agent-authored rung messages

### Task 8: Store `rungs`; add repeatable `--rung`

**Files:**
- Modify: `lib/core.py` (`add_commitment`)
- Modify: `cli/ask.py`
- Test: `tests/test_sundial.py::TestCore` (new method)

**Interfaces:**
- Produces: `core.add_commitment(..., rungs=None)` stores a list of ≤3 pre-composed strings.

- [ ] **Step 1: Write the failing test** — add to `TestCore`:

```python
    def test_add_commitment_stores_rungs(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply",
                                  rungs=["knock one", "knock two", "final call"])
        self.assertEqual(rec["rungs"], ["knock one", "knock two", "final call"])
        self.assertNotIn("rungs", core.add_commitment("q2?", "+0m",
                                                       kind="awaiting-reply"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestCore::test_add_commitment_stores_rungs -q`
Expected: FAIL — unexpected keyword argument `rungs`

- [ ] **Step 3: Write minimal implementation** — add `rungs: list | None = None` to the `add_commitment` signature (end of params) and after the `default_action` block:

```python
        if rungs:
            rec["rungs"] = [str(r) for r in rungs][:3]
```

In `cli/ask.py`, add the flag:

```python
    ap.add_argument("--rung", action="append", dest="rungs", default=None,
                    help="pre-composed message for a rung (repeat ≤3, in order)")
```

Validate and pass through (add before the `add_commitment` call, and add `rungs=args.rungs` to it):

```python
    if args.rungs and len(args.rungs) > 3:
        ap.error("at most 3 --rung messages")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (199 total)

- [ ] **Step 5: Commit**

```bash
git add lib/core.py cli/ask.py tests/test_sundial.py
git commit -m "feat(ask): --rung stores agent-authored per-rung voice"
```

### Task 9: Watcher replays stored rungs + appends `default_action`

**Files:**
- Modify: `watcher/watcher.py` (`pending_ping`)
- Test: `tests/test_sundial.py::TestAbsenceClock` (new methods)

**Interfaces:**
- Consumes: commitment `rungs` list + `default_action`.
- Produces: `pending_ping` prefers `c["rungs"][rung-1]`; the final rung appends the default-action clause. Falls back to the static pools when `rungs` absent.

- [ ] **Step 1: Write the failing test** — add to `TestAbsenceClock`:

```python
    def test_pending_ping_uses_stored_rungs(self):
        c, now = self._c(60)
        c["rungs"] = ["my rung one", "my rung two", "my final"]
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertEqual(hit[0], 1)
        self.assertIn("my rung one", hit[1])

    def test_final_rung_appends_default_action(self):
        c, now = self._c(60)
        c["rungs"] = ["r1", "r2", "final call"]
        c["default_action"] = "back up then halt"
        hit = watcher.pending_ping(c, self._entry(unseen=3000), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertIn("back up then halt", hit[1])

    def test_no_rungs_falls_back_to_pools(self):
        c, now = self._c(60)  # no rungs key
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertIsNotNone(hit)
        self.assertNotIn("my rung one", hit[1])  # came from a static pool
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestAbsenceClock -q -k "stored_rungs or default_action or falls_back"`
Expected: FAIL — stored rungs ignored

- [ ] **Step 3: Write minimal implementation** — in `watcher/watcher.py`, replace the tail of `pending_ping` (the part after `text, cid = ...`) so stored rungs win:

```python
    text, cid = c.get("text", ""), c.get("id", "")
    stored = c.get("rungs")
    if isinstance(stored, list) and len(stored) >= ripe and stored[ripe - 1]:
        msg = str(stored[ripe - 1])
        if ripe == policy.TIER_TABLE[policy.tier_of(c)]["rungs"]:
            da = c.get("default_action")
            if da:
                msg = f"{msg} — proceeding to {da} or standing down."
        return ripe, _cap_message(msg)
    if c.get("kind") != "awaiting-reply":
        return ripe, pick_message(cid, PLAIN_POOL, text=text)
    if state == "elsewhere" and app:
        pool = ELSEWHERE_POOLS[ripe - 1]
        return ripe, pick_message(cid, pool, text=text, app=app)
    return ripe, pick_message(cid, RUNG_POOLS[ripe - 1], text=text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (202 total)

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_sundial.py
git commit -m "feat(watcher): replay agent-authored rungs + state the specific default"
```

---

## SLICE ④ — SwiftBar instant-sync

### Task 10: `core.refresh_menubar()` + test seam

**Files:**
- Modify: `lib/core.py` (add helper + seam)
- Test: `tests/test_sundial.py` (new class `TestMenubarSync`)

**Interfaces:**
- Produces: `core.refresh_menubar() -> None` (fail-safe); `core._menubar_spawn(cmd)` (replaceable seam).

- [ ] **Step 1: Write the failing test**:

```python
class TestMenubarSync(unittest.TestCase):
    def test_refresh_fires_swiftbar_url(self):
        seen = []
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: seen.append(cmd)
        try:
            core.refresh_menubar()
            self.assertEqual(len(seen), 1)
            self.assertIn("swiftbar://refreshplugin?name=sundial", " ".join(seen[0]))
        finally:
            core._menubar_spawn = orig

    def test_refresh_never_raises(self):
        orig = core._menubar_spawn
        def boom(cmd):
            raise OSError("no swiftbar")
        core._menubar_spawn = boom
        try:
            core.refresh_menubar()  # must swallow
        finally:
            core._menubar_spawn = orig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestMenubarSync -q`
Expected: FAIL — `module 'core' has no attribute '_menubar_spawn'`

- [ ] **Step 3: Write minimal implementation** — add to `lib/core.py` (add `import subprocess` to the imports at the top of the file):

```python
def _menubar_spawn(cmd) -> None:
    """Fire-and-forget opener; tests replace this seam."""
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def refresh_menubar() -> None:
    """Push SwiftBar to re-read Sundial's state immediately (Sundial → SwiftBar
    signal; the plugin stays strictly read-only). Fail-safe: a missing SwiftBar
    or unknown URL scheme never raises. The 30s poll remains the backstop."""
    try:
        _menubar_spawn(["/usr/bin/open", "-g",
                        "swiftbar://refreshplugin?name=sundial"])
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (204 total)

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_sundial.py
git commit -m "feat(core): refresh_menubar — push SwiftBar to re-read on demand"
```

### Task 11: Watcher refreshes the menu bar on state change

**Files:**
- Modify: `watcher/watcher.py` (`run_cycle`)
- Test: `tests/test_sundial.py::TestAbsenceClock` (new method)

**Interfaces:**
- Consumes: `core.refresh_menubar`.

- [ ] **Step 1: Write the failing test** — add to `TestAbsenceClock` (mirrors `test_run_cycle_holds_while_here_and_accrues`'s stubbing, but with an AWAY presence so a fire happens):

```python
    def test_run_cycle_refreshes_menubar_on_fire(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds, core._menubar_spawn)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            refreshed = []
            core._menubar_spawn = lambda cmd: refreshed.append(cmd)
            watcher.desktop_notify = lambda t, m: True
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 9999.0,
                                               "front_app": None}
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            try:
                core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertTrue(refreshed)  # a fire → menu bar refreshed
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds, core._menubar_spawn) = orig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestAbsenceClock::test_run_cycle_refreshes_menubar_on_fire -q`
Expected: FAIL — `refreshed` is empty

- [ ] **Step 3: Write minimal implementation** — in `run_cycle`, add near the top (right after `prev = record_presence(snap, now)`):

```python
    state_changed = (prev.get("state") != snap["state"])
```

At the two fire sites, set the flag. In the return-nudge branch, after `dirty = True`, add `state_changed = True`. In the batch fire loop, after its `dirty = True`, add `state_changed = True`. In the meeting/build offer branches, after each `opportunities.count_offer(today)`, add `state_changed = True`.

At the very end of `run_cycle` (after the `if dirty: core.write_json(NOTIFIED, notified)` block), add:

```python
    if state_changed or dirty:
        core.refresh_menubar()
```

(`state_changed` is assigned once and only ever flipped to True, so the offer branches inside the `try` can set it without a `nonlocal`/scoping issue — it's a plain local in `run_cycle`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (205 total)

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_sundial.py
git commit -m "feat(watcher): refresh menu bar on fire / offer / presence change"
```

### Task 12: CLI verbs + prompt hook refresh the menu bar

**Files:**
- Modify: `cli/ask.py`, `cli/answered.py`, `cli/done.py`, `cli/remember.py`, `cli/decline.py`, `cli/allow.py`, `hooks/prompt_submit.py`
- Test: `tests/test_sundial.py::TestPromptSubmitHook` (new method)

**Interfaces:**
- Consumes: `core.refresh_menubar`.

- [ ] **Step 1: Write the failing test** — add to `TestPromptSubmitHook` (it already redirects `core.DATA`/`core.COMMITMENTS`). Assert that disarming an ask on a human prompt refreshes the bar:

```python
    def test_disarm_refreshes_menubar(self):
        refreshed = []
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: refreshed.append(cmd)
        try:
            core.add_commitment("q?", "+0m", kind="awaiting-reply")
            prompt_submit.build_context({"prompt": "hello",
                                         "transcript_path": None})
            self.assertTrue(refreshed)  # disarm changed state → bar refreshed
        finally:
            core._menubar_spawn = orig
```

(If `build_context`'s signature in this repo differs, match the existing call convention used by the other `TestPromptSubmitHook` tests — the assertion is the point: a disarm triggers a refresh.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sundial.py::TestPromptSubmitHook::test_disarm_refreshes_menubar -q`
Expected: FAIL — `refreshed` empty

- [ ] **Step 3: Write minimal implementation:**

In each of `cli/ask.py`, `cli/answered.py`, `cli/done.py`, `cli/remember.py`, `cli/decline.py`, `cli/allow.py`: add `core.refresh_menubar()` as the last line of `main()` (after the `print(...)`). `core` is already imported in each.

In `hooks/prompt_submit.py`: after the block that calls `core.close_awaiting_detailed()` (the disarm), add — guarded so a no-op prompt with nothing to disarm still stays cheap but correct:

```python
        core.refresh_menubar()
```

Place it where the closed-count is known so it only fires when something actually closed (if the hook tracks the closed list, gate on it: `if closed: core.refresh_menubar()`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sundial.py -q`
Expected: PASS (206 total)

- [ ] **Step 5: Commit**

```bash
git add cli/ hooks/prompt_submit.py tests/test_sundial.py
git commit -m "feat(cli): every state-mutating verb + disarm refreshes the menu bar"
```

---

## Final verification

- [ ] Run the whole suite: `python3 -m pytest tests/ -q` → **all green (~206 tests)**.
- [ ] Lint: `ruff check .` (repo ships a `.ruff_cache`, so ruff is the house linter) → clean.
- [ ] Manual drive (documented in the verify task, not a unit test): arm `sundial ask "x" --weight high --confidence 0.9 --default "do y"`, confirm the record, confirm `sundial due` shows the tier, and confirm SwiftBar refreshes immediately after `sundial done <id>`.

## Self-review notes (author)

- **Spec coverage:** ① Tasks 1–4; ② Tasks 5–7; ③ Tasks 8–9; ④ Tasks 10–12. `--weight/--confidence/--irreversible/--default/--rung` all covered. Autonomy gate truth table exhaustive in Task 5. Delivery-never-suppressed rail is preserved (tiering only changes timings/sound, never gates a popup) and re-checked in the audit.
- **The one open tunable** (0.80/0.95 bars, `AUTONOMY_PRESENT_MIN_S`) is isolated to named constants in `policy.py` — retune without touching logic.
- **Backward-compat:** every new `add_commitment` param defaults to the absent/legacy behavior; the `normal` tier row equals the old constants; stored-rungs fall back to pools. All 184 existing tests must stay green after each task.
