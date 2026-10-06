# CLAUDE.md

Guidance for agents (and people) working on this repository.

## What this is

Setup and runtime for running a Claude Code agent on a dedicated Mac mini,
contained by macOS account isolation, locked Claude Code settings, scoped
secrets, monitoring and backups, with an optional AI watcher. Read
`docs/architecture.md` before changing anything structural, and
`docs/threat-model.md` before changing anything security-related.

Note that this repo is *about* an agent machine; it is usually developed on a
different machine (or in the cloud). Code here must never assume it is running
on the Mini unless it's in `setup/` or a health check.

## Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                                   # all tests (Linux or macOS)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
shellcheck -x -s bash setup/install.sh setup/lib/common.sh setup/steps/*.sh bin/agent-session
shellcheck -s sh bin/macagent bin/macagent-hook
python -m macagent --root /tmp/x <command>         # run any CLI command against a scratch root
```

CI runs all of the above, plus a full `setup/install.sh --dry-run` on macOS
with the system bash 3.2.

## Invariants — do not break these

Each is backed by a decision record in `docs/decisions/` and, where possible, a
test. Changing one requires a new decision record in the same PR.

1. **The agent account is never an admin** and never gets sudo or write access
   to root-owned paths. (ADR 0001)
2. **The hook and the watcher never return "allow".** `GateDecision` has no
   allow; the hook drops anything other than ask/deny. No allow rules in
   managed settings. (ADR 0002)
3. **Every watcher / hook / eventd failure falls back to the baseline and is
   recorded and surfaced.** Never block the agent indefinitely; never fail
   silently. (ADR 0003)
4. **Timeout ordering:** watcher ≤ 6s < hook socket 8s < managed-settings hook
   timeout 10s. (`tests/test_managed_settings.py`)
5. **No paths or settings from environment variables** in code reachable from
   the hook; wrappers run Python with `-I`. (ADR 0004)
6. **The core package is stdlib-only.** Backends add optional extras and import
   lazily. (ADR 0004)
7. **Only eventd writes the event log**; `record` requests are accepted only
   from trusted uids; tool events only via `tool_event`. (ADR 0005)
8. **Paths are defined once** in `src/macagent/paths.py` and mirrored in
   `setup/lib/common.sh`, the launchd plists and `bin/` wrappers.
   `tests/test_paths.py` keeps them in sync.
9. **Installed files get explicit owners and modes** in setup; the
   `install-integrity` check must pass on a fresh install.

## Conventions

- **Python:** 3.11+ syntax, `from __future__ import annotations`, dataclasses
  for values, `StrEnum` for enums, no I/O in value types. Lines ≤ 110.
- **Shell (`setup/`, `bin/agent-session`):** must run on macOS `/bin/bash` 3.2
  (no associative arrays, `${var,,}`, `mapfile`). Every change goes through
  `run`/`srun`/`as_agent` so `--dry-run` works, and every step is idempotent.
  One step per file in `setup/steps/`, defining `step_<name>`.
- **Health checks** report facts and never repair. Register with
  `@check("id")` in `src/macagent/health/checks.py`, return an `Outcome`,
  add tests with `FakeRunner`. FAIL = a protection is missing; WARN =
  degraded but contained; INFO = neutral fact.
- **Events:** add new types to `EventType`; consumers must tolerate unknown
  types; bump `SCHEMA_VERSION` for breaking changes to `data`.
- **Docs move with code.** If behavior in `docs/` changes, update the doc in
  the same PR.

## Common tasks

- *Add a health check:* `checks.py` + tests in `tests/test_health.py` + a row
  in `docs/architecture.md` if it covers a new failure mode.
- *Add a watcher backend:* follow "Adding a backend" in `docs/watcher.md`.
- *Add a setup step:* new `setup/steps/<name>.sh`, add the name to `STEPS` in
  `setup/install.sh`, document it in `docs/setup-guide.md`.
- *Change managed settings:* edit `config/managed-settings.json`; if you change
  a required value, update `REQUIRED_MANAGED_SETTINGS` and write a decision record.
