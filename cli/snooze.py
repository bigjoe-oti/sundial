#!/usr/bin/env python3
"""snooze -- owner-declared quiet window: hold popups and sound.

  sundial snooze 45m     hold delivery for 45 minutes
  sundial snooze off     clear the window
  sundial snooze         show status

Detection and ledgers keep running; only delivery is held. A HIGH-tier
commitment past its wall ceiling still breaks through (honesty rail)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core       # noqa: E402
import estimator  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Hold notification delivery.")
    ap.add_argument("duration", nargs="?", default=None,
                    help="e.g. 45m, 2h — or 'off' to clear; omit for status")
    args = ap.parse_args()
    p = core.DATA / "snooze.json"
    now = core.now_utc()

    if args.duration is None:
        try:
            s = core.read_json(p, None)
            until = core.parse_iso(s.get("until")) if isinstance(s, dict) else None
            if until and now < until:
                print(f"snoozed for another {core.humanize_delta((until - now).total_seconds())}.")
            else:
                print("not snoozed.")
        except Exception:
            print("not snoozed.")
        return

    if args.duration == "off":
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        print("snooze cleared.")
        core.refresh_menubar()
        return

    secs = estimator.parse_duration(args.duration)
    if not secs or secs <= 0:
        print(f"can't parse duration {args.duration!r} (try 45m, 2h).")
        sys.exit(1)
    from datetime import timedelta
    core.write_json(p, {"until": (now + timedelta(seconds=secs)).isoformat(),
                        "set_at": now.isoformat()})
    print(f"snoozed for {core.humanize_delta(secs)}. high-tier wall-ceiling "
          "fires still break through.")
    core.refresh_menubar()


if __name__ == "__main__":
    main()
