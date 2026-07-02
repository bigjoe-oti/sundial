# Awaiting-Reply Nudges — Wall Clock v1.5 Design

**Date:** 2026-07-02
**Status:** Approved by the owner (design gate passed in-session)
**Scope:** AI-WallClock-Project — new `awaiting-reply` commitment kind, escalation
ladder, disarm hook, agent autonomy protocol.

## Problem

When the agent asks the human a blocking question mid-session and the human
walks away, nothing follows up. The session hangs indefinitely (the exact
failure paperclip #4022 documents, unshipped). The Wall Clock already has the
pieces — a commitments ledger, a no-LLM launchd watcher, desktop notifications —
but no flow connects "agent is blocked on human" to them.

## Prior art (researched 2026-07-02)

- SessionStart time/reminder injection: commodity (Symfolidity's
  claude-code-reminder). We already have it.
- No-LLM wake triggers: validated by arXiv 2605.30152 (LLM-free triggering
  beats LLM triggering; on-device). Our watcher is the degenerate-easy case.
- Escalation-then-autonomy on a blocked human: HumanLayer does it cloud-only;
  Claude Code notification hooks are local but one-shot; paperclip #4022 is a
  proposal with one silent timeout. **The local desktop escalation ladder that
  terminates in the agent proceeding or standing down exists nowhere found.**
  This spec is the novel claim.

## Decisions (made at design gate)

1. **Trigger — hybrid.** The agent judges what is truly blocking and arms the
   commitment itself via CLI. No Stop-hook heuristics (rejected: per-turn tax,
   false arms). Deterministic machinery only where determinism is cheap.
2. **Ladder — 10/20/50.** Nudge at 10 min, again at 20, final at 50. Agent may
   stretch the base delay by judgment (heavy asks → `+1h`).
3. **Disarm — any prompt, any session.** Any user prompt proves the human is
   back; that is the nudge's whole job.
4. **Architecture — watcher-carried (Approach A).** Durable logic lives in the
   existing launchd watcher, not in session-bound tasks or extra hooks.

## Data model

`commitments.json` rows gain two optional fields (absent = old behavior):

```json
{
  "id": "…", "created_at": "…", "due_at": "…", "text": "…",
  "source": "…", "status": "open",
  "kind": "awaiting-reply",        // default "plain" when absent
  "session_id": "…"                // informational; disarm is global
}
```

`notified.json` values change from a bare ISO string to
`{"count": 2, "last": "…iso…"}`. The watcher migrates legacy string values on
read (string → `{"count": 1, "last": <string>}`). Plain commitments keep the
once-ever ping (count caps at 1). No other files change shape.

## Escalation ladder

Agent asks at T0 and arms with `due_at = T0 + 10m` (default).

| Rung | Fires at (relative to due_at) | Channels | Message |
|------|-------------------------------|----------|---------|
| 1 | due + 0m (= T0+10m) | popup + in-chat line | `the owner — I'm blocked on: <text>` |
| 2 | due + 10m (= T0+20m) | popup only | `Still blocked (20m): <text>` |
| 3 | due + 40m (= T0+50m) | popup + in-chat autonomy verdict | `Final nudge (50m): <text> — proceeding on my judgment or standing down.` |

Popups come from the watcher (all three rungs). In-chat lines come from the
agent's sleepers (rungs 1 and 3 only — see protocol below); the watcher never
touches the chat.

Rules:
- Max three pings per item, ever. Rung count persists in `notified.json`.
- Watcher cycle fires **at most the single highest ripe rung** per item per
  cycle: the highest rung whose time has passed and whose number exceeds the
  stored `count`. After firing, `count` is set to that rung's number, so
  skipped rungs are permanently swallowed — after quiet hours, morning delivers
  one catch-up ping, never a burst.
- Quiet hours unchanged: 08:00–22:00 local only.
- Plain commitments are untouched by all of the above.

## CLI

- `wallclock ask "question"` — records an awaiting-reply commitment,
  `--due +10m` default, `source` defaults to `agent-blocked`. Prints the id.
- `wallclock answered [--quiet]` — marks **all** open awaiting-reply items done.
  `--quiet` suppresses output and always exits 0 (hook mode). Exit 0 even when
  nothing was open.
- `core.parse_due` learns relative forms: `+NNm`, `+NNh` (integer minutes/hours
  from now). Absolute forms unchanged.

## Hooks

One addition to `~/.claude/settings.json`:

```json
"UserPromptSubmit": [{"hooks": [{"type": "command",
  "command": "python3 …/AI-WallClock-Project/hooks/prompt_submit.py"}]}]
```

`hooks/prompt_submit.py` has two jobs:
1. **Disarm** — calls `core` to close open awaiting-reply items.
2. **Ambient clock (Amendment A)** — reads/updates `data/last_prompt.json`
   (`{"ts": iso}`) and emits `additionalContext` of the form
   `<wall-clock-tick>Now: Thu 02 Jul 2026, 02:15 PM (EEST). Elapsed since your
   previous prompt: 47m.</wall-clock-tick>` on every user prompt, giving the
   agent per-turn time sense and inter-prompt deltas for free.

Same fail-safe rail as `session_start.py` — any exception exits 0; a clock bug
must never block a prompt. Reads/writes two small JSONs; no transcript access.

## Agent protocol (memory-encoded, not code)

When asking a truly blocking question the agent:
1. Runs `wallclock ask "<question summary>"` (stretching `--due` by judgment).
2. Launches two background sleepers: `sleep 600` (rung-1 chat line) and
   `sleep 3000` (rung-3 autonomy moment).
3. On the 10-min wake: if the item is closed → do nothing (human replied; hook
   disarmed it). If still open → write one short in-chat line restating the
   blocking question, then keep waiting.
4. On the 50-min wake: if closed → do nothing. If still open → close it, then
   either **proceed autonomously, stating the assumption chosen**, or **stand
   down** with a note for the human's return, by confidence.

Recorded as a feedback memory (`feedback` type) so it survives sessions.
Sleepers are never killed on reply; they fire and find nothing to do.

## Error handling

- Both hooks: catch-all → exit 0 (existing rail, extended to the new hook).
- Watcher: existing behavior preserved — malformed `notified.json` degrades to
  `{}`; a malformed commitment row is skipped, not fatal.
- `wallclock answered` on empty/missing ledger: no-op, exit 0.

## Testing

Extend `tests/test_wallclock.py` (temp data dir fixtures, frozen clock):
- relative `parse_due` (`+10m`, `+2h`, invalid → error)
- rung selection at due+0/+5/+10/+39/+40/+120 minutes
- highest-ripe-rung collapse (overnight catch-up = exactly one ping)
- max-3 cap; plain commitments still cap at 1
- `notified.json` legacy-string migration
- `answered` closes all and only awaiting-reply items
- ambient clock: `last_prompt.json` stamping, elapsed-delta formatting, first
  prompt of a session (no prior stamp) omits the delta line
End-to-end: scripted run with a 1-minute ladder in a temp dir, then one live
`watcher.py --force` smoke check.

## Out of scope (unchanged v1 rails)

- No Stop hook, no transcript reads per turn.
- Decay stays computed-only; nothing auto-forgotten.
- No cloud delivery of any kind; osascript only.
- The LaunchAgent must be loaded by the human
  (`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sundial.watcher.plist`)
  — the agent is barred from installing persistence itself, by design.
