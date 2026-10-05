"""Run the real CLI as separate processes, the way launchd and Claude Code do."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

from macagent import protocol
from macagent.eventlog import read_events
from macagent.events import EventType


def _cli(root: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "macagent", "--root", root, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_daemon_hook_and_viewer(paths):
    root = str(paths.lib_dir).split("/usr/local")[0]
    daemon = subprocess.Popen(
        [sys.executable, "-m", "macagent", "--root", root, "eventd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                assert protocol.request(paths.socket_path, {"type": "ping"}, timeout=1)["ok"]
                break
            except OSError:
                if time.monotonic() > deadline or daemon.poll() is not None:
                    raise AssertionError(f"eventd did not start: {daemon.stderr.read()}") from None
                time.sleep(0.05)

        event = {
            "session_id": "s",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "cwd": "/tmp",
        }
        hook = _cli(root, "hook", stdin=json.dumps(event))
        assert hook.returncode == 0
        assert hook.stdout == ""  # no watcher: defer to the baseline

        viewer = _cli(root, "events", "--type", EventType.TOOL_PRE)
        assert "Bash: echo hello" in viewer.stdout

        check = _cli(root, "check", "--only", "eventd")
        assert "event daemon running" in check.stdout
    finally:
        daemon.send_signal(signal.SIGTERM)
        daemon.wait(timeout=10)

    assert daemon.returncode == 0
    assert not paths.socket_path.exists()
    types = [e.type for e in read_events(paths.event_log)]
    assert types[0] == EventType.EVENTD_START
    assert types[-1] == EventType.EVENTD_STOP


def test_hook_with_daemon_down_is_silent(paths):
    root = str(paths.lib_dir).split("/usr/local")[0]
    result = _cli(root, "hook", stdin=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read"}))
    assert (result.returncode, result.stdout) == (0, "")
