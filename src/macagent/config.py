"""Load and validate ``config.toml``.

Validation is strict on purpose: an unknown key is almost always a typo, and a
typo in a security setting should fail loudly at install time rather than be
silently ignored. ``config/macagent.example.toml`` documents every key.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .watcher import BACKENDS, WatcherMode

# Timeout chain for in-band (gate) watcher calls. Each stage must give up
# before the stage that waits on it, so a slow watcher degrades to the
# baseline instead of tripping Claude Code's own hook timeout:
#   watcher.timeout_seconds  <=  MAX_WATCHER_TIMEOUT_SECONDS (eventd stops waiting)
#   <  hook.SOCKET_TIMEOUT_SECONDS                            (hook stops waiting)
#   <  "timeout" in config/managed-settings.json              (Claude Code stops waiting)
# tests/test_timeouts.py enforces the ordering.
MAX_WATCHER_TIMEOUT_SECONDS = 6.0


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NotifyConfig:
    backend: str = "none"  # "none" | "ntfy"
    server: str = "https://ntfy.sh"
    topic: str = ""
    token_file: str = ""  # optional; path to a file holding an ntfy access token


@dataclass(frozen=True)
class HealthConfig:
    backup_max_age_hours: float = 26.0
    # true: FileVault must be on. false: must be off (auto-restart chosen over
    # encryption). Unset: report the state without judging it.
    expect_filevault: bool | None = None
    reminder_interval_hours: float = 6.0
    tmux_path: str = "/opt/homebrew/bin/tmux"
    tailscale_path: str = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    agent_session_name: str = "agent"


@dataclass(frozen=True)
class WatcherConfig:
    backend: str = "none"
    mode: WatcherMode = WatcherMode.OFF
    timeout_seconds: float = 5.0
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    agent_user: str = "agent"
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    watcher: WatcherConfig = field(default_factory=WatcherConfig)


def load_config(path: Path) -> Config:
    """Load config from ``path``. A missing file yields the defaults."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return parse_config(raw, source=str(path))


def parse_config(raw: dict[str, Any], source: str = "config") -> Config:
    _reject_unknown(raw, {"agent_user", "notify", "health", "watcher"}, source)
    agent_user = _get(raw, "agent_user", str, Config.agent_user, source)
    if not agent_user or agent_user == "root":
        raise ConfigError(f"{source}: agent_user must name a dedicated non-root account")
    return Config(
        agent_user=agent_user,
        notify=_parse_notify(_table(raw, "notify", source), f"{source} [notify]"),
        health=_parse_health(_table(raw, "health", source), f"{source} [health]"),
        watcher=_parse_watcher(_table(raw, "watcher", source), f"{source} [watcher]"),
    )


def _parse_notify(raw: dict[str, Any], where: str) -> NotifyConfig:
    _reject_unknown(raw, {"backend", "server", "topic", "token_file"}, where)
    d = NotifyConfig()
    cfg = NotifyConfig(
        backend=_get(raw, "backend", str, d.backend, where),
        server=_get(raw, "server", str, d.server, where).rstrip("/"),
        topic=_get(raw, "topic", str, d.topic, where),
        token_file=_get(raw, "token_file", str, d.token_file, where),
    )
    if cfg.backend not in {"none", "ntfy"}:
        raise ConfigError(f"{where}: backend must be 'none' or 'ntfy', got {cfg.backend!r}")
    if cfg.backend == "ntfy":
        if not cfg.topic:
            raise ConfigError(f"{where}: topic is required when backend = 'ntfy'")
        if not cfg.server.startswith("https://"):
            raise ConfigError(f"{where}: server must be an https:// URL")
    return cfg


def _parse_health(raw: dict[str, Any], where: str) -> HealthConfig:
    _reject_unknown(
        raw,
        {
            "backup_max_age_hours",
            "expect_filevault",
            "reminder_interval_hours",
            "tmux_path",
            "tailscale_path",
            "agent_session_name",
        },
        where,
    )
    d = HealthConfig()
    expect_filevault = raw.get("expect_filevault", d.expect_filevault)
    if expect_filevault is not None and not isinstance(expect_filevault, bool):
        raise ConfigError(f"{where}: expect_filevault must be true or false (or omitted)")
    cfg = HealthConfig(
        backup_max_age_hours=_get_number(raw, "backup_max_age_hours", d.backup_max_age_hours, where),
        expect_filevault=expect_filevault,
        reminder_interval_hours=_get_number(raw, "reminder_interval_hours", d.reminder_interval_hours, where),
        tmux_path=_get(raw, "tmux_path", str, d.tmux_path, where),
        tailscale_path=_get(raw, "tailscale_path", str, d.tailscale_path, where),
        agent_session_name=_get(raw, "agent_session_name", str, d.agent_session_name, where),
    )
    if cfg.backup_max_age_hours <= 0 or cfg.reminder_interval_hours <= 0:
        raise ConfigError(f"{where}: hour values must be positive")
    return cfg


def _parse_watcher(raw: dict[str, Any], where: str) -> WatcherConfig:
    _reject_unknown(raw, {"backend", "mode", "timeout_seconds", "options"}, where)
    d = WatcherConfig()
    backend = _get(raw, "backend", str, d.backend, where)
    if backend not in BACKENDS:
        known = ", ".join(sorted(BACKENDS))
        raise ConfigError(f"{where}: unknown backend {backend!r}; known backends: {known}")
    mode_raw = _get(raw, "mode", str, d.mode.value, where)
    try:
        mode = WatcherMode(mode_raw)
    except ValueError:
        valid = ", ".join(m.value for m in WatcherMode)
        raise ConfigError(f"{where}: mode must be one of: {valid}") from None
    if backend == "none" and mode is not WatcherMode.OFF:
        raise ConfigError(f"{where}: mode {mode.value!r} needs a backend; set backend or use mode = 'off'")
    timeout = _get_number(raw, "timeout_seconds", d.timeout_seconds, where)
    if not 0 < timeout <= MAX_WATCHER_TIMEOUT_SECONDS:
        raise ConfigError(f"{where}: timeout_seconds must be > 0 and <= {MAX_WATCHER_TIMEOUT_SECONDS}")
    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ConfigError(f"{where}: options must be a table")
    return WatcherConfig(backend=backend, mode=mode, timeout_seconds=timeout, options=options)


def _table(raw: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: [{key}] must be a table")
    return value


def _reject_unknown(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _get(raw: dict[str, Any], key: str, kind: type, default: Any, where: str) -> Any:
    value = raw.get(key, default)
    if not isinstance(value, kind):
        raise ConfigError(f"{where}: {key} must be a {kind.__name__}")
    return value


def _get_number(raw: dict[str, Any], key: str, default: float, where: str) -> float:
    value = raw.get(key, default)
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: {key} must be a number")
    return float(value)
