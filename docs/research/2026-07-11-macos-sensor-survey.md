# Sundial sensor survey: what else macOS lets a local agent read (2026-07-11)

Scope: macOS 15/26-era, Apple Silicon, deterministic, no-cloud, launchd-agent context (per-user GUI session — NOT a root daemon; several APIs below die in daemon context). Every signal was tested read-only on this machine where safe; permission claims for the uncertain ones were web-verified against primary sources (ActivityWatch, OverSight/Guard, Apple dev forums, drewkerr Focus gist, Snelson SSID reference). Current sensors already shipped: HID idle (ioreg), frontmost app (lsappinfo), screen lock (ioreg), pmset assertions (WebRTC/display-keepers), vnstat, mdfind, ps builds.

## Summary table

| Signal | Mechanism | Permission | Poll cost / 10-min cycle | Sundial axis | Verdict | Status |
|---|---|---|---|---|---|---|
| Concurrent Claude/agent sessions | `ps -axo pid,etime,tty,comm` filter `comm == claude` | none | ~10 ms (already sampling ps) | (b) estimation covariate | **adopt-now #1** | VERIFIED (2 sessions live) |
| CPU load avg | `sysctl -n vm.loadavg` (+ `hw.ncpu` once) | none | ~5 ms | (b) | **adopt-now #2** | VERIFIED (4.27 on 8 cores) |
| Memory pressure + swap | `sysctl kern.memorystatus_vm_pressure_level`, `sysctl -n vm.swapusage`, `vm_stat` | none | ~15 ms | (b) | **adopt-now #2** (same bundle) | VERIFIED (35% free, swap 5.2/6 GB used) |
| Thermal state | `NSProcessInfo.thermalState` via ctypes/pyobjc (0–3) | none | ~50 ms | (b) | **adopt-now #3** | VERIFIED (0 = nominal). `pmset -g therm` is useless on Apple Silicon — only historic warnings |
| Battery/AC + Low Power Mode | `pmset -g batt`; `NSProcessInfo.isLowPowerModeEnabled` | none | ~20 ms | (b) + (a) context | **adopt-now #4** | VERIFIED (AC, 80%, LPM off) |
| Mic in use | CoreAudio `kAudioDevicePropertyDeviceIsRunningSomewhere` on default input, ctypes | none (reading state ≠ capturing) | ~10 ms | (c) + (d) + meeting corroboration | **adopt-now #5** | VERIFIED (False; web: same mechanism as OverSight) |
| Camera in use | CoreMediaIO `kCMIODevicePropertyDeviceIsRunningSomewhere`, ctypes | none | ~10 ms | (c) + (d) | **adopt-now #5** (same helper) | VERIFIED (1 device, not running) |
| Audio output active | CoreAudio running-somewhere on default output | none | ~10 ms | (d) music-vs-call discrimination | **adopt-now #5** (same helper) | VERIFIED (False) |
| Input event rates | `CGEventSourceCounterForEventType(kCGEventSourceStateHIDSystemState, type)` deltas | none — counters are NOT an event tap; no Input Monitoring/Accessibility (web-confirmed: ActivityWatch ships it permission-free) | ~5 ms | (a) engagement intensity; better breakpoints | **adopt-now #6** | VERIFIED (keydown ctr 10,370; 5 s idle delta = 0, correct) |
| Display count / mirroring | `CGGetOnlineDisplayList` + `CGDisplayIsInMirrorSet`, ctypes | none | ~10 ms | (d) presentation detection | **adopt-now #7** | VERIFIED (2 online, mirrored) |
| Calendar busy-state | EventKit (pyobjc `requestFullAccessToEvents`) or icalBuddy | one-time Calendar grant; headless gotcha: bare `python3` from launchd has no Info.plist → TCC silently denies; grant must land on the responsible binary or an .app wrapper | ~100 ms once granted | (c) — strongest review-latency signal there is | **adopt-later** | UNVERIFIED locally (icalBuddy not installed; testing = TCC prompt on owner's screen) |
| Focus / DND state | parse `~/Library/DoNotDisturb/DB/Assertions.json` (manual Focus) + `ModeConfigurations.json` (scheduled triggers) | Full Disk Access on the reading binary in launchd context (read fine from this shell — inherited FDA; web: getfocus/drewkerr both require FDA) | ~10 ms | (c) + (d) | **adopt-later** | VERIFIED read+parse (no active assertion; owner has 3 modes, none scheduled) |
| App-switch events (push) | `NSWorkspace.didActivateApplicationNotification`, pyobjc runloop LaunchAgent | none (must be per-user agent, never a daemon) | zero-poll, event-driven | (a) edges + breakpoint delivery | **adopt-later** | VERIFIED pyobjc 12.1 + NSWorkspace present; runloop not stood up |
| Time Machine backup running | `tmutil status` | none | ~50 ms | (b) IO-contention covariate | **adopt-later** | VERIFIED |
| `log show/stream` predicates | `/usr/bin/log show --last 30s --predicate …` | none for most subsystems | ~4 s per query (measured) or a persistent `log stream` child | niche detectors | **adopt-later** | VERIFIED (4.05 s) |
| Window titles | CGWindowList `kCGWindowName` | Screen Recording TCC | — | — | **excluded** | VERIFIED blocked (18 windows, 0 titles, `CGPreflightScreenCaptureAccess` False) |
| Wi-Fi SSID / location | `ipconfig getsummary`, CoreWLAN | redacted on 15 without tricks; Tahoe: Location + signing, LaunchAgents get nil | — | — | **excluded** | VERIFIED redacted here |
| Screen Time app usage | knowledgeC.db / Biome / DeviceActivity | FDA + undocumented schema / family-controls entitlement | — | — | **excluded** | UNVERIFIED (documented) |
| Now-playing metadata | MediaRemote framework | private API, locked down 15.4+ | — | — | **excluded** | — |
| Per-process power/GPU | `powermetrics` | sudo | — | — | **excluded** | VERIFIED refuses non-root |
| launchd job states | `launchctl list` | none | ~30 ms | — | **excluded** (readable, no decision value; 515 jobs = noise) | VERIFIED |

## Adopt-now (ranked by value-per-complexity for the Phase B estimation loop)

### 1. Concurrent agent-session count — the workload covariate Phase B most needs
`ps -axo pid,etime,tty,comm` filtered to `comm` basename `claude` (plus distinct-tty count for human terminal sessions). This machine right now: 2 claude processes, 2 ttys, one at 23% CPU. Integration: `presence.sample_ps()` already runs every cycle — extend `parse_ps_builds`-style parsing with a `parse_ps_agents()` that returns `{count, total_etime_s, ttys}` and log it as a `habits.jsonl` line. When Phase B regresses actual-vs-estimated duration, "how many sibling agents were running" is the single most causal, most controllable feature — and it's free.

### 2. System-load bundle: loadavg + memory pressure + swap
`sysctl -n vm.loadavg vm.swapusage` + `sysctl kern.memorystatus_vm_pressure_level` (1=normal 2=warn 4=critical); whole bundle measured at 30 ms. Integration: one `log_habit({"kind": "sysload", ...})` per cycle with `load1/ncpu` (normalized), pressure level, swap-used fraction. This box was at load 4.27/8 cores with swap 85% full during a Claude session — exactly the regime where builds and test suites run 2-3x their estimates. These are the covariates that let Phase B say "your estimate was fine; the machine was drowning."

### 3. Thermal state + Low Power Mode
`NSProcessInfo.processInfo.thermalState` (0 nominal → 3 critical) and `isLowPowerModeEnabled`, both via a ~15-line ctypes `objc_msgSend` helper (verified working; no pyobjc dependency needed, though pyobjc-Cocoa 12.1 is installed). Apple Silicon throttles under thermal pressure and Low Power Mode caps performance cores — silent, invisible causes of "why did this run long." Sample once per cycle into the same sysload habit line. Note: `pmset -g therm` returns only historic warning notes on Apple Silicon; NSProcessInfo is the correct mechanism.

### 4. Battery/AC state
`pmset -g batt` — one line to parse (`'AC Power'|'Battery Power'`, percent). On-battery is both a covariate (some machines throttle) and context (owner may be mobile → slower review latency). Trivially rides the existing `_run()` pattern.

### 5. Mic / camera / audio-output triad — one ctypes helper, three signals
CoreAudio `AudioObjectGetPropertyData` with selector `'gone'` (`DeviceIsRunningSomewhere`) on the default input and output devices; CoreMediaIO same property for camera devices. All three verified working, zero TCC (reading device state is not capturing — this is OverSight's and Guard's mechanism, public and documented). Decision table: **mic on** → owner is on a call → review-latency high, do NOT chime (courtesy hard-gate stronger than the current WebRTC-assertion heuristic, and catches non-WebRTC calls like FaceTime/native Zoom audio); **camera on** → corroborates meeting-start/end transitions in `detect_meeting`; **output on, mic off** → media playback → whisper-volume chimes, not silence. Integration: one `av_state()` helper in presence.py returning `{mic, cam, audio_out}`, folded into `sound_allowed()` and `detect_meeting()`.

### 6. Input event-rate deltas
`CGEventSourceCounterForEventType(1, type)` for keydown/click/scroll/move — monotonic counters, no event contents, no Input Monitoring TCC (web-confirmed; ActivityWatch ships identical calls permission-free). Store last cycle's counters, log the deltas. Upgrades presence from binary here/away to engagement intensity: 600 keydowns/cycle = deep work (defer harder, coarser breakpoint); ~0 keydowns but mouse-moves = passive reading (fine time to knock). Also sharpens `wait_for_breakpoint`: a typing-burst end is a truer pause than raw idle crossing 15 s.

### 7. Display mirroring — the presentation gate
`CGGetOnlineDisplayList` + `CGDisplayIsInMirrorSet` (verified: this machine reports 2 online, mirrored). Mirrored displays ≈ presenting/screen-sharing to a room — the one moment a Sundial popup is maximally embarrassing. Courtesy rule: mirrored → suppress popups AND audio for the cycle (macOS mutes its own notifications when mirroring; Sundial's `afplay` chimes bypass that today).

## Adopt-later

### Calendar busy-state (highest review-latency value; permission plumbing is the work)
Mechanism: EventKit via pyobjc (`EKEventStore.requestFullAccessToEvents`) or `brew install ical-buddy` then `icalBuddy -n eventsToday`. The catch (web-verified): the Calendar grant attaches to the responsible binary — a bare `python3` under launchd has no Info.plist, so TCC denies silently with no prompt. Sundial already ships `Sundial.app` for notification attribution; the same wrapper approach (or a one-time interactive icalBuddy run from a granted terminal) solves it. Payoff: "owner's calendar says busy until 15:00" converts review-latency prediction from inference to lookup — the single best future feature for tier timing. UNVERIFIED locally (testing would pop a TCC dialog on the owner's screen).

### Focus / Do-Not-Disturb state
Parse `~/Library/DoNotDisturb/DB/Assertions.json` — key `data[0].storeAssertionRecords` present ⇔ a manual Focus is active (verified readable and parseable here). Two caveats (web-verified): (1) launchd context needs Full Disk Access granted to the reading binary — it read fine from this shell only because the shell's host app has FDA; (2) scheduled Focus writes NO assertion — you must also evaluate `ModeConfigurations.json` triggers (this owner has none scheduled, so the simple check would currently be sufficient). Value: (d) never chime into DND, (c) Focus-on predicts slow replies. Adopt when the FDA setup step is acceptable; degrade to None-safe like every other sensor.

### NSWorkspace app-switch notifications (event-driven upgrade)
`NSWorkspace.didActivateApplicationNotification` via a pyobjc runloop — no TCC, works headless, but only as a per-user LaunchAgent (never a root daemon). This replaces app-switch polling with push, giving exact presence edges and real-time breakpoint delivery instead of `DEFER_POLL_S` sampling. It's an architecture change (a persistent runloop process alongside the 10-min cycle), so it belongs after Phase B, not inside it.

### Minor: `tmutil status` (backup running = IO contention covariate, verified, free) and `log show --predicate` detectors (verified ~4 s/query — affordable once per cycle for a targeted question, but everything we currently want is available cheaper above; a persistent `log stream` child is the event-driven fallback if a future signal has no other read path).

## Excluded

- **Window titles (CGWindowList `kCGWindowName`)** — requires Screen Recording TCC (verified: 0 titles readable, preflight False) and violates Sundial's stated privacy boundary ("no window titles" — presence.py docstring). Owner-name-only window lists work TCC-free but add ~nothing over `lsappinfo front`.
- **Wi-Fi SSID / network location** — verified redacted via `ipconfig getsummary` here; `airport` removed (14.4+), `wdutil` needs sudo; Tahoe regressed further (CoreWLAN wants Location Services + real code-signing, and LaunchAgents get nil even then). Fragile per point-release; not worth it.
- **Screen Time app-usage** — knowledgeC.db needs Full Disk Access and an undocumented schema mid-migration to Biome; DeviceActivity/FamilyControls needs an Apple-approved entitlement. Not for a local script.
- **Now-playing metadata (MediaRemote)** — private framework, actively locked down since macOS 15.4. The CoreAudio output-running bit above covers the courtesy need.
- **powermetrics** (per-process power/GPU/thermal detail) — root only.
- **`launchctl list`** — readable (515 jobs) but carries no presence/estimation/courtesy decision value.
- **Keyboard/mouse event taps (CGEventTap)** — needs Input Monitoring TCC and reads contents; the permission-free counters (adopt-now #6) deliver the rate signal without touching it.

**Cost bottom line:** the entire adopt-now set adds roughly 150 ms and zero TCC prompts to the existing 10-minute cycle, needs no new dependencies (ctypes only; pyobjc 12.1 present but optional), and feeds Phase B four new estimation covariates (agent count, load/memory, thermal, power), two review-latency signals (mic/camera call state), and two courtesy gates (mirroring, media-vs-call) — all in the same `_run()`/`log_habit` idioms presence.py already uses.
