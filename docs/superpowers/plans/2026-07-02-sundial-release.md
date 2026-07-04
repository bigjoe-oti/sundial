# Sundial Part 2: Public Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scrubbed, rebranded, audit-passed staging repo at `~/Desktop/sundial-staging` ready for the owner to push to github.com/bigjoe-oti/sundial — everything short of the push itself.

**Architecture:** `git archive` exports master's tree to a staging directory (never touching the private repo); staged-only transformations apply the scrub inventory and the Sundial rebrand; the controller authors README + essay; a clean-eyes audit agent (not the scrubbers) re-scans everything; a single fresh-history commit finishes it.

**Tech Stack:** bash, Python 3.11 stdlib, git. Reference doc every task must read: `.superpowers/sdd/scrub-inventory.md` (in the PRIVATE repo).

## Global Constraints

- STAGING ONLY: every edit in this plan happens under `~/Desktop/sundial-staging`. The private repo at `~/Desktop/AI-WallClock-Project` is read-only reference (its `.superpowers/sdd/scrub-inventory.md` and code may be read, never written). The private repo's live `data/` dir is untouchable.
- The staging suite must stay green (65 tests) after every task: `cd ~/Desktop/sundial-staging && python3 tests/test_wallclock.py`.
- Rebrand vocabulary (exact): project name **Sundial**; CLI `bin/sundial`; launchd label `com.sundial.watcher`; applet `Sundial.app`; notification title `"Sundial"`; env vars `SUNDIAL_TZ`, `SUNDIAL_MEMORY_DIR`; MIT license, `Copyright (c) 2026 J. Servo LLC`.
- No git operations in staging until Task 4 (the fresh `git init`).
- Author identity for the final commit: `Sundial <sundial@users.noreply.github.com>` (placeholder — the owner may amend at push).

---

### Task 1: Staging export + scrub + genericize

**Files:**
- Create: `~/Desktop/sundial-staging/` (entire tree via `git archive`)
- Modify (in staging only): `lib/core.py`, `watcher/watcher.py`, `hooks/*.py` (env names if present), `setup.sh`, `README.md`, `SETUP.md`, `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`, `tests/test_wallclock.py`
- Delete (in staging): `watcher/com.sundial.watcher.plist`, `watcher/cron_check.py`, all `.DS_Store`, any `data/*` except two seed files

**Interfaces:**
- Produces: a staging tree with zero remaining references to the dev machine's home path, the private repo's internal tool integrations, or the owner/second-user's personal names outside this build-diary narrative, and `data/` contains exactly `commitments.json` (`[]`) and `session-ledger.json` (`[]`).

- [ ] **Step 1: Export**

```bash
mkdir -p ~/Desktop/sundial-staging && cd <private-dev-repo> \
  && git archive master | tar -x -C ~/Desktop/sundial-staging \
  && ls ~/Desktop/sundial-staging
```
Expected: full tree (bin cli data docs hooks lib setup.sh SETUP.md tests watcher README.md .gitignore).

- [ ] **Step 2: Apply every MUST + SHOULD + OPTIONAL item** from the private repo's scrub inventory to the staging tree. Transformations:
  - `lib/core.py`: `MEMORY_DIR = Path(os.environ.get("SUNDIAL_MEMORY_DIR", str(Path.home() / ".claude" / "memory")))` with comment `# Where your agent's long-term memory lives (for decay scoring). Set SUNDIAL_MEMORY_DIR to your harness's memory dir (Claude Code: ~/.claude/projects/<project-slug>/memory).`; `DEFAULT_TZ = os.environ.get("SUNDIAL_TZ", "UTC")` with comment `# Local timezone for quiet hours and display. Override with SUNDIAL_TZ.`
  - `watcher/watcher.py`: both personal-name fallbacks in `owner_name()` → `"Friend"`.
  - `rm watcher/com.sundial.watcher.plist watcher/cron_check.py`; `find . -name .DS_Store -delete`.
  - `data/`: keep only `commitments.json` and `session-ledger.json`, each containing `[]`.
  - README "Reused logic" section → `## Provenance` with one line: `Timezone and metering logic adapted from internal utilities; rewritten standalone here.`
  - Docs sweep (`docs/superpowers/**/*.md`): dev-machine paths generalized to `~`-relative notation; the owner's and second user's personal names replaced with role labels; lines naming unrelated private internal tools dropped; the launchd label generalized to `com.sundial.watcher`.
  - `tests/test_wallclock.py`: personal-name fixture strings replaced with a neutral name (`"Ada"`); SETUP.md example flag genericized to `--name YourName`.
  - Env renames everywhere: the private repo's old-prefix `*_TZ` / `*_MEMORY_DIR` names → `SUNDIAL_TZ` / `SUNDIAL_MEMORY_DIR` (code + docs + setup.sh).
  - `.gitignore`: ensure it covers `data/` live files (keep existing entries), `.pytest_cache/`, `.DS_Store`, `watcher/Sundial.app/` (renamed in Task 2 — add both `Wall Clock.app` and `Sundial.app` lines now).

