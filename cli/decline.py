#!/usr/bin/env python3
"""decline -- tell Sundial to stop offering a kind of opportunity."""

import argparse
import sys
from pathlib import Path

WATCHER_DIR = Path(__file__).resolve().parent.parent / "watcher"
sys.path.insert(0, str(WATCHER_DIR))
import opportunities  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="Decline an opportunity kind (e.g. meeting-start, curiosity).")
    ap.add_argument("kind", help="opportunity kind to decline")
    args = ap.parse_args()

    n = opportunities.decline_kind(args.kind)
    print(f"declined {args.kind} ({n}/{opportunities.DECLINE_SUPPRESS_AT} to suppress)")


if __name__ == "__main__":
    main()
