"""Task 13: sundial status --json — one read-only payload for every
at-a-glance surface (SwiftBar, waybar, Hermes). Never writes data/, never
signals the watcher."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import core  # noqa: E402

if __import__("os").environ.get("SUNDIAL_DATA_DIR"):
    core.DATA = Path(__import__("os").environ["SUNDIAL_DATA_DIR"])


def _presence(data):
    try:
        doc = core.read_json(data / "presence.json", {})
        return doc.get("state") if isinstance(doc, dict) else None
    except Exception:
        return None


def build_status(data) -> dict:
    now = core.now_utc()
    asks = [c for c in core.load_commitments()
            if c.get("status") == "open" and c.get("kind") == "awaiting-reply"]
    # actionable offers exclude curiosity (passive context — never a badge)
    offers = []
    try:
        opps = core.read_json(data / "opportunities.json", [])
        if isinstance(opps, list):
            offers = [o for o in opps
                      if o.get("status") == "offered"
                      and o.get("kind") != "curiosity"]
    except Exception:
        pass

    estimate_at_risk = None
    try:
        items = core.load_commitments()
        now2 = datetime.now(timezone.utc)
        for c in items:
            est = c.get("est")
            if not (isinstance(est, dict) and est.get("p90_s")):
                continue
            created = core.parse_iso(c.get("created_at"))
            due = core.parse_iso(c.get("due_at"))
            p90 = est["p90_s"]
            text = str(c.get("text", ""))[:60]
            if due is not None:
                remaining = (due - now2).total_seconds()
                if remaining < p90:
                    estimate_at_risk = {
                        "text": text, "reason": "deadline-tight",
                        "remaining_s": max(remaining, 0), "p90_s": p90}
                    break
            elif created is not None and (now2 - created).total_seconds() > p90:
                estimate_at_risk = {"text": text, "reason": "over-p90",
                                    "elapsed_s": (now2 - created).total_seconds(),
                                    "p90_s": p90}
                break
    except Exception:
        pass

    queued = 0
    try:
        doc = core.read_json(data / "session_speak.json", {})
        q = doc.get("queue") if isinstance(doc, dict) else None
        if isinstance(q, list):
            queued = sum(1 for e in q
                         if isinstance(e, dict) and not e.get("consumed"))
    except Exception:
        pass

    snoozed = bool(core.snooze_active(now))

    return {
        "presence": _presence(data),
        "open_asks": len(asks),
        "actionable_offers": len(offers),
        "estimate_at_risk": estimate_at_risk,
        "snoozed": snoozed,
        "session_queue": queued,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only status snapshot.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    status = build_status(core.DATA)
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
