# Temporal-scene sweep: where Sundial stands (2026-07-17)

12-agent adversarially-structured sweep (6 landscape scouts, 5 novelty
refuters, 1 refinement mapper — all Sonnet, ~750k tokens, 299 tool uses,
11.5 min; Fable synthesis). Every novelty claim below was attacked by a
dedicated refuter instructed to find prior art; novelty language in this
digest survives only where the attack failed. Raw returns in session task
archive (`wq2isadeu.output`); this digest is the durable record. Companion
to the 2026-07-03 HCI digest (which remains the interruption-science
baseline and is not re-covered here).

## Headline verdict: composition-novel, not mechanism-novel — and the field is walking toward us

Honest answer to "are we making true revolutionary progress": **no single
mechanism in Sundial is revolutionary — several are decades old — but the
composition survived every refutation attempt, and no shipped product or
paper found anywhere combines its four pillars.** As of July 2026, every
major agent stack (OpenAI, Google, Anthropic, Microsoft, Devin/Cursor-class)
ships *scheduling* — mature, near-universal, cron-with-a-natural-language-face.
None ships temporal *perception*: not elapsed-session awareness, not agent
age, not presence-gated speech, not self-calibrated estimation. The clearest
single artifact: an open, unanswered issue on Anthropic's own repo
(anthropics/claude-code #32913) asking for exactly the temporal awareness
Sundial already has running.

The sober counterweight: the research frontier is independently converging
on our exact design space (Google's May-2026 "proactivity, not just
autonomy" paper is nearly a blueprint of our escalation-cost logic), and one
independent team (Epoch) shipped the self-calibration+honesty-rule
sub-mechanism two months before our Phase B. The window is real; it is also
visibly closing. Revolutionary, no. Early to an inevitable spot with a
working composite nobody else has assembled — yes, defensibly.

## Novelty scoreboard (verdict-gated)

| Claim | Verdict | The evidence that decides it |
|---|---|---|
| present-silence | **REFUTED at mechanism level** (application remains open) | Horvitz "Attention-Sensitive Alerting" UAI 1999 (arxiv.org/abs/1301.6707); US7,444,383 bounded deferral via local sensors w/ confidence measure (2008); US11,012,574 low-pass-filtered opportune-moment delivery. No prior art applies it to an agent gating its own self-initiated speech — but the mechanism we lean on is squarely 1999–2010 prior art. |
| presence-ladder | survives (adjacent-only) | Every ingredient precedented separately (PagerDuty ladders, Slack idle-gated routing, SimpliSafe hard-ceiling escalation, Apple Critical Alerts, Fogarty sensors) — no single system combines graduated offsets + away/busy-elsewhere weighting + courtesy muting + hard ceiling from local sensors. |
| self-clocked-estimation | survives (parallel-work — closest call) | **Epoch (KyaniteLabs, github.com/KyaniteLabs/Epoch)**: MCP server, first npm publish 2026-05-07, own-ledger recalibration + n≥5 honesty rule — strikingly close on the calibration sub-mechanism, two months before our Phase B. Diverges on commitment-verb auto-close and the two-clock split. Weakened, not refuted. |
| two-clocks | survives (adjacent-only) | Stage-clock P50/P90 exists in team analytics (LinearB-class); TraceLab (arXiv 2606.30560) measures human-vs-agent time in coding sessions descriptively; CCPM touch/wait-time is the conceptual ancestor. Nobody composes two self-measured clocks into a forward estimate with at-risk semantics. |
| temporal-nervous-system (the composition) | **survives (adjacent-only)** | 14 searches, no source assembles watcher+hooks substrate, ambient self-age/commitments context, and a closed self-calibration loop. Ingredients documented in isolation (Sensa ambient context, Claude Code hooks ecosystem, Letta heartbeat); the assembly is ours. |

## The scene in one pass

- **Anthropic**: date-only injection (issue #32913 names the rest as absent);
  Routines/Cowork/`/loop` = scheduling + unattended execution, no perception.
  One genuine presence-gate shipped: native "Session recap" (terminal
  unfocused + 3-min threshold) — a sliver of pillar 2, inside our platform.
- **OpenAI**: Tasks = NL cron (~hourly cap); Codex cron tooling is an
  unmerged community feature request (openai/codex #25466).
- **Google**: Jules async tasks; but the real signal is the arXiv paper
  2605.06717 (below).
- **Microsoft**: Scout/Work IQ (private preview, Aug-2026 target) — learns
  work patterns, calendar/deadline-triggered proactive nudges. Not
  sensor-gated, but the biggest *narrative* competitor for "proactive AI."
- **Startups**: nothing found combining the pillars; Bond (YC S25
  "AI Chief of Staff") closest in framing, not mechanism.
- **Research**: "temporal blindness" is now a named, replicated finding
  (2604.00010: self-duration estimates 4–10x off; 2601.13206: deadline
  failures; per-turn time injection lifts negotiation closure 4%→32%).
  Metacognition track found no paper shipping a pre-register/auto-close/
  self-calibrate loop — targeted searches for that architecture came back
  empty.

## Threats (ranked)

1. **Anthropic absorption** — Session recap is already a shipped presence
   gate; issue #32913 is a standing invitation for them to build pillar 1
   natively. Every primitive they ship narrows our third-party gap.
2. **Epoch** — parallel work on estimation, active (v0.3.0 published
   2026-07-10). Watch its trajectory; two of our four estimation mechanisms
   remain ours alone.
3. **Google's blueprint paper** (arXiv 2605.06717) — formalizes
   interruption-cost gating for coding agents, silence as a first-class
   action. A reference implementation from them lands squarely on us.
4. **Microsoft Scout** — narrative risk at Microsoft-365 scale, not
   technical equivalence.

## Validations (design bets confirmed by independent evidence)

- Composite-architecture bet: five independent sweeps, zero full matches.
- "Trigger path never thinks" (no-LLM sensing): reconfirmed from new angles
  (2605.30152; TicToc; the Google proactivity paper).
- Breakpoint delivery: five-day field study (IUI'26, 2601.10253, n=15, 229
  interventions): boundary-timed interventions ~52% engagement vs 62%
  dismissal mid-task; interpreted in 45s vs 101s — field confirmation of
  what we adopted from theory.
- Honesty rules: two 2026 papers (2604.00010, 2602.06948) confirm raw LLM
  self-estimates of duration AND success are badly miscalibrated absent an
  external mechanism — estimator.py's refusal to trust the model is the
  validated stance. Epoch independently converged on an n≥5 gate.

## Refinement doors (ranked by leverage-per-effort)

1. **Prior-art refresh (0.5d, docs only)** — add Horvitz UAI'99 + US7,444,383
   as present-silence's named ancestors in README's prior-art section; our
   own kill squad found them, our ethos says publish them.
2. **Wall-time vs execution-time guard (house-found, ~0.5d)** — surfaced by
   the Phase B soak close itself (sample #9, ratio 392.7x from a 5.5-day
   idle span): `done` should record `ratio: null` + note when wall duration
   is wildly out of band. Spec pending owner greenlight.
3. **Owner-declared snooze verb (0.5d)** — `sundial snooze <duration>`;
   resolves the interview-vs-Figma ambiguity ELSEWHERE can't see.
4. **Mid-task budget-crossing nudges (1d)** — flag once at 50/80/100% of the
   active commitment's P90 via the existing per-prompt tick. Evidence:
   live elapsed-time feedback moved on-time completion 30%→53.3%
   (Timely-RL, arXiv 2601.16486).
5. **Batch same-cycle offers into one digest notification (1d)** — the
   welcome-back digest pattern, applied to opportunity popups.
6. **Confidence-from-history advisory for the autonomy gate (1.5d)** —
   at ask-time, print the bucket's historical answered/declined resolution
   rate; 2602.06948 shows agents stay overconfident even WITH access to
   their own history unless an external mechanism intervenes. Feeds the
   0.95 proceed threshold, so it earns the full TDD treatment.
7. **Opt-in off-desktop escalation at the wall ceiling (2d)** — the one
   documented gap vs the Horvitz lineage we claim (PRIORITIES escalated
   desktop→pager); AppleScript→Messages ping, owner-enrolled only.

## Method + sourcing caveats

OpenAI primary pages returned 403 (facts rest on convergent secondary
coverage — flagged, lower confidence). US11,012,574 abstract from search
snippet, not clean fetch. releasebot.io's claim of shipped Codex scheduling
contradicts the primary source (open unmerged issue) — treated as
unconfirmed. Everything else in the scoreboard was verified against a
fetched primary source by the refuting agent.
