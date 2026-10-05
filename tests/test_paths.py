"""Paths are defined in Python and mirrored in shell and launchd files; keep them in sync."""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

from conftest import REPO_ROOT

from macagent.paths import DEFAULT_PATHS, Paths


def _shell_constants() -> dict[str, str]:
    text = (REPO_ROOT / "setup/lib/common.sh").read_text()
    return dict(re.findall(r'^(MACAGENT_[A-Z_]+)="([^"]*)"', text, flags=re.MULTILINE))


def test_shell_constants_match_python():
    consts = _shell_constants()
    assert consts["MACAGENT_LIB_DIR"] == str(DEFAULT_PATHS.lib_dir)
    assert consts["MACAGENT_SUPPORT_DIR"] == str(DEFAULT_PATHS.support_dir)
    assert consts["MACAGENT_LOG_DIR"] == str(DEFAULT_PATHS.log_dir)
    assert consts["MACAGENT_MANAGED_SETTINGS"] == str(DEFAULT_PATHS.managed_settings)


def test_launchd_plists_use_installed_entry_point_and_log_dir():
    labels = _shell_constants()["MACAGENT_LAUNCHD_LABELS"].split()
    for label in labels:
        plist = plistlib.loads((REPO_ROOT / f"config/launchd/{label}.plist").read_bytes())
        assert plist["Label"] == label
        assert plist["ProgramArguments"][0] == str(DEFAULT_PATHS.lib_dir / "bin" / "macagent")
        for key in ("StandardOutPath", "StandardErrorPath"):
            assert Path(plist[key]).parent == DEFAULT_PATHS.log_dir


def test_eventd_runs_as_service_account():
    plist = plistlib.loads((REPO_ROOT / "config/launchd/local.macagent.eventd.plist").read_bytes())
    assert plist["UserName"] == _shell_constants()["MACAGENT_SERVICE_USER"]


def test_wrappers_use_isolated_venv_python():
    venv_python = str(DEFAULT_PATHS.lib_dir / "venv" / "bin" / "python")
    for name in ("macagent", "macagent-hook"):
        text = (REPO_ROOT / "bin" / name).read_text()
        assert f"exec {venv_python} -I -m macagent" in text, name


def test_hook_wrapper_ignores_arguments():
    # The hook's behavior must be fixed by root-owned files, not by its caller.
    text = (REPO_ROOT / "bin/macagent-hook").read_text()
    assert '"$@"' not in text


def test_under_rebases_every_path(tmp_path):
    rebased = Paths.under(tmp_path)
    for name in ("lib_dir", "support_dir", "log_dir", "managed_settings"):
        assert str(getattr(rebased, name)).startswith(str(tmp_path))
    assert rebased.socket_path == tmp_path / "Library/Application Support/MacAgent/run/eventd.sock"


def test_production_socket_path_fits_sun_path():
    assert len(str(DEFAULT_PATHS.socket_path).encode()) < 104
