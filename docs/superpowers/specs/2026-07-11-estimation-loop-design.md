# Phase B: closing the estimation loop — design

**Date:** 2026-07-11
**Status:** approved (brainstormed with Yousef; autonomous execution greenlit)
**Builds on:** `lib/estimator.py` (Method B percentile calibration, shipped `bab9a8e`)

## Problem

The self-estimation engine exists and is mathematically done: `estimator.py`
calibrates a raw duration guess into P50/P90 for both clocks (my execution,
the owner's review) from measured ratio history, with a small-n honesty rule
and bucket support. It is **starved**: the live ledger holds 8 estimate
events, all hand-recorded on 2026-07-04, none since; buckets never used;
nothing consumes the output. Manual capture died in one day — that is data,
and this design treats it as such.

Phase B is therefore not "build an estimator." It is **close the loop around
the one that exists**: capture that cannot lapse, surfaces that make the
numbers act, and a live soak that proves the flywheel spins unattended.

## Decisions (brainstorm outcomes)

1. **Capture: automatic, on the mutation path.** Estimate open/close is a
   synchronous side effect inside the commitment verbs. No watcher diffing
   (10-min quantization poisons the data), no hook inference (violates the
   no-guessing rail). Capture survives because commitment mutations *must*
   happen for Sundial to function at all.
2. **Unit: commitments only.** No second lifecycle. Coverage boundary is
   explicit and accepted: work that never becomes a commitment stays
   uncalibrated — pressure to route real promises through the clock.
3. **Surfaces: deadline sanity at creation, session-start two-clock block,
   SwiftBar menu bar.** (Weekly report card: out of scope.)
4. **Buckets: agent-declared at creation** (`--bucket build|research|ops|write`
   recommended taxonomy; free-form allowed). Engine's ≥5-per-bucket fallback
   already guards scarce data.
5. **Done bar: loop proven live.** Merged is not done. Done = the live
   install auto-captures real estimate pairs with zero manual bookkeeping,
   all three surfaces render real numbers, and samples visibly accrue over a
   monitored soak.

## The two kinds ARE the two clocks

A structural gift discovered during grounding — no new taxonomy needed:

| Commitment kind | Blocked on | Lifecycle | Feeds |
|---|---|---|---|
| `awaiting-reply` (`ask`) | the human | armed → answered (prompt-submit disarm) | **Review clock** — `answered` latency events, *already wired* (`hooks/prompt_submit.py`) |
| `plain` (`remember`) | the agent | recorded → `done` | **Execution clock** — the new capture below |

Review-clock capture needs **zero new code**; it is data-poor only because
asks are rare. The execution clock is what this design wires.

## Design

### 1. Capture — commitment lifecycle instrumentation

**Creation** (`core.add_commitment`, exposed via `remember --est … --bucket …`):

- New optional params `est` (duration string, `estimator.parse_duration`
  grammar) and `bucket` (str).
- `est_s` = parsed `est` if given, else `due_at − created_at` when a due date
  exists and is positive. Neither → no estimate; the commitment is not part
  of the loop (unchanged behavior).
- When `est_s` resolves, the commitment record gains an `est` object — the
  display snapshot read by the surfaces:
  `{"est_s", "bucket", "p50_s", "p90_s", "n", "confidence"}`
  where p50/p90/n/confidence come from `estimator.estimate_execution(est_s,
  DATA, bucket)` at creation time.
- An **open estimate event** is appended to `habits.jsonl` via
  `record_estimate(..., actual_s=None, cid=<commitment id>)` — the
  pre-registration that keeps the loop honest (est logged *before* the work).
  `record_estimate` gains an optional `cid` field; audit trail links to the
  commitment.

**Close** (`core.resolve_commitment(id, "done")` path, via `done`):

- `actual_s = closed_at − created_at`.
- Appends the closing estimate event (`est_s` from the stored snapshot,
  `actual_s`, computed `ratio`, `bucket`, `cid`).
- **Only status `done` records a ratio.** Declined/cancelled/expired closes
  record nothing — an abandoned task is not a completed sample. Open events
  with no close are already excluded from calibration (ratio `None`).

**Failure contract:** capture never blocks or raises through a verb
(`record_estimate` is already fail-safe by contract; the new code keeps it).

### 2. Deadline sanity at creation

At `remember` time, when an estimate resolved AND calibration has data
(`n > 0`), compare calibrated P90 against time-to-due:

- `p90_s > (due_at − created_at)` → print one honest line:
  `⚠ history: P90 ~2h10m for this (n=9, ratio p90 1.6×) — deadline leaves 1h30m; pad it or tighten scope.`
- When `est` was omitted (est_s = due − created), the check degenerates to
  "historical P90 ratio > 1.0" — still meaningful: history says promises run
  over by that factor.
- `n == 0` → print nothing beyond normal output. The honesty rule forbids
  confident-sounding numbers from no data.
- Advisory only: never blocks, never mutates the deadline.

Pure helper `estimator.sanity_line(est_s, ttd_s, calib) -> str | None` so the
threshold logic is unit-testable without IO.

### 3. Session-start two-clock block

`hooks/session_start.py` adds a short section (same fail-safe try/except
pattern as the autonomy block):

- For open plain commitments carrying an `est` snapshot: elapsed vs P50/P90 —
  flag `running long (elapsed 1h40m > P90 1h20m)` only when elapsed > P90.
  Cap at 5 lines.
- One calibration-health line:
  `Estimation: 12 closed samples, ratio P50 0.9× (high confidence); review clock n=1.`

### 4. SwiftBar surface

`contrib/sundial.30s.sh` (read-only by charter) gains one dropdown line for
the nearest-due open estimated commitment: `⏱ <text> — P90 <t> (<state>)`,
rendered red when elapsed > stored `p90_s`. Reads only `commitments.json`
(the `est` snapshot — this is why the snapshot lives on the record); no math
duplication in shell. `refresh_menubar()` already fires on every mutating
verb, so the face updates instantly.

### 5. Buckets

`remember --bucket <str>`; recommended taxonomy `build | research | ops |
write` documented in README. Free-form accepted. Engine unchanged.

### 6. Deploy + live soak (the done bar)

1. Live install (`~/Desktop/AI-WallClock-Project`) synced to main; watcher
   restarted; SwiftBar verified.
2. Dogfood immediately: the Phase B build itself gets a pre-registered
   estimate (`bucket=build`) in the live ledger, closed on ship — sample #1
   of the new era flows through the new pipe.
3. Soak: over the following days of natural commitment flow, verify estimate
   pairs accrue with zero manual bookkeeping and surfaces render real
   numbers. A live Sundial commitment ("Phase B soak check", due +3d) makes
   the clock itself schedule its own verification.

### Out of scope

Weekly report card; declared-task verb for non-commitment work (revisit if
coverage proves thin); bucket auto-derivation; any change to the calibration
math; macOS sensor expansion (separate research digest,
`docs/research/2026-07-11-macos-sensor-survey.md`, feeds future specs).

## Testing

TDD throughout, mirroring house style (pure logic tested directly, IO paths
via temp-dir integration):

- est_s derivation: explicit `est` wins; due-derived fallback; neither → no
  `est` object, no event; garbage `--est` → verb errors cleanly (parse is
  strict by design).
- Creation writes the open event with `cid` and the snapshot on the record;
  calibration snapshot matches `estimate_execution` output.
- `done` writes the closing event with correct `actual_s`/`ratio`; declined /
  non-done closes write nothing; closing a commitment with no `est` writes
  nothing.
- `sanity_line`: warns iff `p90_s > ttd_s` and `n > 0`; silent otherwise.
- Session-start block: running-long flag iff elapsed > P90; line cap; block
  degrades to nothing on malformed data.
- Fail-safety: habits.jsonl unwritable → verbs still succeed.
- SwiftBar: shell-embedded python reader tested by invoking it against a
  fixture data dir (same pattern as existing plugin reads, if any) or kept
  trivially thin.

## Risks

- **Sample trickle, not flood:** commitments are created at human pace;
  confidence will be "low" (n<5) for a while. Accepted — the honesty rule
  exists precisely for this; the soak bar checks *flow*, not volume.
- **Ask-heavy usage starves execution samples:** if most commitments are asks,
  plain-promise flow stays thin. Mitigation: EA protocol already routes real
  moves through `remember`; revisit declared-task verb if the soak shows
  thinness.
- **Snapshot staleness:** p50/p90 on the record freeze at creation. Accepted
  for display; calibration itself always recomputes from full history.
