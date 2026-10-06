# shellcheck shell=bash
# Keep the Mac awake and reachable, and bring it back after a power cut.

step_power() {
    step "Power settings"
    # sleep 0: never sleep. disksleep 0: keep disks spinning.
    # womp 1: wake for network access. autorestart 1: boot after power failure.
    srun pmset -a sleep 0 disksleep 0 womp 1 autorestart 1
    ok "sleep disabled, wake-on-network and auto-restart enabled"
    info "if FileVault is on, the Mac waits at the unlock screen after a restart (see docs/setup-guide.md)"
}
