"""Install locations — the single source of truth for where things live.

``setup/lib/common.sh`` mirrors these values; ``tests/test_paths.py`` keeps the
two in sync.

Security note: paths are never read from environment variables. The hook runs
inside the agent's process tree, so anything in the environment is under the
agent's control. Tests and local development rebase paths explicitly with
:meth:`Paths.under` (exposed on the CLI as ``--root``, which is safe because the
hook's command line is fixed by root-owned managed settings).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

LIB_DIR = Path("/usr/local/lib/macagent")
SUPPORT_DIR = Path("/Library/Application Support/MacAgent")
LOG_DIR = Path("/Library/Logs/MacAgent")
MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")


@dataclass(frozen=True)
class Paths:
    lib_dir: Path = LIB_DIR
    support_dir: Path = SUPPORT_DIR
    log_dir: Path = LOG_DIR
    managed_settings: Path = MANAGED_SETTINGS

    @classmethod
    def under(cls, root: Path | str) -> Paths:
        """Rebase every location under ``root`` (tests and local development)."""
        root = Path(root)

        def rebase(p: Path) -> Path:
            return root / p.relative_to(p.anchor)

        return cls(
            lib_dir=rebase(LIB_DIR),
            support_dir=rebase(SUPPORT_DIR),
            log_dir=rebase(LOG_DIR),
            managed_settings=rebase(MANAGED_SETTINGS),
        )

    @property
    def hook_command(self) -> Path:
        return self.lib_dir / "bin" / "macagent-hook"

    @property
    def config_file(self) -> Path:
        return self.support_dir / "config.toml"

    @property
    def secrets_dir(self) -> Path:
        return self.support_dir / "secrets"

    @property
    def run_dir(self) -> Path:
        return self.support_dir / "run"

    @property
    def socket_path(self) -> Path:
        return self.run_dir / "eventd.sock"

    @property
    def state_dir(self) -> Path:
        return self.support_dir / "state"

    @property
    def health_state_file(self) -> Path:
        return self.state_dir / "health.json"

    @property
    def event_log(self) -> Path:
        return self.log_dir / "events.jsonl"


DEFAULT_PATHS = Paths()
