#!/usr/bin/env bash
# Sundial Linux setup: systemd user timer (10-min watcher tick) + env notes.
# Notification delivery uses notify-send; sensors degrade to None where the
# desktop lacks them (xprintidle/loginctl/xdotool) — None softens, never blocks.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")" && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "ERROR: systemd not found (timer requires it)" >&2; exit 1; }

UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/sundial-watcher.service" <<EOF
[Unit]
Description=Sundial watcher cycle

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 $PROJ/watcher/watcher.py
EOF

cat > "$UNIT_DIR/sundial.timer" <<EOF
[Unit]
Description=Run Sundial watcher every 10 minutes

[Timer]
OnCalendar=*-*-* *:00/10
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now sundial.timer

echo "Sundial Linux setup complete:"
echo "  - timer: systemctl --user status sundial.timer"
echo "  - optional tools for richer sensing: xprintidle, xdotool, notify-send, spd-say"
echo "  - set SUNDIAL_TZ and SUNDIAL_WEBHOOK_URL as needed"
