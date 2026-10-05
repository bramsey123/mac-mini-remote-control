from __future__ import annotations

import io
import json

import pytest

from macagent import hook
from macagent.watcher import GateDecision


def _pre_tool_use(**overrides):
    event = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_use_id": "toolu_1",
        "permission_mode": "auto",
        "cwd": "/Users/agent/work",
        "transcript_path": "/Users/agent/.claude/x.jsonl",
    }
    event.update(overrides)
    return event


def test_payload_keeps_only_recorded_fields():
    payload = hook.build_payload(_pre_tool_use())
    assert set(payload) == set(hook.PAYLOAD_FIELDS)
    assert "transcript_path" not in payload


def test_payload_bounds_large_values():
    big = "x" * (hook.MAX_STRING_CHARS * 3)
    payload = hook.build_payload(_pre_tool_use(tool_input={"content": big, "items": list(range(500))}))
    content = payload["tool_input"]["content"]
    assert len(content) < len(big)
    assert "truncated" in content and "sha256:" in content
    assert len(payload["tool_input"]["items"]) == hook.MAX_LIST_ITEMS + 1


def test_payload_bounds_nesting():
    nested: dict = {}
    cursor = nested
    for _ in range(hook.MAX_DEPTH + 5):
        cursor["k"] = {}
        cursor = cursor["k"]
    payload = hook.build_payload(_pre_tool_use(tool_input=nested))
    assert "nested too deeply" in json.dumps(payload)


@pytest.mark.parametrize("decision", [GateDecision.DENY, GateDecision.ASK])
def test_ask_and_deny_produce_hook_output(decision):
    output = hook.hook_output({"decision": decision.value, "reason": "because"})
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.value,
            "permissionDecisionReason": "because",
        }
    }


@pytest.mark.parametrize(
    "response", [{"decision": "defer"}, {"decision": "allow"}, {"decision": None}, {"error": "boom"}, {}]
)
def test_everything_else_produces_no_output(response):
    # In particular "allow" is never passed through (ADR 0002).
    assert hook.hook_output(response) is None


def test_daemon_down_means_no_output_and_exit_zero(paths):
    stdout = io.StringIO()
    code = hook.run(io.StringIO(json.dumps(_pre_tool_use())), stdout, paths.socket_path)
    assert code == 0
    assert stdout.getvalue() == ""


@pytest.mark.parametrize("stdin", ["", "not json", "[]", json.dumps(_pre_tool_use(hook_event_name="Stop"))])
def test_unusable_input_is_ignored(paths, stdin):
    stdout = io.StringIO()
    assert hook.run(io.StringIO(stdin), stdout, paths.socket_path) == 0
    assert stdout.getvalue() == ""
