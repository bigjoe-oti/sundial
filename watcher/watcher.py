#!/usr/bin/env python3
"""Sundial — always-on local watcher.

Runs via launchd 24/7, even when no Claude session is open. Checks for
commitments that have come due and fires a LOCAL macOS desktop notification,
once per item. Fully local: notifications go through /usr/bin/osascript
(built in), never a cloud relay. Launch-shy: one ping per commitment. Sound
courtesy reads presence, not the clock: chimes/speech mute when the screen
is locked or you've been away 30+ minutes; popups and detection run around
the clock.

Usage:
  watcher.py            normal cycle (launchd runs this); runs any hour
  watcher.py --force    legacy flag, now a no-op (cycles always run)
  watcher.py --test     fire one test notification to prove the channel
"""

import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import core  # noqa: E402

import presence  # noqa: E402  (same directory)
import opportunities  # noqa: E402  (same directory)
import owner_model  # noqa: E402  (same directory)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import policy  # noqa: E402

UNSEEN_OFFSETS = (600, 1200, 3000)   # 10/20/50 min of not-seeing-the-chat
ELSEWHERE_WEIGHT = 0.5               # two busy minutes = one absent minute
WALL_CEILING_S = 5400                # 90 min: final rung fires regardless
CYCLE_S = 600
PRESENCE_FILE = core.DATA / "presence.json"
WELCOME_MIN_AWAY_S = 1200   # 20 min: a shorter return is a glance, not a departure

OSASCRIPT = "/usr/bin/osascript"
NOTIFIED = core.DATA / "notified.json"
SOUND_AWAY_MAX_S = 1800  # 30 min: mute audio once away this long (screen-lock mutes sooner)
NOTIFIED_TTL_DAYS = 7  # sweep closed commitments' entries once this stale

RUNG_OFFSETS = (0, 600, 2400)  # seconds after due_at -> T0+10/20/50min when due=T0+10m

BREAKPOINT_IDLE_S = 15   # named assumption: tuned during the proving week
DEFER_MAX_S = 180        # bounded deferral window (research-backed)
DEFER_POLL_S = 10


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
    """One presence snapshot per cycle. Tests monkeypatch THIS function.
    A locked screen is the hardest 'away' signal there is and dominates the
    idle/front-app heuristic outright: a lidless, humming Mac kept idle time
    low with background work can otherwise read as 'present' across a long
    absence -- lock cannot be fooled that way."""
    idle = presence.idle_seconds()
    front = presence.front_app() if idle is not None else None
    state = presence.derive_state(idle, front, presence.cli_apps(core.DATA))
    locked = sample_screen_locked()
    if locked is True:
        state = "away"
    return {"state": state, "idle_s": idle, "front_app": front,
            "locked": locked}


MEETING_MAX_PLAUSIBLE_S = 4 * 3600   # beyond this the machine slept, not met

OFFER_POOL = {
    "meeting-start": (
        "{owner}, meeting detected on {app}. Want minutes? Say the word in chat and hand me the transcript or your notes after.",
        "A {app} meeting just began. If you want an MOM out of it, I'm in — just tell me in chat.",
    ),
    "meeting-end": (
        "Meeting over ({duration_m}m on {app}). Hand me notes, a transcript, or a recording path and I'll draft the minutes.",
        "{owner}, that was {duration_m} minutes of {app}. Want the MOM drafted? Drop me the material in chat.",
    ),
    "meeting-end-stale": (
        "That {app} meeting wrapped a while back — want minutes from any notes you have?",
        "{owner}, an old {app} meeting finally closed out. If notes exist, I can still draft the MOM in chat.",
    ),
    "build-finished": (
        "Your {cmd} run just finished ({duration_m}m). Want me to look at the results?",
        "{owner}, {cmd} wrapped after {duration_m}m — shall I check the output / next step?",
    ),
}


def sample_assertions_raw() -> str:
    """One pmset assertions dump per cycle. Tests monkeypatch THIS. Kept as
    the raw string (not pre-parsed into a set) because run_cycle needs two
    different views of it: display-sleep procs (asserting_display_procs) AND
    WebRTC-call procs (assertion_triples -> webrtc_procs) -- a dedicated app
    like zoom.us and a browser tab running Google Meet are told apart by
    which of those two a proc shows up in."""
    return presence.assertions_raw()


