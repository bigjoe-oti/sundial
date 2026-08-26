#!/usr/bin/env bash
# Sundial — handoff installer.
#
# Wires this project into a Mac: resets/creates the agent identity, points
# lib/core.py at this user's memory dir, compiles the notification applet,
# installs the launchd watcher, and registers the two Claude Code hooks.
#
# Usage:
#   ./setup.sh [--name NAME] [--memory-dir DIR] [--fresh] [--silent] [--speak [VOICE]]
#
#   --name NAME        owner name used in blocked-nudge messages (default: Friend)
#   --memory-dir DIR    where this agent's long-term memory lives
#                       (default: "$HOME/.claude/projects/-Users-$USER/memory")
#   --silent            write data/chime.txt 'off' (no nudge sounds)
#   --speak [VOICE]     write data/speak.txt (final rung speaks aloud)
#   --fresh             wipe agent identity (new birth.json, empty
#                       commitments/ledger, no notified/notify/presence state,
#                       no meeting/folder/opportunity/habit/build/owner-model
#                       history)
#
# Optional config files (create by hand in data/, all read fresh each cycle):
#   meeting_apps.txt    one app name per line, added to the meeting-detection
#                       allowlist (zoom.us, Teams, FaceTime, Webex, Skype)
#   watch_roots.txt     one folder path per line to watch for new
#                       subfolders (default: ~/Desktop)
#   ignore_paths.txt    one path prefix per line the curiosity sensor should
#                       never mention (e.g. Sundial's own repo)
#   chime.txt           'off', or a float to scale nudge-sound volume
#   speak.txt           voice name (or empty) to speak the final rung aloud
set -euo pipefail

# 0. Platform target: macos (default, full installer) | hermes (hook+tick
#    wiring only — no applet, no launchd) | linux (delegated to setup-linux.sh).
#    Parsed FIRST so it composes with the flag loop below.
PLATFORM="macos"
REST=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    *) REST+=("$1"); shift ;;
  esac
done
if [[ ${#REST[@]} -gt 0 ]]; then set -- "${REST[@]}"; else set -- ; fi

NAME="Friend"
MEMORY_DIR="$HOME/.claude/projects/-Users-$USER/memory"
FRESH=0
SILENT=0
SPEAK=0
SPEAK_VOICE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --memory-dir) MEMORY_DIR="$2"; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    --silent) SILENT=1; shift ;;
    --speak)  SPEAK=1
              if [[ "${2:-}" != "" && "${2:-}" != --* ]]; then SPEAK_VOICE="$2"; shift; fi
              shift ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
done

echo "== Sundial setup =="

if [[ "$PLATFORM" == "linux" ]]; then
  echo "[setup] linux target: delegating to setup-linux.sh..."
  exec bash "$(dirname "$0")/setup-linux.sh" "$@"
fi

if [[ "$PLATFORM" == "hermes" ]]; then
  echo "[setup] hermes target: hook + tick wiring only (no applet, no launchd)."
  PROJ="$(cd "$(dirname "$0")" && pwd)"
  command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found" >&2; exit 1; }
  cat <<HERMES

Hermes integration checklist (see docs/integrations/hermes.md):
  1. Session start / per-prompt injection:
       python3 $PROJ/bin/sundial-hermes-hook <<< '{"event":"session_start"}'
       echo '{"event":"prompt_submit","prompt":"..."}' | \\
         python3 $PROJ/bin/sundial-hermes-hook
  2. Cron tick (exactly ONE driver — verify no launchd plist and no other
     sundial cron job exists before registering a 10m watcher tick).
  3. Export SUNDIAL_TZ (default UTC) and SUNDIAL_MEMORY_DIR for your profile.
  4. Run 'sundial doctor' to verify the whole chain.
HERMES
  exit 0
fi

# 1. Guards: macOS + required tools.
echo "[1/8] checking platform and required tools..."
if [[ "$(uname)" != "Darwin" ]]; then
  echo "ERROR: Sundial is macOS-only (needs osascript/launchd). Use --platform hermes or linux." >&2
  exit 1
fi
for bin in python3 osacompile launchctl; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "ERROR: required tool not found on PATH: $bin" >&2
    exit 1
  }
done
echo "  -> Darwin, python3, osacompile, launchctl all present"

