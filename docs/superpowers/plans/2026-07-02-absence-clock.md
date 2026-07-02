# Sundial Part 1: Absence-Clock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The watcher's escalation ladder measures absence, not time: three-state presence (HERE/ELSEWHERE/AWAY) drives a weighted unseen-clock, a return-nudge, state-aware voice, escalating chimes, and an opt-in spoken final rung.

**Architecture:** A new pure-parser module `watcher/presence.py` senses idle time (ioreg) and frontmost app (lsappinfo) and derives a state. `watcher/watcher.py`'s `run_cycle` samples presence once per cycle, accrues per-item `unseen_s`/`here_s` in `notified.json`, ripens rungs on unseen-time with a 90-minute wall ceiling, holds popups while HERE, fires a return-nudge on AWAY→back transitions, forks message pools by state, and plays chimes via afplay. Every sensor failure degrades gracefully toward v1.5 behavior.

**Tech Stack:** Python 3.11 stdlib only. macOS built-ins: `/usr/sbin/ioreg`, `/usr/bin/lsappinfo`, `/usr/bin/afplay`, `/usr/bin/say`. `unittest` (run: `python3 tests/test_wallclock.py`).

## Global Constraints

- Stdlib only; zero third-party imports. All JSON via `core.write_json`.
- A LIVE launchd daemon runs `watcher/watcher.py` every 600s against the real data dir: **never leave watcher.py or presence.py in a broken intermediate state** — complete each file's edit in one pass, then test. NEVER run hooks, watcher cycles, or any code against the real `data/` dir in tests — redirect `core.DATA`, `core.COMMITMENTS`, `watcher.NOTIFIED` (and new paths) to temp dirs (existing tests show the pattern).
- Work on branch `sundial-absence-clock` (Task 1 creates it from master).
- Constants, exact values from spec: `PRESENCE_IDLE_S = 180`; `ELSEWHERE_WEIGHT = 0.5`; `UNSEEN_OFFSETS = (600, 1200, 3000)`; `WALL_CEILING_S = 5400`; chime map rung1 `Tink` 0.35, rung2 `Glass` 0.5, rung3/ceiling `Hero` 0.6, return `Purr` 0.35; ELSEWHERE chime volume × 0.6; HERE → no chime.
- Privacy rail: idle duration + frontmost app NAME only. No window titles, no content.
- Every rung-3 / final message in EVERY pool must state the autonomy consequence.
- Suite baseline: 40 tests green. Each task's expected count is stated in its steps.
- Degrade contract: `front_app` unavailable → HERE/ELSEWHERE collapse to `"present"` (pauses ladder like HERE, popups not held-forever thanks to ceiling); `idle_seconds` unavailable → state `None` → **v1.5 wall-clock semantics, byte-identical** (legacy `RUNG_OFFSETS = (0, 600, 2400)` path relative to `due_at`).

---

### Task 1: Presence sensors (`watcher/presence.py`)

**Files:**
- Create: `watcher/presence.py`
- Test: `tests/test_wallclock.py` (new class `TestPresence`; also add `import presence` beside the existing `import watcher`)

**Interfaces:**
- Consumes: nothing project-internal (pure module; subprocess wrappers only).
- Produces (exact, later tasks rely on these):
  - `presence.PRESENCE_IDLE_S = 180`
  - `presence.parse_idle(ioreg_text: str) -> float | None`
  - `presence.parse_front(lsappinfo_text: str) -> str | None`
  - `presence.idle_seconds() -> float | None` (subprocess wrapper, never raises)
  - `presence.front_app() -> str | None` (subprocess wrapper, never raises)
  - `presence.cli_apps(data_dir) -> tuple` (defaults + optional `cli_apps.txt` lines)
  - `presence.derive_state(idle: float | None, front: str | None, cli: tuple) -> str | None` — returns `"here" | "elsewhere" | "away" | "present" | None`

- [ ] **Step 1: Create branch**

```bash
cd <private-dev-repo> && git checkout -b sundial-absence-clock
```

- [ ] **Step 2: Write the failing tests** — in `tests/test_wallclock.py`, add below the existing `import prompt_submit` block:

```python
import presence  # noqa: E402  (watcher dir already on sys.path)
```

and the test class:

