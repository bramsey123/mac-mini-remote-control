# 0003: The watcher is optional; every failure falls back to the baseline, loudly

**Status:** accepted

## Context

The AI watcher is expected to become a key component, but the system has to be
usable before it exists and while it's broken. Two bad outcomes to avoid:
*fail closed* (a dead watcher blocks the agent, so people switch it off) and
*fail silent* (a dead watcher looks like a quiet one, so people trust an
absent control). Claude Code itself lets a tool call proceed when a hook
crashes or times out.

## Decision

- The watcher has a permanent slot (interface, modes, registry, config,
  health check) and ships as `NullWatcher`. Mode `off` is the default.
- Every watcher, hook or event-daemon failure resolves to "no opinion": the
  tool call proceeds under the baseline (account isolation, locked settings,
  auto mode).
- Every such failure is recorded and surfaced: `watcher.verdict` events with
  outcome `failed`/`timed out`, rate-limited push notifications, and health
  checks (`eventd`, `watcher`).
- Timeouts are ordered so the watcher gives up first, then the hook, then
  Claude Code (enforced by a test).

## Consequences

- The baseline (layers 1–3) must be good enough on its own; the watcher adds
  coverage on top of it.
- In gate mode, a slow watcher lets actions through. That is accepted in
  exchange for never blocking the agent indefinitely, and it is visible.
- Watcher backends are judged in observe mode before they get gate powers.
