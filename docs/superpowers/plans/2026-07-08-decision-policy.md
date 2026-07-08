# Decision Policy Implementation Plan (rev. 2 — post-audit)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give `sundial ask` urgency tiers, a confidence-governed autonomy gate (≥0.95-only in v1), agent-authored escalation voice, and an instantly-syncing menu-bar face — without breaking any rail.

**Architecture:** A new `lib/policy.py` owns the decision vocabulary (tier→timings table + the pure `autonomy_decision`). `watcher.py` reads the tier table (LLM-free, date arithmetic) and speaks tier-honest copy. `core.add_commitment` stores optional policy fields; `cli/ask.py` exposes them. `session_start` surfaces autonomy verdicts. `core.refresh_menubar()` pushes SwiftBar to re-read on every state mutation.

**Tech Stack:** Python 3.9+ stdlib only, macOS built-ins (`open`, `say`). unittest. No third-party deps.

## Global Constraints

- **No LLM in the watcher/trigger path.** Agent voice is pre-composed at ask-time and replayed verbatim.
- **Delivery never suppressed** — tiering/confidence soften SOUND only; popups + the tier wall ceiling always fire.
- **Every delivery is honest** — no message states an elapsed time it didn't wait; every terminal rung states the autonomy contract.
- **Zero third-party dependencies.** Stdlib + macOS built-ins only.
- **Backward compatibility.** An untagged `sundial ask` behaves byte-identically to today; all 184 existing tests pass unchanged after every task.
- **Fail-safe.** Every new path degrades silently; never blocks a session or crashes a cycle.
- Baseline: `python3 -m pytest tests/ -q` → **184 passed**. Expected end state: **~209**.
- All commits land on branch `feat/decision-policy`.

### Post-audit changes folded in
- **Present-silence DEFERRED** (audit S1): the `here_s ≥ 60` consent predicate is unsound as wired to `accrue`. v1 gate is irreversible→require-yes, confidence ≥0.95→proceed, else stand-down. No present-silence branch. (Fast-follow once accrual is ripeness-gated.)
- **Honest tier copy** (audit B1/B2): high/low tiers use number-free fallback pools; the terminal rung always carries the autonomy contract. Normal tier copy untouched.
- Mechanical fixes: high-tier test age `_c(30)`; real `build_context(core, data)` signature; `decline.py`/`allow.py` need a `core` import; existing run_cycle tests stub `core._menubar_spawn`.

---

## SLICE ① — Urgency tiering (honest)

### Task 1: `lib/policy.py` — tier table + `tier_of`

**Files:** Create `lib/policy.py`; Test: `tests/test_sundial.py` (new class `TestPolicyTiers`).
**Interfaces produced:** `TIER_TABLE: dict`, `DEFAULT_TIER = "normal"`, `tier_of(commitment) -> str`.

- [ ] **Step 1: Failing test** — add `import policy  # noqa: E402` beside the other `lib` imports (after `import tzutil`), then append:

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
        self.assertEqual(policy.tier_of({"weight": "bogus"}), "normal")

    def test_all_tiers_offsets_match_rungs(self):
        for t in ("low", "normal", "high"):
            self.assertIn(t, policy.TIER_TABLE)
            self.assertEqual(len(policy.TIER_TABLE[t]["offsets"]),
                             policy.TIER_TABLE[t]["rungs"])
