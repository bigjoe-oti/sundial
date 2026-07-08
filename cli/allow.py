#!/usr/bin/env python3
"""allow -- re-enable offers for a previously-declined opportunity kind."""

import argparse
import sys
from pathlib import Path

WATCHER_DIR = Path(__file__).resolve().parent.parent / "watcher"
sys.path.insert(0, str(WATCHER_DIR))
import opportunities  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="Re-enable offers for a declined opportunity kind.")
    ap.add_argument("kind", help="opportunity kind to re-enable")
    args = ap.parse_args()

    opportunities.allow_kind(args.kind)
    print(f"{args.kind} offers re-enabled.")
    core.refresh_menubar()


if __name__ == "__main__":
    main()
