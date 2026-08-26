#!/bin/bash
# Sundial SwiftBar plugin v3 — thin formatter over `sundial status --json`.
# All ledger-parsing logic moved to cli/status.py (read-only, single source).
#
# <bitbar.title>Sundial</bitbar.title>
# <bitbar.version>v3.0</bitbar.version>
# <bitbar.desc>One-glance Sundial status: presence, open asks & offers, and your own estimate / at-risk timing.</bitbar.desc>
# <bitbar.author>J. Servo</bitbar.author>
# <bitbar.abouturl>https://github.com/bigjoe-oti/sundial</bitbar.abouturl>
#
# Install: COPY this file into your SwiftBar plugin folder as a REAL file --
# NOT a symlink (SwiftBar freezes symlinked plugins). Export SUNDIAL_HOME or
# edit the fallback below.

_SELF="$(readlink "$0" 2>/dev/null || echo "$0")"
_ROOT="$(cd "$(dirname "$_SELF")/.." 2>/dev/null && pwd)"
[ -d "$_ROOT/data" ] || _ROOT="$HOME/sundial"
SUNDIAL_HOME="${SUNDIAL_HOME:-$_ROOT}"

# status.py resolves lib/ relative to ITS OWN location, so always run the
# repo copy; SUNDIAL_DATA_DIR points it at the installed data/ dir.
STATUS_JSON="$(SUNDIAL_DATA_DIR="$SUNDIAL_HOME/data" \
  python3 "${SUNDIAL_HOME%/}/cli/status.py" --json 2>/dev/null)"

# Degradation rule: any failure -> bare sun glyph, no partial text.
if [ -z "$STATUS_JSON" ]; then
  echo "○"
  exit 0
fi

echo "$STATUS_JSON" | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except Exception:
    print("○"); raise SystemExit

# Presence dot: Apple system colors, flat (adaptive bar style)
state = s.get("presence")
if state == "here":
    sym, color = "●", "#34C759"
elif state == "elsewhere":
    sym, color = "●", "#FF9500"
else:
    sym, color = "○", "#8E8E93"

line = sym
n_asks = int(s.get("open_asks") or 0)
n_offers = int(s.get("actionable_offers") or 0)
if n_asks > 0:
    line += f" {n_asks}⏳"
if n_offers > 0:
    line += f" {n_offers}✋"

risk = s.get("estimate_at_risk")
if risk:
    line += " ⏱"

if s.get("snoozed"):
    line += " 😴"
q = int(s.get("session_queue") or 0)
if q > 0:
    line += f" 🗣{q}"

print(line + f" | color={color}")

print("---")
print(f"{sym} Presence: {state or chr(117)+chr(110)+chr(107)} | color={color}")
if s.get("snoozed"):
    print("😴 snoozed | color=orange")
if q > 0:
    print(f"🗣 {q} queued for session | color=orange")
print("---")
if risk:
    reason = risk.get("reason")
    text = risk.get("text", "")
    if reason == "deadline-tight":
        print(f"⏱ {text} — at risk: deadline tighter than P90 | color=red")
    else:
        print(f"⏱ {text} — running past P90 | color=red")
else:
    print("No estimated commitment at risk")
'
