#!/usr/bin/env python3
"""ask -- arm an awaiting-reply commitment: the agent is blocked on the human."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Arm an awaiting-reply nudge.")
    ap.add_argument("text", help="the blocking question, summarized")
    ap.add_argument("--due", default="+10m",
                    help="+NNm/+NNh, YYYY-MM-DD, or ISO datetime (default +10m)")
    ap.add_argument("--source", default="agent-blocked", help="where it came from")
    ap.add_argument("--session", default=None, help="asking session id (informational)")
    ap.add_argument("--weight", choices=("low", "normal", "high"),
                    default="normal", help="urgency tier (default normal)")
    ap.add_argument("--confidence", type=float, default=None,
                    help="0..1 sureness in the default action if unanswered")
    ap.add_argument("--irreversible", action="store_true",
                    help="destructive/one-way; never auto-proceeds on silence")
    ap.add_argument("--default", dest="default_action", default=None,
                    help="action taken if you never answer (stated in the final rung)")
    args = ap.parse_args()

    if args.confidence is not None and not (0.0 <= args.confidence <= 1.0):
        ap.error("--confidence must be between 0 and 1")

    rec = core.add_commitment(args.text, args.due, args.source,
                              kind="awaiting-reply", session_id=args.session,
                              weight=args.weight, confidence=args.confidence,
                              irreversible=args.irreversible,
                              default_action=args.default_action)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    tier = rec.get("weight", "normal")
    print(f"armed [{rec['id']}] ({tier}) {rec['text']}  (rung 1 due: {when})")


if __name__ == "__main__":
    main()
