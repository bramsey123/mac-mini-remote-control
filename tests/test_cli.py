from __future__ import annotations

import json

from conftest import REPO_ROOT

from macagent.cli import format_event, main
from macagent.eventlog import EventLog
from macagent.events import Event, EventType


def test_config_check(capsys, tmp_path):
    assert main(["config-check", str(REPO_ROOT / "config/macagent.example.toml")]) == 0
    bad = tmp_path / "bad.toml"
    bad.write_text('agent_user = "root"\n')
    assert main(["config-check", str(bad)]) == 1
    assert "non-root" in capsys.readouterr().err


def test_events_lists_recent_events(capsys, paths):
    log = EventLog(paths.event_log)
    log.append(Event(EventType.TOOL_PRE, "hook", {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}))
    log.append(Event(EventType.HEALTH_RESULT, "healthcheck", {"check": "backups", "status": "fail"}))
    root = str(paths.lib_dir).split("/usr/local")[0]
    assert main(["--root", root, "events", "-n", "10"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Bash: ls -la" in out[0]
    assert "backups: fail" in out[1]
    assert main(["--root", root, "events", "--type", "health.result", "--json"]) == 0
    [line] = capsys.readouterr().out.splitlines()
    assert json.loads(line)["type"] == "health.result"


def test_check_json_output(capsys, paths):
    root = str(paths.lib_dir).split("/usr/local")[0]
    code = main(["--root", root, "check", "--json", "--only", "eventd", "watcher"])
    results = json.loads(capsys.readouterr().out)
    assert [r["check_id"] for r in results] == ["eventd", "watcher"]
    assert code == 1  # eventd isn't running


def test_notify_and_only_are_exclusive(capsys, paths):
    root = str(paths.lib_dir).split("/usr/local")[0]
    assert main(["--root", root, "check", "--notify", "--only", "eventd"]) == 2


def test_notify_test_without_backend(capsys, paths):
    root = str(paths.lib_dir).split("/usr/local")[0]
    assert main(["--root", root, "notify-test"]) == 1
    assert "disabled" in capsys.readouterr().err


def test_hook_command_never_fails(monkeypatch, paths):
    import io

    root = str(paths.lib_dir).split("/usr/local")[0]
    monkeypatch.setattr("sys.stdin", io.StringIO("{broken"))
    assert main(["--root", root, "hook"]) == 0


def test_format_event_truncates_long_commands():
    line = format_event(
        {"ts": "t", "type": "tool.pre", "data": {"tool_name": "Bash", "tool_input": {"command": "x" * 500}}}
    )
    assert line.endswith("…")
    assert len(line) < 220
