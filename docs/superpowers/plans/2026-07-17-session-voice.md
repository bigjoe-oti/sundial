# Session-Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route ripe commitment fires to a claimed warm Claude session instead of a desktop popup, per spec `docs/superpowers/specs/2026-07-17-session-voice-design.md`.

**Architecture:** One routing decision in the watcher's fire loop (fresh claim → speak queue; stale → popup; rung 3 → both), claim/queue helpers in `lib/core.py`, automatic claim refresh in the prompt hook, a standing-duty line in the session-start hook. Composition stays procedural (agent-side); routing and bookkeeping are code.

**Tech Stack:** Python 3.11 stdlib, unittest + tempfile (house pattern).

## Global Constraints

- Repo `/Users/OTI_1/Desktop/AI-WallClock-Project`; work on branch `session-voice` off `main`; live install IS the checkout.
- Fail-safe contract: no new IO path may crash the watcher, a verb, or a hook — try/except to no-op.
- Test isolation: tempdir only; NEVER revert/delete/checkout anything under `data/` (incidents #3/#4). Suite: `python3 tests/test_sundial.py -v` (272 green at branch time — verify count before starting).
- **Incident-#5 rail:** any test/probe reaching run_cycle or delivery paths MUST stub `watcher.desktop_notify`, `watcher.chime`, `watcher.speak_final` (and `watcher._spawn`) to no-op recorders, restored in finally.
- Spec invariants (binding): one channel per fire (rung 3: both); rung accounting identical on both channels; stale claim ⇒ byte-identical to today; snooze/ceiling/caps apply BEFORE routing; claim TTL 3600s; queue prunes consumed >24h, cap 20 drop-oldest.
- Data contracts (exact): `data/session_claim.json` = `{"ts": iso-utc, "ttl_s": 3600, "session": str}`; `data/session_speak.json` = `{"queue": [{"cid","rung","message","text","ts","consumed"}]}`.
- TDD per task; commit per task; author config already set.

---

### Task 1: claim + queue helpers in core

**Files:**
- Modify: `lib/core.py` (new helpers near `snooze_active`, which lives in core since commit 267cf1a)
- Test: `tests/test_sundial.py` (new class `TestSessionClaim`)

**Interfaces:**
- Produces: `core.write_session_claim(data_dir=None, ttl_s=3600, session="cli") -> None` (fail-safe, writes claim with ts=now_utc); `core.session_claim_fresh(now, data_dir=None) -> bool` (missing/garbage/expired → False; same guard style as `core.snooze_active`); `core.append_session_speak(entry: dict, data_dir=None) -> None` (appends to queue, prunes consumed entries older than 24h, caps queue at 20 drop-oldest; fail-safe).

- [ ] **Step 1: failing tests**

```python
class TestSessionClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_claim_roundtrip_fresh(self):
        now = datetime.now(timezone.utc)
        core.write_session_claim(data_dir=self.data, ttl_s=3600)
        self.assertTrue(core.session_claim_fresh(now, data_dir=self.data))

    def test_claim_expired_missing_garbage(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))
        (self.data / "session_claim.json").write_text(json.dumps(
            {"ts": (now - timedelta(seconds=3700)).isoformat(), "ttl_s": 3600}))
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))
        (self.data / "session_claim.json").write_text("{broken")
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))

    def test_speak_append_prune_cap(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=25)).isoformat()
        core.write_json(self.data / "session_speak.json", {"queue": [
            {"cid": "old1", "rung": 1, "message": "m", "text": "t",
             "ts": old, "consumed": True}]})
        core.append_session_speak({"cid": "new1", "rung": 1, "message": "m",
                                   "text": "t", "ts": now.isoformat(),
                                   "consumed": False}, data_dir=self.data)
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual([e["cid"] for e in q], ["new1"])   # old consumed pruned
        for i in range(25):
            core.append_session_speak({"cid": f"c{i}", "rung": 1, "message": "m",
                                       "text": "t", "ts": now.isoformat(),
                                       "consumed": False}, data_dir=self.data)
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual(len(q), 20)                        # cap, drop-oldest
        self.assertEqual(q[-1]["cid"], "c24")
```

NOTE: match the test file's existing imports (json/timedelta/timezone present since the sprint). Unconsumed entries are NEVER pruned by age — only consumed ones.

- [ ] **Step 2: run, observe AttributeError failures** — `python3 tests/test_sundial.py -v 2>&1 | grep -A2 TestSessionClaim`
- [ ] **Step 3: implement** (in `lib/core.py`, after `snooze_active`; mirror its data_dir-defaulting and guard style):

```python
def write_session_claim(data_dir=None, ttl_s=3600, session="cli"):
    """Session heartbeat: 'a warm session is listening.' Fail-safe no-op."""
    try:
        d = Path(data_dir) if data_dir is not None else DATA
        write_json(d / "session_claim.json",
                   {"ts": now_utc().isoformat(), "ttl_s": float(ttl_s),
                    "session": str(session)})
    except Exception:
        pass


def session_claim_fresh(now, data_dir=None) -> bool:
    """True iff a live session claim exists. Missing/garbage/expired -> False."""
    try:
        d = Path(data_dir) if data_dir is not None else DATA
        c = read_json(d / "session_claim.json", None)
        ts = parse_iso(c.get("ts")) if isinstance(c, dict) else None
        ttl = c.get("ttl_s") if isinstance(c, dict) else None
        return (ts is not None and isinstance(ttl, (int, float))
                and (now - ts).total_seconds() < ttl)
    except Exception:
        return False


def append_session_speak(entry, data_dir=None):
    """Queue a routed fire for the claimed session. Prunes consumed entries
    older than 24h; hard cap 20 (drop-oldest). Fail-safe no-op."""
    try:
        d = Path(data_dir) if data_dir is not None else DATA
        p = d / "session_speak.json"
        cur = read_json(p, {})
        q = cur.get("queue") if isinstance(cur, dict) else None
        q = [e for e in q if isinstance(e, dict)] if isinstance(q, list) else []
        cutoff = now_utc() - timedelta(hours=24)
        def keep(e):
            if not e.get("consumed"):
                return True
            ts = parse_iso(e.get("ts"))
            return ts is not None and ts > cutoff
        q = [e for e in q if keep(e)]
        q.append(dict(entry))
        write_json(p, {"queue": q[-20:]})
    except Exception:
        pass
```

NOTE: check `timedelta` is imported in core.py (it is — used by `_attach_estimate` region); verify before assuming.

- [ ] **Step 4: green + full suite** — task tests pass; suite all green.
- [ ] **Step 5: commit** — `git add lib/core.py tests/test_sundial.py && git commit -m "feat(core): session claim + speak-queue helpers (session-voice T1)"`

---

### Task 2: watcher routing in the fire loop

**Files:**
- Modify: `watcher/watcher.py` (fire loop inside `run_cycle` — the loop that iterates the post-snooze-filter batch and calls `desktop_notify`; ALSO the return-nudge delivery site)
- Test: `tests/test_sundial.py` (new class `TestSessionRouting`)

**Interfaces:**
- Consumes: `core.session_claim_fresh(now, data_dir)`, `core.append_session_speak(entry, data_dir)` from T1.
- Produces: habit events `{"kind": "fire", ..., "channel": "session"|"desktop"}` (add the channel field to BOTH delivery sites' log_habit calls); queue entries per the data contract.

- [ ] **Step 1: failing tests** — read the current `run_cycle` FIRST (the sprint rewired it: snooze filter now runs pre-wait; return-nudge has its own delivery+breakthrough block). Then write tests using the reviewer-probe pattern (tempdir data, stubbed notifiers per incident-#5 rail, minimal commitment fixtures that `pending_ping`/`wall_ceiling_passed`/`policy.tier_of` accept — copy fixture shapes from the sprint's TestSnooze run_cycle tests):

```python
class TestSessionRouting(unittest.TestCase):
    # fixtures/stubs per TestSnooze pattern: repoint core.DATA to tempdir,
    # stub desktop_notify/chime/speak_final/_spawn to recorders, restore in finally.

    def test_fresh_claim_rung1_routes_to_queue_not_popup(self):
        # fresh claim + due commitment at rung 1, state away (past ladder gate)
        # -> session_speak.json gains 1 unconsumed entry with cid/rung/message,
        #    desktop_notify recorder EMPTY, entry["count"] advanced to 1,
        #    habit fire event has channel == "session"

    def test_stale_claim_pops_as_today(self):
        # claim absent -> desktop_notify recorder has 1 call, queue file absent
        #    or unchanged, habit channel == "desktop"

    def test_rung3_mirrors_both_channels(self):
        # fresh claim + commitment forced to rung 3 (past final offset/ceiling)
        # -> queue entry AND desktop_notify call, count == 3

    def test_snooze_holds_session_channel_too(self):
        # fresh claim + snooze active + non-breakthrough commitment
        # -> no queue entry, no popup, snooze-hold habit logged (existing behavior)

    def test_rung_accounting_parity(self):
        # two identical commitments, one cycle with fresh claim, one without
        # (two runs) -> notified.json entry shape/count identical across runs
```

Write these as real tests (the sprint's TestSnooze tests show exactly how to force ladder states — reuse their fixture constants). Every assertion above is binding.

- [ ] **Step 2: observe failures.**
- [ ] **Step 3: implement.** In the batch fire loop, replace the unconditional delivery block with routing:

```python
            claim = core.session_claim_fresh(fire_now, core.DATA)
            to_session = claim and rung < 3
            mirror = claim and rung == 3
            if to_session or mirror:
                core.append_session_speak({
                    "cid": c.get("id"), "rung": rung, "message": message,
                    "text": str(c.get("text", ""))[:120],
                    "ts": fire_now.isoformat(), "consumed": False},
                    core.DATA)
            if (not to_session) or mirror:
                desktop_notify("Sundial", message)
                chime(rung, state, audible)
                if rung == 3:
                    speak_final(message, audible,
                                force=(policy.tier_of(c) == "high"))
```

Entry/notified/dirty/state_changed bookkeeping and the log_habit call remain OUTSIDE this conditional, unchanged except `"channel": ("session" if to_session or mirror else "desktop")` added to the fire habit. Compute `claim` ONCE before the loop (single read per cycle), not per commitment. Apply the same routing to the return-nudge delivery site (it fires `desktop_notify` for awaiting-reply returns): fresh claim → queue (rung value: use the ripe rung), stale → popup; its snooze/breakthrough logic stays exactly as the sprint left it.

- [ ] **Step 4: green + full suite.**
- [ ] **Step 5: commit** — `"feat(watcher): route ripe fires to claimed warm session — rung 3 mirrors, ledgers identical (session-voice T2)"`

---

### Task 3: hooks — claim refresh + standing duty

**Files:**
- Modify: `hooks/prompt_submit.py` (in `main()` or `build_context` — refresh claim on every HUMAN prompt; skip machine events via the existing `is_machine_event` guard)
- Modify: `hooks/session_start.py` (context line: standing duty to arm the sentinel; plus surface unconsumed queue entries at session start)
- Test: `tests/test_sundial.py` (extend hook integration tests)

**Interfaces:**
- Consumes: `core.write_session_claim`, `core.session_claim_fresh`, queue file contract.
- Produces: session-start context block lines: `Session-voice: N message(s) queued — read data/session_speak.json, speak them time-situated, mark consumed.` and (always, when feature files exist or not) one standing-duty line: `Session-voice duty: arm a Monitor on data/session_speak.json (fallback: ScheduleWakeup 1200s+); refresh the claim each wake via ./bin/sundial-claim or core.write_session_claim.`

- [ ] **Step 1: failing tests** — (a) driving prompt_submit main() via stdin JSON (pattern exists from sprint T3 smoke) with tempdir data leaves a fresh `session_claim.json`; a machine-event prompt does NOT refresh it; (b) session_start context contains the queue line when an unconsumed entry exists, omits it when queue empty/absent. Read both hooks fully first; match their local style.
- [ ] **Step 2: observe failures.**
- [ ] **Step 3: implement** — claim refresh is 2 lines + try/except in the human-prompt path; session_start block mirrors how existing optional blocks append. Keep the standing-duty line SHORT (it rides every session).
- [ ] **Step 4: green + full suite.**
- [ ] **Step 5: commit** — `"feat(hooks): human prompts refresh session claim; session start surfaces speak queue + sentinel duty (session-voice T3)"`

---

### Task 4: docs + dogfood soak arming

**Files:**
- Modify: `README.md` (short "Session voice" paragraph under the honesty-rails/architecture area: what it is, the one-channel invariant, stale-claim fallback), `docs/ARCHITECTURE.md` (data-file table + routing note).
- No code.

- [ ] **Step 1: write both doc edits** (concise; cite the spec file).
- [ ] **Step 2: verify** — `grep -n "session_claim\|session_speak" README.md docs/ARCHITECTURE.md` ≥ 3 hits.
- [ ] **Step 3: arm the soak** — `./bin/sundial remember "session-voice soak: fires routing to warm sessions? claim fresh from real prompts? corrections on stale entries?" --due +3d --est 20m --bucket ops`
- [ ] **Step 4: commit** — `"docs: session-voice architecture + README; soak armed (session-voice T4)"`

---

### Task 5: QC gate

- [ ] Full suite green; count recorded.
- [ ] `/code-review` (high) on `main...session-voice`; fix CONFIRMED findings; re-review.
- [ ] Live verify: with the real session claim fresh (the controller session will have armed it), create a real short-due commitment, kickstart the watcher when it ripens per ladder, confirm the queue entry lands INSTEAD of a popup, then speak it in-session and mark consumed; close the commitment honestly. (Real ledger use is intentional dogfood — no synthetic instant closes.)
- [ ] Confidence report per task (95% gate; name assumptions: TTL constant, cap constants, procedural composition unverifiable by unit tests — verified by the soak instead).
- [ ] Merge to `main`; close the build estimate `1c603848` via `./bin/sundial done 1c603848`; update memory (project_wallclock + MEMORY.md hook line).

---

## Self-review notes

- Spec coverage: claim/queue files (T1), routing + rung-3 mirror + parity + snooze-holds-all (T2), refresh + surfacing + duty (T3), docs + soak (T4), gates (T5). Composition behavior is procedural by spec — tested via soak, stated in the confidence report.
- Anchors are post-sprint (`267cf1a`); every code task instructs read-the-current-function-first.
- Both delivery sites (batch fire loop AND return-nudge) get routing; T2 names both.
