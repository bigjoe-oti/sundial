# Sundial — Decision Policy, Agent-Authored Escalation & Menu-Bar Sync (design)

Status: approved (Yousef, 2026-07-08). Supersedes the flat 10/20/50 ladder with a
tiered, confidence-aware decision policy. Four slices, each its own TDD cycle.

## Goal

Give a blocking `sundial ask` two orthogonal dimensions — **urgency** and **confidence** —
plus a **reversibility** safety flag, so the agent can (a) pace how hard it chases the human,
(b) author its own reasoned escalation voice, and (c) decide, on a governed contract, whether
to proceed autonomously or stand down when the human never answers. Plus: make the SwiftBar
menu-bar face reflect state changes **immediately**, not on a 30s poll.

## Rails this must not break (hard invariants)

1. **No LLM in the trigger path.** The watcher never calls a model. Agent voice is
   *pre-composed at ask-time* and merely *replayed* by the watcher.
2. **Delivery is never suppressed.** Tiering/confidence/quiet-hours may soften **sound only**;
   popups and the 90-minute wall ceiling always fire. Enforced by test.
3. **Zero third-party dependencies.** Stdlib + macOS built-ins only.
4. **Backward compatibility.** An untagged `sundial ask` behaves byte-identically to today.
5. **Fail-safe.** Every new path degrades silently; a bug here never blocks a session or crashes
   a cycle.

## Two axes + one flag

- **Urgency** `low | normal | high` — how costly to stall. Governs **chase intensity**.
- **Confidence** `0.0–1.0` — sureness in the stated default. Governs **proceed vs stand down**.
- **Reversibility** — `--irreversible` flag (default: reversible). A distinct safety axis; a
  70%-reversible call is safer to auto-take than a 96%-destructive one, so it cannot fold into
  confidence.

## Data model (commitment record additions)

New optional keys on an `awaiting-reply` commitment, all absent → today's behavior:

```json
{
  "weight": "high",                 // low|normal|high; absent → "normal"
  "confidence": 0.9,                // float 0..1; absent → unstated (conservative)
  "irreversible": true,             // bool; absent → false
  "default_action": "back up then halt; do not drop the column",
  "rungs": ["...r1...", "...r2...", "...r3..."]   // agent's own voice; absent → static pools
}
```

## CLI (`cli/ask.py`)

```
sundial ask "<question>"
    --weight low|normal|high        # default normal
    --confidence FLOAT              # 0..1; omitted → unstated
    --irreversible                  # flag; default off
    --default "TEXT"                # the action taken if unanswered; stated in the final rung
    --rung "TEXT" (repeatable, ≤3)  # optional pre-composed per-rung voice
```