```python
class TestPresence(unittest.TestCase):
    IOREG_SAMPLE = (
        '    | |   "HIDParameters" = {...}\n'
        '    | |   "HIDIdleTime" = 45000000000\n'
    )
    LSAPPINFO_SAMPLE = (
        '"ASN:0x0-0x12f12f-Figma:" info:\n'
        '    "LSDisplayName"="Figma"\n'
        '    "LSBundlePath"="/Applications/Figma.app"\n'
    )

    def test_parse_idle(self):
        self.assertEqual(presence.parse_idle(self.IOREG_SAMPLE), 45.0)
        self.assertIsNone(presence.parse_idle("no idle line here"))
        self.assertIsNone(presence.parse_idle(""))

    def test_parse_front(self):
        self.assertEqual(presence.parse_front(self.LSAPPINFO_SAMPLE), "Figma")
        self.assertIsNone(presence.parse_front("garbage"))
        self.assertIsNone(presence.parse_front(""))

    def test_cli_apps_default_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            apps = presence.cli_apps(Path(d))
            self.assertIn("Terminal", apps)
            self.assertIn("iTerm2", apps)
            (Path(d) / "cli_apps.txt").write_text("MyTerm\n\nGhostty\n")
            apps2 = presence.cli_apps(Path(d))
            self.assertIn("MyTerm", apps2)
            self.assertIn("Terminal", apps2)  # defaults kept

    def test_derive_state_truth_table(self):
        cli = ("Terminal", "iTerm2")
        self.assertEqual(presence.derive_state(300.0, "Figma", cli), "away")
        self.assertEqual(presence.derive_state(10.0, "Figma", cli), "elsewhere")
        self.assertEqual(presence.derive_state(10.0, "Terminal", cli), "here")
        self.assertEqual(presence.derive_state(10.0, None, cli), "present")
        self.assertIsNone(presence.derive_state(None, "Figma", cli))
        self.assertEqual(presence.derive_state(180.0, "Terminal", cli), "away")  # boundary: >= is away
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestPresence -v 2>&1 | tail -6`
Expected: ERROR at import — `ModuleNotFoundError: No module named 'presence'`.

- [ ] **Step 4: Create `watcher/presence.py`**

```python
#!/usr/bin/env python3
"""Sundial presence sensing — zero-dep, privacy-bounded.

Reads two macOS built-ins: HIDIdleTime (seconds since last keyboard/mouse
input) via ioreg, and the frontmost application NAME via lsappinfo. Nothing
else: no window titles, no input content, nothing leaves the machine.

States: "here" (input recent AND a CLI app is frontmost — the human can see
the chat), "elsewhere" (input recent, other app frontmost — busy, hasn't
seen the chat), "away" (no input for PRESENCE_IDLE_S), "present" (input
recent but frontmost unknown — 2-state degrade), None (idle unknown — full
degrade; callers must fall back to wall-clock semantics)."""

import re
import subprocess
from pathlib import Path

IOREG = "/usr/sbin/ioreg"
LSAPPINFO = "/usr/bin/lsappinfo"
PRESENCE_IDLE_S = 180

DEFAULT_CLI_APPS = (
    "Terminal", "iTerm2", "Ghostty", "Warp", "Alacritty", "kitty",
    "Visual Studio Code", "Code", "Cursor",
)

_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
_FRONT_RE = re.compile(r'"LSDisplayName"\s*=\s*"([^"]+)"')


def parse_idle(ioreg_text: str):
    m = _IDLE_RE.search(ioreg_text or "")
    return int(m.group(1)) / 1_000_000_000 if m else None


def parse_front(lsappinfo_text: str):
    m = _FRONT_RE.search(lsappinfo_text or "")
    return m.group(1) if m else None


def _run(cmd):
    try:
        r = subprocess.run(cmd, timeout=5, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def idle_seconds():
    return parse_idle(_run([IOREG, "-c", "IOHIDSystem", "-d", "4"]))


def front_app():
    asn_line = _run([LSAPPINFO, "front"]).strip()
    if not asn_line:
        return None
    return parse_front(_run([LSAPPINFO, "info", "-only", "name", asn_line]))


def cli_apps(data_dir) -> tuple:
    extra = ()
    try:
        raw = (Path(data_dir) / "cli_apps.txt").read_text(encoding="utf-8")
        extra = tuple(line.strip() for line in raw.splitlines() if line.strip())
    except OSError:
        pass
    return DEFAULT_CLI_APPS + extra


def derive_state(idle, front, cli) -> "str | None":
    if idle is None:
        return None
    if idle >= PRESENCE_IDLE_S:
        return "away"
    if front is None:
        return "present"
    return "here" if front in cli else "elsewhere"
```

