from __future__ import annotations

import io
import json
import os
import sys
import threading
import time

import pytest

from macagent import hook, protocol
from macagent.eventlog import read_events
from macagent.events import Event, EventType, Source
from macagent.watcher import Alert, GateDecision, GateVerdict, Severity, Watcher, WatcherMode


class ScriptedWatcher(Watcher):
    name = "scripted"

    def __init__(self, review=None, observe=None):
        self._review = review or (lambda event: GateVerdict.defer())
        self._observe = observe or (lambda event: ())
        self.observed: list[Event] = []
        self.closed = False

    def review(self, event):
        return self._review(event)

    def observe(self, event):
        self.observed.append(event)
        return self._observe(event)

    def close(self):
        self.closed = True


def _events(paths, type_=None):
    return [e for e in read_events(paths.event_log) if type_ is None or e.type == type_]


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_off_mode_records_and_defers(make_service, paths):
    service = make_service()
    verdict = service.handle_tool_event({"tool_name": "Bash"}, peer_uid=501)
    assert verdict.decision is GateDecision.DEFER
    [event] = _events(paths, EventType.TOOL_PRE)
    assert event.data == {"tool_name": "Bash", "peer_uid": 501}
    assert _events(paths, EventType.WATCHER_VERDICT) == []


def test_gate_mode_relays_watcher_deny(make_service, paths):
    watcher = ScriptedWatcher(review=lambda e: GateVerdict(GateDecision.DENY, "no"))
    service = make_service(watcher, WatcherMode.GATE)
    verdict = service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
    assert verdict == GateVerdict(GateDecision.DENY, "no")
    [record] = _events(paths, EventType.WATCHER_VERDICT)
    assert record.data["decision"] == "deny"
    assert record.data["outcome"] == "ok"


def test_gate_timeout_falls_back_to_baseline_and_alerts(make_service, paths, notifier):
    release = threading.Event()

    def slow(event):
        release.wait(5)
        return GateVerdict(GateDecision.DENY, "too late")

    service = make_service(ScriptedWatcher(review=slow), WatcherMode.GATE, timeout=0.05)
    try:
        verdict = service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
    finally:
        release.set()
    assert verdict.decision is GateDecision.DEFER
    assert _events(paths, EventType.WATCHER_VERDICT)[0].data["outcome"] == "timed out"
    assert service.snapshot()["gate_timeout"] == 1
    assert any("unavailable" in title for title, *_ in notifier.sent)


@pytest.mark.parametrize(
    "review",
    [
        lambda e: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda e: "deny",  # wrong type
        lambda e: GateVerdict("allow", "sneaky"),  # not a GateDecision
    ],
)
def test_gate_failures_fall_back_to_baseline(make_service, paths, review):
    service = make_service(ScriptedWatcher(review=review), WatcherMode.GATE)
    verdict = service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
    assert verdict.decision is GateDecision.DEFER
    assert _events(paths, EventType.WATCHER_VERDICT)[0].data["outcome"] == "failed"
    assert service.snapshot()["gate_error"] == 1


def test_observe_mode_never_gates_but_raises_alerts(make_service, paths, notifier):
    def observe(event):
        if event.type == EventType.TOOL_PRE:
            return [Alert(Severity.CRITICAL, "odd activity", "details")]
        return ()

    watcher = ScriptedWatcher(review=lambda e: GateVerdict(GateDecision.DENY, "x"), observe=observe)
    service = make_service(watcher, WatcherMode.OBSERVE)
    verdict = service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
    assert verdict.decision is GateDecision.DEFER  # observe mode cannot block
    assert _wait_for(lambda: _events(paths, EventType.WATCHER_ALERT))
    [alert] = _events(paths, EventType.WATCHER_ALERT)
    assert alert.data["severity"] == "critical"
    assert alert.data["event_ids"] == [_events(paths, EventType.TOOL_PRE)[0].id]
    assert _wait_for(lambda: any(p == "urgent" for _, _, p, _ in notifier.sent))


def test_watcher_never_observes_its_own_output(make_service):
    watcher = ScriptedWatcher(observe=lambda e: [Alert(Severity.INFO, "echo")])
    service = make_service(watcher, WatcherMode.GATE)
    service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
    assert _wait_for(lambda: watcher.observed)
    time.sleep(0.05)
    assert all(e.source != Source.WATCHER for e in watcher.observed)