- [ ] **Step 3: Verify**

Run the staging suite, then confirm the acceptance greps for dev-machine identifiers and personal names return zero hits outside this narrative.
Expected: `OK` (65 tests), both greps exit 1 (zero hits).

---

### Task 2: Sundial rebrand

**Files (staging only):**
- Rename: `bin/wallclock` → `bin/sundial`
- Modify: `bin/sundial`, `watcher/watcher.py`, `watcher/notifier.applescript.tmpl`, `setup.sh`, `SETUP.md`, `README.md`, `tests/test_wallclock.py`, `cli/*.py` (only if they print the old name)

**Interfaces:**
- Produces: `bin/sundial {now|remember|due|done|ask|answered}` working; watcher notifies with title `"Sundial"` via `Sundial.app` (osascript fallback unchanged); setup.sh generates `com.sundial.watcher` plist and compiles `watcher/Sundial.app`; zero occurrences of `wallclock`/`Wall Clock` outside docs history narrative — check with `grep -ri "wall.clock" --exclude-dir=.git --exclude-dir=docs`.

- [ ] **Step 1:** `git mv`-less rename (`mv bin/wallclock bin/sundial`), update its usage string and internal name.
- [ ] **Step 2:** `watcher/watcher.py`: `NOTIFIER_APP` → `Path(__file__).resolve().parent / "Sundial.app"`; every `desktop_notify("Wall Clock", ...)` → `desktop_notify("Sundial", ...)`; `--test` message reworded to `"Sundial watcher test. If you see this, the channel works and nothing left your machine."`
- [ ] **Step 3:** `setup.sh`: label `com.sundial.watcher`, plist filename `com.sundial.watcher.plist`, applet output `watcher/Sundial.app`, all user-facing echo text says Sundial; `SETUP.md` + `README.md` renamed accordingly (README gets fully rewritten by the controller afterward — only fix names here).
- [ ] **Step 4:** tests: update any assertion on the notification title or applet path; suite green (65).

```bash
cd ~/Desktop/sundial-staging && python3 tests/test_wallclock.py 2>&1 | tail -2 && bash -n setup.sh && echo ok \
  && grep -ri "wall.clock" --exclude-dir=.git --exclude-dir=docs . ; echo "grep exit $? (want 1)"
```

---

### Task 3: Demo assets

**Files (staging only):**
- Create: `demo/demo.tape` (VHS script), `demo/RECORDING.md` (manual fallback checklist)

**Interfaces:**
- Produces: a VHS tape that, when run with `vhs demo/demo.tape` on a machine with vhs installed, records: `./bin/sundial ask "should the header be sticky?"` → shows `sundial due` → narrates (Sleep + typed comments) the 10/20/50 ladder and return-nudge → `./bin/sundial answered`. RECORDING.md: numbered manual steps to capture the same flow plus one macOS notification screenshot (System will show sender "Sundial"), noting the absence-clock twist to demonstrate (popup holds while a CLI app is frontmost).

- [ ] Write both files; validate the tape's syntax by inspection (vhs not assumed installed); suite untouched.

---

### Task 4: Fresh init + pre-push audit gate

**Files (staging only):**
- Create: `LICENSE` (MIT, `Copyright (c) 2026 J. Servo LLC`), `.git/` via fresh init

**Interfaces:**
- Produces: one commit `Sundial v1.0 — a sense of time for AI agents` authored `Sundial <sundial@users.noreply.github.com>` on branch `main`; audit report at `~/Desktop/AI-WallClock-Project/.superpowers/sdd/prepush-audit.md`.

- [ ] **Step 1:** Write LICENSE; `cd ~/Desktop/sundial-staging && git init -b main && git add -A && git -c user.name="Sundial" -c user.email="sundial@users.noreply.github.com" commit -m "Sundial v1.0 — a sense of time for AI agents"`.
- [ ] **Step 2 (controller dispatches CLEAN-EYES audit agent — not any agent that performed Tasks 1-3):** full privacy re-scan of the staging tree AND its single commit (names, emails, paths, project references, secrets), suite run, `bash -n setup.sh`, `git log --format='%an %ae'` check. Verdict PASS required.
- [ ] **Step 3:** STOP. Output for the owner: `cd ~/Desktop/sundial-staging && git remote add origin https://github.com/bigjoe-oti/sundial.git && git push -u origin main` (after his `gh auth login`).

---

## Controller-authored (not subagent tasks)
- `README.md` full rewrite (the flag): tagline, thesis, ladder diagram, quickstart, honesty rails, prior-art section, roadmap.
- `docs/escalation-then-autonomy.md` (the essay).
Both written by the controller directly into staging between Tasks 2 and 4, then included in Task 4's commit and audited in Step 2.
