# Awaiting-Reply Nudges (Wall Clock v1.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the agent asks a blocking question and the human walks away, escalate local desktop nudges at 10/20/50 minutes (chat lines at 10 and 50 via agent sleepers), disarm on any user prompt, and inject an ambient per-prompt clock.

**Architecture:** All durable logic rides the existing launchd watcher (`watcher.py`), which learns a per-item escalation ladder persisted in `notified.json`. A new `UserPromptSubmit` hook disarms pending items and injects "now + elapsed since last prompt" context. Two new CLI verbs (`ask`, `answered`) are thin wrappers over new `core` functions. In-chat rungs are agent protocol (background sleepers), not code.

**Tech Stack:** Python 3.11 stdlib only (`/usr/local/bin/python3` for the watcher, `python3` elsewhere), `unittest`, launchd, osascript. No dependencies, no network.

## Global Constraints

- Stdlib only; zero third-party imports (spec: "Local-first … No network, no deps").
- Every hook must exit 0 on any exception ("a clock bug must never block a prompt").
- All JSON writes go through `core.write_json` (atomic tmp+replace).
- Quiet hours 08:00–22:00 local, enforced only in `watcher.run_cycle` via existing `NOTIFY_START, NOTIFY_END = 8, 22`.
- Plain commitments keep the once-ever ping (max count 1). Ladder applies only to `kind == "awaiting-reply"`.
- Exact notification strings (spec ladder table):
  - Rung 1: `the owner — I'm blocked on: <text>`
  - Rung 2: `Still blocked (20m): <text>`
  - Rung 3: `Final nudge (50m): <text> — proceeding on my judgment or standing down.`
  - Plain items keep: `Due now: <text>`
- Rung offsets relative to `due_at`: `(0, 600, 2400)` seconds (= T0+10/20/50 min when due = T0+10m).
- Run tests with: `python3 tests/test_wallclock.py` (unittest, verbosity=2). All 19 existing tests must stay green.

---

### Task 1: Relative due parsing in core

**Files:**
- Modify: `lib/core.py:151-167` (`parse_due`; also add `re`, `timedelta` imports at top)
- Test: `tests/test_wallclock.py` (class `TestCore`)

**Interfaces:**
- Consumes: existing `core.parse_due(due_str) -> datetime|None`, `core.now_utc()`
- Produces: `parse_due` additionally accepts `"+NNm"` / `"+NNh"` (integer minutes/hours from now), returning an aware UTC datetime. All existing forms unchanged.

- [ ] **Step 1: Write the failing tests** — add to `TestCore` in `tests/test_wallclock.py`:

```python
    def test_parse_due_relative_minutes(self):
        before = core.now_utc()
        due = core.parse_due("+10m")
        self.assertIsNotNone(due)
        secs = (due - before).total_seconds()
        self.assertTrue(595 <= secs <= 605, f"expected ~600s, got {secs}")

    def test_parse_due_relative_hours(self):
        before = core.now_utc()
        due = core.parse_due("+2h")
        secs = (due - before).total_seconds()
        self.assertTrue(7195 <= secs <= 7205, f"expected ~7200s, got {secs}")

    def test_parse_due_relative_invalid(self):
        self.assertIsNone(core.parse_due("+m"))
        self.assertIsNone(core.parse_due("+5d"))   # only m/h supported
        self.assertIsNone(core.parse_due("10m"))   # must start with +
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestCore -v 2>&1 | tail -8`
Expected: `test_parse_due_relative_minutes` and `test_parse_due_relative_hours` FAIL (`AssertionError: unexpectedly None`); `test_parse_due_relative_invalid` may already pass.

- [ ] **Step 3: Implement** — in `lib/core.py`, change the imports line and `parse_due`:

```python
# top of file: extend the datetime import and add re
import re
from datetime import datetime, timedelta, timezone
```

