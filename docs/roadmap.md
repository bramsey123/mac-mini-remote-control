# Roadmap

Ordered by value. Each item should land as its own PR with tests and, where it
touches an invariant, a decision record.

## Next: first AI watcher backend

The slot, modes, failure handling and tests exist (see [watcher.md](watcher.md)).
To add:

1. `macagent replay <log>`: run a backend over a recorded event log offline
   and report what it would have alerted on or blocked. Build this first, so
   every backend (and every prompt change) can be measured before it runs live.
2. A Claude-based backend using the official Anthropic Python SDK, as an
   optional install extra. Observe mode first; gate mode once replay shows an
   acceptable false-alarm rate.
3. Per-session context for the watcher (what was the agent asked to do?) so it
   can judge whether actions fit the task.

## Soon

- **Session autostart after reboot.** A LaunchDaemon that starts
  `agent-session` as the agent at boot. Needs verifying on the hardware first:
  whether Claude Code's login survives a headless start, and how the login
  keychain behaves without a GUI session.
- **Uninstall script** mirroring `install.sh`.
- **More event sources:** `PostToolUse` (outcomes, not just intentions) and
  session start/stop, so the log shows full sessions.
- **SSH hardening step:** key-only auth, allowed users, Tailscale-only listening.

## Later

- **Outbound network visibility.** Record which hosts the agent account
  connects to (e.g., via a host firewall such as LuLu, or Tailscale's network
  logs) and feed it into the event stream for the watcher.
- **Off-machine log copy**, so even someone with root on the Mini can't erase history.
- **Weekly digest** notification: what the agent did, health over the week.
- **Agent workspace snapshots** (APFS snapshots or a git-based checkpoint) for
  faster rollback of the agent's own projects than a full Time Machine restore.
