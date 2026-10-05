"""The health checks.

Each check verifies that one protection described in docs/architecture.md is
still in place. They run as root every 15 minutes (LaunchDaemon) and on demand
with ``sudo macagent check``.

Checks report facts; they never repair anything. Fixing is a human decision,
made by re-running setup or editing config.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .. import protocol
from ..watcher import WatcherMode
from .base import HealthContext, Outcome, Status, check

PING_TIMEOUT_SECONDS = 3.0

# Settings that must hold in managed-settings.json. Keep in sync with
# config/managed-settings.json; tests/test_managed_settings.py checks both.
REQUIRED_MANAGED_SETTINGS: dict[tuple[str, ...], Any] = {
    ("permissions", "disableBypassPermissionsMode"): "disable",
    ("permissions", "defaultMode"): "auto",
    ("allowManagedHooksOnly",): True,
    ("allowManagedPermissionRulesOnly",): True,
}


# --- Account isolation --------------------------------------------------------


@check("agent-account")
def agent_account(ctx: HealthContext) -> Outcome:
    user = ctx.config.agent_user
    if ctx.home_of(user) is None:
        return Outcome(Status.FAIL, f"agent account {user!r} does not exist")
    result = ctx.runner.run(["dseditgroup", "-o", "checkmember", "-m", user, "admin"])
    answer = result.stdout.strip().split(" ", 1)[0].lower()
    if answer == "no":
        return Outcome(Status.OK, f"{user!r} is a standard (non-admin) account")
    if answer == "yes":
        return Outcome(
            Status.FAIL,
            f"{user!r} is an ADMIN — isolation is broken",
            "Remove it: sudo dseditgroup -o edit -d " + user + " -t user admin",
        )
    return Outcome(Status.ERROR, "could not determine admin membership", result.stderr.strip())


@check("agent-secrets")
def agent_secrets(ctx: HealthContext) -> Outcome:
    home = ctx.home_of(ctx.config.agent_user)
    if home is None:
        return Outcome(Status.ERROR, "agent account missing; see agent-account")
    env_file = home / ".config" / "macagent" / "agent.env"
    try:
        st = env_file.stat()
    except FileNotFoundError:
        return Outcome(Status.INFO, "no agent.env yet (no secrets provisioned)", str(env_file))
    if st.st_mode & 0o077:
        return Outcome(
            Status.FAIL,
            "agent.env is readable by other accounts",
            f"chmod 600 {env_file} (mode is {stat.filemode(st.st_mode)})",
        )
    return Outcome(Status.OK, "agent secrets file is private to the agent")


# --- Tamper resistance --------------------------------------------------------


@check("managed-settings")
def managed_settings(ctx: HealthContext) -> Outcome:
    path = ctx.paths.managed_settings
    problem = _protection_problem(path, ctx.trusted_uid)
    if problem:
        return Outcome(Status.FAIL, "managed settings are not protected", problem)
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Outcome(Status.FAIL, "managed settings unreadable", str(exc))
    errors = managed_settings_errors(settings, str(ctx.paths.hook_command))
    if errors:
        return Outcome(Status.FAIL, "managed settings drifted from the required baseline", "; ".join(errors))
    return Outcome(Status.OK, "managed settings enforce the baseline")


def managed_settings_errors(settings: dict[str, Any], hook_command: str) -> list[str]:
    errors = []
    for key_path, expected in REQUIRED_MANAGED_SETTINGS.items():
        actual = _dig(settings, key_path)
        if actual != expected:
            errors.append(f"{'.'.join(key_path)} is {actual!r}, expected {expected!r}")
    commands = [
        hook.get("command")
        for group in _dig(settings, ("hooks", "PreToolUse")) or []
        if isinstance(group, dict)
        for hook in group.get("hooks", [])
        if isinstance(hook, dict)
    ]
    if hook_command not in commands:
        errors.append(f"PreToolUse hook {hook_command} is not registered")
    return errors


@check("install-integrity")
def install_integrity(ctx: HealthContext) -> Outcome:
    problems = []
    for root in (ctx.paths.lib_dir, ctx.paths.config_file):
        if not root.exists():
            problems.append(f"{root} is missing")
            continue
        for path in _walk(root):
            problem = _protection_problem(path, ctx.trusted_uid)
            if problem:
                problems.append(problem)
    secrets = ctx.paths.secrets_dir
    if secrets.exists():
        mode = secrets.stat().st_mode
        if mode & 0o007:
            problems.append(f"{secrets} is accessible to other accounts ({stat.filemode(mode)})")
    if problems:
        shown = problems[:5] + ([f"…and {len(problems) - 5} more"] if len(problems) > 5 else [])
        return Outcome(Status.FAIL, "installed files can be modified by the wrong account", "; ".join(shown))
    return Outcome(Status.OK, "installed code and config are root-owned and not writable by others")


# --- Monitoring pipeline ------------------------------------------------------


@check("eventd")
def eventd(ctx: HealthContext) -> Outcome:
    response = _ping(ctx)
    if isinstance(response, str):
        return Outcome(Status.FAIL, "event daemon unreachable — tool calls are not being recorded", response)
    return Outcome(Status.OK, f"event daemon running (v{response.get('version', '?')})")


@check("watcher")
def watcher(ctx: HealthContext) -> Outcome:
    cfg = ctx.config.watcher
    if cfg.mode is WatcherMode.OFF:
        return Outcome(Status.INFO, "AI watcher not enabled (optional; see docs/watcher.md)")
    response = _ping(ctx)
    if isinstance(response, str):
        summary = f"watcher {cfg.backend!r} configured but event daemon unreachable"
        return Outcome(Status.FAIL, summary, response)
    info = response.get("watcher") or {}
    if info.get("name") != cfg.backend or info.get("mode") != cfg.mode.value:
        return Outcome(
            Status.WARN,
            "running watcher differs from config (restart eventd to apply)",
            f"running {info.get('name')}/{info.get('mode')}, configured {cfg.backend}/{cfg.mode.value}",
        )
    stats = info.get("stats") or {}
    failures = sum(int(stats.get(k, 0)) for k in ("gate_timeout", "gate_error", "observe_errors", "dropped"))
    if failures:
        return Outcome(
            Status.WARN,
            f"watcher {cfg.backend!r} has had {failures} failure(s) since eventd started",
            json.dumps(stats, sort_keys=True),
        )
    return Outcome(Status.OK, f"watcher {cfg.backend!r} running in {cfg.mode.value} mode")


# --- Recovery -----------------------------------------------------------------


_BACKUP_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{6})")


@check("backups")
def backups(ctx: HealthContext) -> Outcome:
    result = ctx.runner.run(["tmutil", "latestbackup"])
    match = _BACKUP_STAMP.search(result.stdout)
    if result.returncode != 0 or not match:
        return Outcome(
            Status.WARN,
            "no Time Machine backup found (or tmutil lacks Full Disk Access)",
            (result.stderr or result.stdout).strip(),
        )
    taken = dt.datetime.strptime(match.group(1), "%Y-%m-%d-%H%M%S").replace(tzinfo=ctx.now().tzinfo)
    age_hours = (ctx.now() - taken).total_seconds() / 3600
    limit = ctx.config.health.backup_max_age_hours
    if age_hours > limit:
        summary = f"last backup is {age_hours:.0f}h old (limit {limit:.0f}h)"
        return Outcome(Status.FAIL, summary, match.group(1))
    return Outcome(Status.OK, f"last backup {age_hours:.1f}h ago")


@check("filevault")
def filevault(ctx: HealthContext) -> Outcome:
    result = ctx.runner.run(["fdesetup", "status"])
    text = result.stdout.strip()
    if "FileVault is On" in text:
        on = True
    elif "FileVault is Off" in text:
        on = False
    else:
        return Outcome(Status.ERROR, "could not read FileVault status", (result.stderr or text).strip())
    expected = ctx.config.health.expect_filevault
    state = "on" if on else "off"
    if expected is None:
        return Outcome(Status.INFO, f"FileVault is {state} (no expectation configured)")
    if on != expected:
        wanted = "on" if expected else "off"
        return Outcome(Status.WARN, f"FileVault is {state}, but config expects it {wanted}")
    return Outcome(Status.OK, f"FileVault is {state}, as configured")


# --- Availability -------------------------------------------------------------


@check("power")
def power(ctx: HealthContext) -> Outcome:
    result = ctx.runner.run(["pmset", "-g"])
    if result.returncode != 0:
        return Outcome(Status.ERROR, "could not read power settings", result.stderr.strip())
    values = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            values[parts[0]] = parts[1]
    problems = []
    if values.get("sleep") != "0":
        problems.append(f"system sleep is {values.get('sleep', 'unknown')} (want 0)")
    if values.get("autorestart") != "1":
        problems.append("restart after power failure is off")
    if problems:
        return Outcome(Status.WARN, "Mac may become unreachable", "; ".join(problems))
    return Outcome(Status.OK, "sleep disabled and auto-restart on")


@check("tailscale")
def tailscale(ctx: HealthContext) -> Outcome:
    result = ctx.runner.run([ctx.config.health.tailscale_path, "status", "--json"])
    if result.returncode == 127:
        summary = "Tailscale CLI not found — remote access may be down"
        return Outcome(Status.WARN, summary, result.stderr.strip())
    try:
        state = json.loads(result.stdout).get("BackendState")
    except ValueError:
        state = None
    if state == "Running":
        return Outcome(Status.OK, "Tailscale connected")
    summary = f"Tailscale not connected (state: {state or 'unknown'})"
    return Outcome(Status.WARN, summary, result.stderr.strip())


@check("agent-session")
def agent_session(ctx: HealthContext) -> Outcome:
    hc = ctx.config.health
    result = ctx.runner.run(
        ["sudo", "-u", ctx.config.agent_user, "-H", hc.tmux_path, "has-session", "-t", hc.agent_session_name]
    )
    if result.returncode == 0:
        return Outcome(Status.OK, f"agent session {hc.agent_session_name!r} is running")
    return Outcome(
        Status.WARN,
        f"agent session {hc.agent_session_name!r} is not running",
        "Start it: ssh in, then: sudo -iu " + ctx.config.agent_user + " agent-session start",
    )


# --- Helpers ------------------------------------------------------------------


def _ping(ctx: HealthContext) -> dict[str, Any] | str:
    """Return the daemon's ping response, or an error description."""
    try:
        response = protocol.request(
            ctx.paths.socket_path, {"type": protocol.RequestType.PING}, timeout=PING_TIMEOUT_SECONDS
        )
    except (OSError, protocol.ProtocolError) as exc:
        return f"{ctx.paths.socket_path}: {exc}"
    if not response.get("ok"):
        return f"unexpected response: {response}"
    return response


def _protection_problem(path: Path, trusted_uid: int) -> str | None:
    """Describe why ``path`` could be modified by an untrusted account, if it can."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return f"{path} is missing"
    if stat.S_ISLNK(st.st_mode):
        return None  # a link's own mode is meaningless; its directory is checked
    if st.st_uid != trusted_uid:
        return f"{path} is owned by uid {st.st_uid}"
    if st.st_mode & 0o022:
        return f"{path} is writable by group/others ({stat.filemode(st.st_mode)})"
    return None


def _walk(root: Path) -> Iterator[Path]:
    yield root
    if root.is_dir() and not root.is_symlink():
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                yield Path(dirpath) / name


def _dig(data: Any, key_path: tuple[str, ...]) -> Any:
    for key in key_path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data
