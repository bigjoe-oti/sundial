#!/usr/bin/env python3
"""due -- list commitments that are overdue or ripening within the horizon."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="List ripe / overdue commitments.")
    ap.add_argument("--horizon-hours", type=int, default=24,
                    help="how far ahead counts as 'due' (default 24h)")
    args = ap.parse_args()

    due = core.due_commitments(args.horizon_hours)
    if not due:
        print("nothing due.")
        return
    for c, delta in due:
        tag = (f"OVERDUE by {core.humanize_delta(delta)}" if delta < 0
               else f"due in {core.humanize_delta(delta)}")
        print(f"[{c['id']}] {c['text']}  ({tag})")


if __name__ == "__main__":
    main()
