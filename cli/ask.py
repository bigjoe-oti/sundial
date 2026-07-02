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
    args = ap.parse_args()

    rec = core.add_commitment(args.text, args.due, args.source,
                              kind="awaiting-reply", session_id=args.session)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    print(f"armed [{rec['id']}] {rec['text']}  (rung 1 due: {when})")


if __name__ == "__main__":
    main()
