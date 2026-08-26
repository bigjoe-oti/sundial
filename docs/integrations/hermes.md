# Sundial × Hermes — fexx profile integration

**Status:** adapter shipped (Task 5); live cron wiring lands with Phase 3 completion.

## How Hermes wears the clock

1. **Session start.** Run:
   ```bash
   python3 ~/Desktop/AI-WallClock-Project/bin/sundial-hermes-hook \
     <<< '{"event":"session_start"}'
   ```
   Honor the `<sundial>` block it prints: local time, agent age, due
   commitments, estimation health, session-voice duty.

2. **Per human prompt.** After answering, run:
   ```bash
   echo '{"event":"prompt_submit","prompt":"<the user message>"}' | \
     python3 ~/Desktop/AI-WallClock-Project/bin/sundial-hermes-hook
   ```
   This disarms any awaiting-reply asks (the human is back!), stamps
   last-prompt time, and surfaces offers/budget flags.

3. **Machine events must be marked.** Cron-injected context and background
   task notifications pass `"machine": true` (or carry the
   `<task-notification>` marker) — they never disarm asks or refresh the
   session claim. A machine event treated as human would silently cancel
   real questions.

## Environment

| Variable | Value for fexx |
|---|---|
| `SUNDIAL_TZ` | set explicitly (default UTC gives wrong timestamps); Asia/Beirut for this owner |
| `SUNDIAL_MEMORY_DIR` | `~/.hermes/profiles/fexx/memories` so decay scores HERMES memory |
| `SUNDIAL_DATA_DIR` | unset in production; hook default `data/` under the repo |

## Known limitations (honest)

- **Disarm latency is habit-based.** Hermes has no guaranteed per-prompt
  hook, so disarm happens when the agent next runs step 2 after a human
  prompt — not mechanically on keystroke like Claude Code. Observed latency
  recorded here once live: _(pending first live week)_.
- **Claim TTL (3600s)** refreshes only on human prompts; long autonomous
  stretches read as unclaimed and fires queue to `session_speak.json`
  rather than routing into the session. Accepted behavior: the claim answers
  "is a human present?", not "is the agent busy?"
