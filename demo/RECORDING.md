# Recording the Sundial demo by hand

Two assets: a ~60s terminal recording and one notification screenshot.

## Terminal recording (if you don't have `vhs`)

1. Fresh install (`./setup.sh --name Demo --fresh`), notifications allowed.
2. Screen-record a terminal at a comfortable font size and walk through:
   - `./bin/sundial ask "should the header be sticky on mobile?"`
   - `./bin/sundial due`
   - Leave the machine untouched. Narrate (or caption) the ladder:
     10 min unseen → first popup + Tink chime; 20 → second, Glass;
     50 → final + Hero, "proceeding on my judgment or standing down."
   - Optional star of the show: sit at the machine in ANOTHER app for a
     few minutes first — the popup names the app and the ladder runs at
     half speed; then in a terminal — the ladder pauses entirely.
   - Return → the welcome-back nudge; then `./bin/sundial answered`.
3. Keep the total under 90 seconds in the edit.

## Notification screenshot

While a nudge is on screen, ⌘⇧4 + space + click the banner. Verify the
sender reads **Sundial** (that attribution is the compiled applet doing
its job). The rung-1 line with the app tease ("I can see Figma has
you...") makes the best single frame.

## Tips

- `--due +1m` on the ask shortens the wait for a recording session; the
  ladder rungs then land at ~1/11/41 unseen-minutes.
- `data/chime.txt` with `1.5` makes chimes read better on mic.
- To force a cycle instead of waiting for launchd:
  `python3 watcher/watcher.py --force`