Validation: `--confidence` outside [0,1] → error (don't guess). `--rung` beyond 3 → error.
`--weight` other than the three tiers → error.

## Slice ① — Urgency tiering + tier→timings table

Replace the single `UNSEEN_OFFSETS`/`WALL_CEILING_S` with a per-tier table in `watcher.py`:

| tier | unseen offsets (s)      | wall ceiling (s) | rungs | final speaks? |
|------|-------------------------|------------------|-------|---------------|
| low    | (1800, 5400)          | 10800            | 2     | no            |
| normal | (600, 1200, 3000)     | 5400             | 3     | opt-in (today)|
| high   | (300, 600, 1200)      | 2400             | 3     | yes           |

- `normal` row == today's exact constants → existing `TestWatcherLadder`/`TestAbsenceClock`
  pass unchanged.
- `ripe_rung()`, `wall_ceiling_passed()`, and the rung-count read the tier from the commitment
  (default `normal`). Pure date arithmetic — no model.
- Chimes/`speak_final` gate on the tier (low never speaks; high speaks at rung 3 regardless of
  the opt-in `speak.txt`, still subject to the audible courtesy gate).

**Risk: LOW.** Additive; default row is a no-op refactor.

## Slice ② — Confidence + reversibility + the autonomy gate

A new deterministic function, `autonomy_decision(commitment, two_clock) -> {action, reason}`,
consumed by the agent (via the context block), **not** the watcher. The watcher's job ends at
delivering the final rung; the *decision* is the agent's, made on its next session using the
governed rule:

- **Irreversible** → `require_explicit_yes`. No silence ever authorizes. Always.
- **Reversible:**
  - `confidence ≥ 0.95` → `proceed`.
  - else → `stand_down` (park + logged reason).

Threshold `AUTONOMY_PROCEED_MIN = 0.95` is a single named constant.

> **v1 scope note (post-audit, 2026-07-08):** the `0.80 ≤ confidence < 0.95` +
> silence-while-**present** `proceed` branch is **DEFERRED**. The audit found the present-silence
> predicate unsound as wired to `accrue`: `here_s` is credited in ~10-min chunks from ask-time
> (not only while ripe) and counts *any* foreground app, so a blip at ask-time could later read
> as "seen and ignored" — risking auto-proceed on a reversible action the human never saw. v1
> therefore proceeds on reversible actions only at `≥ 0.95`. Present-silence returns as a
> fast-follow once accrual is ripeness-gated (a dedicated "present-while-ripe" counter,
> `"here"`-only, `≥ N` cycles). **This does not touch the chase policy** — busy-but-present
> high-urgency chasing stays.

The terminal rung's message always states the **specific** `default_action`, not the generic
contract. On the agent's next session, the SessionStart block surfaces: what fired and the
`autonomy_decision` verdict — the agent then executes or reconsiders through its own 95% gate.

**Risk: MEDIUM — safety-critical.** Hard tests: irreversible + any input → never `proceed`;
reversible below 0.95 → never `proceed`; the function is pure and total.

## Slice ③ — Agent-authored rung messages

`pending_ping`/`pick_message` prefer the commitment's stored `rungs[i]` when present, else fall
back to the existing static pools (full backward-compat). The stored strings pass through the
same length caps and fail-safe formatting. The final rung appends the `default_action` clause if
one is set. Zero LLM in the path — the watcher replays stored text.

**Risk: LOW.** Purely additive to the message-selection choke point.

## Slice ④ — SwiftBar instant-sync

Today `contrib/sundial.30s.sh` polls every 30s. Add **push-on-change**: a fail-safe helper
`refresh_menubar()` that fires `open -g "swiftbar://refreshplugin?name=sundial"` (SwiftBar's
refresh URL scheme; plugin name = filename stem `sundial`). Fire-and-forget, wrapped so a missing
SwiftBar / unknown scheme never raises.

Call sites (every state mutation the face reflects — presence, open asks, offers):
- **watcher.py:** after any `desktop_notify` fire, after a new offer/opportunity, on a presence
  transition, on welcome-back.
- **CLI verbs that mutate state:** `ask`, `answered`, `done`, `remember`, `decline`, `allow`.

The 30s poll stays as a backstop. The plugin remains strictly read-only; this is the reverse
signal (Sundial → SwiftBar), which does not violate the plugin's "never writes/never signals"
contract. Helper lives in `lib/core.py` (shared by CLI + watcher).

**Risk: LOW.** Cosmetic; fully degradable.

## Backward compatibility

- No new flags → `weight=normal`, no `confidence`, `irreversible=false`, static pools, today's
  timings. Every existing test must pass with zero edits.
- Records written by older versions (no new keys) read as defaults everywhere.

## Testing strategy (per slice)

- ①: tier table selection; `normal` == legacy (regression); low=2-rung/no-speak; high speaks at 3.
- ②: the autonomy gate truth table exhaustively — irreversible×{present,absent}×{silence},
  reversible×confidence-bands×{present,absent}; purity/totality (no exception on any input).
- ③: stored rungs preferred; fallback to pools when absent; caps still applied; default_action
  appended at rung 3.
- ④: `refresh_menubar` fires the right `open` argv (seam-injected, like `_spawn`); never raises
  when the opener fails; called at each mutation site.

## Risks (consolidated)

- **R1 autonomy over-reach** → the irreversible hard-stop test is the gate; ② does not ship
  without it green.
- **R2 delivery suppression** → assert no tier/confidence/quiet combination drops a popup or the
  wall ceiling.
- **R3 friction** → only `--weight` is common; the rest are opt-in.
- **R4 SwiftBar name drift** → if the user renamed the plugin file, the refresh name won't match;
  document that the stem must stay `sundial`, and degrade silently (the 30s poll still covers it).

## Build sequence

① tiering → ② confidence+gate (heaviest TDD) → ③ authored messages → ④ menu-bar sync.
Each committed separately on `feat/decision-policy`, all 184 existing tests staying green.

## Confirmed decisions

1. Confidence = numeric `0..1` + separate `--irreversible` flag. ✅
2. Silent consent = **reversible-only**; irreversible always needs explicit yes. ✅ —
   and, per the post-audit scope note above, the *present-silence* half is **deferred**: v1
   reversible auto-proceed is `≥ 0.95` only. Present-silence returns once accrual is
   ripeness-gated.
3. Chase = high-urgency **and** low-confidence chase hardest; high-urgency-but-confident chases
   less. ✅ — but realized *simply*: **chase intensity is set by the urgency tier alone** (the ①
   table); **confidence governs only the autonomy outcome** (②). The "confident → chases less"
   property emerges because a confident agent *resolves* the ask (disarming the ladder) rather
   than by suppressing rungs. Explicit confidence-modulated rung-capping is deliberately
   deferred (YAGNI) — it can be added later without restructuring.
4. SwiftBar reflects changes immediately via push-on-change refresh. ✅ (slice ④)

## One open tunable

The reversible auto-proceed bar — `AUTONOMY_PROCEED_MIN = 0.95` — is a single named constant,
trivially retunable without code restructuring. Yousef can tighten it later.
