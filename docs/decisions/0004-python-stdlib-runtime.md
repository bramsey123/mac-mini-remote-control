# 0004: Python 3.12, standard library only, in a root-owned virtualenv

**Status:** accepted

## Context

The runtime (event daemon, hook, health checks) runs inside the security
boundary as root or `_agentwatch`, and the hook runs on every tool call. macOS
ships Python 3.9 only via the Command Line Tools; the official Anthropic SDK
(needed for the planned watcher backend) requires a newer Python, and
`tomllib` needs 3.11.

## Decision

- Install Homebrew's Python 3.12 and create a virtualenv at
  `/usr/local/lib/macagent/venv`, owned by root.
- The core package uses only the standard library. Watcher backends that need
  packages declare optional extras and import them lazily.
- Entry points run Python in isolated mode (`-I`) from root-owned wrappers, so
  environment variables, user site-packages and the current directory (all
  under the agent's control) can't change what code runs.
- No code reachable from the hook reads paths or settings from environment
  variables.

## Consequences

- Each dependency added to the core is a new piece of attack surface and needs
  a good reason (and a new decision record).
- The package is tested on 3.11–3.13 in CI; production runs 3.12.