- [ ] **Step 5: Run the full suite to verify green**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK`, 45 tests (40 + 5 new — count the methods you added; adjust this number to the actual method count and keep it consistent below).

- [ ] **Step 6: Sanity-check the real sensors once (read-only, harmless)**

Run: `python3 -c "import sys; sys.path.insert(0,'watcher'); import presence; print(presence.idle_seconds(), presence.front_app())"`
Expected: a small float and an app name (e.g. `1.2 Terminal`). If either prints `None` on this Mac, STOP and report DONE_WITH_CONCERNS naming which sensor failed.

- [ ] **Step 7: Commit**

```bash
git add watcher/presence.py tests/test_wallclock.py
git commit -m "feat(presence): idle + frontmost-app sensors, three-state derivation"
```

---

### Task 2: State-aware voice pools (pure additions)

**Files:**
- Modify: `watcher/watcher.py` (extend pools + generalize `pick_message`)
- Test: `tests/test_wallclock.py` (extend `TestWatcherLadder`)

**Interfaces:**
- Consumes: existing `RUNG_POOLS`, `PLAIN_POOL`, `owner_name()`.
- Produces:
  - `ELSEWHERE_POOLS: tuple[tuple, tuple, tuple]` — app-aware rung pools using `{owner}/{text}/{app}`.
  - `RETURN_POOL: tuple` — welcome-back pool using `{owner}/{text}/{away_m}`.
  - `pick_message(commitment_id: str, pool: tuple, **fields) -> str` — formats via `str.format_map`; on ANY formatting error falls back to `pool[0]`, and if that also fails returns `fields.get("text", "")`. (Signature change: `text` moves into `**fields` — existing calls become `pick_message(cid, pool, text=text)`.)

- [ ] **Step 1: Write the failing tests** — add to `TestWatcherLadder`:

```python
    def test_elsewhere_pools_have_app_and_rung3_consequence(self):
        for pool in watcher.ELSEWHERE_POOLS:
            for entry in pool:
                s = entry.format(owner="O", text="T", app="Figma")
                self.assertIn("T", s)
        for entry in watcher.ELSEWHERE_POOLS[2]:
            s = entry.format(owner="O", text="T", app="Figma")
            self.assertTrue(any(k in s for k in
                ("judgment", "deciding without you", "my call now",
                 "take it from here", "standing down")), s)

    def test_return_pool_formats(self):
        for entry in watcher.RETURN_POOL:
            s = entry.format(owner="O", text="T", away_m=25)
            self.assertIn("T", s)

    def test_pick_message_kwargs_and_fallback(self):
        msg = watcher.pick_message("00000000", watcher.ELSEWHERE_POOLS[0],
                                   text="T", app="Figma")
        self.assertIn("T", msg)
        # missing 'app' → format fails → falls back to pool[0]; pool[0] also
        # needs app → final fallback: the text itself
        bad = watcher.pick_message("00000000", ("{never_defined}: {text}",),
                                   text="T")
        self.assertEqual(bad, "T")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestWatcherLadder -v 2>&1 | tail -8`
Expected: ERROR — `module 'watcher' has no attribute 'ELSEWHERE_POOLS'`.

- [ ] **Step 3: Implement** — in `watcher/watcher.py`, add below `PLAIN_POOL`:

```python
ELSEWHERE_POOLS = (
    (   # rung 1 — cheeky, app-aware
        "{owner}, I know you're busy with {app} — I won't take much of your time, I just need your call on: {text}",
        "I can see {app} has you. One opinion and I'll vanish: {text}",
        "Whatever {app} is doing, it can spare you ten seconds: {text}",
    ),
    (   # rung 2
        "Second knock, {owner} — {app} is lovely, but this is still waiting: {text}",
        "Me and {app} are now competing for you (20m): {text}",
        "{owner}, blink twice if {app} is holding you hostage (20m): {text}",
    ),
    (   # rung 3 — final; every entry states the autonomy consequence
        "{owner}, {app} can wait one beat — final call on: {text} — deciding without you otherwise.",
        "Last call, {owner} (50m): {text} — I take it from here or park it.",
        "Even {app} thinks you should answer this (50m): {text} — my call now, or the shelf.",
    ),
)

RETURN_POOL = (
    "While you were away ({away_m}m): {text}",
    "Welcome back, {owner}. This ripened in your absence: {text}",
    "You were gone {away_m} minutes. The question aged well: {text}",
)
```

and replace `pick_message` with:

```python
def pick_message(commitment_id: str, pool: tuple, **fields) -> str:
    """Deterministic per-item voice: same commitment always gets the same
    line. Formatting is fail-safe: a bad template falls back to the pool's
    classic first entry, then to the bare text."""
    try:
        idx = int(commitment_id, 16) % len(pool)
    except (ValueError, TypeError):
        idx = 0
    fields.setdefault("owner", owner_name())
    for candidate in (pool[idx], pool[0]):
        try:
            return candidate.format(**fields)
        except (KeyError, IndexError, ValueError):
            continue
    return str(fields.get("text", ""))
```

then update the two existing call sites in `pending_ping`:

```python
    message = (pick_message(cid, RUNG_POOLS[ripe - 1], text=text) if awaiting
               else pick_message(cid, PLAIN_POOL, text=text))
