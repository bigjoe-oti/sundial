# Session-voice: the warm session as a delivery channel — design

Approved conversationally by Yousef 2026-07-17 (scope, mechanism, and the
speak-on-wake question all settled in dialogue; this document is the
write-down).

## Problem

When a commitment's escalation ladder fires today, the only delivery channel
is a macOS notification — a template string, even when a Claude session with
the full context of that commitment sits open and warm. Issue #32913-class
temporal awareness got Sundial to *inject* context on the human's keystroke;
nothing lets the agent *initiate* inside the session.

## Decision record (from the design dialogue)

- **Channel model:** the watcher stays the brain (all ripeness, presence,
  ladder, cap judgment); the open session becomes the *freshest delivery
  channel*, claimed via heartbeat. "The clock decides when, the agent
  decides what to say."
- **Speech scope:** commitments only. Opportunities/offers keep their
  existing channels. Widen later from evidence, not ambition.
- **Speak-on-wake, not speak-on-frontmost:** the session composes into the
  transcript the moment its sentinel wakes on a routed fire, regardless of
  window focus. Holding for frontmost would rebuild a second presence judge
  session-side, violating the one-judgment-engine rail. An unfocused
  transcript is a pull surface — it waits without buzzing.
- **Time-situated composition:** messages are written to read correctly at
  any reading delay ("came due 24 minutes ago, while you were on the call"),
  never "just now". Same discipline as welcome-back briefs.
- **Rung accounting is shared:** a session-delivered fire advances
  entry/count in notified.json exactly like a popup — the 3-pings-ever cap
  governs speech too.
- **Rung 3 mirrors to both channels** (popup AND session) — the final rung's
  honesty weight justifies belt-and-braces. Rungs 1–2 are single-channel.
- **Staleness correction:** every sentinel wake reconciles the queue against
  current ledger state; if a queued item was resolved meanwhile, the wake
  appends a one-line correction instead of a stale ask.
- **Harness attention notification:** Claude Code may notify on turn-end
  while unfocused. Accepted as-is: the only buzz it produces is one the
  ladder already authorized.

## Architecture

Two new data files (both live in `data/`, git-ignored like all live state):

**`data/session_claim.json`** — written by the session side, read by the
watcher:
```json
{"ts": "2026-07-17T12:00:00+00:00", "ttl_s": 3600, "session": "<label>"}
```
Claim freshness = `now - ts < ttl_s`. A dead session stops refreshing; the
claim goes stale; popups resume. Never uncovered.

**`data/session_speak.json`** — written by the watcher, consumed by the
session:
```json
{"queue": [{"cid": "9be21c07", "rung": 1, "message": "<ladder message>",
            "text": "<commitment text>", "ts": "...", "consumed": false}]}
```

### Watcher side (the only engine change)

In `run_cycle`'s fire loop, one routing decision immediately before
`desktop_notify`:

- fresh claim AND rung < 3 → append to `session_speak.json` queue, log habit
  `{"kind": "fire", "channel": "session", ...}`, skip popup + chime.
- fresh claim AND rung == 3 → queue AND popup (mirror).
- stale/missing claim → popup exactly as today, habit `channel: "desktop"`.

Everything upstream — ripeness, presence weighting, snooze, breakpoint
deferral, wall ceiling, snooze breakthrough — runs unchanged BEFORE this
point. Snooze holds session routing too (a held fire is held on every
channel). Entry bookkeeping identical on both paths.

Queue hygiene: watcher prunes consumed entries older than 24h on write;
queue capped at 20 entries (drop-oldest, log a habit if the cap ever trims —
it shouldn't).

### Session side (no engine, a standing duty + a sentinel)

- `hooks/session_start.py` gains one context line when a speak queue exists
  or claim support is live: a standing-duty reminder to arm the sentinel and
  refresh the claim.
- The agent (Claude, in-session) arms a **Monitor** on `session_speak.json`
  (fallback: a slow ScheduleWakeup heartbeat, 1200s+, which doubles as the
  claim refresher). On each wake:
  1. refresh `session_claim.json`;
  2. read queue; reconcile each unconsumed entry against current
     commitments.json state (resolved → one-line correction; open → compose);
  3. speak: situated brief (what came due, when, what the session knows,
     proposed next move); mark consumed;
  4. re-arm.
- Claim TTL: 3600s (2–3× the heartbeat cadence). Session exit needs no
  cleanup — TTL is the cleanup.

The composing behavior itself is procedural (agent-side memory/standing
duty + the hook reminder), not code. The *routing and bookkeeping* are code
and get the full TDD treatment.

## Honesty rails (restated as invariants)

1. No LLM in the trigger path — the watcher's routing check is date/file
   arithmetic.
2. A fire is delivered on exactly one channel (rung 3: both, by declared
   exception), and advances rung accounting identically either way.
3. Stale claim ⇒ behavior is byte-identical to today's watcher.
4. Snooze, ceilings, caps, present-silence: all apply before routing; the
   session channel can never receive a fire the desktop channel wouldn't.
5. Messages carry their own timestamps; no "just now" language.

## Testing

House style: pure logic direct, IO via tempdir integration, delivery paths
with desktop_notify/chime/speak_final stubbed (incident-#5 rail).

- claim freshness: fresh/stale/missing/garbage/ttl-boundary.
- routing: fresh claim rung 1 → queue not popup; rung 3 → both; stale →
  popup only; snoozed+fresh-claim non-breakthrough → neither (held), habit
  logged.
- rung accounting parity: session-routed fire advances entry identically to
  popup path.
- queue hygiene: consumed-pruning, cap behavior.
- reconcile logic (pure fn): resolved-meanwhile → correction line; open →
  compose input; malformed queue → degrade silent.
- fail-safety: unwritable/corrupt claim or speak file never crashes
  run_cycle or the hook.

## Out of scope

Opportunities/offers routing to session; multi-session claims (last-writer
wins is fine for one human); off-desktop escalation; any change to ladder
timing or calibration math.
