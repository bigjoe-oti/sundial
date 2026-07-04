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
│ commitments.json   promises with due times & status              │
│ notified.json      per-item rung counts, unseen/here clocks,     │
│                    deferral telemetry                            │
│ opportunities.json detected moments & offer status               │
│ habits.jsonl       append-only behavioral observations           │
│ presence.json · meeting_state.json · known_folders.json          │
└──────────────┬───────────────────────────────────────────────────┘
               ▼ surfaced by hooks
┌─ THE AGENT (your LLM assistant, judgment only) ──────────────────┐
│ session_start hook   clock, age, due items, open offers          │
│ prompt_submit hook   per-prompt tick · auto-disarm on human      │
│                      input (machine events filtered) · offers    │
│ the autonomy contract: after the final rung, the agent proceeds  │
│ on stated judgment or stands down — silence is interpreted by    │
│ presence (unseen vs sat-there), never assumed                    │
└──────────────────────────────────────────────────────────────────┘
```

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
  `data/chime.txt`, `data/speak.txt` — config without code.
- `contrib/sundial.30s.sh` — SwiftBar menu-bar face (optional).
- The hooks are thin: any harness that can run a command per prompt and
  read stdout can mount Sundial.
