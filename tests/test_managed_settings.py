"""The shipped managed settings must satisfy the invariants the health check enforces."""

from __future__ import annotations

import json

from conftest import REPO_ROOT

from macagent import hook
from macagent.config import MAX_WATCHER_TIMEOUT_SECONDS
from macagent.health.checks import managed_settings_errors
from macagent.paths import DEFAULT_PATHS

SETTINGS = json.loads((REPO_ROOT / "config/managed-settings.json").read_text())


def _hooks():
    return [h for group in SETTINGS["hooks"]["PreToolUse"] for h in group["hooks"]]


def test_shipped_settings_meet_the_baseline():
    assert managed_settings_errors(SETTINGS, str(DEFAULT_PATHS.hook_command)) == []


def test_hook_matches_all_tools():
    # Per the Claude Code docs, omitting "matcher" matches every tool.
    for group in SETTINGS["hooks"]["PreToolUse"]:
        assert "matcher" not in group


def test_no_allow_rules_are_shipped():
    # Allow rules skip auto mode's classifier; the baseline grants nothing up front.
    assert not SETTINGS["permissions"].get("allow")


def test_timeout_chain_degrades_to_baseline():
    """A slow watcher must give up before the hook does, and the hook before Claude Code.

    Otherwise Claude Code's own hook timeout fires first and the event isn't
    recorded at all.
    """
    [entry] = _hooks()
    assert MAX_WATCHER_TIMEOUT_SECONDS < hook.SOCKET_TIMEOUT_SECONDS < entry["timeout"]


def test_health_check_notices_missing_invariants():
    broken = json.loads(json.dumps(SETTINGS))
    broken["allowManagedHooksOnly"] = False
    del broken["permissions"]["disableBypassPermissionsMode"]
    errors = managed_settings_errors(broken, str(DEFAULT_PATHS.hook_command))
    assert len(errors) == 2