# 2. Resolve project root (this script's directory).
PROJ="$(cd "$(dirname "$0")" && pwd)"
echo "[2/8] project root: $PROJ"

# 3. Data: fresh agent identity, or keep what's there. Owner name is always set.
mkdir -p "$PROJ/data"
echo "[3/8] preparing data/ ..."
if [[ "$FRESH" -eq 1 || ! -f "$PROJ/data/birth.json" ]]; then
  echo "  -> starting a fresh agent identity (birth, notify state cleared)"
  printf '[]' > "$PROJ/data/commitments.json"
  printf '[]' > "$PROJ/data/session-ledger.json"
  rm -f "$PROJ/data/birth.json" "$PROJ/data/notified.json" \
        "$PROJ/data/memory-weights.json" "$PROJ/data/last_prompt.json" \
        "$PROJ/data/notify.txt" "$PROJ/data/presence.json" \
        "$PROJ/data/meeting_state.json" "$PROJ/data/known_folders.json" \
        "$PROJ/data/opportunities.json" "$PROJ/data/opportunity_prefs.json" \
        "$PROJ/data/habits.jsonl" "$PROJ/data/habits.1.jsonl" \
        "$PROJ/data/build_state.json" "$PROJ/data/owner_model.json"
else
  echo "  -> existing agent identity found in data/birth.json, keeping it (pass --fresh to reset)"
fi
printf '%s' "$NAME" > "$PROJ/data/owner.txt"
echo "  -> owner set to: $NAME"
if [[ "${SILENT:-0}" == "1" ]]; then echo "off" > "$PROJ/data/chime.txt"; fi
if [[ "${SPEAK:-0}" == "1" ]]; then echo "${SPEAK_VOICE:-}" > "$PROJ/data/speak.txt"; fi

# 4. Point lib/core.py's MEMORY_DIR default at this machine's memory
#    directory (SUNDIAL_MEMORY_DIR still overrides it at runtime).
echo "[4/8] setting MEMORY_DIR -> $MEMORY_DIR"
python3 - "$PROJ/lib/core.py" "$MEMORY_DIR" <<'PYEOF'
import re
import sys

path, new_dir = sys.argv[1], sys.argv[2]
src = open(path, encoding="utf-8").read()
pattern = re.compile(
    r'(MEMORY_DIR = Path\(os\.environ\.get\("SUNDIAL_MEMORY_DIR",\s*)'
    r'str\(Path\.home\(\) / "\.claude" / "memory"\)'
    r'(\)\))'
)
if not pattern.search(src):
    sys.exit("ERROR: MEMORY_DIR pattern not found in lib/core.py")
quoted = '"' + new_dir.replace("\\", "\\\\").replace('"', '\\"') + '"'
src = pattern.sub(lambda m: m.group(1) + quoted + m.group(2), src, count=1)
open(path, "w", encoding="utf-8").write(src)
PYEOF
echo "  -> lib/core.py updated"

# 5. Compile the notification applet from the committed template so macOS
#    attributes notifications to "Sundial" instead of a script runner.
echo "[5/8] compiling watcher/Sundial.app ..."
TMP_APPLESCRIPT="$(mktemp -t sundial-notifier)"
sed "s|__NOTIFY_TXT__|$PROJ/data/notify.txt|" \
  "$PROJ/watcher/notifier.applescript.tmpl" > "$TMP_APPLESCRIPT"
osacompile -o "$PROJ/watcher/Sundial.app" "$TMP_APPLESCRIPT"
rm -f "$TMP_APPLESCRIPT"
echo "  -> watcher/Sundial.app compiled"

# 5b. Fix applet identity/icon/signing so Notification Center actually
#     registers it (delivery-incident fixes, 2026-07-03): no
#     CFBundleIdentifier meant the applet could never register; a stray
#     CFBundleIconName shadowed our CFBundleIconFile so the custom icon
#     never showed.
APPLET="$PROJ/watcher/Sundial.app"
plutil -replace CFBundleIdentifier -string "com.sundial.notifier" "$APPLET/Contents/Info.plist"
plutil -replace CFBundleIconFile -string "applet" "$APPLET/Contents/Info.plist"
plutil -remove CFBundleIconName "$APPLET/Contents/Info.plist" 2>/dev/null || true
if [[ -f "$PROJ/assets/sundial-logo.png" ]]; then
  ICONWORK="$(mktemp -d -t sundial-iconset)"
  ICONSET="$ICONWORK/applet.iconset"
  mkdir -p "$ICONSET"
  for sz in 16 32 128 256 512; do
    sz2x=$((sz * 2))
    sips -z "$sz" "$sz" "$PROJ/assets/sundial-logo.png" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null
    sips -z "$sz2x" "$sz2x" "$PROJ/assets/sundial-logo.png" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$ICONWORK/applet.icns"
  cp "$ICONWORK/applet.icns" "$APPLET/Contents/Resources/applet.icns"
  rm -rf "$ICONWORK"
  echo "  -> custom icon installed from assets/sundial-logo.png"
