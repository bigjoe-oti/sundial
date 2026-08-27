"""Sundial v3 portable package.

Namespace shell for platform backends and agent adapters, re-exporting the
hardened core engine from lib/core.py for unified module & package access.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from lib.core import (  # noqa: E402
    BIRTH,
    COMMITMENTS,
    DATA,
    DEFAULT_TZ,
    LEDGER,
    MEMORY_DIR,
    PROJECT_ROOT,
    WEIGHTS,
    WORK_END,
    WORK_START,
    _attach_estimate,
    _close_estimate,
    _finalize_row,
    _ledger_lock,
    _menubar_spawn,
    _path,
    _save_ledger,
    add_commitment,
    append_session_speak,
    best_effort_tokens,
    close_awaiting,
    close_awaiting_detailed,
    consume_session_speak,
    due_commitments,
    get_or_create_birth,
    humanize_age,
    humanize_delta,
    load_commitments,
    load_ledger,
    now_local,
    now_utc,
    parse_due,
    parse_iso,
    read_json,
    refresh_menubar,
    resolve_commitment,
    session_claim_fresh,
    session_speak_pending,
    snooze_active,
    start_session,
    tzinfo,
    write_json,
    write_session_claim,
)

__version__ = "3.1.0"

__all__ = [
    "DATA",
    "PROJECT_ROOT",
    "COMMITMENTS",
    "LEDGER",
    "BIRTH",
    "WEIGHTS",
    "MEMORY_DIR",
    "DEFAULT_TZ",
    "WORK_START",
    "WORK_END",
    "now_utc",
    "tzinfo",
    "now_local",
    "parse_iso",
    "parse_due",
    "read_json",
    "write_json",
    "_path",
    "_ledger_lock",
    "get_or_create_birth",
    "humanize_age",
    "humanize_delta",
    "load_commitments",
    "add_commitment",
    "_attach_estimate",
    "resolve_commitment",
    "_close_estimate",
    "close_awaiting_detailed",
    "close_awaiting",
    "snooze_active",
    "write_session_claim",
    "session_claim_fresh",
    "append_session_speak",
    "consume_session_speak",
    "session_speak_pending",
    "due_commitments",
    "_menubar_spawn",
    "refresh_menubar",
    "load_ledger",
    "_save_ledger",
    "start_session",
    "_finalize_row",
    "best_effort_tokens",
    "__version__",
]
