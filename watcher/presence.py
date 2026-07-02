#!/usr/bin/env python3
"""Sundial presence sensing — zero-dep, privacy-bounded.

Reads two macOS built-ins: HIDIdleTime (seconds since last keyboard/mouse
input) via ioreg, and the frontmost application NAME via lsappinfo. Nothing
else: no window titles, no input content, nothing leaves the machine.

States: "here" (input recent AND a CLI app is frontmost — the human can see
the chat), "elsewhere" (input recent, other app frontmost — busy, hasn't
seen the chat), "away" (no input for PRESENCE_IDLE_S), "present" (input
recent but frontmost unknown — 2-state degrade), None (idle unknown — full
degrade; callers must fall back to plain elapsed-time semantics)."""

import re
import subprocess
from pathlib import Path

IOREG = "/usr/sbin/ioreg"
LSAPPINFO = "/usr/bin/lsappinfo"
PRESENCE_IDLE_S = 180

DEFAULT_CLI_APPS = (
    "Terminal", "iTerm2", "Ghostty", "Warp", "Alacritty", "kitty",
    "Visual Studio Code", "Code", "Cursor",
)

_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
_FRONT_RE = re.compile(r'"LSDisplayName"\s*=\s*"([^"]+)"')


def parse_idle(ioreg_text: str):
    m = _IDLE_RE.search(ioreg_text or "")
    return int(m.group(1)) / 1_000_000_000 if m else None


def parse_front(lsappinfo_text: str):
    m = _FRONT_RE.search(lsappinfo_text or "")
    return m.group(1) if m else None


def _run(cmd):
    try:
        r = subprocess.run(cmd, timeout=5, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def idle_seconds():
    return parse_idle(_run([IOREG, "-c", "IOHIDSystem", "-d", "4"]))


def front_app():
    asn_line = _run([LSAPPINFO, "front"]).strip()
    if not asn_line:
        return None
    return parse_front(_run([LSAPPINFO, "info", "-only", "name", asn_line]))


def cli_apps(data_dir) -> tuple:
    extra = ()
    try:
        raw = (Path(data_dir) / "cli_apps.txt").read_text(encoding="utf-8")
        extra = tuple(line.strip() for line in raw.splitlines() if line.strip())
    except OSError:
        pass
    return DEFAULT_CLI_APPS + extra


def derive_state(idle, front, cli) -> "str | None":
    if idle is None:
        return None
    if idle >= PRESENCE_IDLE_S:
        return "away"
    if front is None:
        return "present"
    return "here" if front in cli else "elsewhere"
