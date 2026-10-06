# 0002: The hook and the watcher never return "allow"

**Status:** accepted

## Context

A Claude Code PreToolUse hook can answer allow, deny or ask, or say nothing.
"Allow" skips Claude Code's normal permission flow, including auto mode's
classifier. A watcher (or a bug in the hook) that could allow would be a way to
switch the baseline off, and a watcher can be fooled by text inside the very
actions it reviews.

## Decision

The hook only ever emits deny or ask, or nothing. The watcher interface has no
allow decision (`GateDecision` is defer/ask/deny). Any other value from the
event daemon, including "allow", is treated as "no opinion". Managed settings
ship no allow rules.

## Consequences

- The guard layers can only make the system stricter than the baseline, never
  looser. A broken or manipulated watcher can at worst cause false blocks.
- Speed-ups that would pre-approve actions are out of scope for this layer; if
  ever wanted, they belong in Claude Code's own permission rules, decided by a human.
- Tests assert there is no allow decision and that "allow" responses are dropped.
