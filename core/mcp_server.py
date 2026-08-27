#!/usr/bin/env python3
"""Sundial — Zero-Dependency Universal MCP (Model Context Protocol) Server.

Provides a pure Python stdlib JSON-RPC 2.0 stdio server for universal agent harness
support (Antigravity, Cursor, Claude Code, Windsurf, OpenCode).

Zero external dependencies: no mcp or fastmcp pip packages required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure lib directory is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
if str(REPO_ROOT / "cli") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "cli"))

import core  # noqa: E402
import estimator  # noqa: E402
import status as status_cli  # noqa: E402
import tzutil  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sundial"
SERVER_VERSION = "3.1.0"

# --------------------------------------------------------------------------- #
# Tool Definitions (JSON Schema)
# --------------------------------------------------------------------------- #

TOOLS = [
    {
        "name": "sundial_now",
        "description": "Query local time, agent age, working hours, and due/overdue commitments.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "sundial_status",
        "description": "Get read-only snapshot of presence, open asks, P90 risk, snooze state, and session queue.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "sundial_ask",
        "description": "Arm an awaiting-reply question when blocked on the human with urgency ladder and autonomy fallback.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The blocking question, summarized.",
                },
                "due": {
                    "type": "string",
                    "description": "+NNm/+NNh, YYYY-MM-DD, or ISO datetime (default '+10m').",
                    "default": "+10m",
                },
                "weight": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "Urgency tier (low, normal, high). Default: normal.",
                    "default": "normal",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "0..1 sureness in the default action if unanswered.",
                },
                "irreversible": {
                    "type": "boolean",
                    "description": "Destructive/one-way; never auto-proceeds on silence.",
                    "default": False,
                },
                "default_action": {
                    "type": "string",
                    "description": "Action taken if user never answers (stated in final rung).",
                },
                "on_proceed": {
                    "type": "string",
                    "description": "Shell command to run if autonomy gate returns proceed.",
                },
                "on_stand_down": {
                    "type": "string",
                    "description": "Shell command to run if autonomy gate returns stand_down.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sundial_remember",
        "description": "Record a ripening promise/commitment with self-calibrated duration estimation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Description of the commitment/task.",
                },
                "due": {
                    "type": "string",
                    "description": "+NNm/+NNh, YYYY-MM-DD, or ISO datetime.",
                },
                "est": {
                    "type": "string",
                    "description": "Raw duration estimate (e.g. '30m', '1h', '1800s').",
                },
                "bucket": {
                    "type": "string",
                    "description": "Task bucket/category (e.g. 'build', 'review', 'research').",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sundial_done",
        "description": "Mark an open commitment as done and close actual/estimate execution pair into habits.jsonl.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The 8-character commitment ID.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sundial_estimate",
        "description": "Calibrate a raw duration guess into empirical P50/P90 durations from historical habits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task description.",
                },
                "raw": {
                    "type": "string",
                    "description": "Raw duration guess, e.g. '30m', '1h', '1800s'.",
                },
                "bucket": {
                    "type": "string",
                    "description": "Optional task category bucket.",
                },
            },
            "required": ["task", "raw"],
            "additionalProperties": False,
        },
    },
]


# --------------------------------------------------------------------------- #
# Tool Handlers
# --------------------------------------------------------------------------- #

def handle_sundial_now(_args: dict[str, Any]) -> str:
    local = core.now_local()
    birth = core.get_or_create_birth()
    born = core.parse_iso(birth.get("created_at"))
    born_local = born.astimezone(core.tzinfo()) if born else None
    working = tzutil.is_working_hours(local.hour, core.WORK_START, core.WORK_END)
    due = core.due_commitments()

    lines = [
        f"{local:%A %d %B %Y, %I:%M %p} ({local.tzname()}, {core.DEFAULT_TZ})",
        f"age: {core.humanize_age(birth.get('created_at', ''))}"
        + (f" (born {born_local:%d %b %Y})" if born_local else ""),
        f"working hours: {'yes' if working else 'no'}",
        f"commitments due/overdue: {len(due)}",
    ]
    for c, delta in due:
        tag = "OVERDUE" if delta < 0 else "due"
        lines.append(f"  - [{tag}] {c['text']}")
    return "\n".join(lines)


def handle_sundial_status(_args: dict[str, Any]) -> str:
    status_data = status_cli.build_status(core.DATA)
    return json.dumps(status_data, indent=2, ensure_ascii=False)


def handle_sundial_ask(args: dict[str, Any]) -> str:
    text = args["text"]
    due = args.get("due", "+10m")
    weight = args.get("weight", "normal")
    confidence = args.get("confidence")
    irreversible = bool(args.get("irreversible", False))
    default_action = args.get("default_action")
    on_proceed = args.get("on_proceed")
    on_stand_down = args.get("on_stand_down")

    if confidence is not None:
        try:
            confidence = float(confidence)
            if not (0.0 <= confidence <= 1.0):
                return "Error: confidence must be between 0.0 and 1.0"
        except (ValueError, TypeError):
            return "Error: invalid confidence value"

    rec = core.add_commitment(
        text=text,
        due_str=due,
        source="mcp",
        kind="awaiting-reply",
        weight=weight,
        confidence=confidence,
        irreversible=irreversible,
        default_action=default_action,
        on_proceed=on_proceed,
        on_stand_down=on_stand_down,
    )
    due_dt = core.parse_iso(rec.get("due_at"))
    when = (due_dt.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due_dt else "no due date")
    tier = rec.get("weight", "normal")
    core.refresh_menubar()
    return f"armed [{rec['id']}] ({tier}) {rec['text']} (rung 1 due: {when})"


def handle_sundial_remember(args: dict[str, Any]) -> str:
    text = args["text"]
    due = args.get("due")
    est = args.get("est")
    bucket = args.get("bucket")

    rec = core.add_commitment(
        text=text,
        due_str=due,
        source="mcp",
        kind="plain",
        est_str=est,
        bucket=bucket,
    )
    due_dt = core.parse_iso(rec.get("due_at"))
    when = (due_dt.astimezone(core.tzinfo()).strftime("%d %b %Y %H:%M")
            if due_dt else "no due date")
    est_info = ""
    if rec.get("est"):
        p90 = rec["est"].get("p90_s")
        if p90:
            est_info = f" (P90: ~{core.humanize_delta(p90)})"
    core.refresh_menubar()
    return f"remembered [{rec['id']}] {rec['text']} (due: {when}){est_info}"


def handle_sundial_done(args: dict[str, Any]) -> str:
    cid = args["id"]
    res = core.resolve_commitment(cid, "done")
    core.refresh_menubar()
    if res:
        return f"resolved [{cid}] as done: {res.get('text')}"
    return f"Error: commitment [{cid}] not found"


def handle_sundial_estimate(args: dict[str, Any]) -> str:
    task = args["task"]
    raw = args["raw"]
    bucket = args.get("bucket")

    raw_s = estimator.parse_duration(raw)
    if raw_s is None:
        return f"Error: invalid duration '{raw}'. Use format like '30m', '1h', '1800s'."

    t = estimator.estimate_timeline(raw_s, core.DATA, bucket=bucket)
    ex = t["execution"]
    rv = t["review"]
    lines = [
        f'Estimate — "{task}" (raw {core.humanize_delta(raw_s)}):',
        f'  Execution: P50 ~{core.humanize_delta(ex["p50_s"]) if ex["p50_s"] is not None else "?"} | '
        f'P90 ~{core.humanize_delta(ex["p90_s"]) if ex["p90_s"] is not None else "?"} '
        f'(n={ex["n"]}, {ex["confidence"]} confidence)',
    ]
    if rv["confidence"] != "none":
        lines.append(
            f'  Review:    P50 ~{core.humanize_delta(rv["p50_s"]) if rv["p50_s"] is not None else "?"} | '
            f'P90 ~{core.humanize_delta(rv["p90_s"]) if rv["p90_s"] is not None else "?"}'
        )
    lines.append(
        f'  End-to-End: P50 ~{core.humanize_delta(t["end_to_end_p50_s"])} | '
        f'P90 ~{core.humanize_delta(t["end_to_end_p90_s"])}'
    )
    return "\n".join(lines)


TOOL_DISPATCH = {
    "sundial_now": handle_sundial_now,
    "sundial_status": handle_sundial_status,
    "sundial_ask": handle_sundial_ask,
    "sundial_remember": handle_sundial_remember,
    "sundial_done": handle_sundial_done,
    "sundial_estimate": handle_sundial_estimate,
}


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 Dispatcher
# --------------------------------------------------------------------------- #

def handle_rpc_request(req: dict[str, Any]) -> dict[str, Any] | None:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # Notification (no id) handling
    if req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS,
            },
        }

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        handler = TOOL_DISPATCH.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }
        try:
            output = handler(args)
            is_err = output.startswith("Error:")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output}],
                    "isError": is_err,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}],
                    "isError": True,
                },
            }

    # Method not found
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


# --------------------------------------------------------------------------- #
# Transport Loop (Supports both Content-Length headers and Line-Delimited)
# --------------------------------------------------------------------------- #

def send_response(resp: dict[str, Any]) -> None:
    body = json.dumps(resp, ensure_ascii=False)
    # Output standard newline-delimited JSON
    sys.stdout.write(body + "\n")
    sys.stdout.flush()


def run_stdio_server() -> None:
    """Read JSON-RPC messages from stdin and write responses to stdout."""
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF

        line_str = line.strip()
        if not line_str:
            continue

        # Check if line contains Content-Length header
        if line_str.lower().startswith("content-length:"):
            try:
                length = int(line_str.split(":", 1)[1].strip())
                # Read until empty line separator
                while True:
                    hdr = sys.stdin.readline()
                    if hdr.strip() == "":
                        break
                content = sys.stdin.read(length)
                req = json.loads(content)
            except Exception:
                continue
        else:
            # Standard newline-delimited JSON
            try:
                req = json.loads(line_str)
            except ValueError:
                continue

        if isinstance(req, dict):
            resp = handle_rpc_request(req)
            if resp is not None:
                send_response(resp)


if __name__ == "__main__":
    try:
        run_stdio_server()
    except (KeyboardInterrupt, BrokenPipeError):
        sys.exit(0)
