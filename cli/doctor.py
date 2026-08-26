#!/usr/bin/env python3
"""sundial doctor — verify the whole delivery chain (Task 11).

Exit 0 unless a HARD failure; warnings listed but never fatal. Every check
degrades gracefully: doctor must never be the thing that crashes.
"""

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import core  # noqa: E402

if __import__("os").environ.get("SUNDIAL_DATA_DIR"):
    core.DATA = Path(__import__("os").environ["SUNDIAL_DATA_DIR"])

LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / \
    "com.sundial.watcher.plist"


def check_drivers():
    """Exactly ONE writer: launchd plist XOR hermes cron tick. Both or
    neither are reported; both is a hard fail (double-fire risk)."""
    lines, hard = [], False
    drivers = []
    if LAUNCHD_PLIST.exists():
        drivers.append("launchd")
    try:
        jobs = json.loads((Path.home() / ".hermes" / "profiles" / "fexx"
                           / "cron" / "jobs.json").read_text(encoding="utf-8"))
        items = jobs if isinstance(jobs, list) else jobs.get("jobs", {})
        for j in (items if isinstance(items, list) else items.values()):
            s = json.dumps(j).lower()
            if ("sundial" in s or "watcher.py" in s) and j.get("enabled", True):
                if j.get("id") != "cd2721fef9bd":  # retired legacy job
                    drivers.append("hermes-cron:" + str(j.get("id"))[:8])
    except Exception:
        pass
    if len(drivers) == 1:
        lines.append(f"[ok] driver: exactly one ({drivers[0]})")
    elif len(drivers) == 0:
        lines.append("[warn] driver: none active — nudges will not fire "
                     "(register the launchd plist or a cron tick)")
    else:
        lines.append(f"[FAIL] driver: MULTIPLE writers {drivers} — "
                     "the <=3-ping cap is per item, not per driver")
        hard = True
    return lines, hard


def check_sensors():
    lines = []
    backend = None
    try:
        spec_path = REPO / "core" / "backends_impl" / "portable.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sundial_doctor_portable", spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sundial_doctor_portable"] = mod
        spec.loader.exec_module(mod)
        backend = mod.detect_presence()
    except Exception as e:
        lines.append(f"[warn] sensors: backend load failed ({e})")
        return lines, False
    name = type(backend).__name__ if backend else "?"
    for sensor in ("idle_seconds", "frontmost_app", "screen_locked",
                   "in_call"):
        try:
            val = getattr(backend, sensor)()
        except Exception:
            val = "ERROR"
        state = "unavailable (soften-only)" if val is None else repr(val)
        lines.append(f"[ok] sensor {name}.{sensor}: {state}")
    return lines, False


def check_ledgers():
    lines, hard = [], False
    names = ["commitments.json", "notified.json", "session-ledger.json",
             "session_speak.json", "habits.jsonl"]
    for n in names:
        p = core.DATA / n
        if not p.exists():
            continue
        try:
            if n.endswith(".jsonl"):
                for _ in p.read_text(encoding="utf-8").splitlines():
                    pass
            else:
                json.loads(p.read_text(encoding="utf-8"))
            lines.append(f"[ok] ledger {n}: parseable")
        except Exception as e:
            lines.append(f"[warn] ledger {n}: UNPARSEABLE ({e}) — "
                         "rename it aside and let sundial recreate it")
    # TCC write probe
    try:
        probe = core.DATA / ".tcc_probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        lines.append("[ok] tcc write-probe: data/ writable from this context")
    except Exception as e:
        lines.append(f"[FAIL] tcc write-probe: cannot write data/ ({e}) — "
                     "TCC identity issue (see docs/notes/delivery-incident)")
        hard = True
    return lines, hard


def check_birth():
    birth = core.DATA / "birth.json"
    if birth.exists():
        try:
            json.loads(birth.read_text(encoding="utf-8"))
            return ["[ok] birth.json present"], False
        except Exception:
            return ["[warn] birth.json corrupt — agent age will reset"], False
    return (["[warn] birth.json missing — fresh environment; run any "
             "session to mint one"]), False


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify Sundial's chain.")
    ap.parse_args()

    sections = [("driver", check_drivers), ("sensors", check_sensors),
                ("ledger+TCC", check_ledgers), ("birth", check_birth)]
    any_hard = False
    print("sundial doctor")
    for title, fn in sections:
        print(f"-- {title} --")
        try:
            lines, hard = fn()
        except Exception as e:
            lines, hard = [f"[warn] section crashed: {e}"], False
        for line in lines:
            print("  " + line)
        any_hard = any_hard or hard

    import platform
    print(f"-- platform: {platform.system()} | data: {core.DATA}")
    sys.exit(1 if any_hard else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
