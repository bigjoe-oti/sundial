#!/bin/bash
# <bitbar.title>Sundial</bitbar.title>
# <bitbar.version>v1.0</bitbar.version>
# <bitbar.desc>One-glance Sundial status: presence, open asks, offers.</bitbar.desc>
# <bitbar.abouturl>https://github.com/</bitbar.abouturl>
#
# SwiftBar menu-bar face for Sundial. Read-only: never writes to data/,
# never signals the watcher. Refreshes every 30s (see filename).
#
# Install: SwiftBar copies plugin scripts into its own plugin folder, so this
# file cannot locate the project relative to itself. Instead it reads
# SUNDIAL_HOME (default: "$HOME/sundial") -- point it at wherever you cloned
# this project, either by exporting SUNDIAL_HOME before SwiftBar launches
# (e.g. in a launchd wrapper) or by editing the default below.

SUNDIAL_HOME="${SUNDIAL_HOME:-$HOME/sundial}"
DATA_DIR="$SUNDIAL_HOME/data"
PROJECT_DIR="$SUNDIAL_HOME"

# --- reads: fail-silent, never crash, never block --------------------------

presence_info() {
    python3 -c '
import json
try:
    with open("'"$DATA_DIR"'/presence.json") as f:
        state = json.load(f).get("state")
except Exception:
    state = None
symbols = {"here": "◉", "elsewhere": "◎", "away": "○"}
print(symbols.get(state, "∅") + "|" + (state or "unknown"))
' 2>/dev/null
}

open_asks_count() {
    python3 -c '
import json
try:
    with open("'"$DATA_DIR"'/commitments.json") as f:
        items = json.load(f)
    n = sum(1 for c in items
            if c.get("status") == "open" and c.get("kind") == "awaiting-reply")
except Exception:
    n = 0
print(n)
' 2>/dev/null
}

offers_count() {
    python3 -c '
import json
try:
    with open("'"$DATA_DIR"'/opportunities.json") as f:
        items = json.load(f)
    n = sum(1 for o in items if o.get("status") == "offered")
except Exception:
    n = 0
print(n)
' 2>/dev/null
}

IFS='|' read -r PRESENCE_SYM PRESENCE_WORD <<< "$(presence_info)"
OPEN_COUNT="$(open_asks_count)"
OFFER_COUNT="$(offers_count)"

# If a read came back truly empty (python3 missing, hard crash), degrade
# the whole bar to the bare sun glyph rather than show broken/partial text.
if [ -z "$PRESENCE_SYM" ] || [ -z "$OPEN_COUNT" ] || [ -z "$OFFER_COUNT" ]; then
    echo "☉"
    exit 0
fi

# --- menu bar line -----------------------------------------------------

LINE="☉ ${PRESENCE_SYM}"
if [ "$OPEN_COUNT" -gt 0 ] 2>/dev/null; then
    LINE="${LINE} ${OPEN_COUNT}⏳"
fi
if [ "$OFFER_COUNT" -gt 0 ] 2>/dev/null; then
    LINE="${LINE} ${OFFER_COUNT}✋"
fi
echo "$LINE"

# --- dropdown ------------------------------------------------------------

echo "---"
echo "Presence: ${PRESENCE_WORD}"
echo "Open asks: ${OPEN_COUNT}"
echo "Offers: ${OFFER_COUNT}"
echo "---"
echo "Open Sundial folder | bash=open param1=${PROJECT_DIR} terminal=false"