```

- [ ] **Step 2: Run to verify FAIL** — `python3 -m pytest tests/test_sundial.py::TestPolicyTiers -q` → `ModuleNotFoundError: policy`.

- [ ] **Step 3: Implement** — create `lib/policy.py`:

```python
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
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py::TestPolicyTiers -q` → 3 pass.
- [ ] **Step 5: Commit** — `git add lib/policy.py tests/test_sundial.py && git commit -m "feat(policy): tier table + tier_of (normal row == legacy)"`

### Task 2: tier-aware `ripe_rung` + `wall_ceiling_passed`

**Files:** Modify `watcher/watcher.py`; Test: `TestAbsenceClock`.
**Interfaces consumed:** `policy.TIER_TABLE`, `policy.tier_of`. Signatures unchanged.

- [ ] **Step 1: Failing test** — add to `TestAbsenceClock`:

```python
    def test_high_tier_faster_offsets(self):
        c, now = self._c(30)          # 30 wall-min < high 40-min ceiling
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
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=99999), now, "away"), 2)

    def test_high_tier_wall_ceiling_at_40min(self):
        c, now = self._c(41)
        c["weight"] = "high"
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "here"), 3)

    def test_normal_tier_unchanged_regression(self):
        c, now = self._c(60)
        for unseen, expected in ((599, 0), (600, 1), (1200, 2), (3000, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"normal unseen={unseen}")
```

- [ ] **Step 2: Run FAIL** — `python3 -m pytest tests/test_sundial.py::TestAbsenceClock -q -k "high_tier or low_tier"` → high/low use normal offsets.

- [ ] **Step 3: Implement** — in `watcher/watcher.py`, add after the existing `import owner_model` block:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import policy  # noqa: E402
```

Replace `wall_ceiling_passed`:

```python
def wall_ceiling_passed(c: dict, now) -> bool:
    """True when this commitment's TIER wall ceiling has passed. Basis:
    created_at, falling back to due_at. Single source of truth for ripe_rung
    and run_cycle."""
    basis = core.parse_iso(c.get("created_at")) or core.parse_iso(c.get("due_at"))
    ceiling = policy.TIER_TABLE[policy.tier_of(c)]["ceiling"]
    return basis is not None and (now - basis).total_seconds() >= ceiling
```

In `ripe_rung`, replace the tail (from `if wall_ceiling_passed(c, now):` to the end):

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

(Leave the plain-kind branch and the legacy-degrade branch above untouched — legacy degrade only fires for weight-less records, i.e. normal semantics. `UNSEEN_OFFSETS`/`WALL_CEILING_S` module constants become unreferenced but harmless; `RUNG_OFFSETS` stays live in the legacy branch.)

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 188.
- [ ] **Step 5: Commit** — `git add watcher/watcher.py tests/test_sundial.py && git commit -m "feat(watcher): tier-aware ripe_rung + wall ceiling"`

### Task 3: store `weight`; `sundial ask --weight`

**Files:** Modify `lib/core.py` (`add_commitment`), `cli/ask.py`; Test: `TestCore`.
**Interfaces produced:** `core.add_commitment(..., weight=None)` stores `weight` only for non-normal tiers.

- [ ] **Step 1: Failing test** — add to `TestCore`:

```python
    def test_add_commitment_stores_weight(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply", weight="high")
        self.assertEqual(rec["weight"], "high")
        self.assertNotIn("weight",
                         core.add_commitment("q2?", "+0m", kind="awaiting-reply",
                                             weight="normal"))
        self.assertNotIn("weight",
                         core.add_commitment("q3?", "+0m", kind="awaiting-reply"))
```

- [ ] **Step 2: Run FAIL** — unexpected keyword `weight`.

- [ ] **Step 3: Implement** — change `add_commitment` signature in `lib/core.py` to add `weight: str | None = None`, and after the `if session_id:` block add:

```python
        if weight and weight != "normal":
            rec["weight"] = weight
```

In `cli/ask.py` add before `args = ap.parse_args()`:

```python
    ap.add_argument("--weight", choices=("low", "normal", "high"),
                    default="normal", help="urgency tier (default normal)")
```

and pass `weight=args.weight` into the `add_commitment(...)` call; change the print to include the tier:

```python
    tier = rec.get("weight", "normal")
    print(f"armed [{rec['id']}] ({tier}) {rec['text']}  (rung 1 due: {when})")
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 189.
- [ ] **Step 5: Commit** — `git add lib/core.py cli/ask.py tests/test_sundial.py && git commit -m "feat(ask): --weight tier flag stored on the commitment"`

### Task 4: honest tier-aware `pending_ping` (number-free high/low copy + terminal contract)

**Files:** Modify `watcher/watcher.py` (add `TIER_RUNG_POOLS`, rewrite `pending_ping`); Test: `TestAbsenceClock`.
**Interfaces:** `pending_ping(c, entry, now, state, app)` unchanged signature. Reads `c["default_action"]` (may be absent) and, later, `c["rungs"]` (added in Task 10).

- [ ] **Step 1: Failing test** — add to `TestAbsenceClock`:

```python
    def test_high_tier_message_states_no_false_minutes(self):
        c, now = self._c(30); c["weight"] = "high"
        hit = watcher.pending_ping(c, self._entry(unseen=1200), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertNotIn("50 min", hit[1])       # normal-pool lie must not appear
        self.assertNotIn("20m", hit[1])
        self.assertIn("standing down", hit[1])    # terminal contract present

    def test_low_terminal_rung_states_contract(self):
        c, now = self._c(60); c["weight"] = "low"
        hit = watcher.pending_ping(c, self._entry(unseen=5400), now, "away", None)
        self.assertEqual(hit[0], 2)               # low max rung
        self.assertIn("standing down", hit[1])    # contract on the LAST rung

    def test_terminal_rung_states_specific_default_action(self):
        c, now = self._c(60); c["weight"] = "high"
        c["default_action"] = "back up then halt"
        hit = watcher.pending_ping(c, self._entry(unseen=1200), now, "away", None)
        self.assertIn("back up then halt", hit[1])

    def test_normal_tier_copy_unchanged(self):
        c, now = self._c(60)                      # normal, no default_action
        hit = watcher.pending_ping(c, self._entry(unseen=3000), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertIn("proceeding on my judgment", hit[1])  # existing rung-3 pool
```

- [ ] **Step 2: Run FAIL** — high tier routes through numbered `RUNG_POOLS`.

- [ ] **Step 3: Implement** — in `watcher/watcher.py`, add near the other pools:

```python
# Tier-neutral fallback copy for NON-normal tiers: no baked-in elapsed minutes
# (the normal pools hardcode 20m/50m, which would lie at other cadences). Rung 3
# always states the autonomy consequence.
TIER_RUNG_POOLS = {
    1: ("{owner} — I'm blocked on: {text}",
        "A question ripened while you were away: {text}"),
    2: ("Still waiting, {owner}: {text}",
        "Second knock: {text}"),
    3: ("Final call, {owner}: {text} — proceeding on my judgment or standing down.",
        "Last knock, {owner}: {text} — I take it from here or stand down."),
}
```

Replace `pending_ping` entirely:

```python
def pending_ping(c: dict, entry: dict, now, state, app) -> "tuple[int, str] | None":
    """The single highest ripe, not-yet-sent rung for a commitment, or None.
    Message priority: agent-authored rungs > plain pool > terminal-with-default
    > normal pools (unchanged) > tier-neutral high/low pools. Every terminal
    rung carries the autonomy contract; no message states a false elapsed time."""
    if c.get("status") != "open" or core.parse_iso(c.get("due_at")) is None:
        return None
    ripe = ripe_rung(c, entry, now, state)
    if ripe <= entry.get("count", 0):
        return None
    text, cid = c.get("text", ""), c.get("id", "")
    tier = policy.tier_of(c)
    is_terminal = (ripe == policy.TIER_TABLE[tier]["rungs"])
    da = c.get("default_action")

    # 1) agent-authored voice wins (populated in slice ③; absent → skip)
    stored = c.get("rungs")
    if isinstance(stored, list) and len(stored) >= ripe and stored[ripe - 1]:
        msg = str(stored[ripe - 1])
        if is_terminal and da:
            msg = f"{msg} — proceeding to {da} or standing down."
        return ripe, _cap_message(msg)

    # 2) plain (non-awaiting) unchanged
    if c.get("kind") != "awaiting-reply":
        return ripe, pick_message(cid, PLAIN_POOL, text=text)

    # 3) terminal rung WITH a stated default: honest, specific, tier-neutral
    if is_terminal and da:
        return ripe, _cap_message(
            f"Final call, {owner_name()} — {text}: proceeding to {da}, or standing down.")

    # 4) NORMAL tier: existing numbered / app-aware pools (behavior unchanged)
    if tier == "normal":
        if state == "elsewhere" and app:
            return ripe, pick_message(cid, ELSEWHERE_POOLS[ripe - 1], text=text, app=app)
        return ripe, pick_message(cid, RUNG_POOLS[ripe - 1], text=text)

    # 5) HIGH / LOW: number-free copy; terminal rung carries the contract
    pool = TIER_RUNG_POOLS[3 if is_terminal else ripe]
    return ripe, pick_message(cid, pool, text=text)
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 193 (existing `test_pending_ping_elsewhere_uses_app_pool`, `test_rung3_always_states_autonomy` etc. stay green via path 4).
- [ ] **Step 5: Commit** — `git add watcher/watcher.py tests/test_sundial.py && git commit -m "feat(watcher): honest tier copy — no false minutes, terminal contract always"`

### Task 5: high tier speaks the final rung unprompted

**Files:** Modify `watcher/watcher.py` (`speak_final`; batch fire loop); Test: `TestAbsenceClock`.
**Interfaces:** `speak_final(message, audible=True, force=False)`.

- [ ] **Step 1: Failing test** — add to `TestAbsenceClock`:

```python
    def test_high_tier_speaks_final_without_speak_txt(self):
        spoken = []
        orig = watcher._spawn
        watcher._spawn = lambda cmd: spoken.append(cmd)
        try:
            watcher.speak_final("final", audible=True, force=False)
            self.assertEqual(spoken, [])                       # no speak.txt → silent
            watcher.speak_final("final", audible=True, force=True)
            self.assertTrue(any("/usr/bin/say" in c for c in spoken))
            spoken.clear()
            watcher.speak_final("final", audible=False, force=True)
            self.assertEqual(spoken, [])                       # courtesy still wins
        finally:
            watcher._spawn = orig
```

- [ ] **Step 2: Run FAIL** — unexpected keyword `force`.

- [ ] **Step 3: Implement** — replace `speak_final`:

```python
def speak_final(message: str, audible=True, force=False) -> None:
    """Spoken final rung. Speaks when `force` (high-urgency tier) OR
    data/speak.txt exists. `audible=False` mutes unconditionally — the same
    courtesy gate as chime(); force never overrides silence-courtesy."""
    if not audible:
        return
    voice, has_speak = "", False
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

In `run_cycle`'s batch fire loop, change `if rung == 3: speak_final(message, audible)` to:

```python
                if rung == 3:
                    speak_final(message, audible,
                                force=(policy.tier_of(c) == "high"))
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 194.
- [ ] **Step 5: Commit** — `git add watcher/watcher.py tests/test_sundial.py && git commit -m "feat(watcher): high tier speaks the (honest) final rung unprompted"`

---

## SLICE ② — Confidence + reversibility + autonomy gate (≥0.95-only, v1)

### Task 6: `policy.autonomy_decision` (safety-critical, simplified)

**Files:** Modify `lib/policy.py`; Test: new class `TestAutonomyGate`.
**Interfaces produced:** `autonomy_decision(commitment, entry=None) -> {"action","reason"}`, `action ∈ {"require_explicit_yes","proceed","stand_down"}`. Pure & total. Constant `AUTONOMY_PROCEED_MIN = 0.95`.

> Present-silence deferred (audit S1). `entry` is accepted for signature stability / future use but not consulted in v1.

- [ ] **Step 1: Failing test**:

```python
class TestAutonomyGate(unittest.TestCase):
    def test_irreversible_never_proceeds(self):
        d = policy.autonomy_decision({"irreversible": True, "confidence": 0.99})
        self.assertEqual(d["action"], "require_explicit_yes")

    def test_high_confidence_reversible_proceeds(self):
        self.assertEqual(policy.autonomy_decision({"confidence": 0.95})["action"], "proceed")
        self.assertEqual(policy.autonomy_decision({"confidence": 0.99})["action"], "proceed")

    def test_below_bar_stands_down(self):
        for conf in (0.0, 0.5, 0.8, 0.9499):
            self.assertEqual(policy.autonomy_decision({"confidence": conf})["action"],
                             "stand_down", f"conf={conf}")

    def test_no_or_garbage_confidence_stands_down(self):
        for c in ({}, {"confidence": None}, {"confidence": "high"}):
            self.assertEqual(policy.autonomy_decision(c)["action"], "stand_down")

    def test_total_never_raises(self):
        for c in (None, {"irreversible": "yes"}, {"confidence": 1.0}):
            self.assertIn(policy.autonomy_decision(c)["action"],
                          ("require_explicit_yes", "proceed", "stand_down"))
```

- [ ] **Step 2: Run FAIL** — no attribute `autonomy_decision`.

- [ ] **Step 3: Implement** — append to `lib/policy.py`:

```python
AUTONOMY_PROCEED_MIN = 0.95   # reversible actions proceed unattended at/above this


def autonomy_decision(commitment: dict, entry: dict | None = None) -> dict:
    """Pure, total gate. Given a commitment (confidence/irreversible), decide
    what the agent may do once the ladder is exhausted and the human still
    hasn't answered.

    v1 rule (present-silence deferred — see spec):
      - irreversible            -> require_explicit_yes (no silence ever authorizes)
      - reversible, conf ≥ 0.95 -> proceed
      - otherwise               -> stand_down

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
    return {"action": "stand_down",
            "reason": f"confidence {conf:.2f} below {AUTONOMY_PROCEED_MIN}"}
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py::TestAutonomyGate -q` → 5 pass.
- [ ] **Step 5: Commit** — `git add lib/policy.py tests/test_sundial.py && git commit -m "feat(policy): autonomy gate — irreversible hard-stop + ≥0.95 proceed"`

### Task 7: store `confidence`/`irreversible`/`default_action`; ask flags

**Files:** Modify `lib/core.py`, `cli/ask.py`; Test: `TestCore`.
**Interfaces:** `core.add_commitment(..., confidence=None, irreversible=False, default_action=None)`.

- [ ] **Step 1: Failing test** — add to `TestCore`:

```python
    def test_add_commitment_stores_policy_fields(self):
        rec = core.add_commitment("drop col?", "+0m", kind="awaiting-reply",
                                  confidence=0.9, irreversible=True,
                                  default_action="back up then halt")
        self.assertEqual(rec["confidence"], 0.9)
        self.assertTrue(rec["irreversible"])
        self.assertEqual(rec["default_action"], "back up then halt")
        bare = core.add_commitment("q?", "+0m", kind="awaiting-reply")
        for k in ("confidence", "irreversible", "default_action"):
            self.assertNotIn(k, bare)
```

- [ ] **Step 2: Run FAIL** — unexpected keyword `confidence`.

- [ ] **Step 3: Implement** — extend `add_commitment` signature with `confidence: float | None = None, irreversible: bool = False, default_action: str | None = None`, and after the `weight` block add:

```python
        if confidence is not None:
            rec["confidence"] = confidence
        if irreversible:
            rec["irreversible"] = True
        if default_action:
            rec["default_action"] = default_action
```

In `cli/ask.py` add flags before parse:

```python
    ap.add_argument("--confidence", type=float, default=None,
                    help="0..1 sureness in the default action if unanswered")
    ap.add_argument("--irreversible", action="store_true",
                    help="destructive/one-way; never auto-proceeds on silence")
    ap.add_argument("--default", dest="default_action", default=None,
                    help="action taken if you never answer (stated in the final rung)")
```

After parse, validate and pass through:

```python
    if args.confidence is not None and not (0.0 <= args.confidence <= 1.0):
        ap.error("--confidence must be between 0 and 1")
```

Add `confidence=args.confidence, irreversible=args.irreversible, default_action=args.default_action` to the `add_commitment(...)` call.

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 195.
- [ ] **Step 5: Commit** — `git add lib/core.py cli/ask.py tests/test_sundial.py && git commit -m "feat(ask): --confidence/--irreversible/--default policy fields"`

### Task 8: `session_start` surfaces autonomy verdicts for exhausted asks

**Files:** Modify `hooks/session_start.py` (`build_block`); Test: `TestSessionStartHook`.
**Interfaces consumed:** `policy.autonomy_decision`, `policy.TIER_TABLE`, `policy.tier_of`; reads `core.DATA/"notified.json"`.

- [ ] **Step 1: Failing test** — add to `TestSessionStartHook`:

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

- [ ] **Step 2: Run FAIL** — "Escalation exhausted" absent.

- [ ] **Step 3: Implement** — in `hooks/session_start.py`, inside `build_block`, immediately BEFORE the `lines.append("\nThis is passive background awareness...")` line, insert:

```python
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
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
            if entry.get("count", 0) < policy.TIER_TABLE[policy.tier_of(c)]["rungs"]:
                continue  # ladder not exhausted — the human may still answer
            verdicts.append((c, policy.autonomy_decision(c, entry)))
        if verdicts:
            lines.append(f"\nEscalation exhausted, your call needed ({len(verdicts)}):")
            for c, v in verdicts[:10]:
                da = c.get("default_action")
                tail = f" → default: {da}" if da else ""
                lines.append(f"  - [{v['action'].upper()}] "
                             f"{str(c.get('text',''))[:120]}{tail} ({v['reason']})")
    except Exception:
        pass
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 196.
- [ ] **Step 5: Commit** — `git add hooks/session_start.py tests/test_sundial.py && git commit -m "feat(hook): surface autonomy verdicts for exhausted asks on session start"`

---

## SLICE ③ — Agent-authored rung messages

### Task 9: store `rungs`; `sundial ask --rung` (repeatable ≤3)

**Files:** Modify `lib/core.py`, `cli/ask.py`; Test: `TestCore`.
**Interfaces:** `core.add_commitment(..., rungs=None)` stores ≤3 strings.

- [ ] **Step 1: Failing test** — add to `TestCore`:

```python
    def test_add_commitment_stores_rungs(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply",
                                  rungs=["knock one", "knock two", "final call"])
        self.assertEqual(rec["rungs"], ["knock one", "knock two", "final call"])
        self.assertNotIn("rungs",
                         core.add_commitment("q2?", "+0m", kind="awaiting-reply"))
```

- [ ] **Step 2: Run FAIL** — unexpected keyword `rungs`.

- [ ] **Step 3: Implement** — add `rungs: list | None = None` to `add_commitment`'s signature (last param) and after the `default_action` block:

```python
        if rungs:
            rec["rungs"] = [str(r) for r in rungs][:3]
```

In `cli/ask.py` add:

```python
    ap.add_argument("--rung", action="append", dest="rungs", default=None,
                    help="pre-composed message for a rung (repeat ≤3, in order)")
```

After parse:

```python
    if args.rungs and len(args.rungs) > 3:
        ap.error("at most 3 --rung messages")
```

Add `rungs=args.rungs` to the `add_commitment(...)` call.

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 197.
- [ ] **Step 5: Commit** — `git add lib/core.py cli/ask.py tests/test_sundial.py && git commit -m "feat(ask): --rung stores agent-authored per-rung voice"`

### Task 10: watcher replays stored rungs

**Files:** `watcher/watcher.py` `pending_ping` already prefers `c["rungs"]` (path 1, written in Task 4). This task only adds the test proving it, since the code path already exists.
**Test:** `TestAbsenceClock`.

- [ ] **Step 1: Failing test** — add to `TestAbsenceClock`:

```python
    def test_pending_ping_uses_stored_rungs(self):
        c, now = self._c(60)
        c["rungs"] = ["my rung one", "my rung two", "my final"]
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertEqual(hit[0], 1)
        self.assertIn("my rung one", hit[1])

    def test_stored_final_rung_appends_default_action(self):
        c, now = self._c(60)
        c["rungs"] = ["r1", "r2", "final call"]
        c["default_action"] = "back up then halt"
        hit = watcher.pending_ping(c, self._entry(unseen=3000), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertIn("back up then halt", hit[1])

    def test_no_rungs_falls_back_to_pool(self):
        c, now = self._c(60)
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertNotIn("my rung one", hit[1])
```

- [ ] **Step 2: Run** — `python3 -m pytest tests/test_sundial.py::TestAbsenceClock -q -k "stored_rungs or stored_final or no_rungs"`. These should PASS immediately (path 1 was built in Task 4). If any fails, the Task-4 `pending_ping` is wrong — fix it there.

- [ ] **Step 3:** (no new impl — coverage task) confirm the whole suite: `python3 -m pytest tests/test_sundial.py -q` → 200.
- [ ] **Step 4: Commit** — `git add tests/test_sundial.py && git commit -m "test(watcher): stored-rung replay + default-action append"`

---

## SLICE ④ — SwiftBar instant-sync

### Task 11: `core.refresh_menubar()` + seam

**Files:** Modify `lib/core.py` (add `import subprocess`, helper + seam); Test: new class `TestMenubarSync`.
**Interfaces produced:** `core.refresh_menubar() -> None` (fail-safe); `core._menubar_spawn(cmd)` (seam).

- [ ] **Step 1: Failing test**:

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
        core._menubar_spawn = lambda cmd: (_ for _ in ()).throw(OSError("no swiftbar"))
        try:
            core.refresh_menubar()   # must swallow
        finally:
            core._menubar_spawn = orig
```

- [ ] **Step 2: Run FAIL** — no attribute `_menubar_spawn`.

- [ ] **Step 3: Implement** — add `import subprocess` to `lib/core.py`'s imports, then:

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

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 202.
- [ ] **Step 5: Commit** — `git add lib/core.py tests/test_sundial.py && git commit -m "feat(core): refresh_menubar — push SwiftBar to re-read on demand"`

### Task 12: watcher refreshes on state change (+ stub existing tests)

**Files:** Modify `watcher/watcher.py` (`run_cycle`), and `TestAbsenceClock.setUp`/`tearDown` to stub `core._menubar_spawn`; Test: `TestAbsenceClock`.

- [ ] **Step 1: Failing test** — first, in `TestAbsenceClock.setUp`, add (so the existing ~15 run_cycle tests never fire a real `open`):

```python
        self._orig_menubar = core._menubar_spawn
        core._menubar_spawn = lambda cmd: None
```

and in `tearDown`: `core._menubar_spawn = self._orig_menubar`. Then add the test:

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
                self.assertTrue(refreshed)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds, core._menubar_spawn) = orig
```

- [ ] **Step 2: Run FAIL** — `refreshed` empty.

- [ ] **Step 3: Implement** — in `run_cycle`, right after `prev = record_presence(snap, now)` add:

```python
    state_changed = (prev.get("state") != snap["state"])
```

In the return-nudge branch, after its `dirty = True`, add `state_changed = True`.
In the batch fire loop, after its `dirty = True`, add `state_changed = True`.
After each `opportunities.count_offer(today)` (both the meeting and build branches), add `state_changed = True`.
At the very end of `run_cycle`, after the `if dirty: core.write_json(NOTIFIED, notified)` block, add:

```python
    if state_changed or dirty:
        core.refresh_menubar()
```

(`state_changed` is a plain local, only ever flipped True; the offer branches are a bare `try`, so no `nonlocal` needed.)

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → 203.
- [ ] **Step 5: Commit** — `git add watcher/watcher.py tests/test_sundial.py && git commit -m "feat(watcher): refresh menu bar on fire / offer / presence change"`

### Task 13: CLI verbs + prompt hook refresh the menu bar

**Files:** Modify `cli/ask.py`, `cli/answered.py`, `cli/done.py`, `cli/remember.py`, `cli/decline.py`, `cli/allow.py`, `hooks/prompt_submit.py`; Test: `TestPromptSubmitHook`.

- [ ] **Step 1: Failing test** — add to `TestPromptSubmitHook`:

```python
    def test_disarm_refreshes_menubar(self):
        refreshed = []
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: refreshed.append(cmd)
        try:
            core.add_commitment("q?", "+0m", kind="awaiting-reply")
            prompt_submit.build_context(core, {"prompt": "hello",
                                               "transcript_path": None})
            self.assertTrue(refreshed)
        finally:
            core._menubar_spawn = orig
```

(Real signature is `build_context(core, data=None)` — first arg is the `core` module.)

- [ ] **Step 2: Run FAIL** — `refreshed` empty.

- [ ] **Step 3: Implement:**

`cli/ask.py`, `cli/answered.py`, `cli/done.py`, `cli/remember.py`: add `core.refresh_menubar()` as the last line of `main()` (each already imports `core`).

`cli/decline.py` and `cli/allow.py`: they import only `opportunities`. Add core to path + import at the top (after the existing `sys.path.insert(... "watcher")` block):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402
```

then add `core.refresh_menubar()` as the last line of `main()`.

`hooks/prompt_submit.py`: `build_context` starts with `closed = core.close_awaiting_detailed()`. After that assignment, add:

```python
    if closed:
        core.refresh_menubar()
```

- [ ] **Step 4: Run PASS** — `python3 -m pytest tests/test_sundial.py -q` → ~209 (all green).
- [ ] **Step 5: Commit** — `git add cli/ hooks/prompt_submit.py tests/test_sundial.py && git commit -m "feat(cli): every state-mutating verb + disarm refreshes the menu bar"`

---

## Final verification

- [ ] Full suite: `python3 -m pytest tests/ -q` → **all green (~209)**.
- [ ] Lint: `ruff check .` → clean.
- [ ] Manual drive: `sundial ask "x" --weight high --confidence 0.9 --default "do y"`; confirm the record has `weight/confidence/default_action`; confirm SwiftBar badge updates immediately after `sundial done <id>`.

## Self-review (author, rev 2)

- **Audit coverage:** S1 present-silence → deferred (gate is ≥0.95-only, Task 6). B1/B2 honesty → Task 4 (number-free tier copy + terminal contract). Mechanical B1 (test age) → Task 2 `_c(30)`. build_context signature → Task 13. decline/allow imports → Task 13. `_menubar_spawn` stub in existing tests → Task 12 setUp. Test counts → corrected (end ~209).
- **Backward-compat:** `normal` row == legacy; all new params default to omit-the-key; `tier_of`/`autonomy_decision`/`pending_ping` on an old record never KeyError; normal-tier message path (Task 4 path 4) is the original code verbatim.
- **Safety:** irreversible can never proceed (checked first, before confidence parse); gate is pure/total; verdicts are advisory to the agent, never executed by the watcher.
- **Deferred (documented):** present-silence consent, once `here_s` accrual is ripeness-gated and `"present"` excluded; reconciling the README "90-minute wall ceiling" wording with per-tier ceilings.
