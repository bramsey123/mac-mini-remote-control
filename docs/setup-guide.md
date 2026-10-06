# Setup guide

About an hour, most of it the manual steps at the end. Everything the script
does is safe to re-run; `--dry-run` shows the changes first.

## 0. Decide these first

**FileVault or unattended restart?** With FileVault on, a Mac that restarts
(power cut, crash, update) waits at the unlock screen until someone types the
password — no SSH, no agent, until you're physically there. With it off, the
Mac comes back on its own, but a stolen Mac's disk is readable.
- Mac stays at home, you're often away → many people choose off, and rely on
  the agent holding only revocable credentials.
- Either way, set `expect_filevault` in `config.toml` so the health check holds
  you to the choice.

**Whose Claude account does the agent use?** Remote Control needs a claude.ai
login in the agent account, so the agent account holds a session for that
Claude account. A separate account or seat for the agent keeps it scoped, the
same as with every other credential. If you use your own, know that the agent
account holds a session for your Claude account.

**Where do alerts go?** The setup uses [ntfy](https://ntfy.sh): free, and the
phone app pushes instantly. Pick a long random topic name — anyone who knows a
public topic can read it.

## 1. Prerequisites

On the Mac mini:
- macOS 13 or later, signed in to **your own admin account**
- Xcode Command Line Tools: `xcode-select --install`
- Homebrew: https://brew.sh
- A Time Machine destination (external disk or network share)

On your phone: the Claude app, Tailscale, ntfy, and an SSH app (e.g. Termius or Blink).

## 2. Run setup

```bash
git clone https://github.com/bramsey123/mac-mini-remote-control.git
cd mac-mini-remote-control
./setup/install.sh --dry-run
./setup/install.sh
```

You'll be asked for your password (sudo) and to choose a password for the new
`agent` account. Steps, in order — run any one alone with `--only <step>`:

| Step | Does |
|------|------|
| `preflight` | Checks macOS, that you're an admin, Command Line Tools, Homebrew |
| `packages` | Installs Python 3.12, tmux, 1Password CLI; checks for Tailscale |
| `accounts` | Creates the standard `agent` account and hidden `_agentwatch` service account; makes sure `agent` isn't an admin |
| `power` | Disables sleep, enables wake-on-network and restart after power failure |
| `runtime` | Installs the `macagent` package into a root-owned virtualenv; links `macagent` and `agent-session` into `/usr/local/bin` |
| `config` | Creates config, secrets, state and log directories with the right owners |
| `claude-settings` | Installs the managed settings (backs up any existing file) |
| `services` | Installs and (re)starts the event daemon and health check |
| `agent-home` | Creates `~agent/work`, a private `agent.env`, and installs Claude Code for the agent |
| `verify` | Runs `sudo macagent check` |

It ends with a list of the manual steps below.

## 3. Remote access

1. Install Tailscale on the Mac (Mac App Store or tailscale.com/download), sign
   in, and turn it on. Do the same on your phone with the same account.
2. System Settings → General → Sharing → **Remote Login**: on, and allow
   only your admin account. (You reach the agent account with `sudo -iu agent`.)
3. Set up SSH keys from your phone's SSH app and connect to the Mac's
   Tailscale name (e.g. `ssh you@mac-mini`). Once keys work, consider turning
   off password logins for SSH.

## 4. Backups

Turn on Time Machine (System Settings → General → Time Machine) to a disk the
agent account can't write to — an external disk or NAS is right. The `backups`
health check alerts when the newest backup is older than
`backup_max_age_hours`. If that check says tmutil lacks Full Disk Access, grant
it to Homebrew's `python3.12` binary in System Settings → Privacy & Security →
Full Disk Access.

## 5. Notifications

```bash
openssl rand -hex 16          # use this as your topic
sudo nano "/Library/Application Support/MacAgent/config.toml"
```

```toml
[notify]
backend = "ntfy"
topic = "the-random-topic"
```

If you use an ntfy access token, store it where the event daemon (and not the
agent) can read it, and point `token_file` at it:

```bash
sudo install -m 640 -o root -g _agentwatch /dev/stdin \
  "/Library/Application Support/MacAgent/secrets/ntfy-token" <<< "tk_your_token"
```

Subscribe to the same topic in the ntfy app, then:

```bash
sudo macagent config-check
sudo macagent notify-test
sudo launchctl kickstart -k system/local.macagent.eventd   # eventd picks up config on restart
```

## 6. The agent's secrets (1Password)

1. In 1Password, create a vault just for the agent (e.g. "Agent"). Put only
   agent-specific items in it: API keys created for the agent, with spending
   limits set at the provider.
2. Create a **service account** with read access to that vault only
   (1Password → Developer → Service Accounts). Copy its token.
3. Put the token in the agent's private env file:
   ```bash
   sudo -iu agent
   nano ~/.config/macagent/agent.env       # OP_SERVICE_ACCOUNT_TOKEN=ops_...
   chmod 600 ~/.config/macagent/agent.env
   ```
4. The agent fetches what it needs on demand, e.g.
   `op read "op://Agent/OpenWeather/credential"`. Revoking the service account
   in 1Password cuts off everything at once.

## 7. Log the agent in (once, interactively)

Over SSH from your admin account:

```bash
sudo -iu agent
cd ~/work
claude              # /login with the Claude account you chose in step 0; trust the folder
                    # check the mode shown is auto, then exit
claude remote-control   # accept the one-time confirmation, then Ctrl-C
agent-session start
```

The session now appears in the Claude app on your phone. It runs in tmux, so
it survives SSH disconnects; `agent-session attach` shows it in the terminal
(detach with Ctrl-b d).

## 8. Check everything

```bash
sudo macagent check
```

Expect OK for everything, INFO for `watcher` (not enabled) and maybe for
`filevault` / `agent-secrets`. Each check's line says what to fix.

## Daily use

| Want to… | Do |
|----------|-----|
| Give the agent work | Claude app → the Mac mini session |
| See what it's been doing | `macagent events -n 30` (or `--type tool.pre`) |
| Check protections | `sudo macagent check` |
| Restart the session after a reboot | `sudo -iu agent agent-session start` |
| Stop it | `sudo -iu agent agent-session stop` |

## Updating

```bash
cd mac-mini-remote-control && git pull && ./setup/install.sh
```

Re-running setup reinstalls the package, re-applies permissions and restarts
the services. Your `config.toml` and `agent.env` are kept.

## Removing

There's no uninstall script yet (roadmap). By hand:

```bash
sudo launchctl bootout system/local.macagent.eventd
sudo launchctl bootout system/local.macagent.healthcheck
sudo rm /Library/LaunchDaemons/local.macagent.*.plist
sudo rm "/Library/Application Support/ClaudeCode/managed-settings.json"
sudo rm -rf /usr/local/lib/macagent /usr/local/bin/macagent /usr/local/bin/agent-session \
  "/Library/Application Support/MacAgent" /Library/Logs/MacAgent
sudo dscl . -delete /Users/_agentwatch && sudo dscl . -delete /Groups/_agentwatch
# and, if you want: sudo sysadminctl -deleteUser agent
```
