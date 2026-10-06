"""Wire protocol between the hook (client) and the event daemon (server).

One request per connection: the client sends a single JSON object terminated
by a newline and reads a single JSON object back. Messages are size-capped so
a misbehaving client cannot make the daemon buffer unbounded input.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
from pathlib import Path
from typing import Any, BinaryIO

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024


class RequestType:
    PING = "ping"
    TOOL_EVENT = "tool_event"
    # Record an arbitrary event. Only accepted from root or the daemon's own
    # account (checked by peer uid), so the agent cannot inject, say, fake
    # health results.
    RECORD = "record"


class ProtocolError(Exception):
    pass


def encode(message: dict[str, Any]) -> bytes:
    data = json.dumps(message, separators=(",", ":"), default=str).encode() + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"message is {len(data)} bytes; limit is {MAX_MESSAGE_BYTES}")
    return data


def read_message(stream: BinaryIO) -> dict[str, Any]:
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        raise ProtocolError("connection closed before a message arrived")
    if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
        raise ProtocolError("message too large or not newline-terminated")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    return message


def request(socket_path: Path, message: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Send one request and return the response. Raises OSError or ProtocolError."""
    payload = encode({"v": PROTOCOL_VERSION, **message})
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(payload)
        with sock.makefile("rb") as stream:
            return read_message(stream)


def peer_uid(sock: socket.socket) -> int | None:
    """Return the uid of the process on the other end of a Unix socket.

    Recorded on every event so forged events from the wrong account stand out.
    Returns None where the platform offers no way to ask.
    """
    try:
        if sys.platform == "darwin":
            # getsockopt(SOL_LOCAL=0, LOCAL_PEERCRED=1) returns a struct xucred:
            # u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]
            raw = sock.getsockopt(0, 1, struct.calcsize("@IIh16I"))
            return struct.unpack_from("@II", raw)[1]
        if hasattr(socket, "SO_PEERCRED"):
            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return struct.unpack("3i", raw)[1]
    except OSError:
        return None
    return None
