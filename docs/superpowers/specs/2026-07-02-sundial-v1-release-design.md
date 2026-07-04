# Sundial v1.0 — Public Release + Absence-Clock Design

**Date:** 2026-07-02
**Status:** GATE PASSED (the owner, 2026-07-02 13:15) — approved with three-state
presence. Decisions: direction 2-phase, name Sundial, scope whole-organism,
home bigjoe-oti/sundial + MIT, launch = write-up + terminal demo, gem = full
absence-clock with HERE/ELSEWHERE/AWAY.
**Depends on:** v1.5 (merged), delivery kit (1523504), voice pools (6b048a6),
scrub inventory (agent report 2026-07-02).

## Thesis

Sundial measures **absence, not time**. Silence-while-present means "not
now"; silence-while-absent means "I haven't seen it." The escalation ladder
and the agent's autonomy decision must distinguish the two.

## Part 1 — The absence-clock (private repo first, then ships in v1.0)

### Presence sensing — THREE STATES (gate-approved upgrade)
- `idle_seconds() -> float | None`: parse `HIDIdleTime` from
  `/usr/sbin/ioreg -c IOHIDSystem` (nanoseconds → seconds).
- `front_app() -> str | None`: parse `/usr/bin/lsappinfo front` +
  `lsappinfo info` for the frontmost app NAME only (no window titles, no
  content). Terminal-ish names (Terminal, iTerm2, Ghostty, Warp, Alacritty,
  kitty, Visual Studio Code, Cursor — constant `CLI_APPS`, setup-overridable
  via `data/cli_apps.txt`) count as "our" surface.
- States: **HERE** = idle < 180s AND front app ∈ CLI_APPS (chat visible);
  **ELSEWHERE** = idle < 180s AND front app ∉ CLI_APPS (busy, hasn't seen
  the chat); **AWAY** = idle ≥ 180s (`PRESENCE_IDLE_S = 180`).
- Either sensor failing → degrade one level: no front_app → HERE/ELSEWHERE
  collapse to PRESENT (2-state model); no idle → **full degrade to v1.5
  wall-clock semantics, byte-identical behavior.**
- Privacy rail: idle duration + app name only; nothing leaves the machine.

### Unseen-time accounting (per open awaiting-reply commitment)
`notified.json` entry grows: `{"count": n, "last": iso, "unseen_s": float,
"here_s": float, "last_cycle": iso}`. Each watcher cycle, per open item:
- `gap = now - last_cycle` (first cycle: `now - created_at`, floor 0).
- State AWAY → `unseen_s += gap`; state ELSEWHERE → `unseen_s += gap *
  ELSEWHERE_WEIGHT` with `ELSEWHERE_WEIGHT = 0.5` — **"two busy minutes equal
  one absent minute":** popups reaching a visibly-working human are guaranteed
  seen, so the ladder stretches to an effective 20/40/100 while they work.
  (A gap far exceeding the 600s interval means the machine slept — sleeping
  counts as AWAY-unseen, correctly.) State HERE → `here_s += gap`, unseen_s
  untouched. **Only HERE pauses the ladder** — a day in Figma no longer
  silences the clock, but sitting in our chat does.
