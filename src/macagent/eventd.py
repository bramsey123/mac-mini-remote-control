"""The event daemon: records every event and hosts the AI watcher slot.

Runs as the ``_agentwatch`` service account (LaunchDaemon), outside the
agent's reach. It owns the event log and, when one is configured, the watcher.

``EventService`` holds all the logic and is transport-independent so it can be
tested directly; ``EventServer`` is a thin Unix-socket front end.
"""

from __future__ import annotations

import contextlib
import os
import queue
import signal
import socketserver
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, protocol
from .config import Config
from .eventlog import EventLog
from .events import Event, EventType, Source, tool_event
from .notify import NotifyError, RateLimitedNotifier, create_notifier
from .paths import Paths
from .watcher import Alert, GateDecision, GateVerdict, Severity, Watcher, WatcherMode, create_watcher

SOCKET_MODE = 0o666  # any local account may submit events; only _agentwatch can write the log
NOTIFY_REPEAT_SECONDS = 15 * 60
OBSERVE_QUEUE_SIZE = 1000
_STOP = object()

_SEVERITY_PRIORITY = {Severity.INFO: "default", Severity.WARNING: "high", Severity.CRITICAL: "urgent"}


@dataclass
class Stats:
    events: int = 0
    gate_ok: int = 0
    gate_timeout: int = 0
    gate_error: int = 0
    observe_errors: int = 0
    dropped: int = 0


