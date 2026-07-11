# Setup

## What this is

Sundial gives a Claude Code agent a local, honest sense of time: a
`<sundial>` block on every session start, a commitments ledger that pings
you when something ripens, and a launchd watcher that fires desktop
notifications even when no session is open. Everything is plain JSON on disk
under `data/` — no network, no dependencies.

A ripe nudge waits for a natural pause (you stop typing or switch apps)
before it delivers, instead of firing mid-keystroke. Sound courtesy reads
your presence, not the clock: chimes/speech mute when the screen is locked
or you've been away 30+ minutes, but popups and detection run 24/7 — there
are no quiet hours. The same watcher also notices meetings (including
Meet-in-Chrome, via WebRTC assertions) and new folders on your Desktop, and
offers to help — capped and deduped, never spammy — while quietly logging a
Habit Ledger of your rhythms for later.

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
cleared notify/presence/opportunity/habit/build/owner-model state
(`owner_model.json` and `build_state.json` included — no stale meeting,
folder, build, or offer history from a previous owner). Never copy the
previous owner's `data/` onto a new machine and skip `--fresh`; that hands
the new agent someone else's age, history, and half-fired nudges. If
`data/birth.json` is already absent, `--fresh` is implied automatically.

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
- macOS will show a **one-time permission prompt** for Sundial — click
  **Allow**. If you dismiss or deny it, every banner drops silently and
  Sundial gives no error.
- If you use an external display, enable **System Settings ->
  Notifications -> "Allow notifications when mirroring or sharing"** —
  otherwise macOS suppresses all banners while mirroring/sharing, with no
  indication anything was blocked.
- Recommended notification style: **Alerts** (not Banners), so a nudge you
  miss stays on screen instead of auto-dismissing.
- Start a **new** Claude Code session — the `<sundial>` block only
  appears on SessionStart, not mid-session.
- Try `bin/sundial now` to see the clock-on-glance output directly.
- Tell the receiving agent about the blocking-question protocol: when it's
  stuck waiting on you, it should run `sundial ask "..."` to arm a
  10/20/50-minute nudge ladder instead of just sitting silent.
- Open meeting/folder offers surface automatically in the `<sundial>` and
  `<sundial-tick>` context blocks — no separate command needed.

## Optional config

Drop these in `data/` any time; the watcher reads them fresh each cycle
(none are required):

- `meeting_apps.txt` — one app name per line, added to the meeting-detection
  allowlist (default: `zoom.us`, Microsoft Teams, FaceTime, Webex, Skype)
- `watch_roots.txt` — one folder path per line to watch for new subfolders
  (default: `~/Desktop`)
- `ignore_paths.txt` — one path prefix per line the curiosity sensor should
  never mention (e.g. to keep Sundial from noticing its own repo); kept
  across `--fresh`, same as the other config files here
- `chime.txt` — `off`, or a float to scale nudge-sound volume (same as
  `--silent` above)
- `speak.txt` — voice name (or empty) to speak the final rung aloud (same as
  `--speak` above)

## Timezone

Sundial displays times and parses date-only deadlines in `SUNDIAL_TZ`
(IANA name, e.g. `Africa/Cairo`) and silently falls back to **UTC** when it
is unset. The variable must reach every entry point separately — none of
them inherit your shell profile:

- **Claude hooks:** prefix both commands in `~/.claude/settings.json`, e.g.
  `SUNDIAL_TZ=Africa/Cairo python3 …/hooks/session_start.py`
- **The watcher:** add an `EnvironmentVariables` dict with `SUNDIAL_TZ` to
  the LaunchAgent plist, then `launchctl bootout` + `bootstrap` to reload.
- **CLI from a terminal:** export it in your shell profile.

The symptom of forgetting one: that surface shows UTC times while the
others show local — deadlines set as bare dates (`--due 2026-07-14`) also
resolve to end-of-day in the wrong zone.

## Optional: menu-bar face

For an at-a-glance presence/asks/offers readout without opening a session,
install [SwiftBar](https://github.com/swiftbar/SwiftBar), copy
`contrib/sundial.30s.sh` into its plugin folder, and set the `SUNDIAL_HOME`
environment variable to this project's path (SwiftBar copies plugin scripts
out of the repo, so the script can't resolve its own location). The plugin
is read-only — it never writes to `data/` or signals the watcher.

## Uninstall

```
launchctl bootout gui/$(id -u)/com.sundial.watcher
rm ~/Library/LaunchAgents/com.sundial.watcher.plist
```

Then remove the `SessionStart` and `UserPromptSubmit` entries added under
`hooks` in `~/.claude/settings.json` (by hand — nothing else in that file is
touched), and delete the project folder.