- Rung ripeness: `unseen_s >= (600, 1200, 3000)[i]` — 10/20/50 minutes of
  genuinely-not-seeing-the-chat since the ask. (Fixes the two-state draft's
  bug where rung 1's `away_s >= 0` was vacuously true at due time.)
  `due_at` (+10m default) remains the earliest watcher pickup, consistent:
  unseen_s ≥ 600 implies wall ≥ 600.
- **Wall ceiling:** wall-elapsed since `created_at` ≥ `WALL_CEILING_S = 5400`
  (90 min) forces the final rung regardless of sensors — the honesty rail
  against reading/video/thinking blind spots.
- Plain commitments: unchanged v1.5 behavior (once-ever ping on wall time).

### Return-nudge
- Global `data/presence.json`: `{"state": "here"|"elsewhere"|"away",
  "since": iso, "idle_s": float, "front_app": str|null}` written every cycle
  (also the seed data for future circadian learning).
- On transition AWAY→(HERE|ELSEWHERE) observed by a cycle, with an open
  awaiting-reply item whose rung 1 is ripe: fire ONE return-nudge from its
  own pool — "While you were away ({away_m}m): {text}" / "Welcome back.
  This ripened in your absence: {text}" — and set `count` to the
  currently-ripe rung (consumes it; no double-knock).

### State-aware voice (the owner addendum, 13:21)
Message pools fork by presence state at fire time; deterministic per-item
pick unchanged; rung-3 entries in EVERY pool state the autonomy consequence.
- **AWAY pools:** the existing v1.5 voice (written for someone who finds the
  notification later).
- **ELSEWHERE pools:** cheeky, app-aware via `{app}` (frontmost app name):
  "{owner}, I know you're busy with {app} — I won't take much of your time,
  I just need your call on: {text}" / "I can see {app} has you. One opinion
  and I'll vanish: {text}" / "Whatever {app} is doing, it can spare you ten
  seconds: {text}". Rung 3 elsewhere: "{owner}, {app} can wait one beat —
  final call on: {text} — deciding without you otherwise."
- **Return pool:** the welcome-backs (above).
- Template variables are only ever {owner}/{text}/{app}/{away_m}; a pool
  entry that fails to format falls back to the classic line (fail-safe).

### Agent protocol update (memory, not code)
Rung-3 wake reads the clocks from `notified.json` (`unseen_s`, `here_s`,
wall): mostly-unseen silence → proceed with stated assumption;
substantial `here_s` (they sat in our chat and chose silence) → soft "not
now", stand down. Update `the-blocking-question-protocol memory`.

### Chimes (the owner addendum, 13:22 — subtle, escalating, state-aware)
- `chime(rung, state)` in watcher: `/usr/bin/afplay -v <vol>
  /System/Library/Sounds/<name>.aiff` fired alongside desktop_notify.
- Sound map: rung 1 `Tink` @0.35, rung 2 `Glass` @0.5, rung 3/ceiling `Hero`
  @0.6, return-nudge `Purr` @0.35. State modifier: ELSEWHERE → vol × 0.6
  (whisper — they're right there); HERE → no chime; AWAY → as mapped (sound
  is the one channel that reaches an in-the-room human the popup can't).
- Config: `data/chime.txt` — absent = defaults on; "off" = silent; a float =
  master volume override. `setup.sh --silent` writes "off".
- Fail-safe: afplay/sound file missing or any error → silent skip.
- Tests: mocked subprocess — correct file/volume per (rung, state); off/
  override honored; missing binary → no exception.

### Spoken final rung (opt-in flourish)
- If `data/speak.txt` exists (contents optionally a `say` voice name), the
  final rung (rung 3 or ceiling) additionally runs `/usr/bin/say` with the
  message. `setup.sh --speak [voice]` writes the file. Never on by default.

### Tests (extend suite, currently 40)
- idle parser: mocked ioreg output → seconds; garbage/None → degrade.
- front_app parser: mocked lsappinfo output → name; garbage/None → 2-state.
- state derivation: (idle, front) → HERE/ELSEWHERE/AWAY truth table incl.
  CLI_APPS membership and data/cli_apps.txt override.
- accrual: AWAY and ELSEWHERE accrue unseen_s; HERE accrues here_s only;
  sleep-gap counts unseen.
- ripeness on unseen_s at (600, 1200, 3000) boundaries; ceiling forces final
  rung at 90m wall even with unseen_s = 0 (present the whole time).
- return-nudge: AWAY→HERE and AWAY→ELSEWHERE with ripe item → one ping,
  count consumed; ELSEWHERE→HERE → none; steady states → none.
- degrade paths: no front_app → 2-state PRESENT semantics; no idle →
  v1.5 wall-clock behavior byte-identical.

## Part 2 — Public release (fresh-history export)

### Repo
- Target: github.com/bigjoe-oti/sundial (exists; token needs `gh auth login`).
- Fresh history: staging build at ~/Desktop/sundial-staging → `git init` →
  single commit "Sundial v1.0" (author: name/email the owner sets at push time)
  → push. Private repo remains the dev copy; public is a curated export.
- LICENSE: MIT, "Copyright (c) 2026 J. Servo LLC" (adjustable at spec review).

### Scrub + genericize (execute full inventory)
Must: MEMORY_DIR → an env var with the old project's prefix, computed default
(`~/.claude/projects/<munged-cwd>/memory` pattern documented, not guessed);
private-tool comments and README provenance genericized; owner fallback
"the owner" → "Friend"; tracked plist git-rm'd (setup.sh generates it);
`data/` ships empty/fresh; memory-weights/birth/notified absent;
`cron_check.py` deleted; `.DS_Store`s removed + gitignored.
Should: docs keep the build-diary narrative, scrubbed of real paths/names;
DEFAULT_TZ → "UTC" fallback (old project's TZ env var stays, for now); pytest_cache gitignored.
Optional (decided): test fixture names → neutral; SETUP.md example
`--name YourName`.

### Rebrand
- CLI: `bin/sundial` (verbs unchanged: now/remember/due/done/ask/answered).
- launchd label + plist: `com.sundial.watcher`. Applet: `Sundial.app`,
  notification title "Sundial". `setup.sh` and SETUP.md updated to match.
- Env vars keep the old project's prefix? NO — public uses `SUNDIAL_TZ`,
  `SUNDIAL_MEMORY_DIR` (private repo keeps its own prefix until it re-syncs).

### README (the flag)
Title + tagline ("a sense of time for AI agents — local-first,
zero-dependency, no LLM in the loop"), thesis line ("Sundial measures
absence, not time"), ASCII ladder diagram, quickstart (`./setup.sh --name You
--fresh`), architecture map, honesty rails, honest prior-art section
(kadenn/chronos, Symfolidity claude-code-reminder, paperclip #4022,
HumanLayer, arXiv 2605.30152), roadmap (v2: ledger-grounded self-estimation —
Phase B runway).

### Essay
`docs/escalation-then-autonomy.md`: the pattern write-up — the missing half
of human-in-the-loop; silence disambiguated by presence; the no-LLM watcher
argument; the live bugs dogfooding caught (machine-event impersonation,
test-disarm) as design lessons. I draft; the owner reads before publish.

### Demo
`demo/`: VHS tape script if `vhs` available, else manual recording checklist.
Story: `sundial ask` → walk away → ladder climbs (absence-clock) → return →
return-nudge greets you. Plus one screenshot of a real "Sundial" notification.

## Sequencing
1. Absence-clock lands in the PRIVATE repo (tests + live daemon soak).
2. Staging export + scrub + rebrand + README/essay/demo.
3. The owner: `gh auth login`, reads essay/spec, pushes (or authorizes push).
4. LinkedIn/promo: explicitly deferred (the owner's call, later).

## Out of scope (v1.0)
- Sibling-session awareness + cross-session nudge routing (the owner's idea,
  2026-07-02): detect other live Claude sessions via transcript-file mtime
  (a session written to in the last N seconds is active — zero-dep liveness,
  no process introspection) and deliver another session's ripened question
  through the session the human is actively using. Parked as the Fleet
  phase's opening feature.
- Circadian learning (presence.json seeds it; v-next).
- Self-estimation engine (Phase B, own spec).
- Fleet/multi-machine (Phase C, unlocked by a second user's install).
- Any cloud channel, any third-party dependency. Still zero-dep, still local.