```

- [ ] **Step 4: Run the full suite**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK` (Task 1 count + 3).

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_wallclock.py
git commit -m "feat(watcher): elsewhere/return voice pools, fail-safe pick_message kwargs"
```

---

### Task 3: Unseen-clock accounting, ripeness, HERE-hold

**Files:**
- Modify: `watcher/watcher.py` (constants, `migrate_entry`, `pending_ping`, `run_cycle`)
- Test: `tests/test_wallclock.py` (new class `TestAbsenceClock`)

**Interfaces:**
- Consumes: Task 1's `presence` module, Task 2's pools/`pick_message`.
- Produces:
  - Constants: `UNSEEN_OFFSETS = (600, 1200, 3000)`, `ELSEWHERE_WEIGHT = 0.5`, `WALL_CEILING_S = 5400`, `CYCLE_S = 600`.
  - `sample_presence() -> dict` — `{"state": str|None, "idle_s": float|None, "front_app": str|None}` (tests monkeypatch THIS, never subprocess).
  - `accrue(entry: dict, state: str|None, now, created) -> None` — mutates entry in place (`unseen_s`, `here_s`, `last_cycle`).
  - `ripe_rung(c: dict, entry: dict, now, state: str|None) -> int` — 0..3; encapsulates unseen thresholds, ceiling, legacy degrade, plain cap.
  - `pending_ping(c, entry, now, state, app) -> tuple[int, str] | None` — new signature (state/app thread through for pool choice).
  - `migrate_entry` now also defaults `unseen_s: 0.0`, `here_s: 0.0`, `last_cycle: None` (coercing non-numeric values to 0.0).
  - `run_cycle(force=False)` behavior: samples presence once; accrues every open awaiting item every cycle (persists even without a ping); holds pings while state == "here" unless the wall ceiling has passed.

- [ ] **Step 1: Write the failing tests** — add the class:

```python
class TestAbsenceClock(unittest.TestCase):
    def _c(self, minutes_since_ask, kind="awaiting-reply"):
        now = core.now_utc()
        created = now - timedelta(minutes=minutes_since_ask)
        c = {"id": "a0000001", "created_at": created.isoformat(),
             "due_at": (created + timedelta(minutes=10)).isoformat(),
             "text": "q?", "source": "t", "status": "open"}
        if kind != "plain":
            c["kind"] = kind
        return c, now

    def _entry(self, unseen=0.0, here=0.0, count=0):
        return {"count": count, "last": None, "unseen_s": unseen,
                "here_s": here, "last_cycle": None}

    def test_accrue_by_state(self):
        c, now = self._c(30)
        for state, unseen, here in (("away", 600.0, 0.0),
                                    ("elsewhere", 300.0, 0.0),
                                    ("here", 0.0, 600.0),
                                    ("present", 0.0, 600.0)):
            e = self._entry()
            e["last_cycle"] = (now - timedelta(seconds=600)).isoformat()
            watcher.accrue(e, state, now, core.parse_iso(c["created_at"]))
            self.assertAlmostEqual(e["unseen_s"], unseen, delta=1.0, msg=state)
            self.assertAlmostEqual(e["here_s"], here, delta=1.0, msg=state)
            self.assertEqual(e["last_cycle"], now.isoformat())

    def test_accrue_sleep_gap_counts_away(self):
        c, now = self._c(60)
        e = self._entry()
        e["last_cycle"] = (now - timedelta(seconds=3600)).isoformat()  # 6x cycle
        watcher.accrue(e, "here", now, core.parse_iso(c["created_at"]))
        self.assertAlmostEqual(e["unseen_s"], 3600.0, delta=1.0)  # slept -> away

    def test_ripe_on_unseen_thresholds(self):
        c, now = self._c(60)
        for unseen, expected in ((0, 0), (599, 0), (600, 1), (1199, 1),
                                 (1200, 2), (2999, 2), (3000, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"unseen={unseen}")

    def test_wall_ceiling_forces_final_even_here(self):
        c, now = self._c(91)  # 91 wall-minutes since ask
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "here"), 3)

    def test_legacy_degrade_matches_v15(self):
        c, now = self._c(60)  # due 50 min ago -> legacy rung 3 (2400s past due)
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, None), 3)
        c2, now2 = self._c(15)  # due 5 min ago -> legacy rung 1
        self.assertEqual(watcher.ripe_rung(c2, self._entry(), now2, None), 1)

    def test_plain_unchanged(self):
        c, now = self._c(300, kind="plain")
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "away"), 1)
        self.assertEqual(
            watcher.ripe_rung(c, self._entry(count=1), now, "away"), 0)

    def test_pending_ping_elsewhere_uses_app_pool(self):
        c, now = self._c(60)
        hit = watcher.pending_ping(c, self._entry(unseen=600), now,
                                   "elsewhere", "Figma")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 1)
        self.assertIn("Figma", hit[1])

    def test_run_cycle_holds_while_here_and_accrues(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])  # held while HERE
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertIn(rec["id"], saved)      # ...but accrual persisted
                self.assertIn("here_s", saved[rec["id"]])
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify) = orig

    def test_run_cycle_fires_when_away_and_ripe(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "away",
                                               "idle_s": 999.0,
                                               "front_app": None}
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify) = orig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestAbsenceClock -v 2>&1 | tail -10`
Expected: ERRORs — `module 'watcher' has no attribute 'accrue'` / `'ripe_rung'` / `'sample_presence'`.

- [ ] **Step 3: Implement** — in `watcher/watcher.py`:

Add imports/constants near the top (below `import core`):

```python
import presence  # noqa: E402  (same directory)

UNSEEN_OFFSETS = (600, 1200, 3000)   # 10/20/50 min of not-seeing-the-chat
ELSEWHERE_WEIGHT = 0.5               # two busy minutes = one absent minute
WALL_CEILING_S = 5400                # 90 min: final rung fires regardless
CYCLE_S = 600
PRESENCE_FILE = core.DATA / "presence.json"
```

Extend `migrate_entry` (replace the function):

```python
def migrate_entry(value) -> dict:
    """notified.json values: legacy bare ISO string -> {'count': 1, 'last': str}.
    Non-int counts and missing absence fields degrade to fresh defaults so
    callers never guard against bad types."""
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        e = dict(value)
    elif isinstance(value, str):
        e = {"count": 1, "last": value}
    else:
        e = {"count": 0, "last": None}
    for k in ("unseen_s", "here_s"):
        if not isinstance(e.get(k), (int, float)):
            e[k] = 0.0
    e.setdefault("last_cycle", None)
    return e