fi
codesign --force --sign - "$APPLET"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APPLET" || true
echo "  -> Sundial.app: bundle id set, icon fixed, codesigned, re-registered with Launch Services"
echo "  -> NOTE: macOS will show a one-time permission prompt for Sundial — click Allow. If you use an external display, enable System Settings > Notifications > 'Allow notifications when mirroring or sharing'. Recommended style: Alerts."

# 6. Install the launchd watcher: runs watcher/watcher.py every 10 minutes.
echo "[6/8] installing the launchd watcher..."
PLIST="$HOME/Library/LaunchAgents/com.sundial.watcher.plist"
PY_BIN="$(command -v python3)"
# launchd stdio MUST live outside the project tree. If $PROJ is under a
# TCC-protected location (~/Desktop, ~/Documents, ~/Downloads), macOS stamps
# every file created there with a `com.apple.macl` xattr recording the creating
# process's TCC identity. launchd can then no longer open that file once the
# identity stops matching — which happens at reboot — and the job dies with
# EX_CONFIG (78) before the program ever runs, leaving an EMPTY log and no other
# symptom. Deleting the log "fixes" it only until the next restart.
# Verified 2026-07-21: /bin/echo -> a fresh file in $PROJ/data exits 0, while
# /bin/echo -> the macl-stamped watcher.log in the SAME directory exits 78.
LOG_DIR="$HOME/Library/Logs/sundial"
mkdir -p "$LOG_DIR"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.sundial.watcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY_BIN</string>
    <string>$PROJ/watcher/watcher.py</string>
  </array>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/watcher.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/watcher.log</string>
</dict>
</plist>
PLISTEOF
launchctl bootout "gui/$(id -u)/com.sundial.watcher" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "  -> loaded: $PLIST"

# 7. Register the SessionStart / UserPromptSubmit hooks in
#    ~/.claude/settings.json without touching any other keys or hook types.
echo "[7/8] wiring Claude Code hooks into ~/.claude/settings.json ..."
python3 - "$PROJ" <<'PYEOF'
import json
import sys
from pathlib import Path

proj = sys.argv[1]
settings_path = Path.home() / ".claude" / "settings.json"
settings_path.parent.mkdir(parents=True, exist_ok=True)

settings = {}
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError:
        settings = {}
if not isinstance(settings, dict):
    settings = {}

hooks = settings.setdefault("hooks", {})
hooks["SessionStart"] = [{
    "matcher": "startup|resume|clear|compact",
    "hooks": [{"type": "command", "command": f"python3 {proj}/hooks/session_start.py"}],
}]
hooks["UserPromptSubmit"] = [{
    "hooks": [{"type": "command", "command": f"python3 {proj}/hooks/prompt_submit.py"}],
}]

settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PYEOF
echo "  -> hooks.SessionStart / hooks.UserPromptSubmit registered"

# 8. Verify: full test suite, then a live notification test.
echo "[8/8] verifying install..."
python3 "$PROJ/tests/test_sundial.py"
python3 "$PROJ/watcher/watcher.py" --test

cat <<BANNER

== Sundial is set up for $NAME ==

The first notification may need your OK: System Settings -> Notifications ->
allow "Sundial".

Start a new Claude Code session to see the <sundial> context block.

Optional: drop meeting_apps.txt, watch_roots.txt, chime.txt, or speak.txt in
data/ to tune meeting detection, folder-watch roots, or sound (see setup.sh
header). A SwiftBar menu-bar face is also available: copy
contrib/sundial.30s.sh into your SwiftBar plugin folder and set SUNDIAL_HOME
to $PROJ.
BANNER
