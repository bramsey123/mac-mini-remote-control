"""Append-only JSON Lines event log with size-based rotation.

The log is owned by the ``_agentwatch`` service account and is not writable by
the agent, so the agent cannot rewrite its own history. Writes are serialized
with a lock because the event daemon is multi-threaded.
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from .events import Event

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUPS = 10
LOG_MODE = 0o640  # owner _agentwatch, group admin: humans can read, the agent cannot


class EventLog:
    def __init__(self, path: Path, max_bytes: int = DEFAULT_MAX_BYTES, backups: int = DEFAULT_BACKUPS):
        if max_bytes <= 0 or backups < 1:
            raise ValueError("max_bytes must be positive and backups at least 1")
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()

    def append(self, event: Event) -> None:
        line = (json.dumps(event.to_dict(), separators=(",", ":"), default=str) + "\n").encode()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._size() + len(line) > self.max_bytes:
                self._rotate()
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, LOG_MODE)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0

    def _rotate(self) -> None:
        oldest = self._backup(self.backups)
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            src = self._backup(index)
            if src.exists():
                src.rename(self._backup(index + 1))
        if self.path.exists():
            self.path.rename(self._backup(1))

    def _backup(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")


def read_events(path: Path, last: int | None = None) -> Iterator[Event]:
    """Yield events from the current log file, optionally only the last N.

    Malformed lines are skipped rather than aborting the read: a viewer that
    stops at the first bad line would hide everything after it.
    """
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    with handle:
        lines: Iterator[str] = iter(deque(handle, maxlen=last)) if last else iter(handle)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                yield Event.from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
