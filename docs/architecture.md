# Architecture

## Goals

1. The agent can use the whole Mac (CPU, disk, installed tools, home network) to build things.
2. You control it from your phone.
3. A compromised or confused agent session can't reach your accounts, your
   data, the backups or the monitoring — and you hear about it when something
   is off.
4. Nothing depends on you approving routine actions. Human approval is kept
   for the few actions that can't be undone.

## Accounts: the trust boundaries

Everything rests on which macOS account owns what.

| Account | Who | Can | Cannot |
|---------|-----|-----|--------|
| *you* (admin) | You, over SSH or at the console | Everything via `sudo`; read the event log (group `admin`) | — |
| `agent` (standard) | Claude Code and everything it runs | Its own home folder, the network, installed tools | `sudo`; your files; system files; the event log; MacAgent config and secrets; stop the services |
| `_agentwatch` (hidden service account) | Event daemon + AI watcher | Write the event log; read watcher/notification secrets | Log in; anything else |
| `root` | LaunchDaemons, health check | Everything | — |

The agent's isolation is enforced by the operating system, so it holds no
matter what code runs inside the agent account. Everything above it (Claude
Code settings, auto mode, the hook) runs *inside* the agent's account and is
best understood as guard rails that stop mistakes, not walls (see
[threat-model.md](threat-model.md)).

## Components

```
                         ┌──────────────────────── agent account ───────────────────────┐
  phone ──Tailscale──▶   │  tmux ─▶ claude remote-control ─▶ tool call                 │
  (Claude app / SSH)     │                    │ managed settings: auto mode, no bypass │
                         │                    ▼                                       │
                         │        PreToolUse hook (macagent-hook)  ── untrusted ──    │
                         └────────────────────┼───────────────────────────────────────┘
                                              │ Unix socket (one JSON request/response)
                         ┌────────────────────▼──────────── _agentwatch ─────────────────┐
                         │  eventd ──▶ events.jsonl (append-only to the agent)           │
                         │     │                                                        │
                         │     └──▶ AI watcher slot: NullWatcher today                   │
                         │            observe: sees every event, raises alerts          │
                         │            gate:    asked before each tool call (ask/deny)   │
                         └──────────────────────────────┬─────────────────────────────────┘
                                                        │ alerts
  root: healthcheck (every 15 min) ── verifies layers ──┴──▶ notifier ──▶ your phone (ntfy)
```

| Component | Code | Runs as | Purpose |
|-----------|------|---------|---------|
| Hook | `src/macagent/hook.py` | agent | Forwards each tool call to eventd; relays a watcher's ask/deny |
| Event daemon | `src/macagent/eventd.py` | `_agentwatch` (LaunchDaemon) | Records events; hosts the watcher |
| Event log | `src/macagent/eventlog.py` | `_agentwatch` | Append-only JSONL, rotated by size |
| Event schema | `src/macagent/events.py` | — | The shared language of all components |
| Watcher slot | `src/macagent/watcher/` | `_agentwatch` | Interface, modes, registry, `NullWatcher` |
| Health checks | `src/macagent/health/` | root (LaunchDaemon, 15 min) | Verify the protections; notify on change |
| Notifier | `src/macagent/notify.py` | root / `_agentwatch` | Push notifications, rate-limited |
| Agent session | `bin/agent-session` | agent | tmux + `claude remote-control`, restart with backoff |
| Setup | `setup/` | you (sudo as needed) | Idempotent install of all of the above |

## The event stream is the backbone

Every component produces or consumes `Event`s (`type`, `source`, `data`, `id`,
`ts`, schema version `v`). Producers today: the hook (`tool.pre`), eventd
(`eventd.start/stop`), the watcher (`watcher.verdict/alert/dropped`) and the
health check (`health.result`). Consumers: the log, `macagent events`, and the
watcher.

This is what makes the watcher first-class without making it a dependency: it
is just another consumer of a stream that exists anyway. New consumers (log
shipping, a dashboard, offline replay to evaluate a watcher) plug in the same
way. See [ADR 0005](decisions/0005-event-stream-backbone.md).

Rules for the stream:
- Only eventd writes the log. Root-level producers send `record` requests,
  which eventd accepts only from root or itself (checked by the peer's uid).
- Tool events arrive only via `tool_event` and carry the sending process's uid.
- Consumers must ignore event types they don't know.
- Breaking changes to an event's `data` bump `SCHEMA_VERSION`.

## Failure modes

| What fails | What happens | How you find out |
|------------|--------------|------------------|
| eventd down / socket gone | Hook exits silently; Claude Code continues under auto mode + isolation. Tool calls are not recorded. | `eventd` health check → FAIL → phone |
| Watcher slow (> timeout) | Call proceeds on the baseline; `watcher.verdict` records `timed out` | Rate-limited push; `watcher` check → WARN |
| Watcher crashes / returns garbage | Same as slow; recorded as `failed` | Same |
| Hook crashes or times out | Claude Code continues (documented hook behavior) | Missing `tool.pre` events; eventd still up |
| Notifier unreachable | Health state isn't saved, so the next run retries the same alert | `healthcheck.err.log` |
| Agent made admin / settings edited / files made writable | Health check FAIL | Push within 15 minutes |
| Mac rebooted | Services restart (launchd); the agent session must be restarted by hand for now | `agent-session` check → WARN |

Pattern: **every failure falls back to the baseline (layers 1–3) and is
reported.** Nothing fails silently, and nothing fails by blocking the agent
indefinitely. See [ADR 0003](decisions/0003-watcher-optional-fail-to-baseline.md).

## Timeouts

A slow watcher must give up before the hook does, and the hook before Claude
Code does — otherwise Claude Code's own timeout fires first and the event
isn't recorded at all:

`watcher.timeout_seconds (≤ 6)  <  hook socket timeout (8)  <  managed-settings hook timeout (10)`

`tests/test_managed_settings.py` enforces this ordering.

## File layout on the Mac

| Path | Owner | Mode | Contents |
|------|-------|------|----------|
| `/usr/local/lib/macagent/` | root | 755, no group/other write | venv, `bin/` wrappers |
| `/Library/Application Support/ClaudeCode/managed-settings.json` | root | 644 | Claude Code baseline (applies to **every** account) |
| `/Library/Application Support/MacAgent/config.toml` | root:`_agentwatch` | 640 | Config (agent can't read) |
| `/Library/Application Support/MacAgent/secrets/` | root:`_agentwatch` | 750 | Notification / watcher credentials |
| `/Library/Application Support/MacAgent/run/eventd.sock` | `_agentwatch` | 666 | Socket (any local account may submit events) |
| `/Library/Application Support/MacAgent/state/` | root | 700 | Health check state |
| `/Library/Logs/MacAgent/` | `_agentwatch`:admin | 750 / files 640 | Event log, service logs |
| `/Library/LaunchDaemons/local.macagent.*.plist` | root | 644 | Services |
| `~agent/.config/macagent/agent.env` | agent | 600 | The agent's own secrets |
