#!/bin/bash
LOGO_B64="iVBORw0KGgoAAAANSUhEUgAAACQAAAAkCAYAAADhAJiYAAAABGdBTUEAALGPC/xhBQAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAeGVYSWZNTQAqAAAACAAEARoABQAAAAEAAAA+ARsABQAAAAEAAABGASgAAwAAAAEAAgAAh2kABAAAAAEAAABOAAAAAAAAAEgAAAABAAAASAAAAAEAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAJKADAAQAAAABAAAAJAAAAAAZgdfLAAAACXBIWXMAAAsTAAALEwEAmpwYAAAMXElEQVRYCaVYCZBU1RU9f+nf3b+7mZ59EBnAGdABlOi4g1HQSnAptYpyKcvCROMSU27RaFwKR2OIpaQ0scpYGi0TXBCjRrBGkwgIIkYRlR2UZZhhmK2Z6Zle/++/5Nw/igsQNHnM0N2v33v3vHvPPff+UfB/jIULL9JOqJ6UiBpZo2d7D0qFotXcNSmjtLR4/+uxyvfdmFrVclRpMDWjoiwxzffRFNKV6o3tQ5H2nXtQXxUqer7Sq0DZmivkVyJkLjn12he3fB8b3wmQT0/kRzf9SINzg6rgjFIhFzVjMUDhdv6s2z6IXW17MKHWgE/r8mtZNrSQkef7pa6vPX5sx/h/fBfPqYdCv3XxHVNWWTWLTN1tDRvq2RoQ9aHCLhYBbfg+gotAAyAqTyzZNjwi4ZRp6Op5YdVtfV75ZNHyxy46+lD2hk88yKqB5XddmTDV37+3PpU8rCKMxtEJwPPwWfsQln3ag82dFtr78hjI2gRRonUFdckQGms0nNhYhrHV4QDo5j1FFBxgSn10oGD5v2y+ZsGzBzEplzjwyKy8+z4amKP4HgqWg1Wb+2FGdTzV2oaVG/phhICqEWF09Vs8wAP5BI3uqUro6M+WYJV8HDcuilknJlEoAceONRH6wqO2gznHXb3gNweyzAjsPzpab7rP1Jw5uqZDQpC3PTz1Vhvu/esWrNs5iKNGx3FEnYlwSIfteIFX4hENIV1DRSKEmjIduqZi1VZ6cmMWkZBKQFEYuhIA91x7+uyZR7nPtG5Z8W3r+wEaWn7nFRXJyCOFfBGuY2P3gIPZD6/Bove7MGXsCHpGgxkRIL5ED0OMRUNdFGEa25txAyAFyyPfFQJWCdbA0o2DWLurgOZxJgylBJf7RsSj068655htTy5ev/7roL5Bav/T+yfFouqjCi0lkwl07LVxydwPsKFtENOayrGlM4fyeIieUMgNua2A8lFXHglC5TJswSARhNQS0vZUESccEcNn3RZ+9fxu9GY8xM0wPNdXjJD/2I6FVx55QECS2m+v3vPQYN5JSpjS5MGNT24JuFBfbWIgJ6GJkE8uuUKL/AleeVpNMoyKuB4AlDnxjvy6vFgt9wzkgYZaE0x/zF3Uh0yBnlR95C2vfMX61Dyx/SWofR6y6ptmNI2Nn71ibR/WbE/jwZc+wyfbBrhRQVN9HLoOZAkmYmjBnGAK6SryRXcYUExHrugEPHFcagPXJaKhwPioqggmjo7Spo9NnUU8/c5eSOZ9tCOL8SMj527IG2fuB8hx7JtGVUWVC06tQ1dfEc/8cxcaR8YQC+vYtCuLs6ZUkEPxgOC8vLgcJRJ6VJWJwypNeiLE0IXh0CsOSeIyZgUmw6yptZSAGFZvy6LM1DGOUvDa6gHyrYQZExOoTuiKXXJu+AagwVUtjYaqzPCY3kKD197vDjRFDpCUL9ouWtf04+LTRuL85jJ+9gODslYyR9aJBmlE6rgKv6cMUDyvPGsUJo8ysGBlKkiAHL0ZC6tBAry9IRvYKpY8ehwzPnr2igYBFYSskN473XWsqFNy0NaVwztrezGJqS0ZxPVMfZXhcPHg39pw+pRazP5hBUoMS3/WI3lVJEn0OA3ZroqeQRd1lXHcf9kRiCgW7n+5I8gqIbnFzMxZPhprw/hgWw67qWGeSyd4jlkcTE/fByieMKepJIlVKmLpx10BOXVqiqpqiIY1prqK6rIIiRvGo290Y3JDFVouGomRSZWSqCBOGRAP2STP6cdU4YnrxqN/YBCPtPahLB4JuKTTDSIDUv/kVUL64eeD8JxSIKgRMzxNAOktLS2qptgTDUOFETGwsXMXKilukiUJKrO8+j5DwQNYyzCUs3HPczvx6NUTMPfSEBatGQgAy3e3XDgOP55ShtWbuvDw4lTgXUPUmfvjkRBD6lEuKBUMVllUw86Uj1hMso8Za3sTKSOK+ouzojEjpNSCC2kZu0noESYBMUw8KvCWfDessl6gut0DFu6ZvwPx8kpcfloN0qkUGsbUYeaUBNZs3o15b6RIaAUG1dojyUWzRK9KtCyvUmbiERVdA6XgfOGNrik16+bPZg1XCtENu4ZMl+7WeECahVIIGQzJbW7eO2QHoeO5cFgkLRbSjz7vxy1PbcHTN03G2k87kdvWhlHlGh5avBfbKILiXduhHskB3OiQPzLnsTYSE72iBByV9Bflpn6ZoyuUqO67lh8Oab5P12r8lRBZJLfj6lw4rMbiKrmlKgCpR6I/XIJqCqIg1vzScPaw4o5gKKSOyRpZzx0CCZ4qgeJ7nqNINL4Y4ZB4b9huhnO65oYLjXXIazxAdicpcJ7nUNgkZG5wmxqWAHG3GJKMo8Ji2tGVQSb1dPWgaWySRlhc+f0d5/uY+3of9qQ9klcASZgUhppiTO9oBBPhfJEf4wQ/rtogDLmgl7WdbF59fDm1zkWvuJXWMLrGRH/GCW5G3SMg3oAXCm7J4BeZ7+PqYrj30jHY9HknVTeNRLIcnT1p/LF1D2KJEQRVi8oYZYBhUhTyiJs9IY7Y4BAHDeQchjhEzws9eHW2vs3XLC6ozDLPcUsbrWIBmfQgJh5mIJ0r8QAvOFDaC1uCzE2iOZL+cy4ZjZWfduDel3uxensRRYqnQw8uWTeAW5/diUwphFvPq6aXybnhrYGHXZ4T8IenZYoeGqo1ZHN5lErsMF1nI/HSAkc+m1spLoiEIzizeVQQmqy0eJyUYipKLWT3PBfXzazFKyt247ev9vJQn0Bc1iuHgictB9A3aOO2v2zH5m4XPz+rkmBtimqJIXHJTTfIKjlTwnvC+KT03YH3cnnrXcESAIokK5dpeiSvsSEbw3DM+EE1NndkUOBBkrYCjtHEJadVY/6S3Vj0cSaIv0GCi8AN5UssvFK/hPBSPnz8eUkPPtxh47KpFTyDwOkq4U+Ji3b0FHHy+BgOl5CJvKihbDyRWLYPUHLqA9tLrrtEZdcnhXFmcw25wsOZdcmEEWjQDycnsfDdbnYCWZSTH0J6IWeeC/sz9AJLQoG9qeSG7EuEFby7aQArNg/hjInxwNtx1jxJ1CK5dQoBCXDpCvj69nE/e2HXPkDBG119bHtn1n+dhXVC/Qhcc84YbOzIoru/gPqaKFas70dHnwWThuSmFDLEKG4Zemd3qoDutE1wNkuIBzPkI8Q0j5FDWzsL+JhtRmNdGD0U1G3dRVx8cgX7ozCWbMqgrc/2KMx/EAwygpDJm/BbxpLuvuLic08eiabDY7ht1ngcP6GcN3exszsfkJHlLajkw9ki/KH0M3V708Uga+KsezarsZQH4YtwRRS+yIa/nZeR9ZPro7iCxVmeSM6YWIbeQefvk3+yYLlgkLEPkDzETT2y8te6oqSZYCwfOh65agLDo7Gc5FEWUQJix1hIpbZJFjOp+Z8S3HwvpUJm5J9MSzZJ6NL0YNzw2Q5bLBfA7edW0ota8D09nTppYvwOya4AzdcByYRyygOb2aDf7HGFVOvR1REsvPskHMdseP+zNJ80osEzmBgNdIWvQnapbSkCGlZmoiEi6TRTbINFaz5uK9AzEcy7vB61CY1hLoiTfXLphsaLn9smtr8cDMI3x++eXbn22gsnoSxmnhGOROkhHRdOHRX0Q8vJo960RS0yUD3CCOQhw7a1xPBkqSsCUpq1GAmfo1Ts7OUDIpu5WSeX4+4LRvI7tjQaCzdNZgr2Xadc/8oT37QeOPfbU8OfMyvv5IOiOkelFwYplP/emkYVgTzVuhNL2XdLd1jBxqybAIcHax3XSrOfzjtMdQSt66yTytmuOjh2TJQJwfuzkBVd/57mqxbMPZBlAXvQMfDO7T+Nm8a8d9b2VYwfaVKjTKEJdnQOYfm6HmztstHem2c3YMG2yBVmnTyHNVKBmxviOJyP3+KO9lQJXYMOTmwwU9TXW068+sXnDmb0vwKSTWsX3DjZdt0Hjp9QdoEQxpPsKRSD7AmZBEiEG3ak0d7WhYYagw5gVlnDGWUY4aDqS8nYuMd6NRHT7zr75pe3HgyMzB8SkCwiNZSh9+6aGVH964lpul3Ix8w4/xwjScoT1vOxqU3+HFMnZWC4wgsoPRThY6H/Lxfqn4698oUlXLovm+TcAw2K/6GHZAQw902ufLPv3TsneMXSdNXwpvL8JnYV1QqbKzZYPnUmTxHu4/pNect9j8+nS4+/9qXth7bw1Yrv5KGvlu/3Tuld1hJzC1a0s6PND0X1wpTZ83P7rfoeE/8BR6TbPcv/ircAAAAASUVORK5CYII="
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

echo "---"
echo "Presence: ${PRESENCE_WORD}"
echo "Open asks: ${OPEN_COUNT}"
echo "Offers: ${OFFER_COUNT}"
echo "---"
echo "Open Sundial folder | bash=open param1=${PROJECT_DIR} terminal=false"