```python
def parse_due(due_str: str | None):
    """Accept 'YYYY-MM-DD' (treated as end of that LOCAL day), a full ISO
    datetime, or a relative '+NNm' / '+NNh' offset from now.
    Return an aware UTC datetime, or None."""
    if not due_str:
        return None
    due_str = due_str.strip()
    m = re.fullmatch(r"\+(\d+)([mh])", due_str)
    if m:
        n = int(m.group(1))
        return now_utc() + timedelta(minutes=n if m.group(2) == "m" else n * 60)
    try:
        if len(due_str) == 10:  # date only
            d = datetime.fromisoformat(due_str)
            d = d.replace(tzinfo=tzinfo(), hour=23, minute=59, second=0)
            return d.astimezone(timezone.utc)
        d = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=tzinfo())
        return d.astimezone(timezone.utc)
    except ValueError:
        return None
```

- [ ] **Step 4: Run the full suite to verify green**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK`, 22 tests.

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_wallclock.py
git commit -m "feat(core): relative +NNm/+NNh due parsing"
```

---

### Task 2: Awaiting-reply commitments in core

**Files:**
- Modify: `lib/core.py:170-195` (`add_commitment`, new `close_awaiting` after `resolve_commitment`)
- Test: `tests/test_wallclock.py` (class `TestCore`)

**Interfaces:**
- Consumes: `core.load_commitments()`, `core.write_json`, Task 1's `parse_due`
- Produces:
  - `add_commitment(text, due_str=None, source="manual", kind="plain", session_id=None) -> dict` — writes `"kind"` only when != `"plain"`, `"session_id"` only when truthy (absent fields = old shape).
  - `close_awaiting(status="answered") -> int` — sets every open `kind=="awaiting-reply"` row to `status`, returns the count closed (0 = no write).

- [ ] **Step 1: Write the failing tests** — add to `TestCore`:

```python
    def test_add_commitment_awaiting_kind(self):
        rec = core.add_commitment("q?", "+10m", "agent-blocked",
                                  kind="awaiting-reply", session_id="sess-1")
        self.assertEqual(rec["kind"], "awaiting-reply")
        self.assertEqual(rec["session_id"], "sess-1")
        self.assertEqual(rec["status"], "open")

    def test_add_commitment_plain_shape_unchanged(self):
        rec = core.add_commitment("plain thing", "+10m")
        self.assertNotIn("kind", rec)
        self.assertNotIn("session_id", rec)

    def test_close_awaiting_closes_all_and_only_awaiting(self):
        core.add_commitment("q1?", "+10m", kind="awaiting-reply")
        core.add_commitment("q2?", "+10m", kind="awaiting-reply")
        plain = core.add_commitment("plain", "+10m")
        n = core.close_awaiting()
        self.assertEqual(n, 2)
        by_id = {c["id"]: c for c in core.load_commitments()}
        self.assertEqual(by_id[plain["id"]]["status"], "open")
        statuses = {c["status"] for c in core.load_commitments() if c.get("kind") == "awaiting-reply"}
        self.assertEqual(statuses, {"answered"})
        self.assertEqual(core.close_awaiting(), 0)  # idempotent, nothing left
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestCore -v 2>&1 | tail -8`
Expected: FAIL/ERROR — `add_commitment() got an unexpected keyword argument 'kind'` and `module 'core' has no attribute 'close_awaiting'`.

- [ ] **Step 3: Implement** — in `lib/core.py`, replace `add_commitment` and add `close_awaiting` directly after `resolve_commitment`:

```python
def add_commitment(text: str, due_str: str | None = None, source: str = "manual",
                   kind: str = "plain", session_id: str | None = None) -> dict:
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
    items.append(rec)
    write_json(COMMITMENTS, items)
    return rec
```

