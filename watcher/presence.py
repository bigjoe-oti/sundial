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

import json
import re
import shutil
import subprocess
from pathlib import Path

IOREG = "/usr/sbin/ioreg"
LSAPPINFO = "/usr/bin/lsappinfo"
PRESENCE_IDLE_S = 180

VNSTAT_BIN = "/opt/homebrew/bin/vnstat"
VNSTAT_BIN_FALLBACK = "/usr/local/bin/vnstat"
NET_BUCKET_S = 300  # vnstat --json f "fiveminute" bucket width, in seconds

DEFAULT_CLI_APPS = (
    "Terminal", "iTerm2", "Ghostty", "Warp", "Alacritty", "kitty",
    "Visual Studio Code", "Code", "Cursor",
)

_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
_FRONT_RE = re.compile(r'"LSDisplayName"\s*=\s*"([^"]+)"')
# Real pmset per-pid lines carry display-sleep ALIASES (NoDisplaySleepAssertion,
# InternalPreventDisplaySleep) — the literal PreventUserIdleDisplaySleep only
# appears in the system-wide summary. Capture up to the solid "): [" delimiter
# so process names containing parens survive whole.
_DISPLAY_ASSERT_RE = re.compile(
    r"pid \d+\((.+?)\): \[.*?(?:PreventUserIdleDisplaySleep"
    r"|NoDisplaySleepAssertion|InternalPreventDisplaySleep)")

# One (proc, assertion-type, assertion-name) triple per per-pid pmset line.
# Paren-safe proc capture (same non-greedy trick as _DISPLAY_ASSERT_RE): the
# name field is what lets us tell a live WebRTC call ("WebRTC has active
# PeerConnections") apart from mere video playback ("Video Wake Lock").
_ASSERT_LINE_RE = re.compile(
    r'pid \d+\((.+?)\): \[[^\]]*\]\s+\S+\s+(\w+) named: "([^"]*)"')


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


def assertions_raw() -> str:
    return _run(["/usr/bin/pmset", "-g", "assertions"])


def asserting_display_procs(raw: str) -> set:
    return set(_DISPLAY_ASSERT_RE.findall(raw or ""))


def assertion_triples(raw: str) -> list:
    """Every (proc, assertion-type, assertion-name) triple in one pmset
    assertions dump, regardless of assertion type -- the WebRTC discriminator
    lives in the NAME field, not the type."""
    return [(proc, kind, name)
            for proc, kind, name in _ASSERT_LINE_RE.findall(raw or "")]


def derive_state(idle, front, cli) -> "str | None":
    if idle is None:
        return None
    if idle >= PRESENCE_IDLE_S:
        return "away"
    if front is None:
        return "present"
    return "here" if front in cli else "elsewhere"


def parse_locked(raw) -> "bool | None":
    try:
        import plistlib
        data = raw.encode() if isinstance(raw, str) else raw
        d = plistlib.loads(data)
        users = d.get("IOConsoleUsers", [])
        if not isinstance(users, list) or not users:
            return None
        return any(bool(u.get("CGSSessionScreenIsLocked")) for u in users
                   if isinstance(u, dict))
    except Exception:
        return None


def screen_locked() -> "bool | None":
    return parse_locked(_run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"]))


def net_rates(raw_json: str) -> "dict | None":
    """Parse `vnstat --json f` output. Picks the interface with the largest
    total traffic (rx+tx) across its last 2 "fiveminute" buckets -- dead
    tunnel interfaces (anpi/utun, all-zero) naturally lose to any interface
    carrying real traffic. Returns Bps averaged over those buckets (bucket
    bytes / 300s). None on any failure or empty/absent data."""
    try:
        data = json.loads(raw_json)
        interfaces = data.get("interfaces") if isinstance(data, dict) else None
        if not interfaces:
            return None
        best, best_total = None, -1.0
        for iface in interfaces:
            traffic = iface.get("traffic") or {}
            buckets = traffic.get("fiveminute") or []
            recent = buckets[-2:]
            if not recent:
                continue
            rx = sum(b.get("rx", 0) for b in recent)
            tx = sum(b.get("tx", 0) for b in recent)
            total = rx + tx
            if total > best_total:
                best_total = total
                n = len(recent)
                best = {"iface": iface.get("name"),
                        "rx_Bps": rx / (n * NET_BUCKET_S),
                        "tx_Bps": tx / (n * NET_BUCKET_S)}
        return best
    except Exception:
        return None


def _vnstat_bin() -> "str | None":
    for candidate in (VNSTAT_BIN, VNSTAT_BIN_FALLBACK):
        if Path(candidate).exists():
            return candidate
    return shutil.which("vnstat")


def net_sample() -> "dict | None":
    """One vnstat snapshot -> net_rates. None if vnstat isn't installed
    anywhere we look, or the read/parse fails."""
    vnstat_bin = _vnstat_bin()
    if vnstat_bin is None:
        return None
    return net_rates(_run([vnstat_bin, "--json", "f"]))
