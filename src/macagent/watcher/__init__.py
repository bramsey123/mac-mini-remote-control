"""AI watcher slot. See docs/watcher.md for the contract and how to add a backend."""

from __future__ import annotations

from collections.abc import Callable

from .interface import Alert, GateDecision, GateVerdict, Severity, Watcher, WatcherMode
from .null import NullWatcher

# Backend name -> factory taking the backend's option table from config.toml.
# Real backends register here and import their dependencies lazily inside the
# factory, so the core install stays dependency-free.
BACKENDS: dict[str, Callable[[dict], Watcher]] = {
    "none": lambda options: NullWatcher(),
}


def create_watcher(backend: str, options: dict | None = None) -> Watcher:
    try:
        factory = BACKENDS[backend]
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(f"unknown watcher backend {backend!r}; known backends: {known}") from None
    return factory(dict(options or {}))


__all__ = [
    "BACKENDS",
    "Alert",
    "GateDecision",
    "GateVerdict",
    "NullWatcher",
    "Severity",
    "Watcher",
    "WatcherMode",
    "create_watcher",
]
