# shellcheck shell=bash
# Install Claude Code managed settings: the baseline the agent cannot change.

step_claude_settings() {
    step "Claude Code managed settings"
    local dest="$MACAGENT_MANAGED_SETTINGS" src="$REPO_DIR/config/managed-settings.json"
    python3 -m json.tool "$src" >/dev/null || die "$src is not valid JSON"

    ensure_dir "$(dirname "$dest")" 755 root:wheel
    if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then
        local backup
        backup="$dest.bak-$(date +%Y%m%d%H%M%S)"
        srun cp -p "$dest" "$backup"
        warn "replaced existing managed settings (backup: $backup)"
    fi
    install_file "$src" "$dest" 644 root:wheel
    info "note: managed settings apply to every account on this Mac, including yours"
    ok "auto mode on, bypass mode disabled, MacAgent hook registered"
}