```python
def close_awaiting(status: str = "answered") -> int:
    """Close every open awaiting-reply commitment (any session). Returns count."""
    items = load_commitments()
    n = 0
    for c in items:
        if c.get("kind") == "awaiting-reply" and c.get("status") == "open":
            c["status"] = status
            n += 1
    if n:
        write_json(COMMITMENTS, items)
    return n
```

- [ ] **Step 4: Run the full suite to verify green**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK`, 25 tests.

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_wallclock.py
git commit -m "feat(core): awaiting-reply commitment kind + close_awaiting"
```

---

### Task 3: CLI verbs `ask` and `answered`

**Files:**
- Create: `cli/ask.py`
- Create: `cli/answered.py`
- Modify: `bin/wallclock` (dispatch case)

**Interfaces:**
- Consumes: Task 2's `core.add_commitment(..., kind="awaiting-reply", session_id=...)`, `core.close_awaiting()`
- Produces: `wallclock ask "text" [--due +10m] [--source agent-blocked] [--session ID]` printing `armed [<id>] …`; `wallclock answered [--quiet]` printing `closed N awaiting-reply item(s).` unless quiet. Both exit 0 on success; `answered --quiet` exits 0 even on internal error.

- [ ] **Step 1: Create `cli/ask.py`** (mirrors `cli/remember.py` structure):

