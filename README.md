# mac-mini-remote-control

Run a Claude Code agent on a dedicated Mac mini: it uses the machine's full
resources and your home internet connection, you drive it from your phone, and
it's contained so that a confused or manipulated agent can't take your
accounts, data or backups down with it.

> **Status:** v0.1 — foundation. Setup, containment, monitoring and the AI
> watcher *slot* are built and tested; the first real AI watcher backend is the
> next milestone ([docs/roadmap.md](docs/roadmap.md)).

## How it's protected

Five layers, each covering for the others' blind spots. The lower layers are
enforced by macOS itself and don't depend on any software behaving well.

| # | Layer | What it does | Enforced by |
|---|-------|--------------|-------------|
| 1 | **Account isolation** | The agent runs as its own standard (non-admin) macOS user. It can't read your files, Keychain or browser sessions, can't use `sudo`, and can't touch the backups or the monitoring. | macOS permissions |
| 2 | **Locked Claude Code settings** | Managed settings the agent can't edit: auto mode on (an AI classifier reviews each action), bypass mode off, the agent can't add its own permission rules or hooks. | Root-owned managed settings |
| 3 | **Scoped secrets** | The agent only holds credentials made *for it*: a 1Password service account limited to one vault, plus revocable, spend-limited API keys. | 1Password + provider dashboards |
| 4 | **Monitoring & recovery** | Every tool call is recorded in a log the agent can't rewrite; health checks verify layers 1–3 every 15 minutes and push to your phone when something changes; Time Machine keeps backups out of the agent's reach. | `_agentwatch` service account, root LaunchDaemons |
| 5 | **Remote access** | Tailscale (no open ports), SSH, and `claude remote-control` so the session appears in the Claude app on your phone. | Tailscale |

The **AI watcher** is a first-class part of layer 4 with a defined slot in the
event flow: when enabled it watches the event stream and can raise alerts or
veto tool calls. Everything works without it, and if it fails, the agent
carries on under layers 1–3 and you get told. See
[docs/watcher.md](docs/watcher.md).

## Quick start

On the Mac mini, from **your own admin account**:

```bash
git clone https://github.com/bramsey123/mac-mini-remote-control.git
cd mac-mini-remote-control
./setup/install.sh --dry-run   # see what will change
./setup/install.sh             # do it; safe to re-run
```

Then finish the manual steps it prints (Tailscale, notifications, logging the
agent in once). The full walkthrough, including the decisions to make first, is
in [docs/setup-guide.md](docs/setup-guide.md).

Day to day, from your phone:

- **Talk to the agent:** open the session in the Claude app (Remote Control).
- **See what it did:** `ssh mini` (over Tailscale), then `macagent events -n 30`.
- **Check the protections:** `sudo macagent check`.
- **Restart the session:** `sudo -iu agent agent-session start`.

## Repository layout

```
bin/            wrappers installed to /usr/local/lib/macagent/bin (macagent, hook, agent-session)
config/         managed settings, config template, launchd services, agent.env template
docs/           architecture, threat model, setup guide, watcher contract, decisions (ADRs)
setup/          idempotent macOS setup: install.sh + one script per step
src/macagent/   the Python package: event daemon, hook, health checks, watcher slot
tests/          pytest suite (runs on Linux and macOS)
```

## Documentation

- [Architecture](docs/architecture.md) — components, trust boundaries, event flow, failure modes
- [Threat model](docs/threat-model.md) — what each layer protects against, and what it doesn't
- [Setup guide](docs/setup-guide.md) — step-by-step, including the manual parts
- [AI watcher contract](docs/watcher.md) — how the watcher plugs in and what it may do
- [Roadmap](docs/roadmap.md)
- [Decisions](docs/decisions/) — why things are the way they are

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
shellcheck -x -s bash setup/install.sh setup/lib/common.sh setup/steps/*.sh bin/agent-session
```

Working on this repo with an agent? Start with [CLAUDE.md](CLAUDE.md).