```

Add the three new functions above `pending_ping`:

```python
def sample_presence() -> dict:
    """One presence snapshot per cycle. Tests monkeypatch THIS function."""
    idle = presence.idle_seconds()
    front = presence.front_app() if idle is not None else None
    state = presence.derive_state(idle, front, presence.cli_apps(core.DATA))
    return {"state": state, "idle_s": idle, "front_app": front}


def accrue(entry: dict, state, now, created) -> None:
    """Advance an item's unseen/here clocks by the gap since last cycle.
    A gap far beyond the cycle interval means the machine slept: sleep
    counts as away. HERE and PRESENT pause the unseen clock."""
    prev = core.parse_iso(entry.get("last_cycle")) or created
    gap = max(0.0, (now - prev).total_seconds())
    eff = "away" if gap > 2 * CYCLE_S else state
    if eff == "away":
        entry["unseen_s"] = entry.get("unseen_s", 0.0) + gap
    elif eff == "elsewhere":
        entry["unseen_s"] = entry.get("unseen_s", 0.0) + gap * ELSEWHERE_WEIGHT
    elif eff in ("here", "present"):
        entry["here_s"] = entry.get("here_s", 0.0) + gap
    # state None: no accrual — legacy wall-clock path handles ripeness
    entry["last_cycle"] = now.isoformat()


def ripe_rung(c: dict, entry: dict, now, state) -> int:
    """Highest ripe rung index (0 = nothing ripe). Encapsulates: plain cap,
    legacy degrade (state None -> v1.5 wall semantics), unseen thresholds,
    and the 90-minute wall ceiling."""
    if c.get("kind") != "awaiting-reply":
        due = core.parse_iso(c.get("due_at"))
        if due is None:
            return 0
        return 1 if (now - due).total_seconds() >= 0 else 0
    if state is None:  # full degrade: v1.5 behavior, offsets relative to due
        due = core.parse_iso(c.get("due_at"))
        if due is None:
            return 0
        elapsed = (now - due).total_seconds()
        ripe = 0
        for i, off in enumerate(RUNG_OFFSETS, start=1):
            if elapsed >= off:
                ripe = i
        return ripe
    created = core.parse_iso(c.get("created_at")) or core.parse_iso(c.get("due_at"))
    if created is None:
        return 0
    if (now - created).total_seconds() >= WALL_CEILING_S:
        return 3
    ripe = 0
    for i, th in enumerate(UNSEEN_OFFSETS, start=1):
        if entry.get("unseen_s", 0.0) >= th:
            ripe = i
    return ripe
```

Replace `pending_ping` (new signature; keeps status/open checks):

```python
def pending_ping(c: dict, entry: dict, now, state, app) -> "tuple[int, str] | None":
    """The single highest ripe, not-yet-sent rung for a commitment, or None."""
    if c.get("status") != "open" or core.parse_iso(c.get("due_at")) is None:
        return None
    ripe = ripe_rung(c, entry, now, state)
    if ripe <= entry.get("count", 0):
        return None
    text, cid = c.get("text", ""), c.get("id", "")
    if c.get("kind") != "awaiting-reply":
        return ripe, pick_message(cid, PLAIN_POOL, text=text)
    if state == "elsewhere" and app:
        pool = ELSEWHERE_POOLS[ripe - 1]
        return ripe, pick_message(cid, pool, text=text, app=app)
    return ripe, pick_message(cid, RUNG_POOLS[ripe - 1], text=text)
```

Replace `run_cycle`:

```python
def run_cycle(force: bool = False) -> None:
    local = core.now_local()
    if not force and not (NOTIFY_START <= local.hour < NOTIFY_END):
        return  # quiet hours: stay silent
    now = core.now_utc()
    snap = sample_presence()
    state, app = snap["state"], snap["front_app"]
    notified = core.read_json(NOTIFIED, {})
    if not isinstance(notified, dict):
        notified = {}
    dirty = False
    for c, _delta in core.due_commitments(0):  # overdue / due-now only
        try:
            entry = migrate_entry(notified.get(c["id"]))
            if state is not None and c.get("kind") == "awaiting-reply":
                created = (core.parse_iso(c.get("created_at"))
                           or core.parse_iso(c.get("due_at")) or now)
                accrue(entry, state, now, created)
                notified[c["id"]] = entry
                dirty = True
            hit = pending_ping(c, entry, now, state, app)
            if hit is None:
                continue
            rung, message = hit
            ceiling = False
            created = core.parse_iso(c.get("created_at"))
            if created is not None:
                ceiling = (now - created).total_seconds() >= WALL_CEILING_S
            if state == "here" and not ceiling:
                continue  # hold: they can see the chat; ceiling overrides
            desktop_notify("Wall Clock", message)
            entry["count"], entry["last"] = rung, now.isoformat()
            notified[c["id"]] = entry
            dirty = True
        except Exception:
            continue
    if dirty:
        core.write_json(NOTIFIED, notified)
