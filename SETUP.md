# Setup

## What this is

Sundial gives a Claude Code agent a local, honest sense of time: a
`<sundial>` block on every session start, a commitments ledger that pings
you when something ripens, and a launchd watcher that fires desktop
notifications even when no session is open. Everything is plain JSON on disk
under `data/` — no network, no dependencies.

## Prerequisites

- macOS (launchd + `osascript` + `osacompile` are required; there is no
  non-Mac path)
- Claude Code installed and working
- `python3` on PATH (stdlib only, no pip installs needed)

## Install

```
git clone <this repo>   # or copy the whole sundial folder over
cd sundial
./setup.sh --name YourName --fresh
```

`--fresh` matters: it starts a **new agent identity** — a new `birth.json`
(the agent's date of birth), empty commitments and session ledger, and
cleared notify state. Never copy the previous owner's `data/` onto a new
machine and skip `--fresh`; that hands the new agent someone else's age,
history, and half-fired nudges. If `data/birth.json` is already absent,
`--fresh` is implied automatically.

Flags:

- `--name NAME` — owner name used in blocked-nudge messages (default `Friend`)
- `--memory-dir DIR` — where this agent's long-term memory lives (default
  `$HOME/.claude/projects/-Users-$USER/memory`)
- `--fresh` — wipe agent identity (see above)
- `--silent` — write `data/chime.txt` `off`: no nudge sounds
- `--speak [VOICE]` — write `data/speak.txt`: the final rung speaks aloud
  (optionally with a specific macOS `say` voice)

## What setup does

1. Guards: confirms macOS, and that `python3`, `osacompile`, `launchctl`
   are on PATH.
2. Resolves the project root from the script's own location.
3. Resets `data/` for a fresh identity (if `--fresh` or no `birth.json` yet),
   and always writes `data/owner.txt` with `--name`.
4. Rewrites `MEMORY_DIR` in `lib/core.py` to `--memory-dir` (machine-local
   memory path).
5. Compiles `watcher/Sundial.app` from `watcher/notifier.applescript.tmpl`
   so notifications are attributed to "Sundial", not a script runner.
6. Installs `~/Library/LaunchAgents/com.sundial.watcher.plist` and
   loads it via `launchctl bootstrap` (runs `watcher/watcher.py` every 10
   minutes).
7. Registers `hooks.SessionStart` and `hooks.UserPromptSubmit` in
   `~/.claude/settings.json`, without touching any other keys or hook types
   already there.
8. Verifies: runs the full test suite, then fires one test notification.

## After install

- Allow notifications: **System Settings -> Notifications -> "Sundial"**
  the first time it tries to fire.
- Start a **new** Claude Code session — the `<sundial>` block only
  appears on SessionStart, not mid-session.
- Try `bin/sundial now` to see the clock-on-glance output directly.
- Tell the receiving agent about the blocking-question protocol: when it's
  stuck waiting on you, it should run `sundial ask "..."` to arm a
  10/20/50-minute nudge ladder instead of just sitting silent.

## Uninstall

```
launchctl bootout gui/$(id -u)/com.sundial.watcher
rm ~/Library/LaunchAgents/com.sundial.watcher.plist
```

Then remove the `SessionStart` and `UserPromptSubmit` entries added under
`hooks` in `~/.claude/settings.json` (by hand — nothing else in that file is
touched), and delete the project folder.
