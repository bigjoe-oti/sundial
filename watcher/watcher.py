#!/usr/bin/env python3
"""Sundial — always-on local watcher.

Runs via launchd even when no Claude session is open. Checks for commitments
that have come due and fires a LOCAL macOS desktop notification, once per item.
Fully local: notifications go through /usr/bin/osascript (built in), never a
cloud relay. Launch-shy: one ping per commitment, quiet hours respected.

Usage:
  watcher.py            normal cycle (launchd runs this); respects quiet hours
  watcher.py --force    run the cycle ignoring quiet hours (for testing)
  watcher.py --test     fire one test notification to prove the channel
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402

import presence  # noqa: E402  (same directory)

UNSEEN_OFFSETS = (600, 1200, 3000)   # 10/20/50 min of not-seeing-the-chat
ELSEWHERE_WEIGHT = 0.5               # two busy minutes = one absent minute
WALL_CEILING_S = 5400                # 90 min: final rung fires regardless
CYCLE_S = 600
PRESENCE_FILE = core.DATA / "presence.json"

OSASCRIPT = "/usr/bin/osascript"
NOTIFIED = core.DATA / "notified.json"
NOTIFY_START, NOTIFY_END = 8, 22  # waking hours (local); stay silent outside
NOTIFIED_TTL_DAYS = 7  # sweep closed commitments' entries once this stale

RUNG_OFFSETS = (0, 600, 2400)  # seconds after due_at -> T0+10/20/50min when due=T0+10m


def owner_name() -> str:
    try:
        name = (core.DATA / "owner.txt").read_text(encoding="utf-8").strip()
        return name or "Friend"
    except OSError:
        return "Friend"


RUNG_POOLS = (
    (   # rung 1 — warm knock
        "{owner} — I'm blocked on: {text}",
        "A question ripened while you were away: {text}",
        "Your agent is standing at the door with: {text}",
        "Tick. You left mid-thought: {text}",
        "I can wait, but the idea can't: {text}",
    ),
    (   # rung 2 — firmer
        "Still blocked (20m): {text}",
        "Second knock, {owner}. The kettle's been whistling 20 minutes: {text}",
        "Twenty minutes of me staring at the ceiling: {text}",
        "{owner}, the question is starting to echo (20m): {text}",
    ),
    (   # rung 3 — final; EVERY entry must state the autonomy consequence
        "Final nudge (50m): {text} — proceeding on my judgment or standing down.",
        "Last call, {owner} (50m): {text} — I take it from here or park it.",
        "50 minutes. The clock strikes autonomy: {text} — my call now, or the shelf.",
        "Three knocks is my limit, {owner} (50m): {text} — deciding without you.",
    ),
)

PLAIN_POOL = (
    "Due now: {text}",
    "It's time: {text}",
    "This one just ripened: {text}",
    "You promised, I remembered: {text}",
)

ELSEWHERE_POOLS = (
    (   # rung 1 — cheeky, app-aware
        "{owner}, I know you're busy with {app} — I won't take much of your time, I just need your call on: {text}",
        "I can see {app} has you. One opinion and I'll vanish: {text}",
        "Whatever {app} is doing, it can spare you ten seconds: {text}",
    ),
    (   # rung 2
        "Second knock, {owner} — {app} is lovely, but this is still waiting: {text}",
        "Me and {app} are now competing for you (20m): {text}",
        "{owner}, blink twice if {app} is holding you hostage (20m): {text}",
    ),
    (   # rung 3 — final; every entry states the autonomy consequence
        "{owner}, {app} can wait one beat — final call on: {text} — deciding without you otherwise.",
        "Last call, {owner} (50m): {text} — I take it from here or park it.",
        "Even {app} thinks you should answer this (50m): {text} — my call now, or the shelf.",
    ),
)

RETURN_POOL = (
    "While you were away ({away_m}m): {text}",
    "Welcome back, {owner}. This ripened in your absence: {text}",
    "You were gone {away_m} minutes. The question aged well: {text}",
)


MAX_NOTIFICATION_LEN = 300
MAX_TEXT_FIELD_LEN = 200


def _cap_message(msg: str) -> str:
    """Backstop cap on the WHOLE notification/spoken string so nothing
    pathological can ever blow up desktop_notify or say."""
    if len(msg) > MAX_NOTIFICATION_LEN:
        return msg[:MAX_NOTIFICATION_LEN] + "…"
    return msg


def pick_message(commitment_id: str, pool: tuple, **fields) -> str:
    """Deterministic per-item voice: same commitment always gets the same
    line. Formatting is fail-safe: a bad template falls back to the pool's
    classic first entry, then to the bare text. The single choke point for
    every fired message, so length-capping here covers pending_ping's rungs
    and the return-nudge alike. The TEXT field is truncated first (200
    chars) BEFORE formatting -- every rung-3 template puts the autonomy
    consequence AFTER {text}, so a whole-message cap alone would slice off
    the clause that matters most; the 300-char whole-message cap stays as a
    backstop only."""
    try:
        idx = int(commitment_id, 16) % len(pool)
    except (ValueError, TypeError):
        idx = 0
    fields.setdefault("owner", owner_name())
    text = str(fields.get("text", ""))
    if len(text) > MAX_TEXT_FIELD_LEN:
        fields["text"] = text[:MAX_TEXT_FIELD_LEN] + "…"
    for candidate in (pool[idx], pool[0]):
        try:
            return _cap_message(candidate.format(**fields))
        except (KeyError, IndexError, ValueError):
            continue
    return _cap_message(str(fields.get("text", "")))


def migrate_entry(value) -> dict:
    """notified.json values: legacy bare ISO string -> {'count': 1, 'last': str}.
    Non-int counts and missing absence fields degrade to fresh defaults so
    callers never guard against bad types."""
    if isinstance(value, dict) and isinstance(value.get("count"), int):
        e = dict(value)
    elif isinstance(value, str):
        e = {"count": 1, "last": value}
    else:
        e = {"count": 0, "last": None}
    for k in ("unseen_s", "here_s"):
        if not isinstance(e.get(k), (int, float)):
            e[k] = 0.0
    e.setdefault("last_cycle", None)
    return e


def sample_presence() -> dict:
    """One presence snapshot per cycle. Tests monkeypatch THIS function."""
    idle = presence.idle_seconds()
    front = presence.front_app() if idle is not None else None
    state = presence.derive_state(idle, front, presence.cli_apps(core.DATA))
    return {"state": state, "idle_s": idle, "front_app": front}


def accrue(entry: dict, state, now, created) -> None:
    """Advance an item's unseen/here clocks by the gap since last cycle.
    A gap far beyond the cycle interval means the machine slept: sleep
    counts as away. HERE and PRESENT pause the unseen clock."""
    prev = core.parse_iso(entry.get("last_cycle")) or created
    gap = max(0.0, (now - prev).total_seconds())
    eff = "away" if gap > 2 * CYCLE_S else state
    if eff == "away":
        entry["unseen_s"] = entry.get("unseen_s", 0.0) + gap
    elif eff == "elsewhere":
        entry["unseen_s"] = entry.get("unseen_s", 0.0) + gap * ELSEWHERE_WEIGHT
    elif eff in ("here", "present"):
        entry["here_s"] = entry.get("here_s", 0.0) + gap
    # state None: no accrual — legacy plain-due-date path handles ripeness
    entry["last_cycle"] = now.isoformat()


def wall_ceiling_passed(c: dict, now) -> bool:
    """True when the 90-min wall ceiling has passed for this commitment.
    Basis: created_at, falling back to due_at (the only field due_commitments
    guarantees). Single source of truth for ripe_rung and run_cycle."""
    basis = core.parse_iso(c.get("created_at")) or core.parse_iso(c.get("due_at"))
    return basis is not None and (now - basis).total_seconds() >= WALL_CEILING_S


def ripe_rung(c: dict, entry: dict, now, state) -> int:
    """Highest ripe rung index (0 = nothing ripe). Encapsulates: plain cap,
    legacy degrade (state None -> v1.5 wall semantics), unseen thresholds,
    and the 90-minute wall ceiling."""
    if c.get("kind") != "awaiting-reply":
        due = core.parse_iso(c.get("due_at"))
        if due is None or entry.get("count", 0) >= 1:
            return 0
        return 1 if (now - due).total_seconds() >= 0 else 0
    if state is None and entry.get("last_cycle") is None:
        # Full legacy degrade: pre-absence-clock / brand-new entry with no
        # accrual history to judge ripeness by -- fall back to v1.5's
        # wall-elapsed-since-due offsets.
        due = core.parse_iso(c.get("due_at"))
        if due is None:
            return 0
        elapsed = (now - due).total_seconds()
        ripe = 0
        for i, off in enumerate(RUNG_OFFSETS, start=1):
            if elapsed >= off:
                ripe = i
        return ripe
    # state is None but the entry HAS accrual history (a sensor blip mid
    # ladder), or state is known: never wall-fallback here -- that would
    # discard real accrual history for a false instant rung-3. Judge
    # ripeness on unseen_s thresholds accrued so far; the wall ceiling still
    # overrides regardless.
    if wall_ceiling_passed(c, now):
        return 3
    ripe = 0
    for i, th in enumerate(UNSEEN_OFFSETS, start=1):
        if entry.get("unseen_s", 0.0) >= th:
            ripe = i
    return ripe


def pending_ping(c: dict, entry: dict, now, state, app) -> "tuple[int, str] | None":
    """The single highest ripe, not-yet-sent rung for a commitment, or None."""
    if c.get("status") != "open" or core.parse_iso(c.get("due_at")) is None:
        return None
    ripe = ripe_rung(c, entry, now, state)
    if ripe <= entry.get("count", 0):
        return None
    text, cid = c.get("text", ""), c.get("id", "")
    if c.get("kind") != "awaiting-reply":
        return ripe, pick_message(cid, PLAIN_POOL, text=text)
    if state == "elsewhere" and app:
        pool = ELSEWHERE_POOLS[ripe - 1]
        return ripe, pick_message(cid, pool, text=text, app=app)
    return ripe, pick_message(cid, RUNG_POOLS[ripe - 1], text=text)


NOTIFIER_APP = Path(__file__).resolve().parent / "Sundial.app"
NOTIFY_TXT = core.DATA / "notify.txt"

CHIME_MAP = {1: ("Tink", 0.35), 2: ("Glass", 0.5), 3: ("Hero", 0.6),
             "return": ("Purr", 0.35)}
SOUNDS_DIR = "/System/Library/Sounds"


def _spawn(cmd) -> None:
    """Fire-and-forget subprocess; tests replace this seam."""
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def chime(kind, state) -> None:
    """Subtle escalating sound beside the popup. HERE: silent for rungs 1-2
    and the return nudge -- but rung 3 (the final/autonomy-consequence fire)
    always plays, since silence there would hide the moment that matters
    most. ELSEWHERE: whisper (x0.6). data/chime.txt: 'off' silences, a float
    scales."""
    try:
        if kind not in CHIME_MAP:
            return
        if state == "here" and kind != 3:
            return
        name, vol = CHIME_MAP[kind]
        cfg = None
        try:
            cfg = (core.DATA / "chime.txt").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if cfg == "off":
            return
        if cfg:
            try:
                vol *= float(cfg)
            except ValueError:
                pass
        if state == "elsewhere":
            vol *= 0.6
        _spawn(["/usr/bin/afplay", "-v", f"{vol:.2f}",
                f"{SOUNDS_DIR}/{name}.aiff"])
    except Exception:
        pass


def speak_final(message: str) -> None:
    """Opt-in spoken final rung: only when data/speak.txt exists."""
    try:
        voice = (core.DATA / "speak.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return
    try:
        cmd = (["/usr/bin/say", "-v", voice, message] if voice
               else ["/usr/bin/say", message])
        _spawn(cmd)
    except Exception:
        pass


def desktop_notify(title: str, message: str) -> bool:
    # Prefer the compiled applet: macOS attributes its notifications to
    # "Sundial" instead of Script Editor. The applet reads title (line 1)
    # and message (rest) from data/notify.txt, so `open` launches are brief
    # and argument-free. Fall back to raw osascript if the applet is missing.
    if NOTIFIER_APP.exists():
        try:
            NOTIFY_TXT.write_text(f"{title}\n{message}", encoding="utf-8")
            r = subprocess.run(["/usr/bin/open", "-g", "-a", str(NOTIFIER_APP)],
                               timeout=10, capture_output=True, text=True)
            if r.returncode == 0:
                # open() is async; give the applet a beat to read the file
                # before a subsequent ping in the same cycle overwrites it.
                subprocess.run(["/bin/sleep", "1"], timeout=5)
                return True
        except Exception:
            pass  # fall through to osascript

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    try:
        r = subprocess.run([OSASCRIPT, "-e", script], timeout=10,
                           capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


def record_presence(snap: dict, now) -> dict:
    """Persist the presence sample; return the PREVIOUS record ({} if none)
    for transition detection. 'since' survives while the state is unchanged —
    it marks when the current state began.

    Path derives from core.DATA at call time (not the import-time
    PRESENCE_FILE constant) so tests that redirect core.DATA are isolated
    automatically — an unpatched test once stamped the LIVE presence.json."""
    pf = core.DATA / "presence.json"
    prev = core.read_json(pf, {})
    if not isinstance(prev, dict):
        prev = {}
    since = (prev.get("since") if prev.get("state") == snap["state"]
             else now.isoformat())
    core.write_json(pf, {
        "state": snap["state"], "since": since,
        "idle_s": snap["idle_s"], "front_app": snap["front_app"]})
    return prev


def _sweep_notified(notified: dict, open_ids: set, now) -> bool:
    """Drop notified.json entries for commitments that are no longer open
    AND whose entry is stale (>= NOTIFIED_TTL_DAYS old). An entry with a
    missing or unparseable 'last' is kept -- staleness can't be proven, so
    don't guess. Mutates `notified` in place; returns True if anything was
    dropped."""
    stale = []
    for cid, entry in notified.items():
        if cid in open_ids:
            continue
        last_str = entry.get("last") if isinstance(entry, dict) else entry
        last = core.parse_iso(last_str) if isinstance(last_str, str) else None
        if last is None:
            continue
        if (now - last).total_seconds() >= NOTIFIED_TTL_DAYS * 86400:
            stale.append(cid)
    for cid in stale:
        del notified[cid]
    return bool(stale)


def run_cycle(force: bool = False) -> None:
    local = core.now_local()
    if not force and not (NOTIFY_START <= local.hour < NOTIFY_END):
        return  # quiet hours: stay silent
    now = core.now_utc()
    snap = sample_presence()
    state, app = snap["state"], snap["front_app"]
    prev = record_presence(snap, now)
    returned = (prev.get("state") == "away"
                and snap["state"] in ("here", "elsewhere"))
    away_since = core.parse_iso(prev.get("since")) if returned else None
    away_m = int((now - away_since).total_seconds() // 60) if away_since else 0
    notified = core.read_json(NOTIFIED, {})
    if not isinstance(notified, dict):
        notified = {}
    dirty = False
    for c, _delta in core.due_commitments(0):  # overdue / due-now only
        try:
            entry = migrate_entry(notified.get(c["id"]))
            if state is not None and c.get("kind") == "awaiting-reply":
                created = (core.parse_iso(c.get("created_at"))
                           or core.parse_iso(c.get("due_at")) or now)
                accrue(entry, state, now, created)
                notified[c["id"]] = entry
                dirty = True
            if returned and c.get("kind") == "awaiting-reply":
                ripe = ripe_rung(c, entry, now, snap["state"])
                if ripe >= 1 and ripe > entry.get("count", 0):
                    msg = pick_message(c.get("id", ""), RETURN_POOL,
                                       text=c.get("text", ""), away_m=away_m)
                    desktop_notify("Sundial", msg)
                    chime("return", snap["state"])
                    entry["count"], entry["last"] = ripe, now.isoformat()
                    notified[c["id"]] = entry
                    dirty = True
                continue  # return-nudge replaces the regular ping this cycle
            hit = pending_ping(c, entry, now, state, app)
            if hit is None:
                continue
            rung, message = hit
            if state == "here" and not wall_ceiling_passed(c, now):
                continue  # hold: they can see the chat; ceiling overrides
            desktop_notify("Sundial", message)
            chime(rung, state)
            if rung == 3:
                speak_final(message)
            entry["count"], entry["last"] = rung, now.isoformat()
            notified[c["id"]] = entry
            dirty = True
        except Exception:
            continue
    open_ids = {c.get("id") for c in core.load_commitments()
                if c.get("status") == "open"}
    if _sweep_notified(notified, open_ids, now):
        dirty = True
    if dirty:
        core.write_json(NOTIFIED, notified)


def main() -> None:
    if "--test" in sys.argv:
        ok = desktop_notify(
            "Sundial",
            "Sundial watcher test. If you see this, the channel works and nothing left your machine.")
        print("notification dispatched" if ok else "notification FAILED")
        return
    run_cycle(force="--force" in sys.argv)


if __name__ == "__main__":
    main()
