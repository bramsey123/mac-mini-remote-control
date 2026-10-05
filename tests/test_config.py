from __future__ import annotations

import pytest
from conftest import REPO_ROOT

from macagent.config import (
    MAX_WATCHER_TIMEOUT_SECONDS,
    Config,
    ConfigError,
    load_config,
    parse_config,
)
from macagent.watcher import WatcherMode


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_example_config_is_valid_and_matches_defaults():
    assert load_config(REPO_ROOT / "config/macagent.example.toml") == Config()


def test_defaults_are_safe():
    cfg = Config()
    assert cfg.watcher.mode is WatcherMode.OFF
    assert cfg.notify.backend == "none"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"agnet_user": "x"}, "unknown key"),
        ({"notify": {"backend": "ntfy"}}, "topic is required"),
        ({"notify": {"backend": "ntfy", "topic": "t", "server": "http://x"}}, "https"),
        ({"notify": {"backend": "pager"}}, "backend must be"),
        ({"notify": "ntfy"}, "must be a table"),
        ({"health": {"backup_max_age_hours": True}}, "must be a number"),
        ({"health": {"backup_max_age_hours": 0}}, "positive"),
        ({"health": {"expect_filevault": "yes"}}, "true or false"),
        ({"watcher": {"mode": "gate"}}, "needs a backend"),
        ({"watcher": {"mode": "sometimes"}}, "mode must be one of"),
        ({"watcher": {"backend": "skynet"}}, "unknown backend"),
        ({"watcher": {"timeout_seconds": MAX_WATCHER_TIMEOUT_SECONDS + 1}}, "timeout_seconds"),
        ({"watcher": {"timeout_seconds": 0}}, "timeout_seconds"),
        ({"watcher": {"options": "x"}}, "options must be a table"),
        ({"agent_user": "root"}, "non-root"),
        ({"agent_user": ""}, "non-root"),
    ],
)
def test_invalid_configs_are_rejected(raw, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_valid_ntfy_config_strips_trailing_slash():
    cfg = parse_config({"notify": {"backend": "ntfy", "topic": "abc", "server": "https://ntfy.example/"}})
    assert cfg.notify.server == "https://ntfy.example"


def test_invalid_toml_reports_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("agent_user = ")
    with pytest.raises(ConfigError, match=str(path)):
        load_config(path)
