"""The AI watcher contract.

The watcher is a first-class component with a fixed slot in the event flow
(docs/watcher.md, ADR 0003). The system must work fully without one, so the
contract is shaped around three rules:

1. **No allow.** A watcher can defer, ask or deny — never allow. "Allow" from a
   Claude Code hook skips the built-in permission system, so a watcher that
   could allow would be a way to switch the baseline off (ADR 0002).
2. **Failure means baseline, loudly.** If a watcher errors or times out, the
   tool call falls back to the baseline (OS isolation + Claude Code auto mode)
   and the failure is recorded and surfaced. It never blocks the agent and
   never fails silently.
3. **Out of the agent's reach.** Watchers run inside the event daemon under
   the ``_agentwatch`` service account, so the agent cannot read their
   credentials, change their configuration or stop them.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..events import Event


class WatcherMode(enum.StrEnum):
    # Watcher never consulted.
    OFF = "off"
    # Watcher sees every event after it is recorded and may raise alerts.
    # It cannot affect tool calls.
    OBSERVE = "observe"
    # OBSERVE, plus the watcher is asked in-band before each tool call and may
    # answer ask or deny.
    GATE = "gate"


class GateDecision(enum.StrEnum):
    DEFER = "defer"  # no objection: Claude Code's own permission flow decides
    ASK = "ask"  # pause and ask the human
    DENY = "deny"  # block this tool call
    # Deliberately no ALLOW. See module docstring, rule 1.


@dataclass(frozen=True)
class GateVerdict:
    decision: GateDecision
    reason: str = ""

    @classmethod
    def defer(cls, reason: str = "") -> GateVerdict:
        return cls(GateDecision.DEFER, reason)


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    severity: Severity
    title: str
    detail: str = ""
    event_ids: tuple[str, ...] = field(default_factory=tuple)


class Watcher(ABC):
    """Base class for watcher backends.

    Implementations must be thread-safe: ``review`` is called concurrently from
    request threads, while ``observe`` is called from a single worker thread.
    Raising from either method is allowed; the event daemon treats it as a
    watcher failure (rule 2).
    """

    #: Short identifier recorded in events and shown by the health check.
    name: str = "unnamed"

    @abstractmethod
    def review(self, event: Event) -> GateVerdict:
        """In-band: judge a ``tool.pre`` event before the tool runs (GATE mode).

        Must return within the configured timeout; slower answers are
        discarded and the call proceeds on the baseline.
        """

    @abstractmethod
    def observe(self, event: Event) -> Sequence[Alert]:
        """Out-of-band: look at any recorded event (OBSERVE and GATE modes).

        The watcher may keep its own state across calls (for example, a
        rolling window of recent activity) to spot patterns no single event
        shows.
        """

    def close(self) -> None:  # noqa: B027 — optional hook; most backends need nothing
        """Release resources. Called once when the event daemon stops."""
