# Sundial v3: Hermes Native + Universal Agent Portability — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Sundial run natively inside Hermes (fexx profile), then generalize it so any agentic CLI/runtime (Claude Code, Codex, OpenCode, custom harnesses, headless servers) can wear the same clock — one core, many adapters.

**Architecture:** Extract a platform-neutral core (`sundial-core`: ledgers, policy, presence contract, estimator — already ~90% pure Python) from the macOS-specific sensor/notify shell. Introduce a **Backend Interface** with three implementations: `macos` (existing sensors/notifications, unchanged behavior), `linux` (XDG idle via `dbus`/`xprintidle`/`WAYLAND`, notify-send), `headless` (no presence sensors — wall ceilings only, delivery via webhook/log). Introduce an **Agent Adapter Interface** so hooks exist per-runtime: `hermes` (native cronjob + gateway delivery), `claude-code` (existing hooks), plus a documented `generic` stdin/stdout hook protocol any runtime can call. The watcher stays date-arithmetic, zero LLM, launch-shy, honesty rails intact.

**Tech Stack:** Python 3.9+ stdlib only (preserve zero-dependency guarantee); pytest; launchd (macOS) + systemd timer (Linux); Hermes cronjob + terminal/file toolsets for the native integration; no new pip deps anywhere.

**Repo of record:** `/Users/OTI_1/Desktop/AI-WallClock-Project` (branch: `v3-portable`). Uncommitted working-tree state (`setup.sh` modified, `bin/wallclock`, `data/` untracked) must be committed or stashed BEFORE Task 0.

---

## Phase 0 — Baseline hardening

