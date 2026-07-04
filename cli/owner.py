#!/usr/bin/env python3
"""owner -- print the Owner Model, a deterministic distillation of the habit ledger."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "watcher"))
import owner_model  # noqa: E402


def main():
    m = owner_model.refresh(force=True)
    if m is None:
        print("no habit data yet.")
        return
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