```

- [ ] **Step 4: Run the full suite**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK` (Task 2 count + 9). NOTE: two pre-existing tests call the OLD `pending_ping(c, entry, now)` 3-arg form (`test_rung_selection_by_elapsed` etc. in `TestWatcherLadder`) — update those call sites to `pending_ping(c, entry, now, "away", None)` and, where they relied on wall-elapsed ripeness with the old `RUNG_OFFSETS`, construct entries with the equivalent `unseen_s` instead (e.g. `{"count": 0, "last": None, "unseen_s": 600.0, "here_s": 0.0, "last_cycle": None}` for rung 1). Legacy wall behavior is now pinned by `test_legacy_degrade_matches_v15` with `state=None`.

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_wallclock.py
git commit -m "feat(watcher): unseen-time ladder — weighted accrual, wall ceiling, HERE-hold"
```

---

### Task 4: Return-nudge + presence.json persistence

**Files:**
- Modify: `watcher/watcher.py` (`run_cycle` gains transition logic; new `record_presence`)
- Test: `tests/test_wallclock.py` (extend `TestAbsenceClock`)

**Interfaces:**
- Consumes: Task 3's `sample_presence`/`accrue`/`ripe_rung`, Task 2's `RETURN_POOL`.
- Produces:
  - `record_presence(snap: dict, now) -> str | None` — reads `PRESENCE_FILE`, returns the PREVIOUS state, writes the new record `{"state", "since", "idle_s", "front_app"}` (preserving `since` when state unchanged).
  - `run_cycle` behavior: on transition `away → here|elsewhere`, for each open awaiting item with `ripe_rung >= 1` and `count < ripe`: fire ONE return-nudge from `RETURN_POOL` (with `away_m` = whole minutes of the just-ended away stretch), set `count` to the ripe rung. Regular pings for those items are skipped that cycle.

- [ ] **Step 1: Write the failing tests** — add to `TestAbsenceClock`:

```python
    def test_record_presence_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, watcher.PRESENCE_FILE)
            core.DATA, watcher.PRESENCE_FILE = dd, dd / "presence.json"
            try:
                now = core.now_utc()
                prev = watcher.record_presence(
                    {"state": "away", "idle_s": 500.0, "front_app": None}, now)
                self.assertEqual(prev, {})            # first record ever
                prev2 = watcher.record_presence(
                    {"state": "away", "idle_s": 800.0, "front_app": None}, now)
                self.assertEqual(prev2.get("state"), "away")
                saved = core.read_json(watcher.PRESENCE_FILE, {})
                self.assertEqual(saved["state"], "away")
                self.assertEqual(saved["since"], now.isoformat())  # preserved
                prev3 = watcher.record_presence(
                    {"state": "here", "idle_s": 1.0, "front_app": "Terminal"},
                    now + timedelta(seconds=600))
                self.assertEqual(prev3.get("state"), "away")
                self.assertEqual(prev3.get("since"), now.isoformat())
                saved = core.read_json(watcher.PRESENCE_FILE, {})
                self.assertEqual(saved["state"], "here")
            finally:
                core.DATA, watcher.PRESENCE_FILE = orig

    def test_return_nudge_fires_once_and_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 1300.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.sample_presence = lambda: {"state": "elsewhere",
                                                   "idle_s": 2.0,
                                                   "front_app": "Figma"}
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)          # exactly one nudge
                self.assertTrue(any(w in fired[0] for w in
                                    ("away", "absence", "gone")), fired[0])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 2)  # consumed rung 2
                watcher.run_cycle(force=True)           # steady elsewhere now
                self.assertEqual(len(fired), 1)          # no double-knock
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.PRESENCE_FILE, watcher.sample_presence,
                 watcher.desktop_notify) = orig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestAbsenceClock -v 2>&1 | tail -8`
Expected: ERROR — `module 'watcher' has no attribute 'record_presence'` (and the return-nudge test fails).

- [ ] **Step 3: Implement** — in `watcher/watcher.py` add above `run_cycle`:

```python
def record_presence(snap: dict, now) -> dict:
    """Persist the presence sample; return the PREVIOUS record ({} if none)
    for transition detection. 'since' survives while the state is unchanged —
    it marks when the current state began."""
    prev = core.read_json(PRESENCE_FILE, {})
    if not isinstance(prev, dict):
        prev = {}
    since = (prev.get("since") if prev.get("state") == snap["state"]
             else now.isoformat())
    core.write_json(PRESENCE_FILE, {
        "state": snap["state"], "since": since,
        "idle_s": snap["idle_s"], "front_app": snap["front_app"]})
    return prev
