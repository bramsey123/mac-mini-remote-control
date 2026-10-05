# 0005: One event stream connects every component

**Status:** accepted

## Context

Several components need to know what happened: the human reviewing activity
from a phone, the health check, notifications, and the AI watcher (which needs
history, not just single actions). Wiring each producer to each consumer would
make every new consumer a change to every producer.

## Decision

All components produce and consume `Event`s with one schema
(`src/macagent/events.py`). The event daemon, running as `_agentwatch`, is the
only writer of the log. Tool events come in through the hook; trusted
producers (root, eventd itself) use the `record` request, checked by peer uid.
Consumers ignore unknown event types; breaking changes bump `SCHEMA_VERSION`.

## Consequences

- The watcher is "just another consumer", which is how it can be first-class
  without being a dependency.
- New capabilities (replay for evaluating watchers, log shipping, a digest)
  read the same stream instead of adding new hooks.
- The log is valuable on its own as an audit trail the agent can't rewrite.
