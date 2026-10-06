# Threat model

What this setup protects, against what, and — just as important — what it
doesn't. If you change the design, update this file in the same PR.

## What we're protecting

1. **Your identity and accounts:** your files, Keychain, browser sessions, email, cloud accounts.
2. **The controls themselves:** managed settings, the event log, the health check, the watcher.
3. **The ability to recover:** backups, and the knowledge of what happened.
4. **Money:** API spend and anything else the agent's credentials can buy.
5. **The agent's own work:** its projects and credentials (lowest priority; recoverable from backups and by revoking keys).

## Where problems come from

- **Mistakes.** The agent misunderstands a task and deletes, overwrites or
  spends something it shouldn't. By far the most common case.
- **Manipulated input.** The agent reads content (web pages, files, issues,
  emails) written by someone else, and that content tries to steer it
  ("prompt injection"). The agent can't reliably tell instructions from data.
- **Compromised dependencies.** Something the agent installs or runs is malicious.
- **Loss of the machine.** Theft, hardware failure, power loss.

We assume the person at the admin account (you) is trusted, and that macOS's
account permissions work as documented.

## What each layer covers

| Risk | Account isolation | Locked settings + auto mode | Scoped secrets | Monitoring | Backups |
|------|:-:|:-:|:-:|:-:|:-:|
| Agent reads or changes your personal data | **Prevents** | — | — | — | Restores |
| Agent disables its own controls | **Prevents** (root-owned) | Reduces | — | **Detects** (health check) | — |
| Agent deletes its own work | — | Reduces | — | Records | **Restores** |
| Agent leaks a credential | — | Reduces | **Limits damage** (scoped, revocable) | Records | — |
| Agent runs up costs | — | Reduces | **Limits** (spend caps) | Records | — |
| Agent harms the backups | **Prevents** | — | — | **Detects** (backup age) | — |
| Mac stolen | — | — | Revoke keys | — | FileVault (if on) |
| Mac silently stops working | — | — | — | **Detects** (health check) | — |

"Reduces" means it catches many cases but can be wrong or gotten around; only
the layers that are enforced by the OS ("Prevents") hold no matter what.

## Known limits

Read these before trusting the system with something that matters.

1. **Leaks can't be undone.** Backups repair *integrity* (deleted or changed
   files). Nothing restores *confidentiality*: once a secret has left the
   machine, the only fix is to revoke it. That's why the agent only gets
   credentials that are scoped and revocable, and why your own never live in
   its account.

2. **Claude Code's settings are enforced by Claude Code.** The agent's Claude
   Code binary lives in its own home folder (`~/.local/bin`, per-user
   install). Managed settings, auto mode and the hook are rules that program
   agrees to follow. They're strong against mistakes and most manipulation,
   but code running with the agent's full privileges could get around them.
   The OS boundary does not have this weakness.

3. **Permission rules cover Claude Code's built-in tools.** A deny rule like
   `Read(~/.ssh/**)` applies to the Read tool, not necessarily to a shell
   command that reads the same file. Treat those rules as guard rails. The
   protection is that the agent's account only contains what it's allowed to have.

4. **Hooks don't block when they fail.** If the hook crashes or times out,
   Claude Code proceeds. The design accepts this (ADR 0003) and makes up for
   it with the health check. It also means a gate-mode watcher can only stop
   what it sees in time.

5. **The event socket accepts events from any local account.** The agent can
   add noise to its own log, but can't edit or delete what's there, and every
   tool event carries the sender's uid.

6. **Managed settings are machine-wide.** Your own Claude Code sessions on
   the Mini get the same baseline and are recorded too. That's intended on a
   dedicated machine; don't install this on a computer you use for other work.

7. **Your home IP is attributable to you.** Traffic from the agent comes
   from your address. Using a residential connection is fine for ordinary
   browsing and building; it doesn't make automated access acceptable to a
   site that forbids it, and anything the agent does online is traceable to
   your household.

8. **The watcher is probabilistic.** An AI watcher (when added) will miss
   some things and flag some harmless ones. It adds coverage; it isn't a
   guarantee. It can never override the OS boundary or allow anything.

9. **FileVault only protects a machine that's off.** While the Mac is
   running and logged in, the disk is unlocked. FileVault matters for theft,
   not for anything the agent does.

## Out of scope (for now)

- Monitoring outbound network traffic. Worth adding (see roadmap); today the
  agent has unrestricted internet access by design.
- A determined attacker with physical access to the Mac.
- Compromise of your admin account or your phone.
