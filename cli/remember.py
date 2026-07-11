#!/usr/bin/env python3
"""remember -- record a ripening commitment with an optional due date."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core       # noqa: E402
import estimator  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Record a ripening commitment.")
    ap.add_argument("text", help="what the commitment is")
    ap.add_argument("--due", default=None,
                    help="YYYY-MM-DD (end of that local day) or full ISO datetime")
    ap.add_argument("--source", default="manual", help="where it came from")
    ap.add_argument("--est", default=None,
                    help="your raw duration guess, e.g. 45m, 1h30m (default: due - now)")
    ap.add_argument("--bucket", default=None,
                    help="task-shape bucket, e.g. build/research/ops/write")
    args = ap.parse_args()

    if args.est is not None and estimator.parse_duration(args.est) is None:
        ap.error(f"bad --est '{args.est}': use e.g. 30m, 1h, 1h30m")

    rec = core.add_commitment(args.text, args.due, args.source,
                              est_str=args.est, bucket=args.bucket)
    due = core.parse_iso(rec["due_at"])
    when = (due.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due else "no due date")
    print(f"recorded [{rec['id']}] {rec['text']}  (due: {when})")
    snap = rec.get("est")
    if snap:
        ttd = ((due - core.parse_iso(rec["created_at"])).total_seconds()
               if due else None)
        line = estimator.sanity_line(snap["est_s"], ttd, snap)
        if line:
            print(line)
    core.refresh_menubar()


if __name__ == "__main__":
    main()
