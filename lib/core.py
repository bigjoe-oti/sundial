"""Sundial — core engine.

Shared by the CLI verbs (now/remember/due) and the session hooks. Owns:
  - paths + atomic JSON IO
  - birth.json (the one timestamp) + age
  - commitments.json (ripening promises)
  - session-ledger.json (the dual-clock log)
  - best-effort session token totals from the transcript

Local-first: everything is plain JSON on disk under ../data. No network, no deps.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA = PROJECT_ROOT / "data"

COMMITMENTS = DATA / "commitments.json"
LEDGER = DATA / "session-ledger.json"
BIRTH = DATA / "birth.json"
WEIGHTS = DATA / "memory-weights.json"

# Where your agent's long-term memory lives (for decay scoring). Set
# SUNDIAL_MEMORY_DIR to your harness's memory dir (Claude Code:
# ~/.claude/projects/<project-slug>/memory).
MEMORY_DIR = Path(os.environ.get("SUNDIAL_MEMORY_DIR", str(Path.home() / ".claude" / "memory")))

# Local timezone for quiet hours and display. Override with SUNDIAL_TZ.
DEFAULT_TZ = os.environ.get("SUNDIAL_TZ", "UTC")

# Quiet window (local hours) used only for the "working hours" flag in v1.
WORK_START, WORK_END = 9, 18


# --------------------------------------------------------------------------- #
# Time primitives (clock-on-glance: deterministic, never a model)
# --------------------------------------------------------------------------- #
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def tzinfo(tz_name: str = DEFAULT_TZ):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone.utc


def now_local(tz_name: str = DEFAULT_TZ) -> datetime:
    return now_utc().astimezone(tzinfo(tz_name))


def parse_iso(s: str | None):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


# --------------------------------------------------------------------------- #
# Atomic JSON IO
# --------------------------------------------------------------------------- #
def read_json(path: Path, default):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return default
    try:
        return json.loads(text)
    except ValueError:
        # Corrupt but present: quarantine the bad bytes beside the original
        # (best effort) rather than silently discarding them, then degrade
        # to default like any other unreadable file.
        #
        # TOCTOU guard: a locked writer may have replaced the path with GOOD
        # bytes between our (unlocked) read and this point -- renaming now
        # would quarantine a healthy file. Re-read and only quarantine what
        # is STILL corrupt; if it healed underneath us, leave it alone.
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return default
        try:
            json.loads(current)
            return default  # healed by a concurrent writer: don't touch it
        except ValueError:
            pass
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = path.with_name(path.name + f".corrupt-{stamp}")
        try:
            path.rename(quarantine)
        except OSError:
            pass
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique-per-call tmp name (vs. a shared "<name>.tmp") means concurrent
    # writers to the same target never share a tmp file, so one writer's
    # in-progress bytes can never be clobbered by another's before either
    # replace happens. fsync forces the bytes to disk before the atomic
    # rename, so a crash can never leave `path` pointing at a half-written
    # tmp.
    fh = tempfile.NamedTemporaryFile(
        mode="w", dir=str(path.parent), delete=False, suffix=".tmp",
        encoding="utf-8")
    tmp = Path(fh.name)
    try:
        with fh:
            fh.write(json.dumps(obj, indent=2, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@contextmanager
def _ledger_lock():
    """Serialize load->mutate->write critical sections across processes and
    threads via an flock on data/.lock. DATA is rebound by tests, so it's
    computed here at call time (not module-import time)."""
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# --------------------------------------------------------------------------- #
# Birth + age
# --------------------------------------------------------------------------- #
def get_or_create_birth() -> dict:
    b = read_json(BIRTH, None)
    if not isinstance(b, dict) or "created_at" not in b:
        b = {"created_at": now_utc().isoformat()}
        write_json(BIRTH, b)
    return b


def humanize_age(created_at_iso: str) -> str:
    born = parse_iso(created_at_iso)
    if born is None:
        return "age unknown"
    secs = int((now_utc() - born).total_seconds())
    if secs < 0:
        return "born today"
    days = secs // 86400
    if days <= 0:
        return "born today"
    years, rem = divmod(days, 365)
    months, d = divmod(rem, 30)
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}mo")
    if d or not parts:
        parts.append(f"{d}d")
    return " ".join(parts) + " old"


def humanize_delta(seconds: float) -> str:
    seconds = int(abs(seconds))
    if seconds < 60:
        return f"{seconds}s"
    mins, _ = divmod(seconds, 60)
    if mins < 60:
        return f"{mins}m"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {m}m" if m else f"{hours}h"
    days, h = divmod(hours, 24)
    return f"{days}d {h}h" if h else f"{days}d"


# --------------------------------------------------------------------------- #
# Commitments (ripening promises)
# --------------------------------------------------------------------------- #
def load_commitments() -> list:
    items = read_json(COMMITMENTS, [])
    return items if isinstance(items, list) else []


def parse_due(due_str: str | None):
    """Accept 'YYYY-MM-DD' (treated as end of that LOCAL day), a full ISO
    datetime, or a relative '+NNm' / '+NNh' offset from now.
    Return an aware UTC datetime, or None."""
    if not due_str:
        return None
    due_str = due_str.strip()
    m = re.fullmatch(r"\+(\d+)([mh])", due_str)
    if m:
        n = int(m.group(1))
        try:
            return now_utc() + timedelta(minutes=n if m.group(2) == "m" else n * 60)
        except OverflowError:
            return None
    try:
        if len(due_str) == 10:  # date only
            d = datetime.fromisoformat(due_str)
            d = d.replace(tzinfo=tzinfo(), hour=23, minute=59, second=0)
            return d.astimezone(timezone.utc)
        d = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=tzinfo())
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def add_commitment(text: str, due_str: str | None = None, source: str = "manual",
                   kind: str = "plain", session_id: str | None = None,
                   weight: str | None = None, confidence: float | None = None,
                   irreversible: bool = False,
                   default_action: str | None = None) -> dict:
    with _ledger_lock():
        items = load_commitments()
        due = parse_due(due_str)
        rec = {
            "id": uuid.uuid4().hex[:8],
            "created_at": now_utc().isoformat(),
            "due_at": due.isoformat() if due else None,
            "text": text,
            "source": source,
            "status": "open",
        }
        if kind != "plain":
            rec["kind"] = kind
        if session_id:
            rec["session_id"] = session_id
        if weight and weight != "normal":
            rec["weight"] = weight
        if confidence is not None:
            rec["confidence"] = confidence
        if irreversible:
            rec["irreversible"] = True
        if default_action:
            rec["default_action"] = default_action
        items.append(rec)
        write_json(COMMITMENTS, items)
        return rec


def resolve_commitment(commitment_id: str, status: str = "done") -> bool:
    with _ledger_lock():
        items = load_commitments()
        hit = False
        for c in items:
            if c.get("id") == commitment_id:
                c["status"] = status
                hit = True
        if hit:
            write_json(COMMITMENTS, items)
        return hit


def close_awaiting_detailed(status: str = "answered") -> list:
    """Close every open awaiting-reply commitment; return the closed records."""
    with _ledger_lock():
        items = load_commitments()
        closed = []
        for c in items:
            if c.get("kind") == "awaiting-reply" and c.get("status") == "open":
                c["status"] = status
                closed.append(dict(c))
        if closed:
            write_json(COMMITMENTS, items)
        return closed


def close_awaiting(status: str = "answered") -> int:
    """Close every open awaiting-reply commitment (any session). Returns count."""
    return len(close_awaiting_detailed(status))


def due_commitments(horizon_hours: int = 24) -> list:
    """Open commitments that are overdue or due within ``horizon_hours``.
    Returns list of (commitment, seconds_until_due) sorted soonest-first
    (overdue = negative)."""
    now = now_utc()
    out = []
    for c in load_commitments():
        if c.get("status") != "open":
            continue
        due = parse_iso(c.get("due_at"))
        if due is None:
            continue
        delta = (due - now).total_seconds()
        if delta <= horizon_hours * 3600:
            out.append((c, delta))
    out.sort(key=lambda x: x[1])
    return out


# --------------------------------------------------------------------------- #
# Session ledger (the dual-clock log)
# --------------------------------------------------------------------------- #
def load_ledger() -> list:
    rows = read_json(LEDGER, [])
    return rows if isinstance(rows, list) else []


def _save_ledger(rows) -> None:
    write_json(LEDGER, rows)


def start_session(session_id: str, source: str = "startup", transcript_path: str | None = None):
    """Open (idempotent per session_id) a ledger row for this session, and
    lazily finalize the PREVIOUS session's row (tokens + end_ts) by reading its
    transcript at most once. This is why there is no per-turn Stop hook: each
    session is closed out cheaply at the next boot instead of taxing every turn.
    Returns (row, previous_row, created_bool)."""
    with _ledger_lock():
        rows = load_ledger()
        existing = None
        previous = None
        for r in rows:
            if r.get("session_id") == session_id:
                existing = r
            else:
                previous = r  # append order -> last non-current is the previous one
        dirty = False
        if previous is not None and previous.get("tokens") is None and previous.get("transcript_path"):
            _finalize_row(previous)
            dirty = True
        created = False
        if existing is None:
            existing = {
                "session_id": session_id,
                "source": source,
                "start_ts": now_utc().isoformat(),
                "end_ts": None,
                "wall_ms": None,
                "tokens": None,
                "transcript_path": transcript_path,
            }
            rows.append(existing)
            created = True
            dirty = True
        elif transcript_path and not existing.get("transcript_path"):
            existing["transcript_path"] = transcript_path
            dirty = True
        if dirty:
            _save_ledger(rows)
        return existing, previous, created


def _finalize_row(row) -> None:
    """Fill a row's token total, plus its end_ts/wall_ms if nothing set them,
    by reading its transcript at most once. Mutates the row in place."""
    row["tokens"] = best_effort_tokens(row.get("transcript_path"))
    if not row.get("end_ts"):
        end = None
        tp = row.get("transcript_path")
        if tp:
            try:
                end = datetime.fromtimestamp(Path(tp).stat().st_mtime, tz=timezone.utc)
            except OSError:
                end = None
        end = end or now_utc()
        row["end_ts"] = end.isoformat()
        start = parse_iso(row.get("start_ts"))
        row["wall_ms"] = int((end - start).total_seconds() * 1000) if start else None


def best_effort_tokens(transcript_path: str | None):
    """Sum the OUTPUT tokens this agent generated this session, read from the
    transcript JSONL. Output tokens are the clean, non-double-counted half of
    the dual clock (input/context tokens are deferred). Returns int, or None if
    the transcript is unavailable or unparseable."""
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    total = 0
    found = False
    try:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                usage = (obj.get("message") or {}).get("usage") if isinstance(obj, dict) else None
                if isinstance(usage, dict) and "output_tokens" in usage:
                    try:
                        total += int(usage["output_tokens"])
                        found = True
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return None
    return total if found else None
