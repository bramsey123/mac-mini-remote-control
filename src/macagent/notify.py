"""Push notifications to the owner's phone.

Notifications are the one channel that reaches a human, so they are rationed:
the health check only notifies on state changes, and repeated messages of the
same kind are rate-limited. Alert fatigue makes the important alert easy to
miss — the same reason we don't lean on approval prompts.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from .config import NotifyConfig

PRIORITIES = ("min", "low", "default", "high", "urgent")


class NotifyError(Exception):
    pass


class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, message: str, priority: str = "default", tags: tuple[str, ...] = ()) -> None:
        """Deliver a notification. Raises NotifyError on failure."""


class NullNotifier(Notifier):
    def send(self, title: str, message: str, priority: str = "default", tags: tuple[str, ...] = ()) -> None:
        pass


class NtfyNotifier(Notifier):
    """https://ntfy.sh (or a self-hosted server). Pick an unguessable topic, or use a token."""

    def __init__(
        self,
        server: str,
        topic: str,
        token: str | None = None,
        timeout: float = 10.0,
        opener: Callable[..., object] = urllib.request.urlopen,
    ):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.token = token
        self.timeout = timeout
        self._open = opener

    def send(self, title: str, message: str, priority: str = "default", tags: tuple[str, ...] = ()) -> None:
        if priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        headers = {"Title": _header_safe(title), "Priority": priority}
        if tags:
            headers["Tags"] = ",".join(tags)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url, data=message.encode(), headers=headers, method="POST")
        try:
            with self._open(req, timeout=self.timeout) as response:  # type: ignore[attr-defined]
                status = getattr(response, "status", 200)
        except OSError as exc:
            raise NotifyError(f"ntfy request failed: {exc}") from exc
        if not 200 <= status < 300:
            raise NotifyError(f"ntfy returned HTTP {status}")


class RateLimitedNotifier(Notifier):
    """Drop repeats of the same ``key`` within ``interval`` seconds."""

    def __init__(self, inner: Notifier, interval: float, clock: Callable[[], float] = time.monotonic):
        self.inner = inner
        self.interval = interval
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def send_keyed(
        self, key: str, title: str, message: str, priority: str = "default", tags: tuple[str, ...] = ()
    ) -> bool:
        """Send unless ``key`` was sent recently. Returns whether it was sent."""
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.interval:
                return False
            self._last[key] = now
        self.inner.send(title, message, priority, tags)
        return True

    def send(self, title: str, message: str, priority: str = "default", tags: tuple[str, ...] = ()) -> None:
        self.send_keyed(title, title, message, priority, tags)


def create_notifier(config: NotifyConfig) -> Notifier:
    if config.backend == "none":
        return NullNotifier()
    if config.backend == "ntfy":
        token = Path(config.token_file).read_text(encoding="utf-8").strip() if config.token_file else None
        return NtfyNotifier(config.server, config.topic, token=token or None)
    raise ValueError(f"unknown notify backend {config.backend!r}")


def _header_safe(text: str) -> str:
    # HTTP headers must be latin-1 and single-line.
    return text.replace("\r", " ").replace("\n", " ").encode("latin-1", "replace").decode("latin-1")
