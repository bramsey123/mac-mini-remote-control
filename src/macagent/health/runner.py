"""Run all health checks, remember their state, and notify on changes.

Notifying on every run would bury real problems in repeats, so the runner only
speaks when something changes: a check gets worse, a check recovers, or a
failure is still unresolved after ``reminder_interval_hours``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import protocol
from ..events import Event, EventType, Source, format_ts
from ..notify import Notifier
from . import checks as _checks  # noqa: F401  (registers the checks)
from .base import REGISTRY, CheckResult, HealthContext, Status


def run_checks(ctx: HealthContext, only: Sequence[str] | None = None) -> list[CheckResult]:
    results = []
    for check_id, fn in REGISTRY:
        if only and check_id not in only:
            continue
        try:
            outcome = fn(ctx)
        except Exception as exc:  # one broken check must not hide the others
            results.append(CheckResult(check_id, Status.ERROR, "check crashed", repr(exc)))
            continue
        results.append(CheckResult(check_id, outcome.status, outcome.summary, outcome.detail))
    return results


@dataclass(frozen=True)
class Change:
    result: CheckResult
    kind: str  # "new-problem" | "worse" | "recovered" | "reminder"


def diff_state(
    previous: dict[str, Any], results: Sequence[CheckResult], now: dt.datetime, reminder: dt.timedelta
) -> tuple[list[Change], dict[str, Any]]:
    """Compare results with the previous state; return changes and the new state."""
    changes: list[Change] = []
    state: dict[str, Any] = {}
    stamp = format_ts(now)
    for result in results:
        prev = previous.get(result.check_id)
        entry = {
            "status": result.status.value,
            "summary": result.summary,
            "since": stamp,
            "notified_at": None,
        }
        if prev is None:
            if result.status.rank > 0:
                changes.append(Change(result, "new-problem"))
                entry["notified_at"] = stamp
        else:
            prev_status = Status(prev["status"])
            if prev_status == result.status:
                entry["since"] = prev.get("since", stamp)
                entry["notified_at"] = prev.get("notified_at")
                if result.status.rank >= Status.FAIL.rank and _due(prev.get("notified_at"), now, reminder):
                    changes.append(Change(result, "reminder"))
                    entry["notified_at"] = stamp
            elif result.status.rank > prev_status.rank:
                changes.append(Change(result, "worse"))
                entry["notified_at"] = stamp
            elif result.status.rank == 0 and prev_status.rank > 0:
                changes.append(Change(result, "recovered"))
            else:
                # Improved but still degraded (fail -> warn): note it, keep quiet.
                entry["notified_at"] = prev.get("notified_at")
        state[result.check_id] = entry
    return changes, state


def notify_changes(notifier: Notifier, changes: Sequence[Change]) -> None:
    if not changes:
        return
    problems = [c for c in changes if c.kind != "recovered"]
    worst = max((c.result.status.rank for c in problems), default=0)
    lines = []
    for change in changes:
        marker = {"recovered": "✅", "reminder": "⏰"}.get(change.kind, "❗")
        lines.append(f"{marker} {change.result.check_id}: {change.result.summary}")
    title = f"MacAgent: {len(problems)} health problem(s)" if problems else "MacAgent: health recovered"
    priority = "high" if worst >= Status.FAIL.rank else "default"
    notifier.send(title, "\n".join(lines), priority=priority, tags=("computer",))


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write atomically so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".health-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def record_changes(socket_path: Path, changes: Sequence[Change]) -> None:
    """Best effort: put health changes into the shared event stream."""
    for change in changes:
        event = Event(
            EventType.HEALTH_RESULT,
            Source.HEALTHCHECK,
            {
                "check": change.result.check_id,
                "status": change.result.status.value,
                "summary": change.result.summary,
                "change": change.kind,
            },
        )
        message = {"type": protocol.RequestType.RECORD, "event": event.to_dict()}
        try:
            protocol.request(socket_path, message, timeout=3.0)
        except (OSError, protocol.ProtocolError):
            return  # eventd is down; the eventd check already reports that


def run_and_notify(ctx: HealthContext, notifier: Notifier) -> tuple[list[CheckResult], list[Change]]:
    results = run_checks(ctx)
    previous = load_state(ctx.paths.health_state_file)
    reminder = dt.timedelta(hours=ctx.config.health.reminder_interval_hours)
    changes, state = diff_state(previous, results, ctx.now(), reminder)
    # If delivery raises NotifyError, state is deliberately not saved: the next
    # run sees the same changes and tries again, instead of recording an alert
    # nobody received.
    notify_changes(notifier, changes)
    save_state(ctx.paths.health_state_file, state)
    record_changes(ctx.paths.socket_path, changes)
    return results, changes


def _due(notified_at: str | None, now: dt.datetime, interval: dt.timedelta) -> bool:
    if not notified_at:
        return True
    try:
        last = dt.datetime.fromisoformat(notified_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - last >= interval
