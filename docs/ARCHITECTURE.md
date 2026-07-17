# Sundial Architecture

Three actors, strictly separated. The separation *is* the design.

```
┌─ SENSORS (zero-permission macOS reads) ──────────────────────────┐
│ HIDIdleTime · frontmost app name · pmset power assertions        │
│ (incl. WebRTC call detection) · screen-lock state · vnstat       │
│ network rates (optional)                                         │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
┌─ THE WATCHER (launchd, every 10 min, pure Python, NO LLM) ───────┐
│ presence.py   sensors → HERE / ELSEWHERE / AWAY                  │
│ watcher.py    unseen-time ladder (10/20/50 min not-seeing-chat,  │
│               ELSEWHERE half-rate, 90-min wall ceiling) ·        │
│               breakpoint delivery (hold ripe nudges ≤3 min for   │
│               a typing pause / app switch) · courtesy (sound     │
│               muted on lock or 30-min absence — never by clock)  │
│ opportunities.py  meeting start/end offers · new-folder          │
│               curiosity · Habit Ledger (habits.jsonl)            │
└──────────────┬───────────────────────────────────────────────────┘
               ▼ writes                              ▲ reads
┌─ THE LEDGERS (plain JSON in data/, git-ignored) ─────────────────┐
│ commitments.json   promises with due times, status & calibrated  │
│                    estimate snapshots (est_s / P50 / P90)        │
│ notified.json      per-item rung counts, unseen/here clocks,     │
│                    present-while-ripe cycles, deferral telemetry │
│ opportunities.json detected moments & offer status               │
│ habits.jsonl       append-only behavioral observations +         │
│                    estimate open/close pairs (the ratio history) │
│ session-ledger.json  dual clock: wall-ms × output tokens/session │
│ session_claim.json heartbeat: fresh routes fires to the session, │
│                     stale/missing falls back to popups           │
│ session_speak.json queue of routed fires; the session drains,    │
│                     marks consumed; watcher prunes/caps at 20    │
│ presence.json · meeting_state.json · known_folders.json          │
└──────────────┬───────────────────────────────────────────────────┘
               ▼ surfaced by hooks
┌─ THE AGENT (your LLM assistant, judgment only) ──────────────────┐
│ session_start hook   clock, age, due items, open offers,         │
│                      autonomy verdicts, two-clock estimation     │
│                      block (running-long flags + calibration)    │
│ prompt_submit hook   per-prompt tick · auto-disarm on human      │
│                      input (machine events filtered) · offers    │
│ the autonomy contract: after the final rung, the agent proceeds  │
│ on stated judgment or stands down — silence is interpreted by    │
│ presence (unseen vs sat-there), never assumed                    │
└──────────────────────────────────────────────────────────────────┘
```

(The ladder cadence above is the **normal** tier; `--weight high|low` retimes it
per the tier table below.)

## The decision policy (v1.3)

An `awaiting-reply` ask carries three optional dials the agent sets when it
blocks: **urgency** (`--weight low|normal|high` → that tier's ladder offsets and
wall ceiling — high 5/10/20 min·40-min ceiling, normal 10/20/50·90-min, low
two rungs·3-hour), **confidence** (`--confidence 0..1`), and **reversibility**
(`--irreversible`). The watcher stays date-arithmetic only: it reads the tier
table in `lib/policy.py` and replays any agent-authored rung text — never a
model. The **autonomy gate** (`policy.autonomy_decision`, consumed by the
session-start hook, never the watcher) is: irreversible → always ask you;
reversible & confidence ≥ 0.95 → proceed; reversible, confidence 0.80–0.95
AND proven present-silence → proceed; else stand down. Present-silence is
proven by a dedicated ripeness-gated counter (`ripe_here_cycles`): ≥3 watcher
cycles sampled strictly "here" while the ask was already ripe — sleep gaps
and mere "present" (screen-share ambiguity) never count, so a blip can never
read as consent.

## Session-voice routing

One routing decision, added immediately before delivery in the watcher's
fire loop (batch fires and the return-nudge site alike): a fresh
`session_claim.json` (written by the session, TTL 3600s) sends rungs 1–2
to `session_speak.json` instead of a popup; rung 3 always mirrors to
both, by declared exception. A stale or missing claim is byte-identical
to today's popup path — nothing is ever left uncovered. Snooze, wall
ceilings, and rung caps all apply upstream of this check, so the session
channel can never receive a fire the desktop channel wouldn't; rung
accounting in `notified.json` advances identically on either channel.
Design record: `docs/superpowers/specs/2026-07-17-session-voice-design.md`.

## The estimation loop (Phase B)

The agent's duration estimates are calibrated from its own measured history,
never guessed. Capture rides the commitment lifecycle — it cannot lapse
without the clock itself lapsing: a plain commitment opens an execution
estimate at creation (`remember --est 45m --bucket build`, else derived from
the deadline) and closes it with the actual on `done`; `awaiting-reply` asks
feed the review clock through their existing answered-latency events. The
engine (`lib/estimator.py`, pure, no LLM) turns the accumulated actual/est
ratios into P50/P90 with a small-n honesty rule (no confident numbers from
thin data). Three read surfaces: a deadline-sanity line at creation when
history's P90 exceeds the time promised, the session-start two-clock block
(running-long flags + calibration health), and the menu-bar ⏱ line (red past
P90). `sundial estimate "<task>" --raw 30m` gives the calibrated view on
demand.

## Design rails (why it's built this way)

1. **The trigger path never thinks.** Wake/escalation decisions are date
   arithmetic over ledgers — testable exhaustively, incapable of
   hallucinating an interruption. The LLM enters only where judgment
   lives: interpreting silence, fulfilling offers.
2. **Live state is not history.** `data/` is git-ignored; ledgers are
   written atomically (unique tmp + fsync + rename) and serialized by an
   flock. Both rules exist because their absence caused real incidents.
3. **Courtesy reads the human, not the clock.** No quiet hours: cycles
   run 24/7; sound gates on screen-lock and absence length. Built for
   owners with rotational rhythms.
4. **Every delivery is honest.** Corruption quarantines instead of
   silently defaulting; notifications post via an identified applet
   (bundle-id'd, icon-bearing) because unidentified ones silently drop;
   stale meeting news is muted, not shouted.
5. **The system studies its owner, consentfully.** The Habit Ledger logs
   events (never content); distillation into learned quiet hours and
   tuned thresholds is deterministic; changes apply only on the owner's
   word.

## Extension points

- `data/meeting_apps.txt`, `data/watch_roots.txt`, `data/cli_apps.txt`,
  `data/chime.txt`, `data/speak.txt`, `data/ignore_paths.txt` — config
  without code. `ignore_paths.txt` lists path prefixes the curiosity sensor
  should never mention — one per line — e.g. to keep Sundial from noticing
  its own repo.
- `contrib/sundial.30s.sh` — SwiftBar menu-bar face (optional).
- The hooks are thin: any harness that can run a command per prompt and
  read stdout can mount Sundial.
