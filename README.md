# Sundial

**A sense of time for AI agents. Local-first, zero-dependency, no LLM in the loop.**

> Sundial measures **absence, not time.**

Your coding agent asks you a question and you walk away. Today, nothing
happens — the session hangs, the question rots, the work stalls. Sundial is
the missing half of human-in-the-loop: when the human is the blocker, the
agent's clock keeps running. It nudges you on your desktop, escalates
politely, greets you when you return — and if you never come back, the agent
proceeds on its own stated judgment or stands down. Deterministically. With
zero model calls.

## What it does

- **Gives the agent a clock.** A session-start hook injects local time, the
  agent's age, time since you last spoke, and any ripened commitments. A
  per-prompt hook adds "now + elapsed since your previous prompt" to every
  message you send.
- **Keeps a promise ledger.** `sundial ask "should the header be sticky?"`
  records a time-stamped *awaiting-reply* commitment. Any prompt you type
  disarms it — the system's whole goal is your attention, and typing proves
  it has it.
- **Escalates through absence.** A launchd watcher (pure Python, no LLM,
  runs every 10 minutes even with no session open) climbs a three-rung
  ladder — but the ladder's clock **only advances while you genuinely
  haven't seen the chat**:

```
            you ask ──► 10 min unseen ──► 20 min ──► 50 min ──► agent decides
  presence:   HERE ▸ clock paused (you can see the chat — silence means "not now")
         ELSEWHERE ▸ half speed  (you're in another app — popups may name it)
              AWAY ▸ full speed  (nobody's home — sound travels farther than pixels)
  backstop: 90 wall-minutes forces the final rung, whatever the sensors say
```

- **Knows away from busy from listening.** Two zero-permission macOS signals:
  seconds since your last keystroke (`HIDIdleTime`) and the frontmost app
  *name* (`lsappinfo`). In another app, the nudge may tease you by name:
  *"I can see Figma has you. One opinion and I'll vanish."*
- **Greets your return.** One welcome-back popup when you come back with
  something ripened — *"While you were away (25m): …"* — instead of a pile
  of stale ones.
- **Escalates in sound, too.** Tink → Glass → Hero chimes rise with the
  rungs (Purr on return), whispered when you're merely busy, silent when
  you're right here. Optionally, the final rung literally speaks
  (`--speak`).
- **Ends in autonomy, not limbo.** The final rung's contract, printed on the
  notification itself: *proceeding on my judgment or standing down.* The
  agent reads both clocks — how long you were gone vs. how long you sat
  there choosing silence — and interprets accordingly. Silence-while-present
  is an answer; silence-while-absent is a void. They deserve different
  responses.

## Quickstart

macOS + a hook-capable agent CLI (built for Claude Code) + Python 3.9+.

```bash
git clone https://github.com/bigjoe-oti/sundial ~/sundial
cd ~/sundial && ./setup.sh --name YourName --fresh
```

`setup.sh` wires the hooks, compiles the notifier app, loads the launchd
watcher, and gives your agent a birth certificate. `--fresh` matters: an
agent's time-sense starts at *its* birth, not the previous owner's. Allow
notifications for "Sundial" when macOS asks. Then, in a session, teach your
agent the habit: when it asks you something blocking, it runs
`sundial ask "<the question>"` — and the machinery above takes over.

```
sundial now                    # time, agent age, due count
sundial ask "text" [--due +10m]
sundial due / answered / done <id>
sundial remember "text" --due 2026-08-01
```

## Honesty rails

- **No LLM decides when to wake.** Ripeness is date arithmetic. (Research
  agrees this is the right call — see prior art.)
- **Nothing leaves the machine.** Notifications via a local compiled applet;
  presence is an idle duration and an app *name* — never window titles,
  never content. All state is plain JSON in `data/`, which is deliberately
  git-ignored: live state is not history.
- **The sensors can be wrong, so they only soften.** A 90-minute wall
  ceiling guarantees the final rung regardless of what presence believes.
- **Max three pings per question, ever.** Quiet hours (08:00–22:00)
  respected; a night's missed rungs collapse into one morning catch-up.
- **Memory decay is computed, never enacted.** ACT-R-style scores over the
  agent's memory files are recorded for future use; nothing auto-forgets.

## Prior art, honestly mapped

Time-injection into agent context is commodity (see
[kadenn/chronos](https://github.com/kadenn/chronos), and Claude Code
reminder hooks like
[claude-code-reminder](https://github.com/JeremieSamson/claude-code-reminder),
whose author explicitly notes it goes silent outside sessions — exactly
where Sundial's watcher begins). Escalation-with-fallback exists cloud-side
(HumanLayer; and [paperclip #4022](https://github.com/paperclipai/paperclip/issues/4022)
proposes timeouts for agents stuck "waiting on human" — unshipped).
[arXiv 2605.30152](https://arxiv.org/abs/2605.30152) shows LLM-free wake
triggers beat LLM ones. The deepest prior art is Horvitz et al.'s
**PRIORITIES** prototype (Microsoft Research, 1999), which already did
presence-aware channel laddering — desktop→pager escalation timed by how
long the user had been away. Sundial stands on that lineage rather than
beside it: what we could not find anywhere is this *combination* — the
agent-blocked-on-human framing, local-first zero-dependency packaging, and
a terminal autonomy contract (the agent proceeds on stated judgment or
stands down when escalation exhausts). That combination is Sundial.
Corrections welcome — file an issue with a link.

The pattern write-up: [docs/escalation-then-autonomy.md](docs/escalation-then-autonomy.md).

## Roadmap

- **v2 — self-estimation:** the ledger already records wall-ms × output
  tokens per session. Next: agents that estimate task duration from their
  own measured history instead of zero-shot guessing (which
  [measurably fails](https://arxiv.org/abs/2604.22750)).
- Learned quiet hours from your actual rhythm; sibling-session awareness;
  cross-machine commitments between two owners' agents.

## Provenance

Timezone and metering logic adapted from internal utilities; rewritten
standalone here. Built in one day, live, by an agent wearing the clock it
was building — four of its design rails exist because the thing caught its
own failure modes in production while under construction.

MIT © 2026 J. Servo LLC
