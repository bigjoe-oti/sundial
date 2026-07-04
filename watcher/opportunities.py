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


OPP_TTL_DAYS = 14
DECLINE_SUPPRESS_AT = 3


def prune_ledger(now) -> bool:
    """Drop terminal ledger records (expired/fulfilled/declined) once they've
    aged past OPP_TTL_DAYS. Open/live records are never touched regardless
    of age. Returns True iff anything was dropped."""
    with core._ledger_lock():
        items = load_ledger()
        keep, dropped = [], False
        for r in items:
            terminal = r.get("status") in ("expired", "fulfilled", "declined")
            det = core.parse_iso(r.get("detected_at"))
            stale = (det is not None and
                     (now - det).total_seconds() >= OPP_TTL_DAYS * 86400)
            if terminal and stale:
                dropped = True
                continue
            keep.append(r)
        if dropped:
            save_ledger(keep)
        return dropped


PREP_DAILY_CAP_DEFAULT = 2


def prep_enabled() -> bool:
    return (core.DATA / "prep_enabled").exists()


def prep_budget() -> int:
    try:
        return int((core.DATA / "prep_budget.txt").read_text(
            encoding="utf-8").strip())
    except (OSError, ValueError):
        return PREP_DAILY_CAP_DEFAULT


def prep_allowed(today: str) -> bool:
    prefs = core.read_json(_prefs_path(), {})
    daily = prefs.get("prep", {}) if isinstance(prefs, dict) else {}
    if daily.get("date") != today:
        # fail-closed: a fresh day still honors a zero/negative budget
        return prep_budget() > 0
    return int(daily.get("count", 0)) < prep_budget()


def count_prep(today: str) -> None:
    with core._ledger_lock():
        prefs = core.read_json(_prefs_path(), {})
        if not isinstance(prefs, dict):
            prefs = {}
        daily = prefs.get("prep", {})
        if daily.get("date") != today:
            daily = {"date": today, "count": 0}
        daily["count"] = int(daily.get("count", 0)) + 1
        prefs["prep"] = daily
        core.write_json(_prefs_path(), prefs)


def build_prep_prompt(rec: dict) -> str:
    ev = rec.get("evidence", {})
    return (
        "You are preparing a minutes-of-meeting scaffold. A meeting on "
        f"{ev.get('app', 'a conferencing app')} started at "
        f"{ev.get('started', 'unknown time')}. Produce a clean markdown "
        "scaffold with: title line, date/time/platform header, Attendees "
        "(placeholder list), Agenda (3 placeholder items), Discussion "
        "notes (empty bullets), Decisions (empty), Action items table "
        "(owner/action/due). Write ONLY the scaffold markdown.")


def decline_kind(kind: str) -> int:
    """Record one decline for `kind` in the manners prefs; returns the new
    count so callers can echo '(n/3 to suppress)'."""
    with core._ledger_lock():
        prefs = core.read_json(_prefs_path(), {})
        if not isinstance(prefs, dict):
            prefs = {}
        declined = prefs.get("declined", {})
        declined[kind] = int(declined.get(kind, 0)) + 1
        prefs["declined"] = declined
        core.write_json(_prefs_path(), prefs)
        return declined[kind]


def allow_kind(kind: str) -> None:
    """Reset a kind's decline count to 0, re-enabling its offers."""
    with core._ledger_lock():
        prefs = core.read_json(_prefs_path(), {})
        if isinstance(prefs, dict) and kind in prefs.get("declined", {}):
            prefs["declined"][kind] = 0
            core.write_json(_prefs_path(), prefs)


def kind_suppressed(kind: str) -> bool:
    """True once `kind` has been declined DECLINE_SUPPRESS_AT+ times without
    an intervening allow_kind()."""
    prefs = core.read_json(_prefs_path(), {})
    if not isinstance(prefs, dict):
        return False
    return int(prefs.get("declined", {}).get(kind, 0)) >= DECLINE_SUPPRESS_AT


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