```python
#!/usr/bin/env python3
"""ask -- arm an awaiting-reply commitment: the agent is blocked on the human."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Arm an awaiting-reply nudge.")
    ap.add_argument("text", help="the blocking question, summarized")
    ap.add_argument("--due", default="+10m",
                    help="+NNm/+NNh, YYYY-MM-DD, or ISO datetime (default +10m)")
    ap.add_argument("--source", default="agent-blocked", help="where it came from")
    ap.add_argument("--session", default=None, help="asking session id (informational)")
    args = ap.parse_args()

    rec = core.add_commitment(args.text, args.due, args.source,
                              kind="awaiting-reply", session_id=args.session)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    print(f"armed [{rec['id']}] {rec['text']}  (rung 1 due: {when})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `cli/answered.py`**:

```python
#!/usr/bin/env python3
"""answered -- disarm all open awaiting-reply commitments (human is back)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Disarm awaiting-reply nudges.")
    ap.add_argument("--quiet", action="store_true", help="hook mode: silent, always exit 0")
    args = ap.parse_args()

    try:
        n = core.close_awaiting()
    except Exception:
        if args.quiet:
            sys.exit(0)
        raise
    if not args.quiet:
        print(f"closed {n} awaiting-reply item(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add dispatch cases in `bin/wallclock`** — extend the `case` block:

```bash
case "$cmd" in
  now)      exec python3 "$ROOT/cli/now.py" "$@" ;;
  remember) exec python3 "$ROOT/cli/remember.py" "$@" ;;
  due)      exec python3 "$ROOT/cli/due.py" "$@" ;;
  done)     exec python3 "$ROOT/cli/done.py" "$@" ;;
  ask)      exec python3 "$ROOT/cli/ask.py" "$@" ;;
  answered) exec python3 "$ROOT/cli/answered.py" "$@" ;;
  *) echo "usage: wallclock {now|remember|due|done|ask|answered} [...]" >&2; exit 1 ;;
esac
```

- [ ] **Step 4: Smoke-test the verbs end to end** (uses the real data dir; cleans up after itself)

Run:
```bash
bin/wallclock ask "SMOKE: plan task 3" && bin/wallclock due && bin/wallclock answered && bin/wallclock due
```
Expected: `armed [<hex8>] SMOKE: plan task 3 (rung 1 due: <+10m local>)` → due list shows the item → `closed 1 awaiting-reply item(s).` → `nothing due.`

- [ ] **Step 5: Commit**

```bash
git add cli/ask.py cli/answered.py bin/wallclock
git commit -m "feat(cli): wallclock ask / answered verbs"
```

---

### Task 4: Watcher escalation ladder

**Files:**
- Modify: `watcher/watcher.py:39-59` (`run_cycle`; add `RUNG_OFFSETS`, `rung_messages`, `migrate_entry`, `pending_ping` above it)
- Test: `tests/test_wallclock.py` (new class `TestWatcherLadder`)

**Interfaces:**
- Consumes: `core.due_commitments(0)`, `core.parse_iso`, `core.read_json`, `core.write_json`, existing `desktop_notify`, `NOTIFIED`, `NOTIFY_START/END`
- Produces (all in `watcher.py`, importable for tests):
  - `RUNG_OFFSETS = (0, 600, 2400)` — seconds after `due_at`
  - `migrate_entry(value) -> dict` — legacy string → `{"count": 1, "last": value}`; dict passes through; anything else → `{"count": 0, "last": None}`
  - `pending_ping(commitment, entry, now_utc) -> tuple[int, str] | None` — (rung_number, message) of the single highest ripe un-sent rung, else None. Plain commitments use a one-rung ladder and the `Due now:` message.
  - `run_cycle(force=False)` — fires at most one notification per item per cycle, then persists `{"count": rung, "last": now_iso}`.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_wallclock.py` (top of file already inserts `lib` on `sys.path`; also insert the watcher dir):

```python
WATCHER_DIR = Path(__file__).resolve().parent.parent / "watcher"
sys.path.insert(0, str(WATCHER_DIR))

import watcher  # noqa: E402
```

```python
class TestWatcherLadder(unittest.TestCase):
    def _c(self, minutes_past_due, kind="awaiting-reply"):
        now = core.now_utc()
        c = {"id": "x1", "created_at": now.isoformat(),
             "due_at": (now - timedelta(minutes=minutes_past_due)).isoformat(),
             "text": "q?", "source": "t", "status": "open"}
        if kind != "plain":
            c["kind"] = kind
        return c, now

    def test_migrate_entry(self):
        self.assertEqual(watcher.migrate_entry("2026-01-01T00:00:00+00:00"),
                         {"count": 1, "last": "2026-01-01T00:00:00+00:00"})
        d = {"count": 2, "last": "x"}
        self.assertEqual(watcher.migrate_entry(d), d)
        self.assertEqual(watcher.migrate_entry(None), {"count": 0, "last": None})

    def test_rung_selection_by_elapsed(self):
        fresh = {"count": 0, "last": None}
        for minutes, expected_rung in [(0, 1), (5, 1), (10, 2), (39, 2), (40, 3), (120, 3)]:
            c, now = self._c(minutes)
            hit = watcher.pending_ping(c, fresh, now)
            self.assertIsNotNone(hit, f"at +{minutes}m")
            self.assertEqual(hit[0], expected_rung, f"at +{minutes}m")

    def test_rung_messages_exact(self):
        c, now = self._c(0)
        self.assertEqual(watcher.pending_ping(c, {"count": 0, "last": None}, now)[1],
                         "the owner — I'm blocked on: q?")
        c, now = self._c(15)
        self.assertEqual(watcher.pending_ping(c, {"count": 1, "last": None}, now)[1],
                         "Still blocked (20m): q?")
        c, now = self._c(45)
        self.assertEqual(
            watcher.pending_ping(c, {"count": 2, "last": None}, now)[1],
            "Final nudge (50m): q? — proceeding on my judgment or standing down.")

    def test_highest_ripe_rung_collapse(self):
        # overnight: all three rungs ripened while quiet; exactly ONE ping (rung 3)
        c, now = self._c(300)
        hit = watcher.pending_ping(c, {"count": 0, "last": None}, now)
        self.assertEqual(hit[0], 3)
        # and after it's recorded, nothing more ever fires
        self.assertIsNone(watcher.pending_ping(c, {"count": 3, "last": "x"}, now))

    def test_already_pinged_rung_stays_silent(self):
        c, now = self._c(5)  # only rung 1 ripe
        self.assertIsNone(watcher.pending_ping(c, {"count": 1, "last": "x"}, now))

    def test_plain_commitment_caps_at_one(self):
        c, now = self._c(300, kind="plain")
        hit = watcher.pending_ping(c, {"count": 0, "last": None}, now)
        self.assertEqual(hit, (1, "Due now: q?"))
        self.assertIsNone(watcher.pending_ping(c, {"count": 1, "last": "x"}, now))

    def test_run_cycle_fires_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            orig_notify = watcher.desktop_notify
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertEqual(fired, ["the owner — I'm blocked on: q?"])
                saved = core.read_json(watcher.NOTIFIED, {})
                (cid,) = saved.keys()
                self.assertEqual(saved[cid]["count"], 1)
                watcher.run_cycle(force=True)   # same cycle again: silent
                self.assertEqual(len(fired), 1)
            finally:
                core.DATA, core.COMMITMENTS, watcher.NOTIFIED = orig
                watcher.desktop_notify = orig_notify
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestWatcherLadder -v 2>&1 | tail -12`
Expected: ERROR — `module 'watcher' has no attribute 'migrate_entry'` / `'pending_ping'`.

- [ ] **Step 3: Implement** — in `watcher/watcher.py`, insert above `run_cycle` and replace `run_cycle`:

```python
RUNG_OFFSETS = (0, 600, 2400)  # seconds after due_at -> T0+10/20/50min when due=T0+10m


def rung_messages(text: str) -> tuple:
    return (
        f"the owner — I'm blocked on: {text}",
        f"Still blocked (20m): {text}",
        f"Final nudge (50m): {text} — proceeding on my judgment or standing down.",
    )


def migrate_entry(value) -> dict:
    """notified.json values: legacy bare ISO string -> {'count': 1, 'last': str}."""
    if isinstance(value, dict) and "count" in value:
        return value
    if isinstance(value, str):
        return {"count": 1, "last": value}
    return {"count": 0, "last": None}


def pending_ping(c: dict, entry: dict, now) -> "tuple[int, str] | None":
    """The single highest ripe, not-yet-sent rung for a commitment, or None.
    Plain commitments have a one-rung ladder (the once-ever ping)."""
    due = core.parse_iso(c.get("due_at"))
    if due is None or c.get("status") != "open":
        return None
    awaiting = c.get("kind") == "awaiting-reply"
    ladder = RUNG_OFFSETS if awaiting else (0,)
    elapsed = (now - due).total_seconds()
    ripe = 0
    for i, offset in enumerate(ladder, start=1):
        if elapsed >= offset:
            ripe = i
    if ripe <= entry.get("count", 0):
        return None
    text = c.get("text", "")
    message = rung_messages(text)[ripe - 1] if awaiting else f"Due now: {text}"
    return ripe, message


def run_cycle(force: bool = False) -> None:
    local = core.now_local()
    if not force and not (NOTIFY_START <= local.hour < NOTIFY_END):
        return  # quiet hours: stay silent
    now = core.now_utc()
    notified = core.read_json(NOTIFIED, {})
    if not isinstance(notified, dict):
        notified = {}
    dirty = False
    for c, _delta in core.due_commitments(0):  # overdue / due-now only
        entry = migrate_entry(notified.get(c["id"]))
        hit = pending_ping(c, entry, now)
        if hit is None:
            continue
        rung, message = hit
        desktop_notify("Wall Clock", message)
        notified[c["id"]] = {"count": rung, "last": now.isoformat()}
        dirty = True
    if dirty:
        core.write_json(NOTIFIED, notified)
```

(Note the deliberate behavior change: one notification per item, no more ">1 items" batch summary — ladders need per-item messages.)

- [ ] **Step 4: Run the full suite to verify green**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK`, 32 tests.

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_wallclock.py
git commit -m "feat(watcher): 10/20/50 escalation ladder for awaiting-reply items"
```

---

### Task 5: UserPromptSubmit hook (disarm + ambient clock)

**Files:**
- Create: `hooks/prompt_submit.py`
- Modify: `~/.claude/settings.json` (hooks registration — outside the repo)
- Test: `tests/test_wallclock.py` (new class `TestPromptSubmitHook`)

**Interfaces:**
- Consumes: Task 2's `core.close_awaiting()`; `core.read_json/write_json/parse_iso/now_utc/now_local/humanize_delta`; `core.DATA`
- Produces:
  - `prompt_submit.build_context(core) -> str` — pure-ish worker: closes awaiting items, stamps `data/last_prompt.json` (`{"ts": iso}`), returns the `<wall-clock-tick>…</wall-clock-tick>` string (first prompt ever = no elapsed line).
  - `__main__` emits `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": <str>}}` on stdout; any exception → exit 0 silently.

- [ ] **Step 1: Write the failing tests** — add to `tests/test_wallclock.py` (below the watcher import):

```python
HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import prompt_submit  # noqa: E402
```

```python
class TestPromptSubmitHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._orig = (core.DATA, core.COMMITMENTS)
        core.DATA, core.COMMITMENTS = d, d / "commitments.json"

    def tearDown(self):
        core.DATA, core.COMMITMENTS = self._orig
        self.tmp.cleanup()

    def test_first_prompt_no_elapsed(self):
        block = prompt_submit.build_context(core)
        self.assertIn("<wall-clock-tick>", block)
        self.assertIn("Now: ", block)
        self.assertNotIn("Elapsed", block)
        stamped = core.read_json(core.DATA / "last_prompt.json", {})
        self.assertIsNotNone(core.parse_iso(stamped.get("ts")))

    def test_second_prompt_has_elapsed_delta(self):
        past = (core.now_utc() - timedelta(minutes=47)).isoformat()
        core.write_json(core.DATA / "last_prompt.json", {"ts": past})
        block = prompt_submit.build_context(core)
        self.assertIn("Elapsed since your previous prompt: 47m.", block)

    def test_disarms_awaiting_items(self):
        core.add_commitment("q?", "+10m", kind="awaiting-reply")
        prompt_submit.build_context(core)
        open_awaiting = [c for c in core.load_commitments()
                         if c.get("kind") == "awaiting-reply" and c["status"] == "open"]
        self.assertEqual(open_awaiting, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestPromptSubmitHook -v 2>&1 | tail -6`
Expected: ERROR at import — `ModuleNotFoundError: No module named 'prompt_submit'`.

- [ ] **Step 3: Create `hooks/prompt_submit.py`**:

```python
#!/usr/bin/env python3
"""UserPromptSubmit hook: (1) disarm awaiting-reply nudges -- the human is
back; (2) ambient wall clock -- inject now + elapsed since the previous
prompt. Fail-safe by construction: any error exits 0 with no output, so a
clock bug can never block a prompt."""

import json
import sys
from pathlib import Path


def build_context(core) -> str:
    core.close_awaiting()

    last_path = core.DATA / "last_prompt.json"
    prev = core.read_json(last_path, {})
    prev_ts = core.parse_iso(prev.get("ts")) if isinstance(prev, dict) else None
    now = core.now_utc()
    core.write_json(last_path, {"ts": now.isoformat()})

    local = core.now_local()
    parts = [f"Now: {local:%a %d %b %Y, %I:%M %p} ({local.tzname()})."]
    if prev_ts is not None:
        delta = core.humanize_delta((now - prev_ts).total_seconds())
        parts.append(f"Elapsed since your previous prompt: {delta}.")
    return "<wall-clock-tick>" + " ".join(parts) + "</wall-clock-tick>"


def main():
    if not sys.stdin.isatty():
        sys.stdin.read()  # consume hook payload; content unused

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import core

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_context(core),
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block a prompt because of a clock bug.
        sys.exit(0)
```

- [ ] **Step 4: Run the full suite to verify green**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK`, 35 tests.

- [ ] **Step 5: Register the hook** — in `~/.claude/settings.json`, add a `UserPromptSubmit` key beside the existing `SessionStart` inside `"hooks"`:

```json
"UserPromptSubmit": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/sundial/hooks/prompt_submit.py"
      }
    ]
  }
]
```

- [ ] **Step 6: Verify the hook script end-to-end as the harness will run it**

Run: `echo '{"session_id":"test"}' | python3 hooks/prompt_submit.py; echo "(exit $?)"`
Expected: one JSON line containing `"additionalContext": "<wall-clock-tick>Now: …` and `(exit 0)`. Run it twice — second run must include `Elapsed since your previous prompt:`.

- [ ] **Step 7: Commit**

```bash
git add hooks/prompt_submit.py tests/test_wallclock.py
git commit -m "feat(hooks): UserPromptSubmit disarm + ambient per-prompt clock"
```

---

### Task 6: Live verification, protocol memory, docs

**Files:**
- Modify: `README.md` (commands + hook sections)
- Modify: `~/.claude/projects/<project-slug>/memory/the-project-memory.md` (status refresh)
- Create: `~/.claude/projects/<project-slug>/memory/the-blocking-question-protocol memory` (+ index line in that dir's `MEMORY.md`)

**Interfaces:**
- Consumes: everything above, plus the live launchd watcher (already bootstrapped).

- [ ] **Step 1: Live ladder smoke test** (real data dir; the launchd watcher is loaded, so use `--force` manually rather than waiting):

```bash
bin/wallclock ask "SMOKE: v1.5 ladder"  --due +0m
/usr/local/bin/python3 watcher/watcher.py --force        # expect popup: rung 1
python3 - <<'EOF'
import json, pathlib
n = json.loads((pathlib.Path("data") / "notified.json").read_text())
print(n)
assert any(isinstance(v, dict) and v.get("count") == 1 for v in n.values())
EOF
bin/wallclock answered
```
Expected: desktop popup `the owner — I'm blocked on: SMOKE: v1.5 ladder`; printed dict shows `"count": 1`; `closed 1 awaiting-reply item(s).`

- [ ] **Step 2: Update `README.md`** — in the Commands block add the two verbs, and in "How the hook wires in" add one sentence:

```
bin/wallclock ask "text" [--due +10m]      # arm an awaiting-reply nudge (agent is blocked)
bin/wallclock answered                     # disarm all awaiting-reply nudges
```

```
`hooks.UserPromptSubmit -> hooks/prompt_submit.py` disarms awaiting-reply
nudges on every user prompt and injects the ambient clock tick (now + elapsed
since the previous prompt).
```

Also delete the "Passive only." bullet from Honesty rails (the ladder makes v1.5 deliberately non-passive) and note the escalation in one line:

```
- Nudges escalate 10/20/50 min for awaiting-reply items only; max three pings,
  quiet hours respected, one catch-up ping after silence, never a burst.
```

- [ ] **Step 3: Write the agent protocol memory** — create `the-blocking-question-protocol memory` in the memory dir with frontmatter `type: feedback`, body: when asking a truly blocking question → `wallclock ask "<summary>"` (stretch `--due` by judgment), launch `sleep 600` and `sleep 3000` background sleepers; 10-min wake = one short in-chat line if still open; 50-min wake = close item, then proceed autonomously stating the assumption, or stand down by confidence; never kill sleepers on reply. Add the index line to `MEMORY.md`.

- [ ] **Step 4: Full suite + commit**

```bash
python3 tests/test_wallclock.py 2>&1 | tail -3   # expect OK, 35 tests
git add README.md
git commit -m "docs: v1.5 verbs, prompt-submit hook, escalation rails"
```

---

## Deferred (explicitly NOT in this plan)

- Self-estimation engine over the ledger (v2 — has its own future spec).