```

Then in `run_cycle`, after the snapshot:

```python
    prev = record_presence(snap, now)
    returned = (prev.get("state") == "away"
                and snap["state"] in ("here", "elsewhere"))
    away_since = core.parse_iso(prev.get("since")) if returned else None
    away_m = int((now - away_since).total_seconds() // 60) if away_since else 0
```

and inside the per-item loop, after `accrue(...)` / before the regular `pending_ping` block:

```python
            if returned:
                ripe = ripe_rung(c, entry, now, snap["state"])
                if ripe >= 1 and ripe > entry.get("count", 0):
                    msg = pick_message(c.get("id", ""), RETURN_POOL,
                                       text=c.get("text", ""), away_m=away_m)
                    desktop_notify("Wall Clock", msg)
                    entry["count"], entry["last"] = ripe, now.isoformat()
                    notified[c["id"]] = entry
                    dirty = True
                continue  # return-nudge replaces the regular ping this cycle
```

- [ ] **Step 4: Run the full suite**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3`
Expected: `OK` (Task 3 count + 2).

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py tests/test_wallclock.py
git commit -m "feat(watcher): return-nudge on away->back transition, presence.json log"
```

---

### Task 5: Chimes + spoken final rung + setup flags

**Files:**
- Modify: `watcher/watcher.py` (new `chime`, `speak_final`; wire into fire sites)
- Modify: `setup.sh` (flags `--silent`, `--speak [voice]`)
- Test: `tests/test_wallclock.py` (extend `TestAbsenceClock`)

**Interfaces:**
- Consumes: fire sites in `run_cycle` (regular ping + return-nudge).
- Produces:
  - `CHIME_MAP = {1: ("Tink", 0.35), 2: ("Glass", 0.5), 3: ("Hero", 0.6), "return": ("Purr", 0.35)}`
  - `chime(kind, state) -> None` — kind ∈ {1,2,3,"return"}; resolves volume (ELSEWHERE × 0.6; HERE → returns without playing; `data/chime.txt`: "off" → silent, float → master volume multiplier), spawns `subprocess.Popen(["/usr/bin/afplay", "-v", vol, path], stdout=DEVNULL, stderr=DEVNULL)`; ANY exception → silent. Exposed seam `watcher._spawn(cmd)` wraps Popen so tests capture commands without processes.
  - `speak_final(message) -> None` — only if `data/speak.txt` exists: voice = its stripped contents; spawns `["/usr/bin/say", message]` or `["/usr/bin/say", "-v", voice, message]`; failures silent.
  - `run_cycle` fire sites call `chime(rung_or_return, state)`, and `speak_final(message)` when the fired rung is 3.

- [ ] **Step 1: Write the failing tests** — add to `TestAbsenceClock`:

```python
    def test_chime_commands_and_state_modifiers(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                watcher.chime(1, "away")
                watcher.chime(3, "elsewhere")
                watcher.chime("return", "elsewhere")
                watcher.chime(2, "here")           # silent
                self.assertEqual(len(calls), 3)
                self.assertIn("Tink.aiff", calls[0][-1])
                self.assertAlmostEqual(float(calls[0][2]), 0.35)
                self.assertIn("Hero.aiff", calls[1][-1])
                self.assertAlmostEqual(float(calls[1][2]), 0.36)  # 0.6*0.6
                self.assertIn("Purr.aiff", calls[2][-1])
            finally:
                core.DATA, watcher._spawn = orig

    def test_chime_config_off_and_volume(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                (Path(d) / "chime.txt").write_text("off")
                watcher.chime(1, "away")
                self.assertEqual(calls, [])
                (Path(d) / "chime.txt").write_text("0.5")
                watcher.chime(2, "away")   # 0.5 base * 0.5 master = 0.25
                self.assertAlmostEqual(float(calls[0][2]), 0.25)
            finally:
                core.DATA, watcher._spawn = orig

    def test_speak_final_only_when_configured(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                watcher.speak_final("msg")          # no speak.txt -> silent
                self.assertEqual(calls, [])
                (Path(d) / "speak.txt").write_text("Samantha")
                watcher.speak_final("msg")
                self.assertEqual(calls[0][:3], ["/usr/bin/say", "-v", "Samantha"])
            finally:
                core.DATA, watcher._spawn = orig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_wallclock.py TestAbsenceClock -v 2>&1 | tail -6`
Expected: ERROR — `module 'watcher' has no attribute '_spawn'` / `'chime'`.

- [ ] **Step 3: Implement** — in `watcher/watcher.py` add above `run_cycle`:

```python
CHIME_MAP = {1: ("Tink", 0.35), 2: ("Glass", 0.5), 3: ("Hero", 0.6),
             "return": ("Purr", 0.35)}
SOUNDS_DIR = "/System/Library/Sounds"


def _spawn(cmd) -> None:
    """Fire-and-forget subprocess; tests replace this seam."""
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def chime(kind, state) -> None:
    """Subtle escalating sound beside the popup. HERE: silent. ELSEWHERE:
    whisper (x0.6). data/chime.txt: 'off' silences, a float scales."""
    try:
        if state == "here" or kind not in CHIME_MAP:
            return
        name, vol = CHIME_MAP[kind]
        cfg = None
        try:
            cfg = (core.DATA / "chime.txt").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if cfg == "off":
            return
        if cfg:
            try:
                vol *= float(cfg)
            except ValueError:
                pass
        if state == "elsewhere":
            vol *= 0.6
        _spawn(["/usr/bin/afplay", "-v", f"{vol:.2f}",
                f"{SOUNDS_DIR}/{name}.aiff"])
    except Exception:
        pass


def speak_final(message: str) -> None:
    """Opt-in spoken final rung: only when data/speak.txt exists."""
    try:
        voice = (core.DATA / "speak.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return
    try:
        cmd = (["/usr/bin/say", "-v", voice, message] if voice
               else ["/usr/bin/say", message])
        _spawn(cmd)
    except Exception:
        pass
```

Wire the fire sites in `run_cycle`: after the return-nudge `desktop_notify(...)` add `chime("return", snap["state"])`; after the regular `desktop_notify("Wall Clock", message)` add:

```python
            chime(rung, state)
            if rung == 3:
                speak_final(message)
```

In `setup.sh`, add to the flag parser (`--silent` and `--speak`), following its existing option style:

```bash
    --silent) SILENT=1; shift ;;
    --speak)  SPEAK=1
              if [[ "${2:-}" != "" && "${2:-}" != --* ]]; then SPEAK_VOICE="$2"; shift; fi
              shift ;;
```

and in the data-dir step:

```bash
if [[ "${SILENT:-0}" == "1" ]]; then echo "off" > "$PROJ/data/chime.txt"; fi
if [[ "${SPEAK:-0}" == "1" ]]; then echo "${SPEAK_VOICE:-}" > "$PROJ/data/speak.txt"; fi
```

(initialize `SILENT=0 SPEAK=0 SPEAK_VOICE=""` beside the other defaults; verify with `bash -n setup.sh`).

- [ ] **Step 4: Run the full suite + setup syntax**

Run: `python3 tests/test_wallclock.py 2>&1 | tail -3 && bash -n setup.sh && echo syntax-ok`
Expected: `OK` (Task 4 count + 3), `syntax-ok`.

- [ ] **Step 5: Commit**

```bash
git add watcher/watcher.py setup.sh tests/test_wallclock.py
git commit -m "feat(watcher): escalating state-aware chimes + opt-in spoken final rung"
```

---

### Task 6: Live soak, merge, protocol memory

**Files:**
- Modify: `README.md` (short absence-clock section, keep house voice)
- Modify: `~/.claude/projects/<project-slug>/memory/the-blocking-question-protocol memory` (controller-assigned — implementer SKIPS this, notes the skip)

**Interfaces:**
- Consumes: everything above; the LIVE launchd daemon.

- [ ] **Step 1: Live verification** (real data dir, deliberately — this is the soak; run from repo root):

```bash
python3 -c "import sys; sys.path.insert(0,'watcher'); import presence, watcher
snap = watcher.sample_presence(); print('snapshot:', snap)
assert snap['state'] in ('here','elsewhere','away','present'), snap"
bin/wallclock ask "SOAK: absence-clock live test" --due +0m
/usr/local/bin/python3 watcher/watcher.py --force
python3 - <<'EOF'
import json, pathlib
n = json.loads(pathlib.Path("data/notified.json").read_text())
soak = [v for v in n.values() if isinstance(v, dict) and "unseen_s" in v]
print("entries with absence fields:", len(soak))
assert soak, "no absence accounting happened"
p = json.loads(pathlib.Path("data/presence.json").read_text())
print("presence:", p)
assert p["state"] in ("here","elsewhere","away","present")
EOF
bin/wallclock answered
```
Expected: a valid snapshot (state almost certainly `here` while you run this — meaning NO popup fires: the HERE-hold working live); `notified.json` gains `unseen_s/here_s/last_cycle` fields; `presence.json` exists with a sane record; `closed 1 awaiting-reply item(s).`

- [ ] **Step 2: README** — add after the escalation-rails bullet, same voice:

```
- Presence-aware: the ladder counts absence, not wall time. HERE (a CLI app
  frontmost) pauses it; ELSEWHERE (working in another app) counts half;
  AWAY counts full. A 90-minute wall ceiling backstops the sensors, and one
  welcome-back nudge greets your return. Signals: HID idle seconds + the
  frontmost app NAME (never window titles or content), all local.
- Chimes: Tink/Glass/Hero escalate with the rungs (Purr on return),
  whispered when you're merely busy, silent when you're right here;
  data/chime.txt ("off" or a volume factor) controls them. Optional
  data/speak.txt makes the final rung literally speak.
```

- [ ] **Step 3: Full suite, merge to master**

```bash
python3 tests/test_wallclock.py 2>&1 | tail -3   # expect OK, final count
git add README.md && git commit -m "docs: absence-clock, presence privacy rails, chimes"
git checkout master && git merge --no-ff sundial-absence-clock -m "Merge sundial-absence-clock: the ladder measures absence, not time"
python3 tests/test_wallclock.py 2>&1 | tail -3   # expect OK on master
```

- [ ] **Step 4 (controller, not implementer): protocol memory** — update `the-blocking-question-protocol memory`: rung-3 wake now reads `unseen_s`/`here_s` from `notified.json`; mostly-unseen silence → proceed with stated assumption, substantial `here_s` → soft "not now", stand down.

---

## Deferred to Plan B (public release)
Staging export, scrub execution, `bin/sundial` rebrand, `SUNDIAL_*` envs,
README-as-flag, essay, demo assets, pre-push audit, `gh` push. Plan B is
written after this plan's code is final.
