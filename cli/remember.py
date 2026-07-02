#!/usr/bin/env python3
"""remember -- record a ripening commitment with an optional due date."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Record a ripening commitment.")
    ap.add_argument("text", help="what the commitment is")
    ap.add_argument("--due", default=None,
                    help="YYYY-MM-DD (end of that local day) or full ISO datetime")
    ap.add_argument("--source", default="manual", help="where it came from")
    args = ap.parse_args()

    rec = core.add_commitment(args.text, args.due, args.source)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    print(f"recorded [{rec['id']}] {rec['text']}  (due: {when})")


if __name__ == "__main__":
    main()
