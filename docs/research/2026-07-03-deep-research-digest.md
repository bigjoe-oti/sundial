# Deep-research digest: what Sundial is missing (2026-07-03)

105-agent adversarially-verified sweep (2.8M tokens, 663 fetches; every claim
3-vote verified against primary sources). Full raw output in session task
archive; this digest is the durable record.

## Headline: our ladder's TIMING is scientifically naive

Three decades of HCI interruption research (Horvitz/MSR, Iqbal & Bailey/CMU)
says nudge timing should NOT be fixed elapsed intervals:

1. **Decision-theoretic gating (NEVA, Horvitz 1999):** alert only when
   expected value of alerting now exceeds expected interruption cost.
2. **Bounded deferral (patented Horvitz mechanism; patents now expired):**
   hold a ripe nudge until a task BREAKPOINT, up to a max window, then fire
   regardless. Field data: busy→free transitions happen within ~1-2 min;
   medium/low urgency tolerates 3-4 min deferral.
3. **Breakpoint delivery is a proven cheap win:** −20% frustration, −25%
   reaction time, at only ~90s average added latency (Iqbal & Bailey TOCHI
   2010); Attelia replicated on mobile: −46% cognitive load (lab), −33%
   (wild). Breakpoints have detectable granularities (fine/medium/coarse);
   coarser = bigger cost reduction.
4. **Relevance tiers the rung:** task-RELEVANT nudges belong at fine/medium
   breakpoints (sooner, while human is in context); general ones at coarse.
   For us: a blockage in the repo the human is currently editing → earlier,
   softer knock than an unrelated agent's blockage.

We already have the sensors for cheap breakpoints: idle-transition edges and
front-app switches ARE fine/medium breakpoints. Implementation sketch: when a
rung ripens, hold it until (idle just crossed ~10s OR front app just changed),
bounded at +3 min, then fire anyway.

## Presence model: validated stance, provable headroom

Cheap-sensor interruptibility models (speech/phone/keyboard/time-of-day) hit
78.9–80% accuracy with NO LLM (Fogarty TOCHI 2005; mobile attentiveness 80%
from usage data alone; inattentive gaps typically 2–5 min). Validates
local-first no-LLM stance; our idle+frontapp-only model leaves accuracy on
the table. Adding simple per-user BEHAVIORAL HISTORY improved engagement
prediction 5-fold (Pielot UbiComp 2017).

## Platform primitives we ignore

- **Windows:** toast `Urgent` (breaks through DND), `Reminder` scenario
  (OS-managed persistence, stays until acted on — requires ≥1 button), and
  interactive toasts: up to 5 buttons + text inputs → **the human could
  answer the agent's question inline from the notification, no context
  switch** (verified verbatim against current MS Learn docs).
- **Linux:** freedesktop urgency levels + feature-detected action buttons —
  map directly onto our rungs.
- macOS: Focus/DND awareness remains the gap our own audit flagged.

## Ecosystem: ACP is the adapter target

Agent-Client Protocol (agentclientprotocol.com, JSON-RPC 2.0) — adopted by
Codex, OpenHands, JetBrains, Devin. One ACP adapter ≈ portability to every
major harness, instead of per-harness hook shims.

## NOVELTY THREAT — prior art, not competitors

**Microsoft PRIORITIES prototype (Horvitz et al., 1999)** already did
presence-aware channel laddering: desktop → phone/pager escalation, timed by
how long the user was away, backed by (now-expired) patents. Sundial's
defensible novelty is therefore NOT the ladder mechanism itself but:
(a) the agent-blocked-on-human framing, (b) local-first zero-dep packaging,
(c) the terminal autonomy contract (proceed-or-stand-down). ACTION: update
the public README prior-art section to cite PRIORITIES — honest flag
maintenance strengthens the claim we keep.

## Ranked build order (research's verdict)

1. Bounded-deferral / breakpoint-aware rung timing (replaces fixed waits).
2. Cheap-signal interruptibility + simple per-user behavioral history.
3. Relevance-tiered rung selection (which repo/app is the blockage about?).
4. Cross-platform OS-native channels with inline answer actions.
5. ACP adapter for harness portability.

(Phase B self-estimation remains a separate, still-unclaimed axis — planning
quality vs. nudge quality; both live.)

## Key sources

- Horvitz NEVA/bounded deferral: arxiv.org/pdf/1301.6707, erichorvitz.com/bdef_studies.pdf
- Iqbal & Bailey: interruptions.net/literature/Iqbal-TOCHI10.pdf, Iqbal-CHI08.pdf
- Fogarty sensors: interruptions.net/literature/Fogarty-TOCHI05.pdf
- Pielot engagement: pielot.org/pubs/Pielot2017-UbiComp-Engagement.pdf
- Attelia et al. survey: arxiv.org/pdf/1711.10171
- Windows toasts: learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-content
- ACP: agentclientprotocol.com/protocol/v1/overview

## Addendum (2026-07-03 PM): verified competitive facts from an external report

An unsourced external report made 4 competitive claims; fact-check killed 3.
The survivors, verified against primary sources:
- **Cloudflare Agents `waitForApproval()`** (merged 2026-01-28, cloudflare/agents#799;
  docs: developers.cloudflare.com/agents/concepts/human-in-the-loop/) — durable
  cloud-side approval gates, timeouts "hours, days, or weeks". Positioning
  contrast: parks-and-waits passively vs our local absence measurement +
  autonomy contract.
- **openclaw/openclaw#52147** (real, closed 2026-03): hardcoded
  DEFAULT_AGENT_TIMEOUT_SECONDS misclassifies tool-execution time as LLM
  timeout → wrongful failover. Evidence of industry time-blindness (cannot
  distinguish activity types) — demand-signal for agent time-sense, though
  NOT about waiting-on-human (the report's framing swapped that in).
Rejected as confabulated: LiveKit "park+Slack" (only an open feature request
exists, livekit/agents#2367); CreateOS "Review Context Packets" (company and
"approval gates" phrase real; the mechanism name invented; CV-governance
product, not agent orchestration).
