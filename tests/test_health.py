from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from macagent.config import Config, HealthConfig, WatcherConfig
from macagent.health import (
    REGISTRY,
    CheckResult,
    CommandResult,
    CommandRunner,
    HealthContext,
    Outcome,
    Status,
    diff_state,
    run_and_notify,
    run_checks,
)
from macagent.health import checks as C
from macagent.notify import NotifyError
from macagent.watcher import BACKENDS, NullWatcher, WatcherMode

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.UTC)


class FakeRunner(CommandRunner):
    def __init__(self, responses: dict[str, CommandResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=15.0):
        self.calls.append(list(argv))
        for prefix, result in self.responses.items():
            if " ".join(argv).startswith(prefix):
                return result
        return CommandResult(127, "", f"{argv[0]}: not found")


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(0, stdout, "")


@pytest.fixture
def agent_home(tmp_path) -> Path:
    home = tmp_path / "agent-home"
    home.mkdir()
    return home


@pytest.fixture
def ctx(paths, agent_home):
    def make(runner: FakeRunner | None = None, config: Config | None = None) -> HealthContext:
        return HealthContext(
            config=config or Config(),
            paths=paths,
            runner=runner or FakeRunner(),
            now=lambda: NOW,
            trusted_uid=os.getuid(),
            home_of=lambda user: agent_home if user == "agent" else None,
        )

    return make


def install_tree(paths, settings: dict | None = None):
    """Lay out a correct installation under the test root."""
    (paths.lib_dir / "bin").mkdir(parents=True)
    (paths.lib_dir / "bin" / "macagent").write_text("#!/bin/sh\n")
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("")
    paths.managed_settings.parent.mkdir(parents=True, exist_ok=True)
    if settings is None:
        settings = json.loads((REPO_ROOT / "config/managed-settings.json").read_text())
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
        hook["command"] = str(paths.hook_command)
    paths.managed_settings.write_text(json.dumps(settings))
    for p in (paths.lib_dir, paths.lib_dir / "bin", paths.lib_dir / "bin" / "macagent"):
        p.chmod(0o755)
    for p in (paths.config_file, paths.managed_settings):
        p.chmod(0o644)


def test_every_check_is_registered_once():
    ids = [check_id for check_id, _ in REGISTRY]
    assert len(ids) == len(set(ids))
    assert {"agent-account", "managed-settings", "install-integrity", "eventd", "watcher", "backups"} <= set(
        ids
    )


# --- agent-account --------------------------------------------------------------


@pytest.mark.parametrize(
    "stdout, status",
    [
        ("no agent is NOT a member of admin", Status.OK),
        ("yes agent is a member of admin", Status.FAIL),
        ("", Status.ERROR),
    ],
)
def test_agent_account_admin_membership(ctx, stdout, status):
    runner = FakeRunner({"dseditgroup": CommandResult(0, stdout, "")})
    assert C.agent_account(ctx(runner)).status is status


def test_agent_account_missing(ctx):
    config = Config(agent_user="nobody-here")
    assert C.agent_account(ctx(config=config)).status is Status.FAIL


# --- agent-secrets ----------------------------------------------------------------


def test_agent_secrets_states(ctx, agent_home):
    assert C.agent_secrets(ctx()).status is Status.INFO
    env = agent_home / ".config/macagent/agent.env"
    env.parent.mkdir(parents=True)
    env.write_text("X=1")
    env.chmod(0o644)
    assert C.agent_secrets(ctx()).status is Status.FAIL
    env.chmod(0o600)
    assert C.agent_secrets(ctx()).status is Status.OK


# --- managed-settings and install-integrity ---------------------------------------


def test_repo_install_passes_integrity_checks(ctx, paths):
    install_tree(paths)
    assert C.managed_settings(ctx()).status is Status.OK
    assert C.install_integrity(ctx()).status is Status.OK


def test_managed_settings_drift_is_a_failure(ctx, paths):
    install_tree(paths, settings={"permissions": {"defaultMode": "bypassPermissions"}})
    outcome = C.managed_settings(ctx())
    assert outcome.status is Status.FAIL
    assert "defaultMode" in outcome.detail
    assert "not registered" in outcome.detail


def test_writable_managed_settings_is_a_failure(ctx, paths):
    install_tree(paths)
    paths.managed_settings.chmod(0o666)
    assert C.managed_settings(ctx()).status is Status.FAIL


def test_group_writable_install_file_is_a_failure(ctx, paths):
    install_tree(paths)
    (paths.lib_dir / "bin" / "macagent").chmod(0o775)
    outcome = C.install_integrity(ctx())
    assert outcome.status is Status.FAIL
    assert "writable" in outcome.detail


def test_wrong_owner_is_a_failure(paths, agent_home):
    install_tree(paths)
    context = HealthContext(config=Config(), paths=paths, trusted_uid=os.getuid() + 1)
    assert C.install_integrity(context).status is Status.FAIL


def test_open_secrets_dir_is_a_failure(ctx, paths):
    install_tree(paths)
    paths.secrets_dir.mkdir(parents=True)
    paths.secrets_dir.chmod(0o755)
    assert C.install_integrity(ctx()).status is Status.FAIL
    paths.secrets_dir.chmod(0o750)
    assert C.install_integrity(ctx()).status is Status.OK


def test_missing_install_is_a_failure(ctx):
    assert C.install_integrity(ctx()).status is Status.FAIL


# --- eventd and watcher -----------------------------------------------------------


def test_eventd_down_is_a_failure(ctx):
    assert C.eventd(ctx()).status is Status.FAIL


def test_eventd_and_watcher_up(ctx, make_service, running_server):
    running_server(make_service())
    assert C.eventd(ctx()).status is Status.OK
    assert C.watcher(ctx()).status is Status.INFO  # off by default: optional, not a problem


@pytest.fixture
def scripted_backend():
    BACKENDS["scripted"] = lambda options: NullWatcher()
    yield
    del BACKENDS["scripted"]


def test_watcher_configured_but_eventd_down(ctx, scripted_backend):
    config = Config(watcher=WatcherConfig(backend="scripted", mode=WatcherMode.OBSERVE))
    assert C.watcher(ctx(config=config)).status is Status.FAIL


def test_watcher_config_mismatch_warns(ctx, make_service, running_server, scripted_backend):
    running_server(make_service())  # runs "none"/off
    config = Config(watcher=WatcherConfig(backend="scripted", mode=WatcherMode.OBSERVE))
    assert C.watcher(ctx(config=config)).status is Status.WARN


# --- backups, filevault, power, tailscale, agent-session -------------------------


@pytest.mark.parametrize(
    "stdout, code, status",
    [
        ("/Volumes/TM/2026-10-05-090000.backup\n", 0, Status.OK),
        ("/Volumes/TM/2026-10-03-090000.backup\n", 0, Status.FAIL),
        ("", 1, Status.WARN),
    ],
)
def test_backups(ctx, stdout, code, status):
    runner = FakeRunner({"tmutil": CommandResult(code, stdout, "")})
    assert C.backups(ctx(runner)).status is status


@pytest.mark.parametrize(
    "stdout, expect, status",
    [
        ("FileVault is On.", None, Status.INFO),
        ("FileVault is On.", True, Status.OK),
        ("FileVault is Off.", True, Status.WARN),
        ("FileVault is Off.", False, Status.OK),
        ("garbage", True, Status.ERROR),
    ],
)
def test_filevault(ctx, stdout, expect, status):
    runner = FakeRunner({"fdesetup": ok(stdout)})
    config = Config(health=HealthConfig(expect_filevault=expect))
    assert C.filevault(ctx(runner, config)).status is status


def test_power(ctx):
    good = " sleep                0\n autorestart          1\n"
    assert C.power(ctx(FakeRunner({"pmset": ok(good)}))).status is Status.OK
    bad = " sleep                10\n autorestart          0\n"
    outcome = C.power(ctx(FakeRunner({"pmset": ok(bad)})))
    assert outcome.status is Status.WARN
    assert "sleep" in outcome.detail and "power failure" in outcome.detail


@pytest.mark.parametrize(
    "result, status",
    [
        (ok(json.dumps({"BackendState": "Running"})), Status.OK),
        (ok(json.dumps({"BackendState": "Stopped"})), Status.WARN),
        (CommandResult(127, "", "not found"), Status.WARN),
    ],
)
def test_tailscale(ctx, result, status):
    runner = FakeRunner({HealthConfig().tailscale_path: result})
    assert C.tailscale(ctx(runner)).status is status


def test_agent_session(ctx):
    runner = FakeRunner({"sudo -u agent": ok()})
    assert C.agent_session(ctx(runner)).status is Status.OK
    assert runner.calls[0][:3] == ["sudo", "-u", "agent"]
    assert C.agent_session(ctx(FakeRunner())).status is Status.WARN


# --- runner -------------------------------------------------------------------------


def test_crashing_check_reports_error_and_others_still_run(ctx):
    def boom(context):
        raise RuntimeError("bug")

    REGISTRY.insert(0, ("boom", boom))
    try:
        results = run_checks(ctx())
    finally:
        REGISTRY.pop(0)
    assert results[0] == CheckResult("boom", Status.ERROR, "check crashed", "RuntimeError('bug')")
    assert len(results) == len(REGISTRY) + 1


def _r(check_id: str, status: Status) -> CheckResult:
    return CheckResult(check_id, status, f"{check_id} is {status.value}")


REMIND = dt.timedelta(hours=6)


def test_first_run_notifies_only_problems():
    changes, state = diff_state({}, [_r("a", Status.OK), _r("b", Status.WARN)], NOW, REMIND)
    assert [(c.result.check_id, c.kind) for c in changes] == [("b", "new-problem")]
    assert state["a"]["notified_at"] is None
    assert state["b"]["notified_at"] is not None


def test_unchanged_state_is_quiet_then_reminds_for_failures():
    _, state = diff_state({}, [_r("a", Status.FAIL), _r("b", Status.WARN)], NOW, REMIND)
    later = NOW + dt.timedelta(hours=1)
    changes, state = diff_state(state, [_r("a", Status.FAIL), _r("b", Status.WARN)], later, REMIND)
    assert changes == []
    much_later = NOW + dt.timedelta(hours=7)
    changes, _ = diff_state(state, [_r("a", Status.FAIL), _r("b", Status.WARN)], much_later, REMIND)
    assert [(c.result.check_id, c.kind) for c in changes] == [("a", "reminder")]


def test_worse_and_recovered_notify_but_partial_improvement_is_quiet():
    _, state = diff_state({}, [_r("a", Status.OK), _r("b", Status.FAIL), _r("c", Status.FAIL)], NOW, REMIND)
    changes, _ = diff_state(
        state, [_r("a", Status.WARN), _r("b", Status.OK), _r("c", Status.WARN)], NOW, REMIND
    )
    assert sorted((c.result.check_id, c.kind) for c in changes) == [("a", "worse"), ("b", "recovered")]


def test_run_and_notify_persists_state(ctx, paths, notifier):
    context = ctx()
    results, changes = run_and_notify(context, notifier)
    assert changes and notifier.sent
    saved = json.loads(paths.health_state_file.read_text())
    assert set(saved) == {r.check_id for r in results}
    notifier.sent.clear()
    _, changes = run_and_notify(context, notifier)
    assert changes == [] and notifier.sent == []


def test_failed_notification_keeps_old_state_so_it_retries(ctx, paths):
    class Failing:
        def send(self, *args, **kwargs):
            raise NotifyError("offline")

    with pytest.raises(NotifyError):
        run_and_notify(ctx(), Failing())
    assert not paths.health_state_file.exists()


def test_outcome_is_plain_data():
    assert Outcome(Status.OK, "fine") == Outcome(Status.OK, "fine")


@pytest.mark.skipif(shutil.which("dseditgroup") is None, reason="macOS only")
def test_real_runner_smoke(paths):
    result = CommandRunner().run(["dseditgroup", "-o", "checkmember", "-m", "root", "admin"])
    assert result.returncode in (0, 67)
