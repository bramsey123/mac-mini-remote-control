"""Building blocks for health checks: results, context and the check registry."""

from __future__ import annotations

import datetime as dt
import enum
import pwd
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..paths import Paths


class Status(enum.StrEnum):
    OK = "ok"
    INFO = "info"  # neutral fact worth showing (e.g. "watcher not enabled")
    WARN = "warn"  # degraded; the agent is still contained
    FAIL = "fail"  # a protection is missing or broken
    ERROR = "error"  # the check itself could not run

    @property
    def rank(self) -> int:
        return {"ok": 0, "info": 0, "warn": 1, "fail": 2, "error": 2}[self.value]


@dataclass(frozen=True)
class Outcome:
    """What a check function returns; the runner attaches the check id."""

    status: Status
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: Status
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Runs system commands. Tests substitute a fake."""

    def run(self, argv: Sequence[str], timeout: float = 15.0) -> CommandResult:
        try:
            proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError:
            return CommandResult(127, "", f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"{argv[0]}: timed out after {timeout}s")
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def lookup_home(user: str) -> Path | None:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return None


@dataclass
class HealthContext:
    config: Config
    paths: Paths
    runner: CommandRunner = field(default_factory=CommandRunner)
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now().astimezone()
    # uid that must own protected files; 0 in production, the test user in tests.
    trusted_uid: int = 0
    # Resolves an account name to its home directory, or None if it doesn't exist.
    home_of: Callable[[str], Path | None] = lookup_home


CheckFn = Callable[[HealthContext], Outcome]
REGISTRY: list[tuple[str, CheckFn]] = []


def check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    """Register a health check. Order of registration is display order."""

    def register(fn: CheckFn) -> CheckFn:
        if any(existing == check_id for existing, _ in REGISTRY):
            raise ValueError(f"duplicate check id {check_id!r}")
        REGISTRY.append((check_id, fn))
        return fn

    return register
