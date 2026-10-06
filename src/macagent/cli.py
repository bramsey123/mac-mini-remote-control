"""``macagent`` command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .paths import DEFAULT_PATHS, Paths

_STATUS_LABEL = {"ok": " OK ", "info": "INFO", "warn": "WARN", "fail": "FAIL", "error": "ERR "}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    paths = Paths.under(args.root) if args.root else DEFAULT_PATHS
    return int(args.func(args, paths) or 0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macagent", description=__doc__)
    parser.add_argument(
        "--root", type=Path, help="rebase all install paths under DIR (development and tests)"
    )
    parser.add_argument("--version", action="version", version=f"macagent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hook", help="Claude Code PreToolUse hook (reads the event on stdin)")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("eventd", help="run the event daemon (normally started by launchd)")
    p.set_defaults(func=cmd_eventd)

    p = sub.add_parser("check", help="run health checks")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--notify", action="store_true", help="remember state and push notifications on changes")
    p.add_argument("--only", nargs="+", metavar="CHECK", help="run only these checks")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("events", help="show recent events from the event log")
    p.add_argument("-n", "--last", type=int, default=50, help="how many events (default 50)")
    p.add_argument("--type", dest="types", action="append", help="only this event type (repeatable)")
    p.add_argument("--json", action="store_true", help="print raw JSON lines")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("config-check", help="validate a config file")
    p.add_argument("file", nargs="?", type=Path, help="defaults to the installed config")
    p.set_defaults(func=cmd_config_check)

    p = sub.add_parser("notify-test", help="send a test notification")
    p.set_defaults(func=cmd_notify_test)
    return parser


def cmd_hook(args: argparse.Namespace, paths: Paths) -> int:
    from . import hook

    # The hook must never break the agent (ADR 0003).
    with contextlib.suppress(Exception):
        hook.run(sys.stdin, sys.stdout, paths.socket_path)
    return 0


def cmd_eventd(args: argparse.Namespace, paths: Paths) -> int:
    from .eventd import serve

    config = _load(paths)
    if config is None:
        return 2
    serve(config, paths)
    return 0


def cmd_check(args: argparse.Namespace, paths: Paths) -> int:
    from .health import HealthContext, Status, run_and_notify, run_checks
    from .notify import NotifyError, create_notifier

    if args.notify and args.only:
        print("--notify runs every check; it can't be combined with --only", file=sys.stderr)
        return 2
    config = _load(paths)
    if config is None:
        return 2
    ctx = HealthContext(config=config, paths=paths, trusted_uid=0 if not args.root else os.getuid())
    if args.notify:
        try:
            results, _ = run_and_notify(ctx, create_notifier(config.notify))
        except NotifyError as exc:
            print(f"warning: notification failed, will retry next run: {exc}", file=sys.stderr)
            results = run_checks(ctx)
    else:
        results = run_checks(ctx, only=args.only)

    if args.json:
        print(json.dumps([{**vars(r), "status": r.status.value} for r in results], indent=2))
    else:
        width = max((len(r.check_id) for r in results), default=0)
        for r in results:
            print(f"[{_STATUS_LABEL[r.status.value]}] {r.check_id:<{width}}  {r.summary}")
            if r.detail and r.status is not Status.OK:
                print(f"       {'':<{width}}  {r.detail}")
        if os.geteuid() != 0 and not args.root:
            print("\nnote: some checks need root; run with sudo for accurate results", file=sys.stderr)
    return 1 if any(r.status.rank >= Status.FAIL.rank for r in results) else 0


def cmd_events(args: argparse.Namespace, paths: Paths) -> int:
    from .eventlog import read_events

    if not os.access(paths.event_log, os.R_OK) and paths.event_log.exists():
        print(f"cannot read {paths.event_log}; run as an admin account", file=sys.stderr)
        return 1
    for event in read_events(paths.event_log, last=None if args.types else args.last):
        if args.types and event.type not in args.types:
            continue
        print(json.dumps(event.to_dict()) if args.json else format_event(event.to_dict()))
    return 0


def cmd_config_check(args: argparse.Namespace, paths: Paths) -> int:
    path = args.file or paths.config_file
    try:
        load_config(path)
    except ConfigError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {path}")
    return 0


def cmd_notify_test(args: argparse.Namespace, paths: Paths) -> int:
    from .notify import NotifyError, create_notifier

    config = _load(paths)
    if config is None:
        return 2
    if config.notify.backend == "none":
        print("notifications are disabled ([notify] backend = 'none')", file=sys.stderr)
        return 1
    try:
        notifier = create_notifier(config.notify)
        notifier.send("MacAgent test", "Notifications are working.", tags=("white_check_mark",))
    except (NotifyError, OSError) as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    print("sent")
    return 0


def format_event(event: dict) -> str:
    """One readable line per event, for skimming on a phone."""
    data = event.get("data") or {}
    kind = event.get("type", "?")
    if kind == "tool.pre":
        tool_input = data.get("tool_input") or {}
        target = next(
            (tool_input[k] for k in ("command", "file_path", "url", "pattern", "path") if k in tool_input),
            "",
        )
        summary = f"{data.get('tool_name', '?')}: {_one_line(str(target))}"
    elif kind == "watcher.verdict":
        summary = f"{data.get('decision')} ({data.get('outcome')}) {data.get('reason', '')}"
    elif kind == "watcher.alert":
        summary = f"[{data.get('severity')}] {data.get('title')}"
    elif kind == "health.result":
        summary = f"{data.get('check')}: {data.get('status')} — {data.get('summary')}"
    else:
        summary = _one_line(json.dumps(data, sort_keys=True))
    return f"{event.get('ts', '?')}  {kind:<16} {summary}"


def _one_line(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _load(paths: Paths):
    try:
        return load_config(paths.config_file)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":
    sys.exit(main())