def sample_screen_locked() -> "bool | None":
    """One screen-lock sample per cycle. Tests monkeypatch THIS function."""
    return presence.screen_locked()


def sample_net() -> "dict | None":
    """One vnstat sample per cycle. Tests monkeypatch THIS function."""
    return presence.net_sample()


def _mdfind_runner(args) -> str:
    r = subprocess.run(args, timeout=15, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def sample_recent_fs() -> list:
    """Curiosity sensor seam. Spotlight when available; legacy poller
    otherwise (same known_folders.json behavior as before)."""
    roots = opportunities.watch_roots()
    if opportunities.mdfind_available():
        return opportunities.detect_recent_fs(roots, _mdfind_runner)
    kf = core.read_json(core.DATA / "known_folders.json", {})
    if not isinstance(kf, dict):
        kf = {}
    events, new_kf = opportunities.detect_new_folders(roots, kf)
    core.write_json(core.DATA / "known_folders.json", new_kf)
    return events


def sample_builds() -> dict:
    """One ps sample per cycle, parsed into build-tool pid -> {cmd,etime_s}.
    Tests monkeypatch THIS function."""
    return presence.parse_ps_builds(presence.sample_ps())


def _desktop_root():
    return Path.home() / "Desktop"


def maybe_auto_watch(event_folder: str) -> None:
    try:
        p = Path(event_folder)
        if p.parent != _desktop_root():
            return
        wf = core.DATA / "watch_roots.txt"
        existing = ([line.strip() for line in
                     wf.read_text(encoding="utf-8").splitlines()
                     if line.strip()] if wf.exists()
                    else [str(r) for r in opportunities.watch_roots()])
        if str(p) in existing:
            return
        existing.append(str(p))
        wf.write_text("\n".join(existing) + "\n", encoding="utf-8")
        opportunities.log_habit({"kind": "auto-watch", "root": str(p)})
    except Exception:
        pass


def sound_allowed(state, prev_presence: dict, now) -> bool:
    """Courtesy reads the human, not the clock: no sounds when the screen
    is locked or the human has been away half an hour+. Popups are silent
    pixels and always allowed; this gates ONLY audio."""
    if sample_screen_locked() is True:
        return False
    if state == "away":
        since = core.parse_iso((prev_presence or {}).get("since"))
        if since is not None and (now - since).total_seconds() >= SOUND_AWAY_MAX_S:
            return False
    return True


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
    """True when this commitment's TIER wall ceiling has passed. Basis:
    created_at, falling back to due_at. Single source of truth for ripe_rung
    and run_cycle."""
    basis = core.parse_iso(c.get("created_at")) or core.parse_iso(c.get("due_at"))
    ceiling = policy.TIER_TABLE[policy.tier_of(c)]["ceiling"]
    return basis is not None and (now - basis).total_seconds() >= ceiling


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
    table = policy.TIER_TABLE[policy.tier_of(c)]
    if wall_ceiling_passed(c, now):
        return table["rungs"]
    ripe = 0
    for i, th in enumerate(table["offsets"], start=1):
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


def _spawn_prep_proc(cmd: list, cwd: str) -> None:
    """Fire-and-forget Haiku prep hand; tests replace this seam."""
    subprocess.Popen(cmd, cwd=cwd, stdout=open(cwd + "/scaffold.md", "w"),
                     stderr=subprocess.DEVNULL)


def _prep_claude_bin() -> "str | None":
    """Resolve the claude binary for silent prep: SUNDIAL_CLAUDE_BIN env
    override first, then a PATH lookup. None means no spawn -- prep must
    never guess at a hardcoded path."""
    return os.environ.get("SUNDIAL_CLAUDE_BIN") or shutil.which("claude")


def maybe_silent_prep(rec: dict, today: str) -> None:
    """Silent MOM-scaffold prep for a just-started meeting -- hard-flagged
    OFF by default (data/prep_enabled must exist). No notification, no
    offer: a scratch dir + prompt.txt is written and a cheap Haiku hand is
    spawned to draft a scaffold that just waits on disk for later. Whole
    body fails safe: a prep miss must never touch a commitment or cycle."""
    try:
        if not opportunities.prep_enabled():
            return
        if not opportunities.prep_allowed(today):
            return
        claude_bin = _prep_claude_bin()
        if claude_bin is None:
            opportunities.log_habit({"kind": "prep", "error": "no-binary"})
            return
        scratch = core.DATA / "opportunities" / rec["id"]
        scratch.mkdir(parents=True, exist_ok=True)
        prompt = opportunities.build_prep_prompt(rec)
        (scratch / "prompt.txt").write_text(prompt, encoding="utf-8")
        # fail-closed: charge the budget BEFORE spawning -- a persistent
        # count fault must bound spawns, not unleash them (worst case one
        # day-slot is charged without a spawn).
        opportunities.count_prep(today)
        _spawn_prep_proc(
            [claude_bin, "-p", prompt, "--model", "haiku"], str(scratch))
        opportunities.log_habit({"kind": "prep", "opp": rec["id"]})
    except Exception:
        pass


def chime(kind, state, audible=True) -> None:
    """Subtle escalating sound beside the popup. HERE: silent for rungs 1-2
    and the return nudge -- but rung 3 (the final/autonomy-consequence fire)
    always plays, since silence there would hide the moment that matters
    most. ELSEWHERE: whisper (x0.6). data/chime.txt: 'off' silences, a float
    scales. `audible=False` (screen locked or long away) mutes unconditionally
    -- courtesy reads presence, not the clock."""
    if not audible:
        return
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


def speak_final(message: str, audible=True) -> None:
    """Opt-in spoken final rung: only when data/speak.txt exists.
    `audible=False` mutes unconditionally, same courtesy gate as chime()."""
    if not audible:
        return
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


def wait_for_breakpoint(sampler, sleeper, start_front, *,
                        max_s=DEFER_MAX_S, poll_s=DEFER_POLL_S,
                        idle_only=False) -> "tuple[str, float]":
    """Bounded deferral: hold a ripe delivery until a natural task
    breakpoint. Polls `sampler()` (a sample_presence-shaped dict) every
    `poll_s` seconds via `sleeper`, up to `max_s`. Returns (reason, elapsed):
      pause   -- input went quiet (idle >= BREAKPOINT_IDLE_S); true on the
                 FIRST sample if the human is already mid-pause
      switch  -- frontmost app changed from `start_front` (suppressed when
                 idle_only: the 2-state degrade has no app sensor)
      bound   -- window expired; fire regardless (the honesty rail)
      degrade -- sensors failed mid-watch; fire now rather than guess
    Injectable sampler/sleeper keep this fully testable without real time."""
    elapsed = 0.0
    while True:
        snap = sampler()
        if snap.get("state") is None:
            return "degrade", elapsed
        idle = snap.get("idle_s")
        if idle is not None and idle >= BREAKPOINT_IDLE_S:
            return "pause", elapsed
        front = snap.get("front_app")
        if (not idle_only and front and start_front
                and front != start_front):
            return "switch", elapsed
        if elapsed >= max_s:
            return "bound", elapsed
        sleeper(poll_s)
        elapsed += poll_s


def run_cycle(force: bool = False) -> None:
    """`force` is accepted for call-site compatibility but no longer gates
    anything: cycles run 24/7 now. Sound courtesy reads presence, not the
    clock -- see sound_allowed()."""
    local = core.now_local()
    now = core.now_utc()
    snap = sample_presence()
    state, app = snap["state"], snap["front_app"]
    prev = record_presence(snap, now)
    audible = sound_allowed(snap["state"], {"state": snap["state"],
        "since": (prev.get("since") if prev.get("state") == snap["state"]
                  else now.isoformat())}, now)
    returned = (prev.get("state") == "away"
                and snap["state"] in ("here", "elsewhere"))
    away_since = core.parse_iso(prev.get("since")) if returned else None
    away_m = int((now - away_since).total_seconds() // 60) if away_since else 0
    # Welcome-back bridge (silent side): on a real return past the glance
    # threshold, drop a one-shot welcome_back.json for the prompt_submit hook
    # to read on the human's next keystroke. This writes NO notification --
    # the CLI greeting rides their own keystroke, so it can never be
    # unsolicited noise. Path from core.DATA at call time (test isolation).
    if returned and away_since is not None:
        away_s = (now - away_since).total_seconds()
        if away_s >= WELCOME_MIN_AWAY_S:
            try:
                # Serialize with the hook's claim so a write can't be clobbered
                # by a mid-claim consume. log_habit stays OUTSIDE the lock --
                # flock is not re-entrant across fds, nesting would deadlock.
                with core._ledger_lock():
                    core.write_json(core.DATA / "welcome_back.json", {
                        "unlocked_at": now.isoformat(), "away_s": away_s,
                        "front_app": snap["front_app"], "consumed": False})
                opportunities.log_habit({"kind": "welcome-back",
                                         "away_s": away_s,
                                         "front": snap["front_app"]})
            except Exception:
                pass
    notified = core.read_json(NOTIFIED, {})
    if not isinstance(notified, dict):
        notified = {}
    dirty = False
    batch = []          # (commitment, entry, rung, message, ceiling_forced)
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
                    chime("return", snap["state"], audible)
                    entry["count"], entry["last"] = ripe, now.isoformat()
                    notified[c["id"]] = entry
                    dirty = True
                    opportunities.log_habit({
                        "kind": "fire", "rung": "return", "state": snap["state"],
                        "defer_reason": "none", "deferred_s": 0.0,
                        "muted": (not audible)})
                continue  # return-nudge replaces the regular ping this cycle
            hit = pending_ping(c, entry, now, state, app)
            if hit is None:
                continue
            ceiling = wall_ceiling_passed(c, now)
            if state == "here" and not ceiling:
                continue  # hold: they can see the chat; ceiling overrides
            rung, message = hit
            batch.append((c, entry, rung, message, ceiling))
        except Exception:
            continue

    if batch:
        deferred_s, reason = 0.0, "none"
        if state in ("elsewhere", "present") or (
                state == "here" and any(b[4] for b in batch)):
            reason, deferred_s = wait_for_breakpoint(
                sample_presence, time.sleep, app,
                idle_only=(state == "present"))
            still_open = {x.get("id") for x in core.load_commitments()
                          if x.get("status") == "open"}
            batch = [b for b in batch if b[0].get("id") in still_open]
        fire_now = core.now_utc()
        for c, entry, rung, message, _ceiling in batch:
            try:
                desktop_notify("Sundial", message)
                chime(rung, state, audible)
                if rung == 3:
                    speak_final(message, audible)
                entry["count"], entry["last"] = rung, fire_now.isoformat()
                entry["deferred_s"], entry["defer_reason"] = deferred_s, reason
                notified[c["id"]] = entry
                dirty = True
                opportunities.log_habit({
                    "kind": "fire", "rung": rung, "state": state,
                    "defer_reason": reason, "deferred_s": deferred_s,
                    "muted": (not audible)})
            except Exception:
                continue

    # --- opportunities: detect, offer, observe (never breaks commitments) --
    try:
        # presence-transition habit
        if prev.get("state") != snap["state"]:
            opportunities.log_habit({"kind": "presence",
                                     "from": prev.get("state"),
                                     "to": snap["state"],
                                     "front": snap.get("front_app")})
        today = local.strftime("%Y-%m-%d")
        # net telemetry: one vnstat sample per cycle. None (no vnstat, no
        # traffic data) degrades silently -- no habit line, no crash.
        net = sample_net()
        if net is not None:
            opportunities.log_habit({"kind": "net", "iface": net["iface"],
                                     "rx_Bps": net["rx_Bps"],
                                     "tx_Bps": net["tx_Bps"]})
        # meetings
        ms = core.read_json(core.DATA / "meeting_state.json", {})
        active = ms.get("active") if isinstance(ms, dict) else None
        raw = sample_assertions_raw()
        display_procs = presence.asserting_display_procs(raw)
        webrtc = opportunities.webrtc_procs(presence.assertion_triples(raw))
        events, new_active = opportunities.detect_meeting(
            display_procs, webrtc, active, now)
        core.write_json(core.DATA / "meeting_state.json",
                        {"active": new_active})
        if new_active is not None and net is not None:
            # Owner-Model material: real calls show sustained symmetric
            # traffic -- corroborates the meeting sensor every cycle it's
            # active, not just on the start/end transitions below.
            opportunities.log_habit({"kind": "meeting-net",
                                     "app": new_active.get("app"),
                                     "rx_Bps": net["rx_Bps"],
                                     "tx_Bps": net["tx_Bps"]})
        for evt in events:
            kind = evt["kind"]
            stale = (kind == "meeting-end"
                     and evt.get("duration_s", 0) > MEETING_MAX_PLAUSIBLE_S)
            # a start makes other-app start offers moot (single active
            # meeting assumption); an end answers ITS OWN app's start offer.
            # Either way the open meeting-start records to expire are:
            expire_start_for = ("other" if kind == "meeting-start" else "same")
            with core._ledger_lock():
                items = opportunities.load_ledger()
                dirty_l = False
                for r in items:
                    if (r.get("kind") != "meeting-start"
                            or r.get("status") != "offered"):
                        continue
                    same_app = (r.get("evidence", {}).get("app")
                                == evt.get("app"))
                    if same_app == (expire_start_for == "same"):
                        r["status"] = "expired"
                        dirty_l = True
                if dirty_l:
                    opportunities.save_ledger(items)
            fields = {"app": evt.get("app", "?")}
            pool_key = kind
            if kind == "meeting-end":
                if stale:
                    pool_key = "meeting-end-stale"   # duration would mislead
                else:
                    fields["duration_m"] = int(evt.get("duration_s", 0) // 60)
            msg = pick_message(uuid.uuid4().hex[:8], OFFER_POOL[pool_key],
                               text="", **fields)
            expiry = ((now + timedelta(seconds=1800)).isoformat()
                      if kind == "meeting-end" else None)
            evidence = dict(evt)
            evidence.pop("kind", None)
            if kind == "meeting-start":
                # net snapshot rides along as corroborating evidence; the
                # started timestamp already makes evidence unique per real
                # meeting, so this extra key never affects dedup.
                evidence["net"] = net
            # evidence carries started/ended timestamps -> each real meeting
            # is unique; dedup only stops same-event re-offers
            rec = opportunities.add_opportunity(kind, evidence, msg, expiry)
            if (rec and not stale and opportunities.offer_allowed(today)
                    and not opportunities.kind_suppressed(kind)):
                desktop_notify("Sundial", msg)
                chime("return", state, audible)
                opportunities.count_offer(today)
            if rec:
                habit = {"kind": "offer", "opp": kind, "app": evt.get("app")}
                if stale:
                    habit["stale"] = True
                opportunities.log_habit(habit)
            if kind == "meeting-start" and rec:
                maybe_silent_prep(rec, today)
        # builds: the compiler/test/docker run that just finished
        build_state = core.read_json(core.DATA / "build_state.json", {})
        if not isinstance(build_state, dict):
            build_state = {}
        b_events, new_build_state = opportunities.detect_build_finished(
            sample_builds(), build_state, now)
        core.write_json(core.DATA / "build_state.json", new_build_state)
        for evt in b_events:
            cmd, duration_s = evt.get("cmd"), evt.get("duration_s", 0)
            evidence = {"cmd": cmd, "duration_s": duration_s,
                        "ended": now.isoformat()}
            msg = pick_message(uuid.uuid4().hex[:8],
                               OFFER_POOL["build-finished"], text="",
                               cmd=cmd, duration_m=int(duration_s // 60))
            rec = opportunities.add_opportunity(
                "build-finished", evidence, msg,
                (now + timedelta(seconds=3600)).isoformat())
            if (rec and opportunities.offer_allowed(today)
                    and not opportunities.kind_suppressed("build-finished")):
                desktop_notify("Sundial", msg)
                chime("return", state, audible)
                opportunities.count_offer(today)
            if rec:
                opportunities.log_habit({"kind": "offer",
                                         "opp": "build-finished", "cmd": cmd})
        # curiosity
        for evt in sample_recent_fs():
            rec = opportunities.add_opportunity(
                "curiosity",
                {"folder": evt["folder"], **({"via": evt["via"]}
                                             if evt.get("via") else {})},
                evt["folder"],
                (now + timedelta(seconds=86400)).isoformat())
            if rec:
                maybe_auto_watch(evt["folder"])
                opportunities.log_habit({"kind": "curiosity",
                                         "folder": evt["folder"]})
        if opportunities.prune_ledger(now):
            pass
        owner_model.refresh()
    except Exception:
        pass

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
