# Decision records

Short records of decisions that shape the system, so later contributors (human
or agent) know *why* and don't undo them by accident. Add one whenever you
change an invariant listed in `CLAUDE.md`, and never edit an accepted record's
decision: supersede it with a new one.

Format: Context → Decision → Consequences. Number sequentially.

| # | Decision |
|---|----------|
| [0001](0001-os-isolation-is-the-boundary.md) | The macOS account boundary is the security boundary |
| [0002](0002-never-allow.md) | The hook and the watcher never return "allow" |
| [0003](0003-watcher-optional-fail-to-baseline.md) | The watcher is optional; every failure falls back to the baseline, loudly |
| [0004](0004-python-stdlib-runtime.md) | Python 3.12, standard library only, in a root-owned virtualenv |
| [0005](0005-event-stream-backbone.md) | One event stream connects every component |
