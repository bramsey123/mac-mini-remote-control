"""The event schema: the common language every component speaks.

Every layer records what it sees as an :class:`Event` in one append-only log.
The health check, the notifier, the ``macagent events`` viewer and the AI
watcher (docs/watcher.md) all consume this stream. Keeping it stable is what
lets new consumers plug in without touching the producers.

Bump :data:`SCHEMA_VERSION` on any breaking change to an event type's ``data``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


class EventType:
    """Known event types. Consumers must tolerate types they don't recognize."""

    TOOL_PRE = "tool.pre"  # the agent is about to run a tool (from the hook)
    WATCHER_VERDICT = "watcher.verdict"  # the watcher's in-band answer, or its failure
    WATCHER_ALERT = "watcher.alert"  # the watcher flagged something out-of-band
    WATCHER_DROPPED = "watcher.dropped"  # observe queue overflowed; events skipped
    HEALTH_RESULT = "health.result"  # one health check's outcome changed
    EVENTD_START = "eventd.start"
    EVENTD_STOP = "eventd.stop"


class Source:
    HOOK = "hook"
    EVENTD = "eventd"
    HEALTHCHECK = "healthcheck"
    WATCHER = "watcher"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def format_ts(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    type: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=lambda: format_ts(utc_now()))
    v: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Event:
        missing = {"type", "source"} - raw.keys()
        if missing:
            raise ValueError(f"event is missing required keys: {sorted(missing)}")
        data = raw.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("event data must be an object")
        kwargs: dict[str, Any] = {"type": str(raw["type"]), "source": str(raw["source"]), "data": data}
        for key in ("id", "ts", "v"):
            if key in raw:
                kwargs[key] = raw[key]
        return cls(**kwargs)


def tool_event(payload: dict[str, Any], peer_uid: int | None) -> Event:
    """Build a ``tool.pre`` event from a hook payload (see hook.build_payload)."""
    return Event(
        type=EventType.TOOL_PRE,
        source=Source.HOOK,
        data={**payload, "peer_uid": peer_uid},
    )
