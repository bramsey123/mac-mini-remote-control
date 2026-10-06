"""The watcher contract (docs/watcher.md) expressed as tests."""

from __future__ import annotations

import pytest

from macagent.events import Event
from macagent.watcher import BACKENDS, GateDecision, NullWatcher, Watcher, WatcherMode, create_watcher


def test_there_is_no_allow_decision():
    assert {d.value for d in GateDecision} == {"defer", "ask", "deny"}


def test_modes():
    assert {m.value for m in WatcherMode} == {"off", "observe", "gate"}


def test_null_watcher_is_the_default_backend():
    watcher = create_watcher("none")
    assert isinstance(watcher, NullWatcher)
    assert watcher.review(Event("tool.pre", "hook")).decision is GateDecision.DEFER
    assert watcher.observe(Event("tool.pre", "hook")) == ()


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="known backends"):
        create_watcher("nope")


def test_backends_produce_watchers():
    for name in BACKENDS:
        assert isinstance(create_watcher(name), Watcher)


def test_watcher_is_abstract():
    with pytest.raises(TypeError):
        Watcher()  # type: ignore[abstract]