class EventService:
    def __init__(
        self,
        log: EventLog,
        watcher: Watcher,
        mode: WatcherMode,
        timeout: float,
        notifier: RateLimitedNotifier,
        observe_queue_size: int = OBSERVE_QUEUE_SIZE,
        trusted_uids: frozenset[int] | None = None,
    ):
        self.log = log
        # Accounts allowed to use the RECORD request: root and ourselves.
        self.trusted_uids = trusted_uids if trusted_uids is not None else frozenset({0, os.getuid()})
        self.watcher = watcher
        self.mode = mode
        self.timeout = timeout
        self.notifier = notifier
        self.stats = Stats()
        self._stats_lock = threading.Lock()
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=observe_queue_size)
        self._observer: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="watcher-review")

    # Lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._append(
            Event(
                EventType.EVENTD_START,
                Source.EVENTD,
                {"version": __version__, "watcher": self.watcher.name, "mode": self.mode.value},
            )
        )
        if self.mode is not WatcherMode.OFF:
            self._observer = threading.Thread(target=self._observe_loop, name="watcher-observe", daemon=True)
            self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._queue.put(_STOP)
            self._observer.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=True)
        try:
            self.watcher.close()
        finally:
            self._append(Event(EventType.EVENTD_STOP, Source.EVENTD, {"stats": self.snapshot()}))

    # Requests ---------------------------------------------------------------

    def dispatch(self, message: dict[str, Any], peer_uid: int | None) -> dict[str, Any]:
        kind = message.get("type")
        if kind == protocol.RequestType.PING:
            return {
                "v": protocol.PROTOCOL_VERSION,
                "ok": True,
                "version": __version__,
                "watcher": self.status(),
            }
        if kind == protocol.RequestType.TOOL_EVENT:
            payload = message.get("payload")
            if not isinstance(payload, dict):
                return {"v": protocol.PROTOCOL_VERSION, "error": "payload must be an object"}
            verdict = self.handle_tool_event(payload, peer_uid)
            return {
                "v": protocol.PROTOCOL_VERSION,
                "decision": verdict.decision.value,
                "reason": verdict.reason,
            }
        if kind == protocol.RequestType.RECORD:
            if peer_uid is None or peer_uid not in self.trusted_uids:
                return {"v": protocol.PROTOCOL_VERSION, "error": "record is restricted to trusted accounts"}
            try:
                event = Event.from_dict(message.get("event") or {})
            except (ValueError, TypeError) as exc:
                return {"v": protocol.PROTOCOL_VERSION, "error": f"invalid event: {exc}"}
            if event.source == Source.HOOK or event.type == EventType.TOOL_PRE:
                return {"v": protocol.PROTOCOL_VERSION, "error": "tool events must use tool_event"}
            self.record(event)
            return {"v": protocol.PROTOCOL_VERSION, "ok": True}
        return {"v": protocol.PROTOCOL_VERSION, "error": f"unknown request type {kind!r}"}

    def handle_tool_event(self, payload: dict[str, Any], peer_uid: int | None) -> GateVerdict:
        event = tool_event(payload, peer_uid)
        self.record(event)
        if self.mode is not WatcherMode.GATE:
            return GateVerdict.defer()

        started = time.monotonic()
        verdict, outcome, error = self._review(event)
        self.record(
            Event(
                EventType.WATCHER_VERDICT,
                Source.WATCHER,
                {
                    "event_id": event.id,
                    "watcher": self.watcher.name,
                    "decision": verdict.decision.value,
                    "reason": verdict.reason,
                    "outcome": outcome,
                    "error": error,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                },
            )
        )
        if outcome != "ok":
            self._notify(
                "watcher-unavailable",
                "MacAgent: watcher unavailable",
                f"The {self.watcher.name} watcher {outcome} on a tool call; the agent continued on the "
                f"baseline (auto mode + account isolation). {error or ''}".strip(),
                "high",
            )
        return verdict

    def record(self, event: Event) -> None:
        self._append(event)
        # The watcher never observes its own output, which would let it feed on itself.
        if self.mode is not WatcherMode.OFF and event.source != Source.WATCHER:
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self._bump("dropped")

    def status(self) -> dict[str, Any]:
        return {"name": self.watcher.name, "mode": self.mode.value, "stats": self.snapshot()}

    def snapshot(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(vars(self.stats))

    # Internals --------------------------------------------------------------

    def _review(self, event: Event) -> tuple[GateVerdict, str, str | None]:
        future = self._executor.submit(self.watcher.review, event)
        try:
            verdict = future.result(timeout=self.timeout)
        except FutureTimeout:
            self._bump("gate_timeout")
            return GateVerdict.defer("watcher timed out"), "timed out", None
        except Exception as exc:  # a watcher bug must never break the agent
            self._bump("gate_error")
            return GateVerdict.defer("watcher failed"), "failed", repr(exc)
        if not isinstance(verdict, GateVerdict) or not isinstance(verdict.decision, GateDecision):
            self._bump("gate_error")
            return GateVerdict.defer("watcher returned an invalid verdict"), "failed", repr(verdict)
        self._bump("gate_ok")
        return verdict, "ok", None

    def _observe_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                alerts = list(self.watcher.observe(item))
            except Exception as exc:
                self._bump("observe_errors")
                self._notify("watcher-observe-error", "MacAgent: watcher error", repr(exc), "high")
                continue
            for alert in alerts:
                self._raise_alert(alert, item)

    def _raise_alert(self, alert: Alert, trigger: Event) -> None:
        if not isinstance(alert, Alert):
            self._bump("observe_errors")
            return
        self._append(
            Event(
                EventType.WATCHER_ALERT,
                Source.WATCHER,
                {
                    "watcher": self.watcher.name,
                    "severity": alert.severity.value,
                    "title": alert.title,
                    "detail": alert.detail,
                    "event_ids": list(alert.event_ids) or [trigger.id],
                },
            )
        )
        self._notify(
            f"alert:{alert.title}",
            f"MacAgent watcher: {alert.title}",
            alert.detail or alert.title,
            _SEVERITY_PRIORITY.get(alert.severity, "default"),
        )

    def _append(self, event: Event) -> None:
        self._bump("events")
        self.log.append(event)

    def _bump(self, counter: str) -> None:
        with self._stats_lock:
            setattr(self.stats, counter, getattr(self.stats, counter) + 1)

    def _notify(self, key: str, title: str, message: str, priority: str) -> None:
        # A notification outage must not take down event recording.
        with contextlib.suppress(NotifyError, ValueError):
            self.notifier.send_keyed(key, title, message, priority, ("rotating_light",))


class _Handler(socketserver.StreamRequestHandler):
    timeout = 10

    def handle(self) -> None:
        service: EventService = self.server.service  # type: ignore[attr-defined]
        try:
            message = protocol.read_message(self.rfile)
            response = service.dispatch(message, protocol.peer_uid(self.connection))
        except protocol.ProtocolError as exc:
            response = {"v": protocol.PROTOCOL_VERSION, "error": str(exc)}
        with contextlib.suppress(OSError):
            self.wfile.write(protocol.encode(response))


class EventServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: Path, service: EventService):
        self.service = service
        _clear_stale_socket(socket_path)
        super().__init__(str(socket_path), _Handler)
        os.chmod(socket_path, SOCKET_MODE)


def build_service(config: Config, paths: Paths) -> EventService:
    notifier = RateLimitedNotifier(create_notifier(config.notify), interval=NOTIFY_REPEAT_SECONDS)
    watcher = create_watcher(config.watcher.backend, config.watcher.options)
    return EventService(
        log=EventLog(paths.event_log),
        watcher=watcher,
        mode=config.watcher.mode,
        timeout=config.watcher.timeout_seconds,
        notifier=notifier,
    )


def serve(config: Config, paths: Paths) -> None:
    service = build_service(config, paths)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    server = EventServer(paths.socket_path, service)

    def shutdown(signum: int, frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    service.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.stop()
        paths.socket_path.unlink(missing_ok=True)


def _clear_stale_socket(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"{path} exists and is not a socket; refusing to replace it")
    path.unlink()
