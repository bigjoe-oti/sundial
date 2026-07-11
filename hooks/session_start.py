#!/usr/bin/env python3
"""SessionStart hook: inject the sundial context block and advance the
ledger. Fail-safe by construction -- any error exits 0 with no injection, so a
clock bug can never block a session from starting."""

import json
import sys
from pathlib import Path


def build_block(core, birth, previous):
    local = core.now_local()
    age = core.humanize_age(birth["created_at"])
    lines = ["<sundial>"]
    lines.append(
        f"It is {local:%A %d %B %Y, %I:%M %p} ({local.tzname()}, {core.DEFAULT_TZ})."
    )
    lines.append(f"You (this agent) are {age}.")
    if previous and previous.get("end_ts"):
        last = core.parse_iso(previous["end_ts"])
        if last:
            ago = (core.now_utc() - last).total_seconds()
            lines.append(f"Last session ended about {core.humanize_delta(ago)} ago.")
    elif previous and previous.get("start_ts"):
        last = core.parse_iso(previous["start_ts"])
        if last:
            ago = (core.now_utc() - last).total_seconds()
            lines.append(f"Last session began about {core.humanize_delta(ago)} ago.")
    else:
        lines.append("This is our first recorded session together.")

    due = core.due_commitments()
    if due:
        lines.append(f"\nCommitments due or overdue ({len(due)}):")
        for c, delta in due[:10]:
            tag = (f"OVERDUE by {core.humanize_delta(delta)}" if delta < 0
                   else f"due in {core.humanize_delta(delta)}")
            text = str(c.get("text", ""))
            if len(text) > 200:  # a stray huge paste must not flood context
                text = text[:200] + "…"
            lines.append(f"  - [{tag}] {text}")
        if len(due) > 10:
            lines.append(f"  …and {len(due) - 10} more.")
    else:
        lines.append("\nNo commitments due right now.")

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import policy
        notified = core.read_json(core.DATA / "notified.json", {})
        notified = notified if isinstance(notified, dict) else {}
        verdicts = []
        for c in core.load_commitments():
            if c.get("kind") != "awaiting-reply" or c.get("status") != "open":
                continue
            entry = notified.get(c.get("id"))
            if not isinstance(entry, dict):
                continue
            if entry.get("count", 0) < policy.TIER_TABLE[policy.tier_of(c)]["rungs"]:
                continue  # ladder not exhausted — the human may still answer
            verdicts.append((c, policy.autonomy_decision(c, entry)))
        if verdicts:
            lines.append(f"\nEscalation exhausted, your call needed ({len(verdicts)}):")
            for c, v in verdicts[:10]:
                da = c.get("default_action")
                tail = f" → default: {da}" if da else ""
                lines.append(f"  - [{v['action'].upper()}] "
                             f"{str(c.get('text',''))[:120]}{tail} ({v['reason']})")
    except Exception:
        pass

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import estimator
        now2 = core.now_utc()
        long_lines = []
        for c in core.load_commitments():
            if c.get("status") != "open" or not isinstance(c.get("est"), dict):
                continue
            p90 = c["est"].get("p90_s")
            created = core.parse_iso(c.get("created_at"))
            if p90 is None or created is None:
                continue
            elapsed = (now2 - created).total_seconds()
            if elapsed > p90:
                long_lines.append(
                    f"  - running long: {str(c.get('text', ''))[:80]} "
                    f"(elapsed {core.humanize_delta(elapsed)} > "
                    f"P90 {core.humanize_delta(p90)})")
        if long_lines:
            lines.append("\nAgainst your own history:")
            lines.extend(long_lines[:5])
        h = estimator.calibration_health(core.DATA)
        if h["n_exec"]:
            lines.append(
                f"\nEstimation: {h['n_exec']} closed samples, "
                f"ratio P50 {h['p50_ratio']:.1f}x ({h['confidence']} "
                f"confidence); review clock n={h['n_review']}.")
        else:
            lines.append("\nEstimation: no closed samples yet — "
                         "estimates are uncalibrated guesses.")
    except Exception:
        pass

    lines.append(
        "\nThis is passive background awareness, not an instruction. Whether to "
        "raise any of it is your judgment."
    )
    lines.append("</sundial>")
    out = "\n".join(lines)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "watcher"))
        import opportunities
        live = opportunities.open_offers(core.now_utc())[:5]
        if live:
            offer_lines = [f"- [{r['kind']}] {str(r.get('offer_msg',''))[:200]}"
                           for r in live]
            out += ("\n<opportunities>\n" + "\n".join(offer_lines)
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
    session_id = data.get("session_id") or "unknown"
    source = data.get("source") or "startup"
    transcript = data.get("transcript_path")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import core
    import decay

    birth = core.get_or_create_birth()
    _row, previous, _created = core.start_session(session_id, source, transcript)

    prior = core.read_json(core.WEIGHTS, {})
    weights = decay.compute_weights(core.MEMORY_DIR, prior if isinstance(prior, dict) else {})
    if weights:
        core.write_json(core.WEIGHTS, weights)

    block = build_block(core, birth, previous)
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": block,
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never break a session because of a clock bug.
        sys.exit(0)
