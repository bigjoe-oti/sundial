# Sundial

<p align="center"><img src="assets/sundial-logo.png" width="180"></p>

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
  runs every 10 minutes even with no session open) climbs an urgency-tiered
  ladder — but the ladder's clock **only advances while you genuinely
  haven't seen the chat**:

```
            you ask ──► 10 min unseen ──► 20 min ──► 50 min ──► agent decides
  presence:   HERE ▸ clock paused (you can see the chat — silence means "not now")
         ELSEWHERE ▸ half speed  (you're in another app — popups may name it)
              AWAY ▸ full speed  (nobody's home — sound travels farther than pixels)
  backstop: a wall ceiling forces the final rung, whatever the sensors say
```

  The cadence above is the **normal** tier. Tag a question's urgency with
  `--weight`: **high** climbs faster (5/10/20 min, 40-min ceiling, speaks its
  final rung), **low** slower and shorter (two rungs, 3-hour ceiling). Every
  tier's terminal rung still states the autonomy contract; none of them ever
  claims an elapsed time it didn't wait.

- **Waits for the pause, not the tick.** A ripe nudge doesn't fire
  mid-keystroke: it holds up to 3 minutes for you to pause typing or switch
  apps, then delivers into that natural gap. Bounded deferral, straight from
  the interruption-science literature — every fired nudge records how long
  it politely waited.
- **Knows away from busy from listening.** Two zero-permission macOS signals:
  seconds since your last keystroke (`HIDIdleTime`) and the frontmost app
  *name* (`lsappinfo`). In another app, the nudge may tease you by name:
  *"I can see Figma has you. One opinion and I'll vanish."*
- **Greets your return.** One welcome-back popup when you come back with
  something ripened — *"While you were away (25m): …"* — instead of a pile
  of stale ones.
- **Escalates in sound, too — with manners.** Tink → Glass → Hero chimes
  rise with the rungs (Purr on return), whispered when you're merely busy,
  silent when you're right here. Optionally, the final rung literally
  speaks (`--speak`). Sound courtesy reads presence, not the clock: chimes
  and speech mute entirely once the screen is locked or you've been away
  30+ minutes — popups and detection keep running regardless.
- **Ends in autonomy, not limbo.** The final rung's contract, printed on the
  notification itself: *proceeding on my judgment or standing down.* The
  agent reads both clocks — how long you were gone vs. how long you sat
  there choosing silence — and interprets accordingly. Silence-while-present
  is an answer; silence-while-absent is a void. They deserve different
  responses. The final rung names the *specific* default it will take
  (*"proceeding to back up then halt, or standing down"*), and the agent acts
  on its own only when it's highly confident **and** the action is reversible
  — anything destructive waits for your explicit yes.

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
sundial ask "text" [--due +10m] [--weight low|normal|high]
                   [--confidence 0..1] [--irreversible] [--default "what I'll do"]
