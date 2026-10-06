"""Claude Code PreToolUse hook: forward each tool call to the event daemon.

This runs as the agent user, inside the agent's process tree, so it is treated
as untrusted plumbing. It holds no secrets and makes no judgments: it records
the event via the daemon and relays the watcher's answer, if any.

Failure handling (ADR 0003): if anything goes wrong — bad input, daemon down,
timeout — the hook exits 0 with no output, which tells Claude Code to carry on
with its normal permission flow (the baseline). The failure doesn't go
unnoticed: the health check alerts when the event daemon is unreachable.

The hook never emits "allow" (ADR 0002).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TextIO

from . import protocol
from .watcher import GateDecision

# Must stay below the hook "timeout" in config/managed-settings.json and above
# config.MAX_WATCHER_TIMEOUT_SECONDS. tests/test_timeouts.py checks this.
SOCKET_TIMEOUT_SECONDS = 8.0

MAX_STDIN_BYTES = 8 * 1024 * 1024
MAX_STRING_CHARS = 8 * 1024
MAX_LIST_ITEMS = 100
MAX_DEPTH = 8

PAYLOAD_FIELDS = ("session_id", "tool_use_id", "tool_name", "tool_input", "cwd", "permission_mode")


def run(stdin: TextIO, stdout: TextIO, socket_path: Path) -> int:
    """Entry point. Always returns 0; see module docstring."""
    try:
        event = json.loads(stdin.read(MAX_STDIN_BYTES))
    except (ValueError, OSError):
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        return 0

    try:
        response = protocol.request(
            socket_path,
            {"type": protocol.RequestType.TOOL_EVENT, "payload": build_payload(event)},
            timeout=SOCKET_TIMEOUT_SECONDS,
        )
    except (OSError, protocol.ProtocolError):
        return 0

    output = hook_output(response)
    if output is not None:
        stdout.write(json.dumps(output))
        stdout.flush()
    return 0


def build_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields worth recording and bound their size."""
    return {key: _bounded(event[key]) for key in PAYLOAD_FIELDS if key in event}


def hook_output(response: dict[str, Any]) -> dict[str, Any] | None:
    """Translate the daemon's answer into Claude Code's hook output format.

    Only ask and deny produce output. Anything else — defer, an unknown value,
    or a hypothetical "allow" — produces nothing, so the baseline decides.
    """
    try:
        decision = GateDecision(response.get("decision"))
    except ValueError:
        return None
    if decision is GateDecision.DEFER:
        return None
    reason = str(response.get("reason") or "Flagged by the MacAgent watcher.")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.value,
            "permissionDecisionReason": reason,
        }
    }


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_DEPTH:
        return "[truncated: nested too deeply]"
    if isinstance(value, str):
        if len(value) <= MAX_STRING_CHARS:
            return value
        digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
        return f"{value[:MAX_STRING_CHARS]}…[truncated {len(value)} chars total, sha256:{digest}]"
    if isinstance(value, dict):
        return {str(k): _bounded(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        items = [_bounded(v, depth + 1) for v in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"[truncated: {len(value) - MAX_LIST_ITEMS} more items]")
        return items
    return value
