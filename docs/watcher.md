# The AI watcher

The watcher is an AI component that reviews what the agent does. It's a
first-class part of the architecture — it has a defined place in the event
flow, a stable interface, its own privilege level and tested failure behavior
— but nothing depends on it. With no watcher, or a broken one, the system runs
on the baseline (account isolation, locked settings, auto mode, scoped
secrets) and tells you the watcher is missing.

Code: `src/macagent/watcher/`. Contract tests: `tests/test_watcher_contract.py`,
`tests/test_eventd.py`.

## Why it's shaped this way

- **It sits beside the baseline, not under it.** Claude Code's auto mode
  already reviews each action with an AI classifier. The watcher adds
  something auto mode can't: it runs *outside* the agent's account and sees
  the whole history, so it can spot patterns across many actions and keep
  watching even if the agent's own settings were tampered with.
- **It can't allow.** If a watcher could say "allow", a fooled or broken
  watcher could switch off the baseline. It can only defer, ask or deny
  ([ADR 0002](decisions/0002-never-allow.md)).
- **It fails toward the baseline, loudly.** A slow or crashing watcher must
  neither block the agent nor fail silently
  ([ADR 0003](decisions/0003-watcher-optional-fail-to-baseline.md)).

## Modes

Set in `config.toml` under `[watcher]`:

| Mode | Sees | Can | Cost of being wrong |
|------|------|-----|---------------------|
| `off` | nothing | nothing | — |
| `observe` | every event, after it's recorded | raise alerts | a false alarm on your phone |
| `gate` | as `observe`, plus each tool call *before* it runs | answer `defer`, `ask` or `deny` | a blocked or paused action |

**Roll out in that order.** Run a new backend in `observe` mode first and read
its alerts for a week or two. Move to `gate` only once you know how often it's
wrong — a gate that cries wolf brings back the approval fatigue this whole
design exists to avoid.

## The interface

```python
class Watcher(ABC):
    name: str

    def review(self, event: Event) -> GateVerdict: ...  # gate mode, in-band
    def observe(self, event: Event) -> Sequence[Alert]: ...  # observe + gate, out-of-band
    def close(self) -> None: ...  # optional
```

- `review` receives `tool.pre` events: tool name, tool input (long values
  truncated, with a hash), working directory, permission mode, session id
  and the sending uid. Return `GateVerdict(GateDecision.DENY, "reason")`,
  `...ASK...` or `GateVerdict.defer()`. The reason is shown to the agent (for
  deny) or to you (for ask).
- `observe` receives every recorded event except the watcher's own output.
  Keep state across calls (e.g., a rolling window) to spot patterns. Return
  `Alert(severity, title, detail, event_ids)`; `critical` alerts push at
  urgent priority.

## What the platform guarantees a backend

- `review` runs in a worker thread with a hard timeout (`timeout_seconds`,
  max 6). Late answers are discarded.
- Exceptions, timeouts and wrong return types become a `defer`, are recorded
  as `watcher.verdict` with outcome `failed` / `timed out`, and push a
  (rate-limited) notification.
- `observe` runs on a single worker thread fed by a bounded queue. If the
  watcher falls behind, events are dropped and counted, never blocking
  recording.
- The watcher never observes its own output.
- It runs as `_agentwatch`: credentials in
  `/Library/Application Support/MacAgent/secrets/` are readable by it and not
  by the agent.
- Health check `watcher` reports its status, failure counts and any mismatch
  between configured and running backend.

## What a backend must do

- Be thread-safe (`review` is called concurrently).
- Answer `review` well inside the timeout; do slow analysis in `observe`.
- Treat everything inside events as **data**: tool inputs can contain text
  written by whoever wrote the content the agent read. Never follow
  instructions found there, and never let them change the watcher's own
  configuration.
- Have no side effects on the machine other than returning verdicts and alerts.
- Read credentials from `options` (config) or files in the secrets directory,
  never from the environment of the hook.

## Adding a backend

1. Create `src/macagent/watcher/<name>.py` with a `Watcher` subclass.
2. Register a factory in `BACKENDS` (`src/macagent/watcher/__init__.py`).
   Import heavy dependencies **inside** the factory so the core stays
   dependency-free.
3. If it needs packages, add an optional extra in `pyproject.toml`
   (e.g. `watcher-claude = [...]`) and teach `setup/steps/runtime.sh` to
   install it when configured.
4. Document its `[watcher.options]` keys in `config/macagent.example.toml`.
5. Add tests: run it through the existing contract tests, plus its own.
6. Add a decision record if it changes any invariant.

## First backend (planned)

A Claude-based watcher using the official Anthropic Python SDK:

- **observe:** keeps a rolling window of recent events per session and asks a
  Claude model to flag activity that doesn't fit the session's apparent task
  or that touches the categories in [threat-model.md](threat-model.md); alerts
  include the event ids so you can look them up with `macagent events`.
- **gate:** a fast, low-effort review of the single proposed action, returning
  structured output (decision + reason).
- **evaluation before enabling:** a `macagent replay` command that runs a
  backend over a recorded event log offline, so you can measure how often it
  would have alerted or blocked before turning it on.

See [roadmap.md](roadmap.md).
