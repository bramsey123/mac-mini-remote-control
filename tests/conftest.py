from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from macagent.eventd import EventServer, EventService
from macagent.eventlog import EventLog
from macagent.notify import Notifier, RateLimitedNotifier
from macagent.paths import Paths
from macagent.watcher import NullWatcher, Watcher, WatcherMode

REPO_ROOT = Path(__file__).resolve().parent.parent


class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, tuple[str, ...]]] = []

    def send(self, title, message, priority="default", tags=()):
        self.sent.append((title, message, priority, tuple(tags)))


@pytest.fixture
def paths() -> Iterator[Paths]:
    # Unix socket paths are length-limited (~104 bytes on macOS), so use a
    # short root under /tmp rather than pytest's deeply nested tmp_path.
    root = Path(tempfile.mkdtemp(prefix="ma-", dir="/tmp"))
    try:
        yield Paths.under(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def make_service(paths: Paths, notifier: RecordingNotifier):
    services: list[EventService] = []

    def factory(
        watcher: Watcher | None = None,
        mode: WatcherMode = WatcherMode.OFF,
        timeout: float = 1.0,
        **kwargs,
    ) -> EventService:
        service = EventService(
            log=EventLog(paths.event_log),
            watcher=watcher or NullWatcher(),
            mode=mode,
            timeout=timeout,
            notifier=RateLimitedNotifier(notifier, interval=0),
            **kwargs,
        )
        service.start()
        services.append(service)
        return service

    yield factory
    for service in services:
        service.stop()


@pytest.fixture
def running_server(paths: Paths):
    """Start an EventServer for a given service; yields a starter function."""
    servers: list[EventServer] = []

    def start(service: EventService) -> EventServer:
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        server = EventServer(paths.socket_path, service)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
