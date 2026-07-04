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
import sys
from pathlib import Path

# Markers the harness injects into machine-generated "prompts". A human
# quoting one of these verbatim at the very start of a message is the
# accepted (rare, harmless) false-positive cost.
MACHINE_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION")


def is_machine_event(prompt: str) -> bool:
    """True when the prompt is a harness re-invocation, not the human typing."""
    head = prompt.lstrip()[:400]
    return any(marker in head for marker in MACHINE_MARKERS)


def build_context(core) -> str:
    closed = core.close_awaiting_detailed()
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
                "additionalContext": build_context(core),
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
