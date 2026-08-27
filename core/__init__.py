"""Sundial v3 portable package.

Namespace shell for platform backends and agent adapters. The hardened
engine remains in lib/core.py (see the v3 plan's Phase 0 scope statement
for why it does not physically migrate).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

try:
    import importlib.util as _util
    _spec = _util.spec_from_file_location("lib_core_engine", _LIB / "core.py")
    if _spec and _spec.loader:
        _mod = _util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        for _k, _v in _mod.__dict__.items():
            if not _k.startswith("__"):
                globals()[_k] = _v
except Exception:
    pass

if TYPE_CHECKING:
    from datetime import datetime

    DATA: Path
    PROJECT_ROOT: Path
    COMMITMENTS: Path
    LEDGER: Path
    BIRTH: Path
    WEIGHTS: Path
    DEFAULT_TZ: str
    WORK_START: int
    WORK_END: int

    def now_utc() -> datetime: ...
    def now_local(tz_name: str = ...) -> datetime: ...
    def tzinfo(tz_name: str = ...) -> Any: ...
    def parse_iso(s: str | None) -> datetime | None: ...
    def parse_due(due_str: str | None) -> datetime | None: ...
    def read_json(path: Path, default: Any) -> Any: ...
    def write_json(path: Path, obj: Any) -> None: ...
    def get_or_create_birth() -> dict[str, Any]: ...
    def humanize_age(created_at_iso: str) -> str: ...
    def humanize_delta(seconds: float) -> str: ...
    def load_commitments() -> list[dict[str, Any]]: ...
    def add_commitment(
        text: str,
        due_str: str | None = None,
        source: str = "manual",
        kind: str = "plain",
        session_id: str | None = None,
        weight: str | None = None,
        confidence: float | None = None,
        irreversible: bool = False,
        default_action: str | None = None,
        rungs: list[str] | None = None,
        est_str: str | None = None,
        bucket: str | None = None,
        on_proceed: str | None = None,
        on_stand_down: str | None = None,
    ) -> dict[str, Any]: ...
    def resolve_commitment(
        commitment_id: str, status: str = "done"
    ) -> dict[str, Any] | None: ...
    def close_awaiting(status: str = "answered") -> int: ...
    def close_awaiting_detailed(status: str = "answered") -> list[dict[str, Any]]: ...
    def snooze_active(now: datetime, data_dir: str | Path | None = None) -> bool: ...
    def session_claim_fresh(
        now: datetime, data_dir: str | Path | None = None
    ) -> bool: ...
    def write_session_claim(
        data_dir: str | Path | None = None,
        ttl_s: float = 3600,
        session: str = "cli",
    ) -> None: ...
    def append_session_speak(
        entry: dict[str, Any], data_dir: str | Path | None = None
    ) -> int: ...
    def consume_session_speak(
        cids: list[str], data_dir: str | Path | None = None
    ) -> int: ...
    def session_speak_pending(
        data_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]: ...
    def due_commitments(
        horizon_hours: int = 24,
    ) -> list[tuple[dict[str, Any], float]]: ...
    def refresh_menubar() -> None: ...
    def load_ledger() -> list[dict[str, Any]]: ...
    def start_session(
        session_id: str,
        source: str = "startup",
        transcript_path: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]: ...

__version__ = "3.1.0"
