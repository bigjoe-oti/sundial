#!/usr/bin/env python3
"""now -- the sense. Clock-on-glance: current local time, age, working hours,
and how many commitments are ripe. Deterministic; never asks a model the time."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402
import tzutil  # noqa: E402


def main():
    local = core.now_local()
    birth = core.get_or_create_birth()
    born = core.parse_iso(birth["created_at"])
    born_local = born.astimezone(core.tzinfo()) if born else None
    working = tzutil.is_working_hours(local.hour, core.WORK_START, core.WORK_END)
    due = core.due_commitments()

    print(f"{local:%A %d %B %Y, %I:%M %p} ({local.tzname()}, {core.DEFAULT_TZ})")
    print(f"age: {core.humanize_age(birth['created_at'])}"
          + (f" (born {born_local:%d %b %Y})" if born_local else ""))
    print(f"working hours: {'yes' if working else 'no'}")
    print(f"commitments due/overdue: {len(due)}")
    for c, delta in due:
        tag = "OVERDUE" if delta < 0 else "due"
        print(f"  - [{tag}] {c['text']}")


if __name__ == "__main__":
    main()
