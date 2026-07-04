#!/usr/bin/env python3
"""Sundial opportunities — the executive-assistant layer's bookkeeping.

Pure, deterministic, no LLM: the ledger of detected moments and offers
(data/opportunities.json), the manners file (opportunity_prefs.json:
daily offer cap), and the Habit Ledger (habits.jsonl) that observes the
owner's rhythms for the future Owner Model. All IO fail-safe."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402

OFFER_DAILY_CAP = 5
HABITS_MAX_BYTES = 5_000_000

DEFAULT_MEETING_APPS = ("zoom.us", "Microsoft Teams", "MSTeams",
                        "FaceTime", "Webex", "Skype")


def _ledger_path():
    return core.DATA / "opportunities.json"


def load_ledger() -> list:
    items = core.read_json(_ledger_path(), [])
    return items if isinstance(items, list) else []


def save_ledger(items: list) -> None:
    core.write_json(_ledger_path(), items)


def evidence_key(kind: str, evidence: dict) -> str:
    return f"{kind}:{json.dumps(evidence, sort_keys=True, ensure_ascii=False)}"


def add_opportunity(kind: str, evidence: dict, offer_msg: str,
                    expires_at) -> "dict | None":
    with core._ledger_lock():
        items = load_ledger()
        key = evidence_key(kind, evidence)
        if any(evidence_key(r.get("kind", ""), r.get("evidence", {})) == key
               for r in items):
            return None
        rec = {"id": uuid.uuid4().hex[:8], "kind": kind,
               "detected_at": core.now_utc().isoformat(), "evidence": evidence,
               "status": "offered", "offer_msg": offer_msg,
               "expires_at": expires_at}
        items.append(rec)
        save_ledger(items)
        return rec


def open_offers(now) -> list:
    with core._ledger_lock():
        items = load_ledger()
        live, dirty = [], False
        for r in items:
            if r.get("status") != "offered":
                continue
            exp = core.parse_iso(r.get("expires_at"))
            if exp is not None and now >= exp:
                r["status"] = "expired"
                dirty = True
                continue
            live.append(r)
        if dirty:
            save_ledger(items)
        return live


def _prefs_path():
    return core.DATA / "opportunity_prefs.json"


def offer_allowed(today: str) -> bool:
    prefs = core.read_json(_prefs_path(), {})
    daily = prefs.get("daily", {}) if isinstance(prefs, dict) else {}
    if daily.get("date") != today:
        return True
    return int(daily.get("count", 0)) < OFFER_DAILY_CAP


def count_offer(today: str) -> None:
    # NOTE: offer_allowed() is a separate read — callers doing the
    # allowed-check + count pair must run both inside their own flow
    # (the lock here only protects the increment from lost updates).
    with core._ledger_lock():
        prefs = core.read_json(_prefs_path(), {})
        if not isinstance(prefs, dict):
            prefs = {}
        daily = prefs.get("daily", {})
        if daily.get("date") != today:
            daily = {"date": today, "count": 0}
        daily["count"] = int(daily.get("count", 0)) + 1
        prefs["daily"] = daily
        core.write_json(_prefs_path(), prefs)


def log_habit(event: dict) -> None:
    """Append one observation to the Habit Ledger. Never raises — a habit
    lost is better than a cycle broken."""
    try:
        p = core.DATA / "habits.jsonl"
        if p.exists() and p.stat().st_size >= HABITS_MAX_BYTES:
            p.replace(core.DATA / "habits.1.jsonl")
        e = dict(event)
        e.setdefault("ts", core.now_utc().isoformat())
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    except Exception:
        pass


def meeting_apps() -> tuple:
    extra = ()
    try:
        raw = (core.DATA / "meeting_apps.txt").read_text(encoding="utf-8")
        extra = tuple(line.strip() for line in raw.splitlines() if line.strip())
    except OSError:
        pass
    return DEFAULT_MEETING_APPS + extra


def webrtc_procs(triples) -> set:
    """Procs holding an assertion whose NAME contains 'webrtc' (matched
    case-insensitively) -- the Chromium discriminator for a live call
    ('WebRTC has active PeerConnections') vs. plain video playback
    ('Video Wake Lock'). Works for any process, not just allowlisted apps:
    this is how Meet-in-Chrome gets caught."""
    return {proc for proc, _kind, name in triples if "webrtc" in name.lower()}


def detect_meeting(display_procs: set, webrtc: set, active, now):
    apps = meeting_apps()
    allow_live = next((a for a in apps if a in display_procs), None)
    webrtc_set = set(webrtc)
    live_set = {a for a in apps if a in display_procs} | webrtc_set
    # Prefer the WebRTC-asserting proc when both an allowlisted app and a
    # WebRTC call are live at once -- it's the stronger signal.
    live = next(iter(sorted(webrtc_set)), None) or allow_live
    if active is None and live is not None:
        started = now.isoformat()
        return ([{"kind": "meeting-start", "app": live, "started": started}],
                {"app": live, "started": started})
    if active is not None and active.get("app") not in live_set:
        started = core.parse_iso(active.get("started"))
        dur = (now - started).total_seconds() if started else 0.0
        return ([{"kind": "meeting-end", "app": active.get("app", "?"),
                  "duration_s": dur, "ended": now.isoformat()}], None)
    return [], active


def watch_roots() -> tuple:
    try:
        raw = (core.DATA / "watch_roots.txt").read_text(encoding="utf-8")
        roots = tuple(Path(line.strip()).expanduser()
                      for line in raw.splitlines() if line.strip())
        if roots:
            return roots
    except OSError:
        pass
    return (Path.home() / "Desktop",)


def detect_new_folders(roots, known: dict):
    events, new_known = [], dict(known)
    for root in roots:
        try:
            names = sorted(p.name for p in Path(root).iterdir()
                           if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        key = str(root)
        if key not in new_known:
            new_known[key] = names          # baseline silently
            continue
        fresh = [n for n in names if n not in set(new_known[key])][:3]
        for n in fresh:
            events.append({"kind": "curiosity",
                           "folder": str(Path(root) / n)})
        # Only REPORTED names join known — overflow beyond the 3-cap stays
        # unknown and surfaces on later cycles instead of vanishing silently.
        new_known[key] = sorted(set(new_known[key]) | set(fresh))
    return events, new_known
