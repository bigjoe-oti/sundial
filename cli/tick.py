#!/usr/bin/env python3
"""sundial tick — record one dual-clock row (wall-ms × tokens) into the
session ledger (Task 7). Hermes runs this at session close or on demand.

  sundial tick --session <id> --wall-ms 45000 [--tokens 12345]

Tokens are NEVER fabricated: absent --tokens records null. Malformed input
exits 0 with no row written (fail-safe rule).

SUNDIAL_DATA_DIR repoints the ledger directory for isolated testing.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import core  # noqa: E402

if __import__("os").environ.get("SUNDIAL_DATA_DIR"):
    core.DATA = Path(__import__("os").environ["SUNDIAL_DATA_DIR"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Record a dual-clock tick.")
    ap.add_argument("--session", required=True)
    ap.add_argument("--wall-ms", required=True,
                    help="wall-clock milliseconds for this stretch")
    ap.add_argument("--tokens", default=None,
                    help="output tokens for the stretch; omit = unknown")
    args = ap.parse_args()

    try:
        wall_ms = int(args.wall_ms)
        if wall_ms < 0:
            return
        tokens = int(args.tokens) if args.tokens is not None else None
        if tokens is not None and tokens < 0:
            tokens = None
    except ValueError:
        return  # malformed input: no row, exit 0

    with core._ledger_lock():
        rows = core.load_ledger()
        rows.append({
            "session_id": str(args.session),
            "source": "hermes-tick",
            "wall_ms": wall_ms,
            "tokens": tokens,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        core._save_ledger(rows)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