BUILD_MIN_S = 60


def detect_build_finished(current: dict, state: dict, now) -> "tuple[list, dict]":
    """Compare this cycle's live build-tool pids (current, from
    presence.sample_ps -> presence.parse_ps_builds) against the pids tracked
    LAST cycle (state, str(pid)-keyed). A pid tracked last cycle but absent
    now just finished; short-lived processes (recorded etime_s < BUILD_MIN_S)
    are noise -- a `make` that ran for 30s never earned a notification. New
    state is simply `current`, restringified, ready to persist as-is."""
    events = []
    for pid_s, info in (state or {}).items():
        try:
            pid = int(pid_s)
        except (TypeError, ValueError):
            continue
        if pid in current:
            continue
        etime_s = info.get("etime_s", 0)
        if etime_s >= BUILD_MIN_S:
            events.append({"kind": "build-finished", "cmd": info.get("cmd"),
                           "duration_s": etime_s})
    new_state = {str(pid): info for pid, info in current.items()}
    return events, new_state


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


FS_WINDOW_S = 1260
CURIOSITY_CAP = 5

IGNORE_PARTS = ("node_modules", ".git", "__pycache__", "venv", ".venv",
                "dist", "build", ".next", ".cache")


def _ignored_prefixes() -> tuple:
    try:
        raw = (core.DATA / "ignore_paths.txt").read_text(encoding="utf-8")
        return tuple(line.strip() for line in raw.splitlines() if line.strip())
    except OSError:
        return ()


def _ignored(path: str, root=None) -> bool:
    """True when any path COMPONENT (not substring) is a known junk dir or
    hidden (starts with '.'). 'distX' is a real folder, not 'dist'. When
    `root` is given, only components RELATIVE to root are checked -- a
    watch root that itself lives under a dotdir (e.g. ~/.config/apps) must
    not blind every child under it. Falls back to the full-path check if
    `path` isn't actually under `root`. Additionally, any path under a
    prefix listed in data/ignore_paths.txt is ignored outright -- built for
    excluding Sundial's own repos from its curiosity (self-noise)."""
    for prefix in _ignored_prefixes():
        if str(path).startswith(prefix):
            return True
    p = Path(path)
    if root is not None:
        try:
            p = p.relative_to(root)
        except ValueError:
            pass
    return any(part in IGNORE_PARTS or part.startswith(".")
               for part in p.parts)


def mdfind_available() -> bool:
    return Path("/usr/bin/mdfind").exists()


def mdfind_recent(root, window_s, runner) -> list:
    """Ask Spotlight for recent creations/additions under root. runner is
    injected (args list -> stdout str) so tests never touch mdfind."""
    queries = (
        f'kMDItemContentType == "public.folder" && '
        f'kMDItemFSCreationDate >= $time.now(-{int(window_s)})',
        f'kMDItemDateAdded >= $time.now(-{int(window_s)})',
    )
    out, seen = [], set()
    for q in queries:
        try:
            raw = runner(["/usr/bin/mdfind", "-onlyin", str(root), q])
        except Exception:
            continue
        for line in (raw or "").splitlines():
            line = line.strip()
            if (not line or line == str(root) or line in seen
                    or _ignored(line, root)):
                continue
            seen.add(line)
            out.append(line)
            if len(out) >= CURIOSITY_CAP:
                return out
    return out


def detect_recent_fs(roots, runner) -> list:
    events, total = [], 0
    for root in roots:
        for path in mdfind_recent(Path(root), FS_WINDOW_S, runner):
            events.append({"kind": "curiosity", "folder": path,
                           "via": "mdfind"})
            total += 1
            if total >= CURIOSITY_CAP:
                return events
    return events


def detect_new_folders(roots, known: dict):
    events, new_known = [], dict(known)
    for root in roots:
        try:
            names = sorted(p.name for p in Path(root).iterdir()
                           if p.is_dir() and not p.name.startswith(".")
                           and p.name not in IGNORE_PARTS)
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