def test_observe_errors_are_counted_not_fatal(make_service):
    def broken(event):
        raise RuntimeError("bad watcher")

    service = make_service(ScriptedWatcher(observe=broken), WatcherMode.OBSERVE)
    service.handle_tool_event({"tool_name": "A"}, peer_uid=None)
    service.handle_tool_event({"tool_name": "B"}, peer_uid=None)
    assert _wait_for(lambda: service.snapshot()["observe_errors"] == 2)


def test_full_observe_queue_drops_and_counts(make_service):
    gate = threading.Event()
    service = make_service(
        ScriptedWatcher(observe=lambda e: gate.wait(5) and ()), WatcherMode.OBSERVE, observe_queue_size=1
    )
    try:
        for _ in range(5):
            service.handle_tool_event({"tool_name": "Bash"}, peer_uid=None)
        assert service.snapshot()["dropped"] >= 1
    finally:
        gate.set()


def test_record_requires_trusted_peer(make_service, paths):
    service = make_service(trusted_uids=frozenset({0}))
    event = Event(EventType.HEALTH_RESULT, Source.HEALTHCHECK, {"check": "x"}).to_dict()
    denied = service.dispatch({"type": "record", "event": event}, peer_uid=501)
    assert "restricted" in denied["error"]
    assert service.dispatch({"type": "record", "event": event}, peer_uid=None)["error"]
    assert service.dispatch({"type": "record", "event": event}, peer_uid=0) == {"v": 1, "ok": True}
    assert len(_events(paths, EventType.HEALTH_RESULT)) == 1


def test_record_cannot_forge_tool_events(make_service):
    service = make_service(trusted_uids=frozenset({0}))
    forged = Event(EventType.TOOL_PRE, Source.HOOK).to_dict()
    assert "tool_event" in service.dispatch({"type": "record", "event": forged}, peer_uid=0)["error"]


def test_dispatch_rejects_unknown_and_malformed(make_service):
    service = make_service()
    assert "unknown request" in service.dispatch({"type": "nope"}, None)["error"]
    assert "payload" in service.dispatch({"type": "tool_event", "payload": []}, None)["error"]


def test_stop_closes_watcher_and_logs(make_service, paths):
    watcher = ScriptedWatcher()
    service = make_service(watcher, WatcherMode.OBSERVE)
    service.stop()
    assert watcher.closed
    assert _events(paths, EventType.EVENTD_STOP)


# --- End to end over the Unix socket ------------------------------------------


def test_ping_over_socket(make_service, running_server, paths):
    running_server(make_service())
    response = protocol.request(paths.socket_path, {"type": "ping"}, timeout=2)
    assert response["ok"] is True
    assert response["watcher"]["name"] == "none"
    assert (paths.socket_path.stat().st_mode & 0o777) == 0o666


def test_hook_end_to_end_deny(make_service, running_server, paths):
    watcher = ScriptedWatcher(review=lambda e: GateVerdict(GateDecision.DENY, "watcher says no"))
    running_server(make_service(watcher, WatcherMode.GATE))
    stdin = io.StringIO(
        json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
    )
    stdout = io.StringIO()
    assert hook.run(stdin, stdout, paths.socket_path) == 0
    output = json.loads(stdout.getvalue())["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"] == "watcher says no"
    [event] = _events(paths, EventType.TOOL_PRE)
    # Peer credentials are how eventd authenticates `record` requests, so they
    # must work on both platforms we run on.
    if sys.platform in ("darwin", "linux"):
        assert event.data["peer_uid"] == os.getuid()


def test_hook_end_to_end_defer_prints_nothing(make_service, running_server, paths):
    running_server(make_service())
    stdout = io.StringIO()
    stdin = io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read"}))
    assert hook.run(stdin, stdout, paths.socket_path) == 0
    assert stdout.getvalue() == ""


def test_server_refuses_to_replace_non_socket(make_service, paths):
    from macagent.eventd import EventServer

    paths.run_dir.mkdir(parents=True)
    paths.socket_path.write_text("not a socket")
    with pytest.raises(RuntimeError, match="not a socket"):
        EventServer(paths.socket_path, make_service())


def test_oversized_message_is_rejected(make_service, running_server, paths):
    import socket

    running_server(make_service())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(paths.socket_path))
        sock.sendall(b"x" * (protocol.MAX_MESSAGE_BYTES + 10) + b"\n")
        with sock.makefile("rb") as stream:
            assert "too large" in protocol.read_message(stream)["error"]
