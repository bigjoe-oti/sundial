#!/usr/bin/env python3
"""estimate -- calibrate a raw duration guess into P50/P90 for both clocks,
grounded in the agent's measured history. Read-only over the ledger."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core        # noqa: E402
import estimator   # noqa: E402


def _fmt(seconds):
    return "?" if seconds is None else core.humanize_delta(seconds)


def main():
    ap = argparse.ArgumentParser(prog="sundial estimate")
    ap.add_argument("task", help="short description of the task")
    ap.add_argument("--raw", required=True,
                    help="raw duration guess, e.g. 30m, 1h, 1800s")
    ap.add_argument("--bucket", default=None, help="optional task-type bucket")
    args = ap.parse_args()

    raw_s = estimator.parse_duration(args.raw)
    if raw_s is None:
        print(f"bad --raw '{args.raw}': use e.g. 30m, 1h, 1800s", file=sys.stderr)
        sys.exit(2)

    t = estimator.estimate_timeline(raw_s, core.DATA, bucket=args.bucket)
    ex, rv = t["execution"], t["review"]
    bucket_note = f', bucket={ex["bucket"]}' if ex["bucket"] else ""
    print(f'Estimate — "{args.task}"   (raw {_fmt(raw_s)})')
    print(f'  My execution:  P50 ~{_fmt(ex["p50_s"])}   P90 ~{_fmt(ex["p90_s"])}'
          f'     (n={ex["n"]}, {ex["confidence"]} confidence{bucket_note})')
    if rv["confidence"] == "none":
        print("  Your review:   unknown (no review-latency data yet)")
    else:
        print(f'  Your review:   P50 ~{_fmt(rv["p50_s"])}   P90 ~{_fmt(rv["p90_s"])}'
              f'     (n={rv["n"]}, {rv["confidence"]} confidence)')
    print(f'  End-to-end:    P50 ~{_fmt(t["end_to_end_p50_s"])}'
          f'   P90 ~{_fmt(t["end_to_end_p90_s"])}')


if __name__ == "__main__":
    main()
