#!/usr/bin/env python3
"""done -- mark a commitment as complete."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Mark a commitment as done.")
    ap.add_argument("id", help="commitment ID (the 8-char hex shown by 'due' or 'remember')")
    args = ap.parse_args()

    if core.resolve_commitment(args.id, "done"):
        print(f"marked [{args.id}] done.")
    else:
        print(f"no open commitment found with id [{args.id}].")
        sys.exit(1)
    core.refresh_menubar()


if __name__ == "__main__":
    main()