### Task 0: Clean tree and feature branch — PRESERVE THE UNCOMMITTED TCC FIX
**Objective:** Start v3 from a verified green baseline WITHOUT losing the uncommitted launchd log-dir/TCC fix in setup.sh (the EX_CONFIG-78-at-reboot hardening, verified 2026-07-21 — launchd stdio must live in ~/Library/Logs/sundial, never inside a TCC-stamped project tree).
**Files:** none created beyond adjudicating untracked state.
1. Adjudicate untracked files: (a) commit the setup.sh TCC fix on main: `git add setup.sh && git commit -m "fix(setup): launchd stdio outside TCC-stamped tree — EX_CONFIG-78 hardening"`; (b) `bin/wallclock` vs `bin/sundial`: `diff bin/wallclock bin/sundial` — identical → delete; different → keep and note divergence; (c) `data/` stays git-ignored, never committed.
2. **Driver inventory (gatekeeper finding — verified 2026-08-26):** NO launchd plist exists on this machine (`~/Library/LaunchAgents` has no sundial entry). Record "no launchd writer present" and move on — later tasks must treat launchd teardown as CONDITIONAL (`if [ -f "$HOME/Library/LaunchAgents/com.sundial.watcher.plist" ]; then launchctl unload ...; fi`), never as an unconditional step.
3. **Existing Hermes cron job:** enabled job `cd2721fef9bd` ("Wall Clock — commitment checker", no_agent, ~15m) points through `~/.hermes/scripts/wallclock_cron_check.py` (symlink) at `watcher/cron_check.py`, which NO LONGER EXISTS in the working tree — the job currently fails silently every tick. Its historical behavior: prints due-commitment lines and stamps `notified.json` with LEGACY bare-ISO strings (handled by `migrate_entry()`'s str branch, so old entries remain readable). Task 6 owns its retirement.
4. THEN branch: `git checkout -b v3-portable`.
5. Run: `python3 -m pytest tests/test_sundial.py -q` — Expected: 298 passed in ~3s (verified 2026-08-26).
6. Commit: `git commit --allow-empty -m "chore: v3-portable baseline (298 tests green)"`.
7. Track this plan file itself: `git add docs/superpowers/plans/ && git commit -m "docs: v3 implementation plan"`.
8. **Two-folder note (verified):** `~/Desktop/sundial-staging` is a STALE clone of the same repo, 3 commits behind main (lacks the SwiftBar copy-not-symlink fix, menu-bar dots, soak docs). ALL v3 work happens in `~/Desktop/AI-WallClock-Project`. Do not touch staging except optionally `git -C ~/Desktop/sundial-staging pull` to fast-forward it, or delete it — never develop in both.

### Forensic rules that bind ALL later tasks (incident-derived, non-negotiable)
These are load-bearing conventions discovered in the code, each tied to a real incident; every new module MUST follow them:
- **Call-time path derivation (incident #6 — live data/ wipe):** never cache `core.DATA`-derived paths at import time; derive via `core._path(name)` / `core.DATA / name` at call time so test repointing isolates correctly.
- **Fail-safe exit:** hooks and optional blocks catch everything and degrade silently (exit 0, no output) — a clock bug can never block a session/prompt/cycle.
- **Single choke points:** message formatting only via `pick_message` (text truncated to 200 chars BEFORE format so terminal-rung autonomy clauses survive the 300-char backstop cap); delivery routing only via `deliver_fire`; ledger writes under `core._ledger_lock()` (fcntl — POSIX-only; Windows is out of scope by declaration, documented in README).
- **Sensor None softens, never blocks:** every sensor seam may return None and callers must degrade to legacy/wall semantics, never crash or stall.
- **Determinism:** message choice via `int(id,16) % len(pool)`; no LLM anywhere in watcher/lib/cli.

### Scope statement: what "core extraction" means here
`core/` is a NEW namespace shell (version, ABCs, backends, adapters) — `lib/core.py`'s engine does NOT physically migrate into it during v3. Rationale: lib/core.py is incident-hardened, 673 lines, and covered by 298 tests; moving it invites regressions for zero behavioral gain. v3's portability comes from backends/adapters wrapping it, not relocating it. Physical consolidation is deferred to a hypothetical v4 with its own migration spec.

## Phase 1 — Core extraction (no behavior change)

### Task 1: Create `core/` package as import shim
**Objective:** Establish the portable package without breaking existing imports.
**Files:** Create `core/__init__.py`; Test: `tests/test_portability.py`.
1. `core/__init__.py` contains ONLY a docstring and `__version__ = "3.0.0a0"` — no re-exports, no edits to `lib/core.py` (hooks already do their own `sys.path.insert` at `hooks/session_start.py:48,73`; nothing to change there).
2. Test (`tests/test_portability.py`): `def test_core_package_importable(): import core; assert core.__version__.startswith("3.")`.
3. Run `pytest tests/test_portability.py -v` → PASS. Commit `refactor(core): add core package skeleton`.
4. Scope note: this is a namespace shell only — see the Phase 0 scope statement for why lib/core.py does not physically move.

### Task 2: Freeze the Backend & Adapter interface contracts
**Objective:** Write the interfaces as code + docs before any implementation.
**Files:** Create `core/backends.py`, `core/adapters.py`, `docs/superpowers/specs/2026-08-26-universal-backend-design.md`.
1. `core/backends.py` defines ABC `PresenceBackend` with methods:
   - `idle_seconds() -> float | None`
   - `frontmost_app() -> str | None`
   - `screen_locked() -> bool | None`
   - `in_call() -> bool | None`
   All may return None = "sensor unavailable"; watchers MUST treat None as softening, never blocking (mirrors existing honesty rail).
2. `core/adapters.py` defines ABC `AgentAdapter` with:
   - `name`, `session_claim_path() -> Path | None`
   - `deliver_fire(rung_text, tier, urgency) -> str` ("popup"|"session"|"webhook")
   - `context_block() -> str` (the `<sundial>` injection payload)
3. Spec doc states the generic hook protocol: JSON on stdin `{event: session_start|prompt_submit|tick, prompt?: str, session_id?: str}` → stdout text block, exit 0 always. This is THE portability contract.
4. Tests pin that both modules import and ABCs reject instantiation.
5. Run pytest → PASS. Commit `feat(core): backend/adapter interface contracts + generic hook spec`.

### Task 3: Split mac-specific code behind `backends/macos.py`
**Objective:** Move HIDIdleTime/lsappinfo/pmset reads out of watcher logic into the macos PresenceBackend; watcher calls `backend.*` only.
**Files:** Create `core/backends/macos.py`; Modify `watcher/presence.py` (replace direct subprocess sensor calls with backend delegation).
1. Failing test first: monkeypatch `PresenceBackend.get()` returning a fake with fixed idle/app values; assert `presence.classify()` returns HERE/ELSEWHERE/AWAY identically to current golden outputs (copy 3 cases from existing `test_sundial.py` fixtures).
2. Implement `MacOSBackend(PresenceBackend)` wrapping the exact existing commands (`ioreg -c IOHIDSystem`, `lsappinfo info -only name`, `pmset -g assertions`) — cut-and-move, no logic edits.
3. Run FULL suite `python3 -m pytest tests/ -q` → PASS (proves no behavior change). Commit `refactor(presence): sensors isolated behind MacOSBackend`.

### Task 4: Notification backend split
**Objective:** Delivery (Sundial.app applet / osascript fallback / chimes / speech) becomes a NotifyBackend too.
**Files:** Create `core/backends/notify.py`; Modify `watcher/watcher.py` fire sites (~lines 482–650: `chime()`, `speak_final()`, `desktop_notify()`, `deliver_fire()`) to call `notifier.deliver(...)`.
1. The MacOSNotifier must encapsulate ALL of today's mechanics verbatim as move-only refactoring: (a) compiled-applet preference with attribution to "Sundial" not Script Editor (TCC identity); (b) `data/notify.txt` title-line-1/rest-of-lines protocol incl. the incident-#6 rule of deriving `core.DATA` at call time, never import time; (c) the async `open -g -a` + 1s sleep race guard before a same-cycle overwrite; (d) raw-osascript fallback with escaping; (e) CHIME_MAP {Tink 0.35, Glass 0.5, Hero 0.6, return Purr 0.35} with presence-scaled volume (whisper x0.6 elsewhere); (f) speech only on terminal rung, `data/speak.txt` opt-in, force only for high tier.
2. Test: fake NotifyBackend records deliveries; assert rung cadence, courtesy muting rules (locked screen, 30-min absence), ≤3-pings cap, and chime/speech gating produce identical call sequences as today's ledger expectations (golden files).
3. Implement `LinuxNotifier(notify-send -u critical -a Sundial)` and `WebhookNotifier(env SUNDIAL_WEBHOOK_URL)` as NotImplementedError stubs (filled Phase 3).
4. Full suite PASS. Commit `refactor(notify): delivery behind NotifyBackend`.

## Phase 2 — Hermes native integration

### Task 5: Hermes adapter (`core/adapters/hermes.py`)
**Objective:** Sundial speaks Hermes: context blocks injected via memory/config, fires delivered through the gateway — with the human-vs-machine distinction preserved.
**Files:** Create `core/adapters/hermes.py`; Create `bin/sundial-hermes-hook` (stdin/stdout script implementing the generic protocol).
1. Test: feed `{"event":"session_start"}` JSON to the hook script via subprocess; assert exit 0, output starts `<sundial>`, contains local time line and due-count line; corrupt JSON input still exits 0 silently.
2. Implement hook by importing `hooks/session_start.py`'s `build_block` (reuse, don't duplicate).
3. **Machine-event filtering is mandatory (forensic finding):** Hermes cron-injected context and task notifications must be classified as machine events exactly as `prompt_submit.is_machine_event` does (`<task-notification>`, `[SYSTEM NOTIFICATION` markers) — they must neither disarm awaiting-reply asks, nor stamp last_prompt.json, nor refresh the session claim. The hermes hook exposes `{"event":"prompt", "machine": true|false}` so the agent's habit layer can pass it honestly; default when absent = treat as human ONLY if the marker scan passes.
4. **Claim-TTL limitation carried honestly (from docs/notes/session-voice-soak-result.md):** claims refresh on human prompts only; long autonomous Hermes stretches read as unclaimed and fires queue instead of routing. Accepted behavior (it answers "is a human present?", not "is the agent busy?") — document in docs/integrations/hermes.md; do NOT paper over it by auto-refreshing claims from agent activity, which would break the presence semantics.
5. `deliver_fire` for hermes writes to `data/session_speak.json` exactly as session-voice does today (channel parity guaranteed by construction); the welcome-back bridge (`welcome_back.json` written by watcher ≥20-min returns, consumed-once by the prompt path with lock serialization) gets a hermes-side consumer that renders the same `<presence-return>` block.
6. Suite PASS. Commit `feat(hermes): native adapter implementing generic hook protocol`.

### Task 6: Wire into this Hermes profile (fexx)
**Objective:** The running agent wears the clock with EXACTLY ONE driver — retiring the dead legacy job, not stacking beside it.
**Files:** Cron job via hermes cronjob tool (10m tick running `python3 $SUNDIAL_HOME/watcher/watcher.py` — same entry point launchd would run); append pointer to fexx memory + AGENTS.md instruction; Create `docs/integrations/hermes.md`.
1. **Retire the dead job FIRST (gatekeeper finding):** remove cron job `cd2721fef9bd` (cronjob action='remove') and delete the dangling symlink `~/.hermes/scripts/wallclock_cron_check.py`. Its script target no longer exists so it currently fails silently every tick — but leaving an enabled second writer is a loaded gun: if `watcher/cron_check.py` were ever restored it would resume stamping `notified.json` with legacy bare-ISO entries alongside the real watcher.
2. **Conditional launchd teardown:** only if `$HOME/Library/LaunchAgents/com.sundial.watcher.plist` exists (it does NOT on this machine as of 2026-08-26) unload and verify; otherwise record "no launchd writer" and proceed.
3. **Single-writer discipline:** exactly one driver ever — verify after wiring (`launchctl list | grep -i sundial` empty AND only the new cron job in jobs.json). The ≤3-pings cap is per-item, not per-driver; two drivers double-fire. `sundial doctor` (Task 11) permanently enforces this check, including warning on any enabled sundial-adjacent hermes cron job other than its own tick.
4. **TCC probe (gatekeeper finding):** the repo lives under ~/Desktop (TCC-stamped). A new writer identity (hermes cron → python3) touches `data/`. Add to doctor: write-probe `touch data/.tcc_probe` from the same context the cron uses and report failure loudly; setup.sh keeps stdio/logs outside the project tree per the v1.0.2 lesson.
5. Environment parity: set `SUNDIAL_TZ` explicitly (default TZ is UTC) and point `SUNDIAL_MEMORY_DIR` at the fexx profile memories dir so decay scoring scores HERMES memory.
6. Manual verification: run hook, confirm `<sundial>` block renders; `sundial ask "integration smoke test" --due +1m --weight high`, wait one tick, confirm fire through cron path. **Disarm honesty:** with no per-prompt hook guarantee in Hermes, disarm is habit-based — verify manually that a human prompt followed by the agent running the hook disarms, and RECORD THE OBSERVED LATENCY in docs/integrations/hermes.md as a known limitation rather than claiming mechanical disarm.
7. Commit `docs(integrations): hermes profile wiring + handoff notes`.

### Task 7: Two-clock ledger for Hermes sessions
**Objective:** Session-ledger dual clock (wall-ms × tokens) populated from Hermes runs; budget-crossing nudges preserved.
**Files:** Modify `lib/core.py` ledger helpers (`start_session`/`best_effort_tokens` already parse transcript JSONL token usage — extend with an explicit-input path); Create `cli/tick.py` recording `{session_id, wall_ms, tokens}` into `data/session-ledger.json`.
1. Test: two sequential `tick` writes accumulate correctly; malformed input ignored (fail-safe contract).
2. Token source precedence: (a) explicit `--tokens N` flag from the calling agent (documented convention), (b) Hermes session metadata if parseable, (c) None — row records wall-ms only. Never fabricate a token count.
3. Estimator consumes ledger rows already — verify `sundial estimate` returns calibrated P50/P90 after ≥3 synthetic entries, and that `estimator.budget_flags` still fires once-per-threshold (50/80/100% of P90) on open estimated plain commitments with its 3×P90 staleness guard intact.
4. Suite PASS. Commit `feat(ledger): hermes two-clock ticks feed estimator`.

## Phase 3 — Cross-platform backends

### Task 8: Linux presence + notify backends
**Objective:** Feature-complete linux backend (dev machine / server with GUI).
**Files:** Create `core/backends/linux.py`; fill LinuxNotifier stub.
1. Sensors: `xprintidle` (X11), `org.freedesktop.ScreenSaver` dbus GetSessionIdleTime (Wayland fallback), frontmost via `xdotool getactivewindow getwindowname` basename-match against app-name heuristics; lock state via `loginctl show-session $XDG_SESSION_ID -p LockedHint`. Every command missing → None (soften-only rule).
2. Notifier: `notify-send -u critical -a Sundial` with sound via `paplay` if present; speech via `spd-say` optional.
3. Tests: pure-unit with monkeypatched subprocess results (CI has no GUI); assert classify parity with macos goldens.
4. Suite PASS. Commit `feat(linux): presence + notify backends`.

### Task 9: Headless backend (servers, containers)
**Objective:** No sensors at all: wall ceilings drive everything; delivery via webhook/log; honesty rail "sensors can be wrong" becomes "there are no sensors."
**Files:** Create `core/backends/headless.py`; Modify backend factory `core/backends/__init__.py::detect()` to choose via `SUNDIAL_BACKEND` env override else platform probe.
1. Test: headless classify always AWAY-equivalent (full-speed ladder); ceiling forces final rung; webhook notifier posts JSON `{text,tier,rung}` (mocked HTTP).
2. Suite PASS. Commit `feat(headless): zero-sensor backend with webhook delivery`.

### Task 10: setup.sh v3 — multi-platform installer
**Objective:** One installer, three targets: `./setup.sh --platform macos|linux|hermes [--fresh]`.
**Files:** Modify `setup.sh` (currently mac-hardwired lines ~57–230); Create `setup-linux.sh` helper for systemd unit `sundial.timer` (10-min OnCalendar) + user notifier config.
1. Preserve all v1.0.2 delivery fixes verbatim in the macos path (TCC identity note lines 158+, mirroring setting checks).
2. hermes target: skip applet compile + launchd entirely; register cron tick + hook instructions; print post-install checklist.
3. Test matrix executed manually: `--platform hermes` dry-run on this machine (idempotent second run changes nothing — diff data/ before/after); linux path validated by `bash -n` + unit review (real validation deferred to CI Task 13, noted honestly in plan output).
4. Commit `feat(setup): multi-platform installer with hermes target`.

## Phase 4 — Polishing

### Task 11: `sundial doctor`
**Objective:** One command verifying the whole chain per README roadmap.
**Files:** Create `cli/doctor.py`; wire in `bin/sundial`.
Checks: exactly-one-driver (launchd plist XOR hermes cron tick; warn on any OTHER enabled sundial-adjacent cron job); backend sensor reachability (each sensor individually, reporting which are None and why); notifier permission registration hint (macos TCC); TCC write-probe (`touch data/.tcc_probe` from cron-equivalent context — the repo sits under TCC-stamped ~/Desktop); webhook URL set (headless); ledger writability + v2-era JSON back-compat probe (see Task 10b); birth.json present; estimator sample-count adequacy; SwiftBar plugin checks (exists, not a symlink, SUNDIAL_HOME resolves, test bar-line render). Exit non-zero only on hard failures; soft warnings listed separately. Tests cover each verdict branch. Commit `feat(cli): sundial doctor`.

### Task 12: Learned quiet hours (roadmap item, deterministic)
**Objective:** Owner Model histogram → suggested quiet hours, applied as *sound-only* muting (delivery never sleeps — preserves rail).
**Files:** Create `lib/quiethours.py` (note spelling); Modify courtesy check in `watcher/watcher.py`.
1. Deterministic rule: hours where owner_model hourly activity p25 == 0 across ≥14 days → quiet. Written to `data/quiethours.json`; `sundial allow quiet-hours` disables. No LLM.
2. **Precedence with existing mechanisms (gatekeeper finding):** learned quiet hours compose with — never conflict with — (a) owner-declared snooze (`sundial snooze`, which already holds delivery), and (b) `tzutil.in_quiet_hours` working-hours flag. Precedence order documented in the module docstring: declared snooze > learned quiet > nothing. Learned quiet gates SOUND ONLY, matching sound_allowed()'s contract; it can never hold a delivery the current code would deliver.
3. Tests: synthetic histograms → expected windows; insufficient data → no quiet hours (fail-open to current behavior). Commit `feat(watcher): learned quiet hours, sound-gated only`.

### Task 10b: Data migration / back-compat guarantee
**Objective:** v3 must read every existing v2-era ledger untouched — no migration, no breakage. (Numbered 10b because it belongs with the Phase-3 installer work; execute it immediately after Task 10, BEFORE Tasks 11–12 which reference it.)
**Files:** Test: `tests/test_v2_compat.py` (fixtures copied from live data/ shapes).
1. Fixtures built from the ACTUAL live file shapes (birth.json, commitments.json with est/P50/P90 snapshots, notified.json with structured entries AND legacy bare-ISO strings from the old cron_check.py writer, habits.jsonl with estimate open/close pairs, session_speak.json queue entries, memory-weights.json).
2. Assert: v3 code loads each unchanged; `migrate_entry()` still absorbs legacy str stamps; new files (`data/quiethours.json`) absent → default-off. Version bump to 3.0.0 changes NO schema.
3. Doctor's runtime back-compat probe (defined here so Task 11 can implement it): attempt `core.read_json` on each present ledger with a malformed-bytes sentinel injected in a TEMP copy — doctor reports "ledger parseable" per file, never crashes on corrupt input.
4. Suite PASS. Commit `test(compat): v2-era ledger fixtures load unchanged under v3`.

### Task 13: Menu-bar face — SwiftBar plugin v3 + Hermes parity
**Objective:** Keep the macOS menu-bar presence first-class and give headless/Linux an equivalent at-a-glance surface; the plugin must survive the backend refactor untouched in behavior.
**Files:** Modify `contrib/sundial.30s.sh`; Create `contrib/sundial-headless-status.sh`; Create `docs/integrations/swiftbar.md`.
1. **Refactor reads through core, keep shell thin.** The plugin currently embeds ~6 inline Python blocks re-parsing ledgers (presence.json, commitments.json, opportunities.json, snooze.json, session_speak.json). Extract a single read-only CLI entry: `sundial status --json` (new `cli/status.py`) emitting one JSON document with presence/open_asks/actionable_offers/estimate_at_risk/snooze/session_queue — plugin becomes a thin jq/python formatter. Read-only contract preserved: never writes data/, never signals the watcher. This also gives Linux/headless the same payload for their own bars (waybar/i3blocks) and for Hermes to render on request.
2. **Preserve the two known SwiftBar traps as install-time checks** (both already bit us once): (a) real file, not symlink — SwiftBar freezes symlinked plugins after one run; setup.sh hermes/macos targets must COPY and verify `[ ! -L ]`; (b) SUNDIAL_HOME must be exported because SwiftBar copies plugins out of the repo and the script's self-path resolution then falls back to `$HOME/sundial`. Add both checks to `sundial doctor` (Task 11): plugin exists, is not a symlink, SUNDIAL_HOME resolves, and a test render of the bar line succeeds.
3. **Behavior parity pinned by tests:** presence dot semantics (● green here / ● orange elsewhere / ○ gray away-unknown), badge counts excluding curiosity offers from ✋ (deliberate: curiosity is passive context), degradation rule (any empty read → bare sun glyph, no partial text), estimate line red-at-risk logic (`remaining < p90` when due, `elapsed > p90` when not), session-queue 🗣 count of unconsumed entries only.
4. **Menu-bar additions for v3 state:** show a ⏱ glyph when any commitment is running past its own P90 even if unestimated items exist; show 🔇 indicator during learned quiet hours so silence is legible as policy, not failure (sound-gated only — delivery unaffected).
5. Tests for `cli/status.py` JSON contract (each field present, correct filtering); shell-level test that the plugin script renders a valid first line from a fixture DATA_DIR via `SUNDIAL_HOME=fixture bash contrib/sundial.30s.sh | head -1`.
6. Suite PASS. Commit `feat(menubar): status CLI + swiftbar v3 + doctor checks`.

### Task 14: CI + release
**Objective:** GitHub Actions matrix (macos-latest, ubuntu-latest) running full suite; versioned release. Runs LAST so v3.0.0 contains all feature work including the menu-bar refactor.
**Files:** Create `.github/workflows/test.yml`; Update `README.md` (portability section, backend table, hermes integration section); bump `core.__init__.__version__` to `3.0.0`.
1. Headless backend makes ubuntu CI meaningful without GUI.
2. Tag `v3.0.0`. Commit `ci+release: v3.0.0`.

---

## Verification (definition of done)
1. Full suite green (≥298 baseline tests + all new) on both CI platforms. **Scope honesty:** CI covers the test suite, NOT installer correctness — setup.sh linux path is validated by `bash -n` + review only (stated in Task 10); DoD does not claim installer coverage.
2. Hermes live: session start shows `<sundial>` block; ask→fire-through-cron verified mechanically; **disarm verified manually with recorded observed latency** in docs/integrations/hermes.md (no mechanical per-prompt disarm exists in Hermes — R1 is a documented limitation, not a solved problem); machine-marker prompts provably do NOT disarm or stamp (unit test).
3. Parity proof: identical commitment produces identical rung sequence pre/post refactor (golden files from Tasks 3/4), across all tiers including snooze holds and high-tier wall-ceiling breakthrough.
4. Honesty rails intact, grep-verified: no LLM call sites in watcher/lib/cli; ≤3 ping cap per item per driver passes; decay computed-not-enacted pinned by an explicit test (assert compute_weights never deletes/mutates memory files — the invariant already stated in lib/decay.py's contract, now enforced); sensors returning None degrade, never block (parametrized None-injection over every sensor seam).
5. `sundial doctor` exits 0 on this Mac; reports actionable items for linux/headless; enforces exactly-one-driver; TCC write-probe passes.
6. Menu-bar parity: plugin renders identical bar line from fixture ledgers pre/post refactor; push-refresh still fires on state change.
7. Back-compat: v2-era fixture ledgers load unchanged under v3 (Task 10b suite green).

## Subsystems inventory (forensic record — every one must survive v3 untouched in behavior)
Escalation ladder (3 tiers × rungs × ceilings × ELSEWHERE half-rate × sleep-as-away accrual) · breakpoint bounded-deferral delivery · message pools (normal/tier-neutral/elsewhere-app-aware/return/plain + agent-authored stored rungs + default-action terminal append) · session-voice routing (claim TTL 3600s, terminal mirror, queue cap-20 evict-consumed-first, write-failure desktop fallback, stale-claim byte-parity) · welcome-back bridge (watcher write → hook consume-once, 20-min threshold, flock serialization) · snooze (owner window, delivery-only hold, high-tier ceiling breakthrough, menu-bar 😴 line) · opportunities (meeting start/end/stale via display assertions + WebRTC discriminator, net corroboration via vnstat, build-finished, any-depth folder curiosity with self-enrolling roots, 5/day cap, evidence dedup, decline×3 suppression, silent prep opt-in with fail-closed budget charge) · owner model (deterministic distill, owner-driven-event filtering for the hourly histogram, ≤6h refresh) · estimator (P50/P90 ratios from habits.jsonl, review-clock latencies, sanity_line deadline warnings, budget_flags once-per-threshold with 3×P90 staleness skip, wall-time outlier self-nulling guard, calibration_health surfaces) · decay (ACT-R compute-only) · SwiftBar (read-only, presence dots, badge exclusions, P90 at-risk red semantics, snooze/queue lines, push-refresh signal, symlink+path traps) · notifier chain (applet attribution/TCC, notify.txt protocol, osascript fallback, chime map w/ presence volume scaling, speak.txt-gated final-rung speech, lock/30-min audio courtesy) · hooks (session_start context incl. autonomy verdicts + two-clock block + estimation health + session-voice duty line; prompt_submit disarm + tick + claim re-arm w/ 60s debounce + offers + welcome-back + budget flags; machine-event filter).

## Risks / open questions
- **R1:** Hermes has no guaranteed per-prompt hook — auto-disarm is habit-based, full stop. The plan no longer pretends otherwise: Task 6 records observed disarm latency as a known limitation; doctor surfaces it. Weakest seam in the whole design.
- **R2 (rewritten — gatekeeper finding):** the real dual-writer hazard was never cron-vs-launchd; it was the EXISTING enabled hermes cron job `cd2721fef9bd` pointing at a deleted script. Retired in Task 6.1; doctor permanently guards against recurrence.
- **R3:** Wayland frontmost-app detection is best-effort; spec accepts None (softening only).
- **R4:** Working tree dirty state resolved in Task 0 (TCC fix committed first; bin/wallclock adjudicated by diff).
- **R5:** fcntl locks are POSIX-only — Windows support explicitly out of scope for v3; documented rather than pretended.
- **R6:** The claim-TTL autonomous-stretch gap (soak doc) is accepted behavior, not a bug to fix silently in the port.
- **R7:** TCC identity churn: moving execution from launchd to hermes cron changes which process identity writes data/ under a TCC-stamped ~/Desktop. Mitigated by Task 6.4 probe + doctor check + logs outside project tree.

## Gatekeeper audit record
First audit (2026-08-26): verdict on pre-patch draft 55/100 with 2 BLOCKERs (phantom launchd premise; live legacy cron job `cd2721fef9bd`), 5 MAJORs, 5 MINORs. All verified against the machine and incorporated.
Re-audit (2026-08-26): **PASS WITH CAVEATS, ~88/100** — all prior fixes verified landed coherently; zero fabricated claims detected; honesty rails intact. Findings applied: MAJOR-1 release ordering (menu-bar Task now precedes the CI/release task as Task 13→14); MINOR-2/3 (Task 10b execution order clarified + doctor's runtime back-compat probe defined); MINOR-4 (`data/quiethours.json` spelling fixed); MINOR-5 (plan file tracked in git, Task 0 step 7). Two-folder hazard documented in Task 0 step 8: `~/Desktop/sundial-staging` is a stale clone 3 commits behind main — all work happens in `AI-WallClock-Project` only. Plan is execution-ready.
