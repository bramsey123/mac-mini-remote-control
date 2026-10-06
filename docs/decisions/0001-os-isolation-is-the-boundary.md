# 0001: The macOS account boundary is the security boundary

**Status:** accepted

## Context

The agent needs broad power: run anything, install tools, use the network. It
also reads content written by strangers, which can steer it, and it makes
mistakes. Controls inside the agent's own account (Claude Code settings,
permission rules, hooks, auto mode) are enforced by software running in that
account. Claude Code itself is installed per user in the agent's home
directory, so code with the agent's privileges could change it.

## Decision

The protection that must hold no matter what is the macOS account boundary:
the agent runs as a standard user with no admin rights, and everything that
protects the human (their data, the backups, the monitoring, the controls'
configuration) is owned by other accounts. Controls inside the agent's account
are layered on top, as guard rails against mistakes, and are described that way.

## Consequences

- Setup must create and maintain the account split, and the health check must
  verify it continuously (`agent-account`, `install-integrity`, `managed-settings`).
- Nothing in this repo may give the agent account admin rights, sudo, or write
  access to root-owned paths.
- Docs must not present in-account controls as hard guarantees.
- Credentials given to the agent are, by definition, exposed to it: they must
  be scoped and revocable.
