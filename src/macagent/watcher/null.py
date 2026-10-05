"""The default watcher: present in every code path, judges nothing."""

from __future__ import annotations

from collections.abc import Sequence

from ..events import Event
from .interface import Alert, GateVerdict, Watcher


class NullWatcher(Watcher):
    name = "none"

    def review(self, event: Event) -> GateVerdict:
        return GateVerdict.defer()

    def observe(self, event: Event) -> Sequence[Alert]:
        return ()
