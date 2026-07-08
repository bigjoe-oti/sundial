#!/usr/bin/env python3
"""answered -- disarm all open awaiting-reply commitments (human is back)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Disarm awaiting-reply nudges.")
    ap.add_argument("--quiet", action="store_true", help="hook mode: silent, always exit 0")
    args = ap.parse_args()

    try:
        n = core.close_awaiting()
    except Exception:
        if args.quiet:
            sys.exit(0)
        raise
    if not args.quiet:
        print(f"closed {n} awaiting-reply item(s).")
    core.refresh_menubar()


if __name__ == "__main__":
    main()
