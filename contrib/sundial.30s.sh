#!/bin/bash
LOGO_B64="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"
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
    # Only ACTIONABLE offers (meeting minutes, build follow-ups). Curiosity
    # is passive context ("I noticed this file"), surfaced in the CLI
    # <opportunities> block -- it must not inflate the menu-bar badge.
    n = sum(1 for o in items
            if o.get("status") == "offered" and o.get("kind") != "curiosity")
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
    echo "☉ | image=${LOGO_B64}"
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
echo "$LINE | image=${LOGO_B64}"

# --- dropdown ------------------------------------------------------------

detail_lines() {
    python3 -c '
import json
D="'"$DATA_DIR"'"; P="'"$PROJECT_DIR"'"
CLICK=" | bash=open param1="+P+" terminal=false"
def clean(s): return str(s).replace("|","/").replace("\n"," ").strip()[:58]
try:
    offs=[o for o in json.load(open(D+"/opportunities.json"))
          if o.get("status")=="offered" and o.get("kind")!="curiosity"]
except Exception: offs=[]
try:
    asks=[c for c in json.load(open(D+"/commitments.json"))
          if c.get("status")=="open" and c.get("kind")=="awaiting-reply"]
except Exception: asks=[]
out=[]
if offs:
    out.append("Offers ("+str(len(offs))+"):")
    for o in offs[:8]:
        out.append("🤝 "+clean(o.get("offer_msg") or o.get("kind"))+CLICK)
if asks:
    out.append("Open asks ("+str(len(asks))+"):")
    for c in asks[:8]:
        out.append("❓ "+clean(c.get("text"))+CLICK)
if not offs and not asks:
    out.append("Nothing pending right now")
print("\n".join(out))
' 2>/dev/null
}

estimate_line() {
    python3 -c '
import json, datetime
try:
    with open("'"$DATA_DIR"'/commitments.json") as f:
        items = json.load(f)
    now = datetime.datetime.now(datetime.timezone.utc)
    best = None
    for c in items:
        est = c.get("est")
        if c.get("status") != "open" or not isinstance(est, dict):
            continue
        key = c.get("due_at") or "9999"
        if best is None or key < best[0]:
            best = (key, c, est)
    if best is not None:
        _, c, est = best
        created = datetime.datetime.fromisoformat(c["created_at"])
        elapsed = (now - created).total_seconds()
        p90 = est.get("p90_s")
        def h(s):
            s = int(s)
            if s >= 86400:
                return f"{s//86400}d{(s%86400)//3600}h"
            return (f"{s//3600}h{(s%3600)//60:02d}m" if s >= 3600
                    else f"{s//60}m")
        text = str(c.get("text", "")).replace("|", "/")[:40]
        due_raw = c.get("due_at")
        due = datetime.datetime.fromisoformat(due_raw) if due_raw else None
        if p90 is None:
            pass
        elif due is not None:
            # red = "start now or your own P90 says you miss the deadline"
            remaining = (due - now).total_seconds()
            if remaining < p90:
                print(f"⏱ {text} — at risk: due in {h(max(remaining, 0))},"
                      f" needs P90 {h(p90)} | color=red")
            else:
                print(f"⏱ {text} — P90 {h(p90)}, due in {h(remaining)}")
        elif elapsed > p90:
            print(f"⏱ {text} — over P90 {h(p90)} | color=red")
        else:
            print(f"⏱ {text} — P90 {h(p90)}, {h(elapsed)} in")
except Exception:
    pass
' 2>/dev/null
}

echo "---"
echo "Presence: ${PRESENCE_WORD}"
echo "---"
detail_lines
EST_LINE="$(estimate_line)"
[ -n "$EST_LINE" ] && echo "$EST_LINE"
echo "---"
echo "Open Sundial folder | bash=open param1=${PROJECT_DIR} terminal=false"