sundial due / answered / done <id>
sundial remember "text" --due 2026-08-01
```

## What else it notices

The watcher is already sampling presence every cycle — this puts that
sampling to work, deterministically, no LLM in the loop:

- **Meeting offers.** Zoom, Teams, FaceTime, Webex, Skype in the foreground —
  or a live WebRTC call in *any* app, so Google Meet in a Chrome tab counts
  too — triggers a one-time offer to draft minutes when it starts, and again
  when it ends. A meeting that closed out long after it plausibly ran (the
  machine slept through it) gets a softer, duration-free offer instead of a
  made-up number.
- **Folder curiosity, any depth.** Spotlight (`mdfind`) watches `~/Desktop`
  (or your own `watch_roots.txt`) for new or recently-added folders *at any
  depth*, not just top-level — capped at 5 per cycle so a bulk unzip doesn't
  flood you. A folder that earns a mention self-enrolls as a new watch root,
  so curiosity naturally follows you deeper into a project. No Spotlight?
  It falls back to the original top-level poller.
- **Build awareness.** Notices when a long-running `xcodebuild`, `npm`,
  `pytest`, `cargo`, `docker`, `make`, or similar just finished (anything
  under a minute is noise, not a build) and offers to look at the results.
- **Manners: decline and allow.** `sundial decline <kind>` (e.g.
  `meeting-start`, `curiosity`, `build-finished`) mutes that kind of offer
  after 3 declines without you saying a word again; `sundial allow <kind>`
  re-enables it. Declined kinds still get recorded to the ledger — just
  never popped up.
- **The Habit Ledger.** Every fire, mute, presence transition, and offer logs
  one line to `data/habits.jsonl` (rotated at 5MB, pruned after 14 days once
  terminal) — raw material for the Owner Model.
- **The Owner Model.** `sundial owner` distills the Habit Ledger — no LLM,
  just medians and percentiles — into active-stretch lengths, reply latency,
  an hourly activity histogram, and offer/fire counts. Deterministic,
  refreshed at most every 6 hours, meant for the agent to read before
  writing a weekly reflection.
- **Silent prep — flagged off by default.** When a meeting starts, Sundial
  can quietly draft a minutes-of-meeting scaffold in the background (no
  popup, no notification) so it's waiting on disk if you want it later. This
  stays OFF until you opt in: create `data/prep_enabled` (touch the file) and
  optionally cap it with `data/prep_budget.txt` (a bare integer, default 2/
  day). It also needs a `claude` binary to hand the draft to — set
  `SUNDIAL_CLAUDE_BIN` to its path, or have `claude` on your `PATH`; missing
  either just skips the spawn (logged, never crashes).
- **Manners, not spam.** At most 5 offers a day, deduped by evidence so the
  same meeting, folder, or build never offers twice. Open offers also ride
  along in the `<sundial>` / `<sundial-tick>` context blocks, so the agent
  can act on one without you repeating yourself in chat.

## Optional: a menu-bar face

Want presence, open asks, and offers at a glance without opening a session?
Install [SwiftBar](https://github.com/swiftbar/SwiftBar), copy
`contrib/sundial.30s.sh` into its plugin folder, and set `SUNDIAL_HOME` to
wherever you cloned this project — SwiftBar copies plugin scripts out of the
repo, so the script can't find its own path and needs to be told. Read-only:
it never writes to `data/` or signals the watcher.

## Honesty rails

- **No LLM decides when to wake.** Ripeness is date arithmetic. (Research
  agrees this is the right call — see prior art.)
- **Nothing leaves the machine.** Notifications via a local compiled applet;
  presence is an idle duration and an app *name* — never window titles,
  never content. All state is plain JSON in `data/`, which is deliberately
  git-ignored: live state is not history.
- **The sensors can be wrong, so they only soften.** A per-tier wall ceiling
  (40 min high / 90 normal / 3 h low) guarantees the final rung regardless of
  what presence believes.
- **Up to three pings per question, ever** (two for the low tier). No quiet hours — the watcher runs
  every 10 minutes around the clock. What's gated is sound, not delivery:
  chimes and speech mute when the screen is locked or you've been away 30+
  minutes; popups and detection never sleep.
- **Opportunity offers are capped and deduped too.** At most 5 a day, one
  offer per real meeting or new folder — never a repeat nag over the same
  evidence.
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
long the user had been away.
The mechanism under present-silence — sensor-gated delivery timing with a
confidence measure, instead of elapsed-time firing — is also Horvitz-era
prior art: "Attention-Sensitive Alerting" ([UAI 1999](https://arxiv.org/abs/1301.6707))
framed it, and [US7,444,383](https://patents.google.com/patent/US7444383)
(bounded deferral via local sensors, filed 2004) implemented it, decades
before us. What we could not find is that mechanism applied to an agent
gating its own self-initiated speech; a 2026 adversarial prior-art sweep
([digest](docs/research/2026-07-17-temporal-scene-sweep.md)) is the current
record of where each claim stands.
Sundial stands on that lineage rather than
beside it: what we could not find anywhere is this *combination* — the
agent-blocked-on-human framing, local-first zero-dependency packaging, and
a terminal autonomy contract (the agent proceeds on stated judgment or
stands down when escalation exhausts). That combination is Sundial.
Corrections welcome — file an issue with a link.

The pattern write-up: [docs/escalation-then-autonomy.md](docs/escalation-then-autonomy.md).

## Troubleshooting delivery

Notifications that compile and run without error can still never reach the
screen. Three silent killers, all fixed in `setup.sh` as of v1.0.2 — see
[docs/notes/delivery-incident-2026-07-03.md](docs/notes/delivery-incident-2026-07-03.md):

- **Click Allow on the one-time permission prompt.** Dismissing or denying
  it drops every banner with no error anywhere.
- **External display?** Enable System Settings -> Notifications -> "Allow
  notifications when mirroring or sharing" — mirroring silently suppresses
  all banners otherwise.
- **Notification style: Alerts**, not Banners, so a missed nudge doesn't
  auto-dismiss before you see it.

## Roadmap

- **v2 — self-estimation (shipped):** plain commitments carry calibrated
  P50/P90 from the agent's own measured ratio history (`--est/--bucket` on
  `remember`) instead of zero-shot guessing (which
  [measurably fails](https://arxiv.org/abs/2604.22750)). Deadlines get a
  sanity check at creation, sessions flag work running past its own P90,
  and the menu bar shows the active promise's calibrated state.
- Learned quiet hours from your actual rhythm; sibling-session awareness;
  cross-machine commitments between two owners' agents; estimation
  covariates from the verified sensor survey
  (docs/research/2026-07-11-macos-sensor-survey.md).
- `sundial doctor` — one command verifying the whole delivery chain
  (daemon, applet identity, permission registration, mirroring setting).

## Provenance

Timezone and metering logic adapted from internal utilities; rewritten
standalone here. Built in one day, live, by an agent wearing the clock it
was building — four of its design rails exist because the thing caught its
own failure modes in production while under construction.

---

<p align="center">Built with obsession by <a href="https://jservo.com"><b>J. Servo</b></a> — agentic systems that keep their promises.</p>

MIT © 2026 J. Servo LLC
