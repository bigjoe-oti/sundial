#!/usr/bin/env python3
"""UserPromptSubmit hook: (1) disarm awaiting-reply nudges -- the human is
back; (2) ambient time signal -- inject now + elapsed since the previous
prompt. Fail-safe by construction: any error exits 0 with no output, so a
clock bug can never block a prompt.

Machine-generated re-invocations (background task notifications) also flow
through this hook. They are NOT the human: they must neither disarm nudges
nor advance the last-prompt stamp, so they are detected and skipped entirely.
"""

import json
import re
import sys
from pathlib import Path

# Markers the harness injects into machine-generated "prompts". A human
# quoting one of these verbatim at the very start of a message is the
# accepted (rare, harmless) false-positive cost.
MACHINE_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION")

# Claude Code appends this to every away_summary recap; strip it so the
# harvested resume line reads clean.
AWAY_TAIL = re.compile(r"\s*\(disable recaps.*?\)\s*$", re.I)
WELCOME_TTL_S = 1800  # 30 min: an older welcome-back self-consumes, no stale greeting


def is_machine_event(prompt: str) -> bool:
    """True when the prompt is a harness re-invocation, not the human typing."""
    head = prompt.lstrip()[:400]
    return any(marker in head for marker in MACHINE_MARKERS)


def read_own_away_summary(transcript_path):
    """The last `away_summary` recap Claude Code wrote for THIS session, or
    None. Per-session by construction: the hook is handed its own
    transcript_path, so with two windows open we never greet with the wrong
    session's story (the globally-newest transcript is not necessarily the
    one being returned to)."""
    if not transcript_path:
        return None
    try:
        found = None
        # errors="replace": a partially-written / non-UTF-8 transcript must
        # degrade to None, never raise into the prompt path.
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):   # bare null/number/list rows
                    continue
                if (r.get("type") == "system"
                        and r.get("subtype") == "away_summary"):
                    c = r.get("content")
                    if isinstance(c, str) and c.strip():
                        found = AWAY_TAIL.sub("", c).strip()
        return found
    except Exception:
        return None   # total by contract: callers rely on this never raising


def welcome_back_block(core, transcript_path) -> str:
    """If a fresh, unconsumed welcome_back.json is waiting, atomically claim it
    (fire once) and return a <presence-return> block. A welcome-back older than
    WELCOME_TTL_S is claimed silently -- no stale greeting hours later.
    Fail-safe: '' on any error, since a greeting bug must never block a prompt.

    Concurrency: the claim (read -> mark consumed) runs under the shared ledger
    lock, double-checked, so two open sessions returning at once can't both
    greet, and a watcher write can't be clobbered mid-claim. The common path
    (no/consumed welcome-back) takes NO lock -- the unlocked pre-check returns
    first -- so only the rare fresh-return prompt ever contends. The transcript
    harvest runs OUTSIDE the lock to keep hold-time microscopic. Path from
    core.DATA at call time (test isolation)."""
    wb_path = core.DATA / "welcome_back.json"
    pre = core.read_json(wb_path, {})            # cheap, unlocked pre-check
    if (not isinstance(pre, dict) or pre.get("consumed")
            or not pre.get("unlocked_at")):
        return ""
    try:
        with core._ledger_lock():
            wb = core.read_json(wb_path, {})     # re-read under lock (claim)
            if (not isinstance(wb, dict) or wb.get("consumed")
                    or not wb.get("unlocked_at")):
                return ""                        # another session won the claim
            core.write_json(wb_path, {**wb, "consumed": True})
    except Exception:
        return ""    # lock/io trouble: skip; not consumed, retried next prompt
    unlocked = core.parse_iso(wb.get("unlocked_at"))
    if (unlocked is None
            or (core.now_utc() - unlocked).total_seconds() >= WELCOME_TTL_S):
        return ""    # stale/unparseable: claimed above, but no greeting
    dur = core.humanize_delta(float(wb.get("away_s") or 0))
    body = f"Friend just returned to the CLI after being away {dur}."
    resume = read_own_away_summary(transcript_path)   # total: never raises
    if resume:
        body += f' When they stepped away, this session\'s recap was: "{resume}".'
    body += (" Greet them warmly with continuity -- acknowledge the gap and"
             " pick the thread back up; don't re-ask what's already settled."
             " This is a returning-human signal, not a task change.")
    return "\n<presence-return>\n" + body + "\n</presence-return>"


def build_context(core, data=None) -> str:
    closed = core.close_awaiting_detailed()
    if closed:
        core.refresh_menubar()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "watcher"))
        import opportunities
        now0 = core.now_utc()
        for c in closed:
            created = core.parse_iso(c.get("created_at"))
            if created is not None:
                opportunities.log_habit({
                    "kind": "answered", "id": c.get("id"),
                    "latency_s": (now0 - created).total_seconds()})
    except Exception:
        opportunities = None

    last_path = core.DATA / "last_prompt.json"
    prev = core.read_json(last_path, {})
    prev_ts = core.parse_iso(prev.get("ts")) if isinstance(prev, dict) else None
    now = core.now_utc()
    core.write_json(last_path, {"ts": now.isoformat()})

    local = core.now_local()
    parts = [f"Now: {local:%a %d %b %Y, %I:%M %p} ({local.tzname()})."]
    if prev_ts is not None:
        delta = core.humanize_delta((now - prev_ts).total_seconds())
        parts.append(f"Elapsed since your previous prompt: {delta}.")
    out = "<sundial-tick>" + " ".join(parts) + "</sundial-tick>"
    try:
        if opportunities is not None:
            live = opportunities.open_offers(core.now_utc())[:5]
            if live:
                lines = [f"- [{r['kind']}] {str(r.get('offer_msg',''))[:200]}"
                         for r in live]
                out += ("\n<opportunities>\n" + "\n".join(lines)
                        + "\n</opportunities>")
    except Exception:
        pass
    try:
        out += welcome_back_block(core, (data or {}).get("transcript_path"))
    except Exception:
        pass
    return out


def main():
    raw = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if is_machine_event(str(data.get("prompt") or "")):
        return  # machine event: no disarm, no stamp, no output

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import core

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_context(core, data),
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never block a prompt because of a clock bug.
        sys.exit(0)
